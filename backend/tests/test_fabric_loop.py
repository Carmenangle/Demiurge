"""智能编造自由循环单测：工具调用→结果回填→完成；审批暂停；失败换方案。"""
from __future__ import annotations

import json

from app.services import capability_sandbox, fabric_loop


def _fake_chat(decisions):
    calls = {"n": 0}

    def fake(base, key, model, system, user, **kw):
        idx = calls["n"]
        calls["n"] += 1
        return json.dumps(decisions[min(idx, len(decisions) - 1)], ensure_ascii=False)
    return fake


def test_自由循环调用工具后完成(tmp_path):
    # 模型第 1 步调 file.list_dir，看到结果后第 2 步宣布完成
    # 路径用 tmp_path：CI 是 Linux runner，不能依赖 Windows 盘符存在
    chat = _fake_chat([
        {"tool": "file.list_dir", "params": {"path": str(tmp_path)}},
        {"done": True, "reply": "目录已确认，任务完成"},
    ])
    outcome = fabric_loop.run_loop(
        intent="看看目录", configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="m", chat_fn=chat,
        max_steps=6,
    )
    assert outcome.status == "done"
    assert outcome.reply == "目录已确认，任务完成"
    assert len(outcome.steps) == 1
    assert outcome.steps[0]["ok"] is True


def test_approval模式durable工具暂停():
    chat = _fake_chat([
        {"tool": "file.write_text", "params": {"path": "D:/tmp/x.txt", "content": "x"}},
    ])
    outcome = fabric_loop.run_loop(
        intent="写文件", configured_models={"chat", "image"},
        access_mode=capability_sandbox.ACCESS_APPROVAL, lease_id="",
        output_dir="D:/tmp",
        chat_base="", chat_key="", chat_model="m", chat_fn=chat,
        max_steps=6,
    )
    assert outcome.status == "awaiting_approval"
    assert outcome.pending_tool == "file.write_text"


def test_full模式durable工具直接执行(tmp_path):
    lease = capability_sandbox.grant(
        "fabric:test", [], mode=capability_sandbox.ACCESS_FULL)
    target = tmp_path / "note.txt"
    chat = _fake_chat([
        {"tool": "file.write_text",
         "params": {"path": str(target), "content": "自由模式写入"}},
        {"done": True, "reply": "写好了"},
    ])
    outcome = fabric_loop.run_loop(
        intent="写个文件", configured_models={"chat", "image"},
        access_mode=capability_sandbox.ACCESS_FULL, lease_id=lease["id"],
        output_dir=str(tmp_path),
        chat_base="", chat_key="", chat_model="m", chat_fn=chat,
        max_steps=6,
    )
    assert outcome.status == "done"
    assert outcome.steps[0]["ok"] is True
    assert target.read_text(encoding="utf-8") == "自由模式写入"
    capability_sandbox.revoke(lease["id"])


def test_工具失败回填后模型换方案():
    # 第 1 步调用不存在的能力 → 失败回填 → 第 2 步模型完成
    chat = _fake_chat([
        {"tool": "ghost.action", "params": {}},
        {"done": True, "reply": "能力不存在，我改用直接回复"},
    ])
    outcome = fabric_loop.run_loop(
        intent="测试失败换方案", configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="m", chat_fn=chat,
        max_steps=6,
    )
    assert outcome.status == "done"
    assert outcome.steps[0]["ok"] is False
    assert "能力清单里没有" in outcome.steps[0]["error"]


def test_步数上限():
    chat = _fake_chat([{"tool": "file.list_dir", "params": {"path": "D:/"}}])
    outcome = fabric_loop.run_loop(
        intent="永远不完成", configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="m", chat_fn=chat,
        max_steps=3,
    )
    assert outcome.status == "step_limit"
    assert len(outcome.steps) == 3


# ── 带图自由循环（看图反推→生成文档）────────────────────────────────────────

def test_带图自由循环走多模态消息():
    # 图片必须以 image_url 内容块进首条用户消息，不得走 JSON 字符串通道
    captured = {}

    def fake_multimodal(base, key, model, messages, **kw):
        captured["messages"] = messages
        return json.dumps({"done": True, "reply": "已结合图片完成"}, ensure_ascii=False)

    def must_not_call(*_args, **_kwargs):
        raise AssertionError("带图时不得走纯文本 chat_fn 通道")

    outcome = fabric_loop.run_loop(
        intent="反推外貌并生成套装文档", history="上文：讨论了角色发型。",
        images=["data:image/png;base64,AAA"],
        configured_models={"chat"},
        chat_base="", chat_key="", chat_model="m",
        chat_fn=must_not_call, chat_messages_fn=fake_multimodal,
        max_steps=4,
    )
    assert outcome.status == "done"
    system, first_user = captured["messages"][0], captured["messages"][1]
    assert first_user["role"] == "user"
    assert [part["type"] for part in first_user["content"]] == ["text", "image_url"]
    assert "上文：讨论了角色发型。" in first_user["content"][0]["text"]
    assert first_user["content"][1]["image_url"]["url"] == "data:image/png;base64,AAA"
    assert "【附图】" in system["content"]


def test_带图自由循环读穿搭文档落盘套装文档(tmp_path):
    # 场景1端到端（模型决策 mock）：看图 → 读时尚文档 → 在作品 docs/ 落盘套装文档
    fashion = tmp_path / "时尚穿搭.md"
    fashion.write_text("## 春季\n风衣配长裙", encoding="utf-8")
    lease = capability_sandbox.grant(
        "fabric:img", [], mode=capability_sandbox.ACCESS_FULL)
    decisions = [
        {"tool": "file.read_text", "params": {"path": str(fashion)}},
        {"tool": "doc.create_repo", "params": {
            "base": str(tmp_path), "rel_path": "套装/唐柚-四季穿搭.md",
            "content": "# 四季套装\n春·套一：风衣配长裙"}},
        {"done": True, "reply": "套装文档已生成"},
    ]
    calls = {"n": 0}

    def fake(base, key, model, messages, **kw):
        idx = min(calls["n"], len(decisions) - 1)
        calls["n"] += 1
        return json.dumps(decisions[idx], ensure_ascii=False)

    outcome = fabric_loop.run_loop(
        intent="看图反推外貌，结合穿搭文档生成四季套装文档",
        images=["data:image/png;base64,AAA"], output_dir=str(tmp_path),
        configured_models={"chat"}, access_mode=capability_sandbox.ACCESS_FULL,
        lease_id=lease["id"],
        chat_base="", chat_key="", chat_model="m",
        chat_messages_fn=fake, max_steps=8,
    )
    assert outcome.status == "done"
    assert len(outcome.steps) == 2 and all(s["ok"] for s in outcome.steps)
    written = tmp_path / "docs" / "套装" / "唐柚-四季穿搭.md"
    assert written.is_file()
    assert "风衣配长裙" in written.read_text(encoding="utf-8")
    capability_sandbox.revoke(lease["id"])
