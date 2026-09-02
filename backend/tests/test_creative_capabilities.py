"""P3 通用创作能力单测：file.write_text / file.list_dir / worldbook.upsert_repo /
character.upsert_repo / doc.create_repo 的真实失败路径与安全边界。"""
from __future__ import annotations

import pytest

from app.services import capability_handlers as ch
from app.services import capability_registry as cr
from app.services import plan_validator
from app.services.structured_contracts import GenerationPlan, PlanBudgets, PlanStep


# ── 注册视图 ──────────────────────────────────────────────────────────────────

def test_新能力已注册且分级正确():
    ops = {cap.operation: cap for cap in cr.all_capabilities()}
    assert ops["file.write_text"].side_effect_level == cr.SIDE_EFFECT_DURABLE
    assert ops["file.list_dir"].side_effect_level == cr.SIDE_EFFECT_READONLY
    assert ops["worldbook.upsert_repo"].side_effect_level == cr.SIDE_EFFECT_DURABLE
    assert ops["character.upsert_repo"].side_effect_level == cr.SIDE_EFFECT_DURABLE
    assert ops["doc.create_repo"].side_effect_level == cr.SIDE_EFFECT_DURABLE


# ── file.write_text ───────────────────────────────────────────────────────────

def test_write_text_拒绝相对路径与目录(tmp_path):
    with pytest.raises(ValueError, match="绝对路径"):
        ch.write_text_file("相对路径.txt", "x")
    with pytest.raises(ValueError, match="目录"):
        ch.write_text_file(str(tmp_path), "x")


def test_write_text_默认拒绝覆盖_可显式覆盖(tmp_path):
    target = tmp_path / "a.txt"
    first = ch.write_text_file(str(target), "一")
    assert target.read_text(encoding="utf-8") == "一"
    assert first["bytes"] > 0
    with pytest.raises(ValueError, match="已存在"):
        ch.write_text_file(str(target), "二")
    ch.write_text_file(str(target), "三", overwrite=True)
    assert target.read_text(encoding="utf-8") == "三"


# ── file.list_dir ─────────────────────────────────────────────────────────────

def test_list_dir_列出但不返回内容(tmp_path):
    (tmp_path / "b.txt").write_text("秘密", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    out = ch.list_dir(str(tmp_path))
    names = {e["name"] for e in out["entries"]}
    assert names == {"b.txt", "sub"}
    assert all("text" not in e and "content" not in e for e in out["entries"])


def test_file_edit按str_replace修改文件(tmp_path):
    target = tmp_path / "code.py"
    target.write_text("x = 1\ny = 2\n", encoding="utf-8")
    out = ch.edit_text_file(str(target), "x = 1", "x = 42")
    assert target.read_text(encoding="utf-8") == "x = 42\ny = 2\n"
    assert out["replaced"] == 1
    with pytest.raises(ValueError, match="不存在"):
        ch.edit_text_file(str(target), "nope", "x")


def test_run_shell执行命令并回传输出(tmp_path):
    out = ch.run_shell("echo hello", cwd=str(tmp_path))
    assert out["exit_code"] == 0
    assert "hello" in out["stdout"]
    with pytest.raises(ValueError, match="cwd"):
        ch.run_shell("echo x", cwd="")

# ── worldbook.upsert_repo ─────────────────────────────────────────────────────

def test_worldbook_upsert建快照并按key更新追加(tmp_path, monkeypatch):
    base, repo_id = str(tmp_path), "work"
    first = ch.upsert_repo_worldbook(base, repo_id, [
        {"keys": ["林晚"], "comment": "女主", "content": "旧内容"},
        {"keys": ["苏城"], "comment": "男主", "content": "路人"},
    ])
    assert first["applied"] == 2
    from app.services import worldbook_store
    book = worldbook_store.read_repo_snapshot(base, repo_id)
    assert book is not None and len(worldbook_store._raw_entries(book)) == 2

    second = ch.upsert_repo_worldbook(base, repo_id, [
        {"keys": ["林晚"], "comment": "女主", "content": "新内容"},
        {"keys": ["赵姨"], "comment": "配角", "content": "帮手"},
    ])
    assert second["applied"] == 2
    book = worldbook_store.read_repo_snapshot(base, repo_id)
    entries = list(worldbook_store._raw_entries(book))
    assert len(entries) == 3  # 林晚更新，赵姨追加
    lin = next(e for e in entries if e.get("keys") == ["林晚"])
    assert lin["content"] == "新内容"


# ── character.upsert_repo ─────────────────────────────────────────────────────

def test_character_upsert写入作品域并读回(tmp_path):
    out = ch.upsert_repo_character(str(tmp_path), {
        "name": "测试角色", "description": "一个测试角色",
        "personality": "", "scenario": "", "first_mes": "你好", "mes_example": "",
    })
    from app.services import character_store
    card = character_store.read_card(str(tmp_path), "测试角色")
    assert card is not None and card.get("name") == "测试角色"
    assert out["name"] == "测试角色"


# ── doc.create_repo ───────────────────────────────────────────────────────────

def test_doc_create写作品docs并拒绝穿越(tmp_path):
    out = ch.create_repo_doc(str(tmp_path), "guide/start.md", "# 开始\n\n你好")
    assert tmp_path.joinpath("docs", "guide", "start.md").read_text(encoding="utf-8") == "# 开始\n\n你好"
    assert out["bytes"] > 0
    with pytest.raises(ValueError, match="穿越"):
        ch.create_repo_doc(str(tmp_path), "../evil.md", "x")
    with pytest.raises(ValueError, match=r"\.md"):
        ch.create_repo_doc(str(tmp_path), "evil.txt", "x")
    with pytest.raises(FileExistsError):
        ch.create_repo_doc(str(tmp_path), "guide/start.md", "再来")


# ── plan_validator 写路径域 ───────────────────────────────────────────────────

def test_validator拦file_write_text越域写(tmp_path):
    ok = plan_validator.validate(GenerationPlan(
        intent="写作品内文件", repo_id="work",
        budgets=PlanBudgets(max_steps=1, max_gpu_tasks=0, max_llm_calls=0),
        steps=[PlanStep(id="s1", operation="file.write_text",
                        params={"path": str(tmp_path / "ok.txt"), "content": "x"})],
        approval_required=["file.write_text"],
    ), capabilities=cr.all_capabilities(), allowed_prefix=str(tmp_path))
    assert ok == []

    bad = GenerationPlan(
        intent="越域写", repo_id="work",
        budgets=PlanBudgets(max_steps=1, max_gpu_tasks=0, max_llm_calls=0),
        steps=[PlanStep(id="s1", operation="file.write_text",
                        params={"path": "D:/outside/evil.txt", "content": "x"})],
        approval_required=["file.write_text"],
    )
    errors = plan_validator.validate(
        bad, capabilities=cr.all_capabilities(), allowed_prefix=str(tmp_path))
    assert any("越出作品域" in e for e in errors)