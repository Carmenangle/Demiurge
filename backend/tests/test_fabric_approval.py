"""approval 自由循环断点续跑（2026-09-06）：内容生成型任务走自由循环 + 逐能力审批。

覆盖：fabric_loop 审批暂停→批准→断点续跑不重复；plan_compiler_node approval 内容型
分支落 checkpoint、对话审批词续跑、固化完成；过期租约批准自动续期（2026-09-14）。
"""
from __future__ import annotations

import json
import time

import pytest

from app.services import (
    agent_graph as ag,
    capability_sandbox,
    fabric_checkpoint,
    fabric_loop,
    plan_tasks,
    task_progress_store,
)


def _fake_chat(decisions):
    calls = {"n": 0}

    def fake(base, key, model, system, user, **kw):
        idx = calls["n"]
        calls["n"] += 1
        return json.dumps(decisions[min(idx, len(decisions) - 1)], ensure_ascii=False)
    return fake


def _seed_already_asked(ctx: dict) -> None:
    """把本轮置为「事前固化询问已经问过」，跳过 2026-09-10 新增的前置闸门。

    本文件测的是 approval 自由循环与断点续跑本身。事前固化询问是后来加的前置闸门
    （清单无同类时先问一句「要不要固化」），这里用「同一意图已问过」的状态跳过它——
    等价于用户已经忽略/答复过那次询问，不再打断。若不是这样，本文件每个用例都得
    多绕一轮「要/直接跑」，噪音大于收益。
    """
    from app.services import recipe_match, recipe_offer_store
    message = str(ctx.get("message") or "")
    recipe_offer_store.save({
        "id": "seed-already-asked",
        "fingerprint": recipe_match.fingerprint(message),
        "intent": message,
        "output_dir": str(ctx.get("output_dir") or ""),
        "thread_id": str(ctx.get("thread_id") or ""),
    })


def test_fabric_loop_审批后断点续跑(tmp_path):
    # 首次：模型要写文件（durable）→ 租约未授权 → awaiting + checkpoint 历史带出
    lease = capability_sandbox.grant(
        "fabric:resume-test", [], mode=capability_sandbox.ACCESS_APPROVAL)
    target = tmp_path / "note.txt"
    chat1 = _fake_chat([
        {"tool": "file.write_text",
         "params": {"path": str(target), "content": "数据"}},
    ])
    out1 = fabric_loop.run_loop(
        intent="写文件", configured_models={"chat"},
        access_mode=capability_sandbox.ACCESS_APPROVAL,
        lease_id=lease["id"], output_dir=str(tmp_path),
        chat_base="", chat_key="", chat_model="m", chat_fn=chat1, max_steps=6,
    )
    assert out1.status == "awaiting_approval"
    assert out1.pending_tool == "file.write_text"
    assert out1.messages, "awaiting 必须带出对话历史供 checkpoint"

    # 批准：追加授权 → 从断点续跑（与真实链路一致：注入批准提示让模型重发被拦操作）
    capability_sandbox.grant_operation(lease["id"], "file.write_text", path=str(tmp_path))
    chat2 = _fake_chat([
        {"tool": "file.write_text",
         "params": {"path": str(target), "content": "数据"}},
        {"done": True, "reply": "已写入并完成"},
    ])
    resume_messages = list(out1.messages) + [{
        "role": "user",
        "content": json.dumps({
            "approval_granted": "用户已批准操作「file.write_text」，请重新发起该操作执行。"},
            ensure_ascii=False),
    }]
    out2 = fabric_loop.run_loop(
        intent="写文件", configured_models={"chat"},
        access_mode=capability_sandbox.ACCESS_APPROVAL,
        lease_id=lease["id"], output_dir=str(tmp_path),
        chat_base="", chat_key="", chat_model="m", chat_fn=chat2, max_steps=6,
        resume={"messages": resume_messages, "steps": out1.steps},
    )
    assert out2.status == "done"
    assert target.is_file()
    assert len(out2.steps) == 1, "断点续跑只执行被拦步骤，不重复已执行步骤"


def test_plan_compiler_approval内容型走自由循环并落checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_tasks, "_agent_access_mode",
                        lambda: capability_sandbox.ACCESS_APPROVAL)
    store: dict = {}
    monkeypatch.setattr(task_progress_store, "load", lambda ns: store.get(ns, {}))
    monkeypatch.setattr(task_progress_store, "save",
                        lambda ns, tasks, limit=100: store.update({ns: tasks}))

    work = tmp_path / "作品"
    work.mkdir()
    target = tmp_path / "note.txt"
    chat = _fake_chat([
        {"tool": "file.write_text",
         "params": {"path": str(target), "content": "x"}},
        {"tool": "file.write_text",
         "params": {"path": str(target), "content": "x"}},
        {"done": True, "reply": "合集卡已整理完成"},
    ])
    ctx = {
        "chat_base": "b", "chat_key": "k", "chat_model": "m",
        "workspace_mode": "story", "has_mcp": False, "agent_cfg": None,
        "message": "根据这本小说制作合集卡，把全局机制、系统判定机制、角色等内容整理好",
        "output_dir": str(work), "repo_id": "work", "thread_id": "work",
        "chat_fn": chat,
    }
    state = {"user_text": ctx["message"], "images": [], "_ctx": ctx}
    _seed_already_asked(ctx)

    # 首次调用 → awaiting + checkpoint 落盘
    res = ag.plan_compiler_node(state)
    assert "需要批准" in res["result_text"]
    pend = fabric_checkpoint.pending(output_dir=str(work))
    assert pend and pend[0]["pending_tool"] == "file.write_text"

    # 对话审批词「批准」→ 追加授权 → 断点续跑 → 完成
    res2 = ag._fabric_approval_word("批准", ctx, str(work), [])
    assert res2 is not None
    assert "合集卡已整理完成" in res2["result_text"]
    assert target.is_file(), "批准后续跑应完成写入"
    assert not fabric_checkpoint.pending(output_dir=str(work)), "完成后 checkpoint 应删除"

    capability_sandbox._reset_for_tests()


def test_approval_step_limit保留断点_继续可接续(tmp_path, monkeypatch):
    """2026-09-07 治本（实锤：step_limit 后 checkpoint 被无条件 delete，
    用户无法再接续）。修复后：_fabric_approval_word 只在 done 删断点，
    step_limit/error 保留 running 断点；用户后续「继续」可 resumable 恢复。"""
    from app.services import fabric_checkpoint
    monkeypatch.setattr(plan_tasks, "_agent_access_mode",
                        lambda: capability_sandbox.ACCESS_APPROVAL)
    store: dict = {}
    monkeypatch.setattr(task_progress_store, "load", lambda ns: store.get(ns, {}))
    monkeypatch.setattr(task_progress_store, "save",
                        lambda ns, tasks, limit=100: store.update({ns: tasks}))

    work = tmp_path / "作品"
    work.mkdir()
    # 模型永远调工具不 done → step_limit
    chat = _fake_chat([
        {"tool": "file.write_text", "params": {"path": str(tmp_path / "x.txt"), "content": "x"}},
        {"tool": "file.write_text", "params": {"path": str(tmp_path / "x.txt"), "content": "x"}},
        {"tool": "file.write_text", "params": {"path": str(tmp_path / "x.txt"), "content": "x"}},
        {"tool": "file.write_text", "params": {"path": str(tmp_path / "x.txt"), "content": "x"}},
    ])
    ctx = {
        "chat_base": "b", "chat_key": "k", "chat_model": "m",
        "workspace_mode": "story", "has_mcp": False, "agent_cfg": None,
        "message": "根据这本小说制作合集卡", "output_dir": str(work),
        "repo_id": "work", "thread_id": "work", "chat_fn": chat,
    }
    state = {"user_text": ctx["message"], "images": [], "_ctx": ctx}
    _seed_already_asked(ctx)

    res = ag.plan_compiler_node(state)
    # 首次 awaiting（第一个 write 被拦）
    assert "需要批准" in res["result_text"]
    assert fabric_checkpoint.pending(output_dir=str(work))

    # 批准续跑 → 授予 write → 模型连续写 3 次后 step_limit
    res2 = ag._fabric_approval_word("批准", ctx, str(work), [])
    assert "step_limit" in res2["result_text"], f"应 step_limit 而非删除: {res2}"
    # 关键断言：step_limit 后断点必须保留（running），用户可再接续
    pend = fabric_checkpoint.pending(output_dir=str(work))
    assert pend, "step_limit 后断点必须保留，不能删除"
    assert pend[0]["status"] == "running"
    # 再接续：resumable 能取到（模拟用户「继续补写」重发）
    cp = fabric_checkpoint.resumable(output_dir=str(work), thread_id="work")
    assert cp is not None and cp["status"] == "running"
    assert cp.get("steps"), "断点应带已执行步骤"
    fabric_checkpoint.delete(pend[0]["id"])
    capability_sandbox._reset_for_tests()


# ── 过期租约批准（2026-09-14 实盘 bug 修复）────────────────────────────────
# 断点停在审批点超过租约 TTL（24h）时：grant_operation 此前只查存在+未撤销，
# 追加「成功」→ 续跑 authorize 查过期再拦 → 批准死循环；若后端重启过租约不在
# 内存，批准则删断点丢进度。修复=授权时拒绝过期租约 + 批准路径先续期救活。


def test_grant_operation_过期租约拒绝追加授权():
    lease = capability_sandbox.grant(
        "fabric:expired-unit", [], mode=capability_sandbox.ACCESS_APPROVAL)
    capability_sandbox._LEASES[lease["id"]]["expires_at"] = 1.0  # 置为远古 = 已过期
    with pytest.raises(PermissionError, match="已过期"):
        capability_sandbox.grant_operation(lease["id"], "file.write_text")


def test_renew_救活过期租约_续期后可授权可执行():
    lease = capability_sandbox.grant(
        "fabric:expired-unit", [], mode=capability_sandbox.ACCESS_APPROVAL)
    capability_sandbox._LEASES[lease["id"]]["expires_at"] = 1.0
    with pytest.raises(PermissionError):
        capability_sandbox.grant_operation(lease["id"], "file.write_text")
    renewed = capability_sandbox.renew(lease["id"])
    assert renewed["expires_at"] > time.time()
    # 续期不扩大授权面：追加仍需逐条批准
    capability_sandbox.grant_operation(lease["id"], "file.write_text", path="D:/w")
    assert capability_sandbox.authorize(lease["id"], "file.write_text", path="D:/w")
    with pytest.raises(PermissionError):
        capability_sandbox.authorize(lease["id"], "doc.create_repo")
    # 撤销后不可续期
    capability_sandbox.revoke(lease["id"])
    with pytest.raises(PermissionError):
        capability_sandbox.renew(lease["id"])


def test_批准过期租约自动续期续跑_断点不删进度保留(tmp_path, monkeypatch):
    """实盘场景：断点停 43h > TTL 24h → 租约过期。批准必须续期救活租约并
    接续完成，而不是续跑即再拦（死循环）或删断点丢已完成步骤。"""
    monkeypatch.setattr(plan_tasks, "_agent_access_mode",
                        lambda: capability_sandbox.ACCESS_APPROVAL)
    store: dict = {}
    monkeypatch.setattr(task_progress_store, "load", lambda ns: store.get(ns, {}))
    monkeypatch.setattr(task_progress_store, "save",
                        lambda ns, tasks, limit=100: store.update({ns: tasks}))

    work = tmp_path / "作品"
    work.mkdir()
    target = tmp_path / "note.txt"
    chat = _fake_chat([
        {"tool": "file.write_text", "params": {"path": str(target), "content": "x"}},
        {"tool": "file.write_text", "params": {"path": str(target), "content": "x"}},
        {"done": True, "reply": "合集卡已整理完成"},
    ])
    ctx = {
        "chat_base": "b", "chat_key": "k", "chat_model": "m",
        "workspace_mode": "story", "has_mcp": False, "agent_cfg": None,
        "message": "根据这本小说制作合集卡，把全局机制整理好",
        "output_dir": str(work), "repo_id": "work", "thread_id": "work",
        "chat_fn": chat,
    }
    _seed_already_asked(ctx)

    res = ag.plan_compiler_node({"user_text": ctx["message"], "images": [], "_ctx": ctx})
    assert "需要批准" in res["result_text"]
    pend = fabric_checkpoint.pending(output_dir=str(work))
    assert pend and pend[0]["pending_tool"] == "file.write_text"
    # 模拟断点停留超过 TTL：租约已过期（未撤销，盘面仍登记）
    capability_sandbox._LEASES[pend[0]["lease_id"]]["expires_at"] = 1.0

    res2 = ag._fabric_approval_word("批准", ctx, str(work), [])
    assert "合集卡已整理完成" in res2["result_text"], res2
    assert target.is_file(), "续期后批准应真正执行写入"
    assert not fabric_checkpoint.pending(output_dir=str(work)), "完成后 checkpoint 应删除"
    capability_sandbox._reset_for_tests()


def test_批准租约已撤销时删断点并明确提示重发(tmp_path, monkeypatch):
    """租约不存在/已撤销确实无法接续：删断点是合理的，但必须明说原因与出路，
    不能只回「审批失败」让用户反复批准。"""
    monkeypatch.setattr(plan_tasks, "_agent_access_mode",
                        lambda: capability_sandbox.ACCESS_APPROVAL)
    store: dict = {}
    monkeypatch.setattr(task_progress_store, "load", lambda ns: store.get(ns, {}))
    monkeypatch.setattr(task_progress_store, "save",
                        lambda ns, tasks, limit=100: store.update({ns: tasks}))

    work = tmp_path / "作品"
    work.mkdir()
    chat = _fake_chat([
        {"tool": "file.write_text",
         "params": {"path": str(tmp_path / "note.txt"), "content": "x"}},
    ])
    ctx = {
        "chat_base": "b", "chat_key": "k", "chat_model": "m",
        "workspace_mode": "story", "has_mcp": False, "agent_cfg": None,
        "message": "根据这本小说制作合集卡", "output_dir": str(work),
        "repo_id": "work", "thread_id": "work", "chat_fn": chat,
    }
    _seed_already_asked(ctx)

    res = ag.plan_compiler_node({"user_text": ctx["message"], "images": [], "_ctx": ctx})
    assert "需要批准" in res["result_text"]
    pend = fabric_checkpoint.pending(output_dir=str(work))
    capability_sandbox.revoke(pend[0]["lease_id"])

    res2 = ag._fabric_approval_word("批准", ctx, str(work), [])
    assert "审批失败" in res2["result_text"]
    assert "重新发起" in res2["result_text"], "必须告知出路：重发任务"
    assert not fabric_checkpoint.pending(output_dir=str(work)), "无法接续的断点应清理"
    capability_sandbox._reset_for_tests()