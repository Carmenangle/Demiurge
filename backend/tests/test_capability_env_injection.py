"""环境注入参数 + 卡归属判定治本回归（2026-09-09）。

根因 ①：落盘类能力的 base/repo_id 必须由运行环境注入，但清单散落在三处硬编码
（plan_tasks.submit_task / plan_compiler / fabric_loop）——新增能力逐处漏加。
character.embed_worldbook / workspace.export_to_library 漏加后：计划能编译，
执行时 handler 缺必需参数（TypeError: missing 2 required positional arguments），
任务停在 partial，用户看不到产物（实锤）。

根因 ②：这两个能力 glob 全库「*/card.json」。base 是作品库根时（实锤 pictures 下
有妈妈娼馆/玫瑰与繁花/御仙）会把当前作品的世界书写进别的作品的卡——数据污染。

覆盖：① 注册表清单与 handler 真实签名一致（防未来漏加）；② 执行层按签名兜底；
③ 卡归属判定（标记优先、卡名=作品名回退、都不匹配则跳过）。
"""
from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

from app.services import capability_registry as cr
from app.services import character_store, plan_tasks, repo_meta, worldbook_store
from app.services.capability_handlers import character_embed_worldbook


def test_环境注入清单覆盖所有需要环境参数的能力():
    """自动一致性：任何 handler 的必需参数里出现 base/repo_id/output_dir/cwd，
    该能力必须登记进 ENV_INJECTED_OPS——否则计划编排会漏注入（实锤 embed_worldbook）。"""
    missing = []
    for cap in cr.all_capabilities():
        module_name, _, func_name = str(cap.handler).partition(":")
        try:
            handler = getattr(importlib.import_module(module_name), func_name)
            signature = inspect.signature(handler)
        except Exception:  # noqa: BLE001 - 未实现/不可导入的 handler 由注册表校验管
            continue
        needs = [name for name, param in signature.parameters.items()
                 if name in cr.ENV_PARAM_SOURCES
                 and param.default is inspect.Parameter.empty
                 and param.kind not in (inspect.Parameter.VAR_POSITIONAL,
                                        inspect.Parameter.VAR_KEYWORD)]
        if needs and cap.operation not in cr.ENV_INJECTED_OPS:
            missing.append((cap.operation, needs))
    assert missing == [], f"这些能力需要环境注入却未登记：{missing}"


def test_执行层兜底按handler签名补齐缺失参数():
    """执行层最后一道防线：计划来自编译/配方/fabric 任一来源漏注入都在这里补齐。"""
    assert plan_tasks._env_params_for_handler(
        "character.embed_worldbook", {}, output_dir="C:/works", repo_id="repo-1",
    ) == {"base": "C:/works", "repo_id": "repo-1"}
    # 已显式给的参数不覆盖
    assert plan_tasks._env_params_for_handler(
        "character.embed_worldbook", {"repo_id": "keep"},
        output_dir="C:/works", repo_id="repo-1",
    ) == {"base": "C:/works"}
    # 未注册能力 → 不猜参数
    assert plan_tasks._env_params_for_handler(
        "nope.nope", {}, output_dir="x", repo_id="y") == {}


def _mk_card(folder: Path, name: str, entries: list[dict] | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    card = {"name": name, "description": "d", "personality": "", "scenario": "",
            "first_mes": "hi", "mes_example": "",
            "character_book": {"entries": entries or []}}
    (folder / "card.json").write_text(
        json.dumps(card, ensure_ascii=False), encoding="utf-8")
    return folder


def _snapshot(base: Path, repo_id: str, content: str = "新内容") -> None:
    (base / repo_id).mkdir(parents=True, exist_ok=True)
    worldbook_store.save_repo_snapshot(str(base), repo_id, {"entries": [
        {"comment": "系统判定机制·A", "content": content, "keys": ["a"]}]})


class TestEmbedScope:
    """embed_worldbook 只认本作品的主卡——绝不 glob 全库（数据污染红线）。"""

    def test_只内嵌带归属标记的本作品卡(self, tmp_path: Path):
        base = tmp_path / "works"
        base.mkdir()
        repo_id = "repo1"
        other = _mk_card(base / "别人的卡", "别人的卡",
                         [{"comment": "别人的条目", "content": "原样"}])
        other_before = (other / "card.json").read_text(encoding="utf-8")
        mine = _mk_card(base / "御仙", "御仙")
        character_store.write_work_marker(str(base), "御仙", repo_id)
        _snapshot(base, repo_id)
        out = character_embed_worldbook(str(base), repo_id)
        assert [item["card"] for item in out["embedded"]] == ["御仙"]
        embedded = json.loads((mine / "card.json").read_text(encoding="utf-8"))
        assert embedded["character_book"]["entries"][0]["content"] == "新内容"
        assert (other / "card.json").read_text(encoding="utf-8") == other_before

    def test_归属标记指向别的作品时不处理(self, tmp_path: Path):
        base = tmp_path / "works"
        base.mkdir()
        repo_id = "repo1"
        alien = _mk_card(base / "别的作品的卡", "别的作品的卡")
        alien_before = (alien / "card.json").read_text(encoding="utf-8")
        character_store.write_work_marker(str(base), "别的作品的卡", "other-repo")
        _snapshot(base, repo_id)
        out = character_embed_worldbook(str(base), repo_id)
        assert out["embedded"] == [] and "跳过" in out["note"]
        assert (alien / "card.json").read_text(encoding="utf-8") == alien_before

    def test_无标记时按卡名等于作品名回退(self, tmp_path: Path, monkeypatch):
        """固化02 约定「卡名=作品名」，无标记也认；其余无标记卡一律跳过。"""
        monkeypatch.setattr(repo_meta, "repo_name", lambda rid: "我的作品")
        base = tmp_path / "works"
        base.mkdir()
        repo_id = "repo1"
        mine = _mk_card(base / "我的作品", "我的作品")
        other = _mk_card(base / "别人的卡", "别人的卡",
                         [{"comment": "别人的条目", "content": "原样"}])
        other_before = (other / "card.json").read_text(encoding="utf-8")
        _snapshot(base, repo_id)
        out = character_embed_worldbook(str(base), repo_id)
        assert [item["card"] for item in out["embedded"]] == ["我的作品"]
        embedded = json.loads((mine / "card.json").read_text(encoding="utf-8"))
        assert embedded["character_book"]["entries"][0]["content"] == "新内容"
        assert (other / "card.json").read_text(encoding="utf-8") == other_before

    def test_世界书快照为空时报错(self, tmp_path: Path):
        base = tmp_path / "works"
        base.mkdir()
        (base / "repo1").mkdir(parents=True, exist_ok=True)
        worldbook_store.save_repo_snapshot(str(base), "repo1", {"entries": []})
        try:
            character_embed_worldbook(str(base), "repo1")
        except ValueError as exc:
            assert "无条目" in str(exc)
        else:  # pragma: no cover - 快照无条目必须报错
            raise AssertionError("空快照应报错")


def test_migrate_mechanical落卡写归属标记(tmp_path: Path):
    """落卡即写归属：embed/export 靠它只处理本作品的卡。"""
    from app.services import character_card
    from app.services.capability_handlers import migrate_mechanical

    card = {"name": "御仙", "spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "御仙", "first_mes": "开场",
                     "character_book": {"entries": [
                         {"comment": "系统判定机制·A", "content": "正文", "keys": ["a"]}]}}}
    png = tmp_path / "card.png"
    png.write_bytes(character_card.build_png_card(card))
    base = tmp_path / "works"
    res = migrate_mechanical(str(png), str(base), "repo1")
    assert res["entries"] == 1
    assert character_store.read_work_marker(str(base), "御仙") == "repo1"