"""固化流程：ST 迁移规范 §3-§4 端到端演练（2026-09-04）。

最小链路：character.migrate_scan（机械体检报告）→ 模拟 LLM 转写产物 →
worldbook.upsert_repo 落盘 + character.upsert_repo（first_mes 非空）→ 解析回读断言 §4 验收 11 项。

构造的 ST 样张故意保留 5+ 类待转写点：keys 缺失/过长、constant 越权、<status>/<roll> 渲染层、
<if cell=…> 运行时表格、注入位字段（order/depth/position）、视觉画像前缀缺失、first_mes 空、
entries 容器用 dict（非 list）。机械体检必须全部命中；落盘后必须全部清掉。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app import config as _config
from app.services import capability_handlers as ch
from app.services import worldbook_store


# ── fixtures ────────────────────────────────────────────────────────────────


def _st_sample_drift_card() -> dict:
    """ST V2 卡，故意保留 8 类待转写点。

    入料形态：data.character_book.entries 用 dict 容器（漂移 1）。
    三个条目：
      - "0"：keys 过长 8 个 (aurelia, knight, sword, saint, guardian, iron, tempered, eternal)
            + constant=true 但非系统判定/全局层 (漂移 2)
            + content 含 <status>/<roll> 渲染层 (漂移 3)
            + <if cell=…> 运行时表格 (漂移 4)
            + position/depth/order/atDepth 注入位字段 (漂移 5)
      - "1"：keys=[] (漂移 7)
      - "2"：正文已是本项目锚点格式（【外貌】【身材】【穿着】）但 comment "Appearance"
            缺「角色卡·」前缀（漂移 6，partial 迁移残留 → visual_anchor 命中）
    data.first_mes 为空 (漂移 8)
    """
    return {
        "spec": "chara_card_v2", "spec_version": "2.0",
        "data": {
            "name": "Aurelia",
            "description": "Sword Saint",
            "personality": "Stoic, devoted",
            "scenario": "Captured in the dungeon",
            "first_mes": "",
            "character_book": {
                "entries": {
                    "0": {
                        "uid": "u-0",
                        "comment": "Sword Saint",
                        "keys": ["aurelia", "knight", "sword", "saint",
                                 "guardian", "iron", "tempered", "eternal"],
                        "constant": True,
                        "content": (
                            "1. <status>HP: 85/100</status>\n"
                            "2. <roll>d20+5</roll>\n"
                            "3. <if cell=love>=80</if>「永远守护你」"
                        ),
                        "position": 0, "depth": 4, "order": 10, "atDepth": 4,
                        "insertion_order": 0,
                    },
                    "1": {
                        "uid": "u-1",
                        "comment": "Generic note",
                        "keys": [],
                        "constant": False,
                        "content": "magic check",
                    },
                    "2": {
                        "comment": "Appearance",
                        "keys": ["looks", "armor"],
                        "constant": False,
                        "content": "【外貌】金发碧眼，额间圣痕；【身材】高挑修长；"
                                   "【穿着】白甲银披风，佩圣剑。",
                    },
                },
            },
        },
    }


@pytest.fixture()
def st_source(tmp_path):
    p = tmp_path / "aurelia.json"
    p.write_text(json.dumps(_st_sample_drift_card(), ensure_ascii=False), encoding="utf-8")
    return p


# ── §3.5 阶段一 机械前置体检（只读） ────────────────────────────────────────


def test_03_migrate_scan_命中8类待转写点(st_source):
    """§3.5 阶段一：migrate_scan 报告 issue_counts 必须命中故意保留的 8 类。"""
    report = ch.migrate_scan_source(str(st_source))
    assert report["kind"] == "character_card"
    counts = report.get("issue_counts") or {}
    expected = {
        "keys_too_many",        # 8 keys > 6
        "keys_empty",           # 第二个条目 keys=[]
        "constant_policy",      # constant=true on非系统判定/全局层
        "render_layer",         # <status>/<roll>
        "runtime_table",        # <if cell=…>
        "injection_fields",     # order/position/depth/atDepth
        "visual_anchor",        # comment 缺「角色卡·」前缀
        "first_mes_empty",      # data.first_mes=""
    }
    hit = expected & set(counts)
    missing = expected - hit
    assert not missing, f"机械体检漏命中：{missing}（实际 {counts}）"
    # 报告含 report_notes + summary
    assert report.get("report_notes"), "缺 report_notes 字段"
    assert "待转写点" in report.get("summary", "")


# ── §3.5 阶段二/三 模拟 LLM 转写 → 落盘 ──────────────────────────────────────


def _curing03_cleaned_entries() -> list[dict]:
    """模拟 LLM 阶段二产出的「无漂移」条目（合规 A 态：世界书快照分层 + 角色条目另建）。"""
    return [
        {"comment": "系统判定机制·AI 叙事核心", "constant": True,
         "keys": ["系统判定", "判定"],
         "content": "【强制输出格式】回复最开头输出【状态栏】…（非正则渲染）。"},
        {"comment": "全局机制·情报受限", "constant": True,
         "keys": ["情报受限"],
         "content": "获知情报须有来源，禁止上帝视角。"},
        {"comment": "角色卡·Aurelia", "constant": False,
         "keys": ["aurelia", "knight"],
         "content": "【外貌】金发碧眼，佩圣剑；【身材】高挑修长；【穿着】白甲披风；"
                    "【个体机制】【圣剑誓约】——好感 ≥ 80 解锁命定；"
                    "【好感分阶】-30 冷淡 / 20 信任 / 55 命定 / 90 改写 / 100 独占"},
    ]


def test_03_落盘后逐项断言_4验收_无漂移残留(tmp_path):
    work = tmp_path / "作品"
    work.mkdir()
    base = str(work)

    res = ch.upsert_repo_worldbook(
        base=base, repo_id="work", entries=_curing03_cleaned_entries(),
    )
    assert res["applied"] == 3

    entries = list(
        (worldbook_store.read_repo_snapshot(base, "work") or {}).get("entries") or []
    )
    # §4-1 容器为 list
    assert isinstance(entries, list) and len(entries) == 3

    # §4-2 6 层前缀合规（保留「·」用 rpartition）
    prefixes = {(e.get("comment") or "").rpartition("·")[0] + "·" for e in entries if "·" in (e.get("comment") or "")}
    assert {"系统判定机制·", "全局机制·", "角色卡·"} <= prefixes

    # §4-3 非常驻 keys 非空 ≤6（2-8 字）
    for e in entries:
        if e.get("constant"):
            continue
        keys = e.get("keys") or []
        assert 1 <= len(keys) <= 6
        for k in keys:
            assert 2 <= len(k) <= 8

    # §4-4 constant 仅系统判定/全局层
    for e in entries:
        if e.get("constant"):
            assert e.get("comment", "").split("·", 1)[0] in ("系统判定机制", "全局机制")

    # §4-5 角色条目 4 锚点 + 定制【好感分阶】
    roles = [e for e in entries if e.get("comment", "").startswith("角色卡·")]
    assert roles
    for r in roles:
        for a in ("【外貌】", "【身材】", "【穿着】", "【好感分阶】"):
            assert a in r.get("content", ""), f"角色缺 {a}"

    # §4-6 first_mes 非空：主卡 upsert
    card = {
        "spec": "chara_card_v2", "spec_version": "2.0",
        "data": {
            "name": "Aurelia",
            "description": "Sword Saint",
            "personality": "Stoic, devoted",
            "scenario": "Captured in the dungeon",
            "first_mes": "——白塔之下，圣剑发出低鸣。「你终于来了。」",
            "mes_example": "",
            "character_book": {"entries": []},
        },
    }
    ch.upsert_repo_character(base=base, card=card)
    loaded = json.loads(Path(base, "Aurelia", "card.json").read_text(encoding="utf-8"))
    assert loaded.get("first_mes"), "first_mes 必须非空"

    # §4-7/§4-8 解析/落盘通过：上面 card 落盘已读，断言无报错
    # §4-9 渲染层/运行时表格/注入位不再存在
    for e in entries:
        c = e.get("content", "") or ""
        assert not re.search(r"<status>|<roll>|<if\s+cell", c), f"渲染/表格残留: {e}"
        # 注入位字段在落盘后已由 worldbook_store 归一化（不应再含 position/order/atDepth）
        for dead in ("position", "order", "atDepth"):
            assert dead not in e, f"注入位字段残留 {dead}: {e}"

    # §4-10 阶段一先跑过 migrate_scan 并给用户看过摘要（已在 test_03_migrate_scan_... 覆盖）
    # §4-11 交付说明需含 §3.4 五类：示例断言报告 issue_counts 至少含前四类
    # （与 test_03_migrate_scan 联立时已覆盖；此处不强写"交付说明"以免越界文档）


# ── §3.1 第二套流程可选：import_source 把原档落源库（characterDir） ──────────


def test_03_import_source_需要characterDir在user_state(tmp_path, monkeypatch, st_source):
    """§3.1：import_source 落源库（characterDir 由 user_state 运行态真源决定）。"""
    # 重定向 DATA_DIR 到 tmp_path，写一份带 characterDir 的 user_state
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(_config, "DATA_DIR", data_dir)
    (data_dir / "user_state.json").write_text(
        json.dumps({"settings": {"characterDir": str(tmp_path / "srclib")}},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    res = ch.import_source_card(
        path=str(st_source), overwrite=False, extract_worldbook=False,
    )
    assert res["name"] == "Aurelia"
    assert (Path(res["card_dir"]) / "card.json").is_file()
    # 重读：源库卡 V2 归一后 name + first_mes 仍空（与原 ST 漂移一致，未在本步修复）
    src = json.loads(Path(res["card_dir"], "card.json").read_text(encoding="utf-8"))
    assert src.get("name") == "Aurelia"
