"""approval 审批绑定「具体动作」并一次性消费（2026-11-05 T3）。

合同：用户批准的是他看到的那一个动作（operation + 环境归一后实参的指纹），
不是「这个工具以后都能调」。因此——
- 换参数（换路径 / 换内容 / 换 overwrite）= 新动作 ⇒ 重新 awaiting；
- 重复同一动作 = 第二次仍 awaiting（一次性条目已在第一次消费）；
- handler 失败也算已使用，重试必须重新批准；
- full 与不传 action_hash 的旧调用（机械计划整计划批准 / 演示流程 / 越域读取）行为不变。

探针口径：只用 tmp_path，不执行真实 shell，租约盘面由 conftest 重定向到临时目录。
"""
from __future__ import annotations

import json
import re
import threading

import pytest

from app.services import (
    agent_fabric as af,
    capability_sandbox,
    fabric_loop,
)

WRITE = "file.write_text"


def _fake_chat(decisions):
    calls = {"n": 0}

    def fake(base, key, model, system, user, **kw):
        idx = calls["n"]
        calls["n"] += 1
        return json.dumps(decisions[min(idx, len(decisions) - 1)], ensure_ascii=False)
    return fake


def _recording_chat(decisions):
    """同上，但记下每次收到的 user 消息，用于断言「批准后续跑提示」的内容。"""
    seen: list[str] = []
    inner = _fake_chat(decisions)

    def fake(base, key, model, system, user, **kw):
        seen.append(str(user))
        return inner(base, key, model, system, user, **kw)
    fake.seen = seen  # type: ignore[attr-defined]
    return fake


def _lease(name: str, mode: str = capability_sandbox.ACCESS_APPROVAL) -> dict:
    return capability_sandbox.grant(f"fabric:{name}", [], mode=mode)


def _run(decisions, *, lease_id, output_dir, access_mode=None, resume=None, max_steps=4):
    return fabric_loop.run_loop(
        intent="动作审批用例", configured_models={"chat"},
        access_mode=access_mode or capability_sandbox.ACCESS_APPROVAL,
        lease_id=lease_id, output_dir=output_dir,
        chat_base="", chat_key="", chat_model="m",
        chat_fn=_fake_chat(decisions), max_steps=max_steps, resume=resume)


def _approved_messages(outcome, note="用户已批准这一个动作，请逐字重发。"):
    return list(outcome.messages) + [
        {"role": "user", "content": json.dumps({"approval_granted": note}, ensure_ascii=False)}]


def _caps(lease_id: str) -> list[dict]:
    with capability_sandbox._LOCK:
        lease = capability_sandbox._LEASES.get(lease_id) or {}
    return [dict(item) for item in (lease.get("capabilities") or [])]


# ── 指纹本体 ────────────────────────────────────────────────────────────────

def test_指纹只认实参_键序无关而内容或路径变化即变():
    h1 = fabric_loop.action_fingerprint(WRITE, {"path": "D:/w/a.txt", "content": "A"})
    h2 = fabric_loop.action_fingerprint(WRITE, {"content": "A", "path": "D:/w/a.txt"})
    assert h1 == h2, "同一动作的键序不得改变指纹"
    assert h1 != fabric_loop.action_fingerprint(WRITE, {"path": "D:/w/a.txt", "content": "B"})
    assert h1 != fabric_loop.action_fingerprint(WRITE, {"path": "D:/w/b.txt", "content": "A"})
    assert h1 != fabric_loop.action_fingerprint("file.edit", {"path": "D:/w/a.txt", "content": "A"})


# ── 租约层一次性语义（含旧调用兼容）────────────────────────────────────────

def test_一次性授权消费后不再放行_重复调用需要重新批准():
    lease = _lease("once")
    h = fabric_loop.action_fingerprint(WRITE, {"path": "D:/w/a.txt", "content": "A"})
    capability_sandbox.grant_operation(lease["id"], WRITE, path="D:/w", action_hash=h)
    capability_sandbox.authorize(lease["id"], WRITE, path="D:/w", action_hash=h)
    with pytest.raises(PermissionError):
        capability_sandbox.authorize(lease["id"], WRITE, path="D:/w", action_hash=h)
    # 不传 hash 的调用方也不得命中一次性条目（否则「批一个动作」等于「批整个工具」）
    with pytest.raises(PermissionError):
        capability_sandbox.authorize(lease["id"], WRITE, path="D:/w")
    capability_sandbox.revoke(lease["id"])


def test_不同指纹不能撞进同operation的幂等分支():
    lease = _lease("other-hash")
    h1 = fabric_loop.action_fingerprint(WRITE, {"path": "D:/w/a.txt", "content": "A"})
    h2 = fabric_loop.action_fingerprint(WRITE, {"path": "D:/w/a.txt", "content": "B"})
    capability_sandbox.grant_operation(lease["id"], WRITE, path="D:/w", action_hash=h1)
    # 追加 h2 必须是新增一条，而不是被「同 operation+path 已授权」短路掉
    capability_sandbox.grant_operation(lease["id"], WRITE, path="D:/w", action_hash=h2)
    bound = [c for c in _caps(lease["id"]) if c.get("action_hash")]
    assert [c["action_hash"] for c in bound] == [h1, h2], bound
    # 两条各自放行一次，互不顶替
    capability_sandbox.authorize(lease["id"], WRITE, path="D:/w", action_hash=h1)
    capability_sandbox.authorize(lease["id"], WRITE, path="D:/w", action_hash=h2)
    with pytest.raises(PermissionError):
        capability_sandbox.authorize(lease["id"], WRITE, path="D:/w", action_hash=h1)

    # 只批过 A 的租约：B 既不能命中幂等分支，也不能命中授权分支
    only_a = _lease("only-a")
    capability_sandbox.grant_operation(only_a["id"], WRITE, path="D:/w", action_hash=h1)
    with pytest.raises(PermissionError):
        capability_sandbox.authorize(only_a["id"], WRITE, path="D:/w", action_hash=h2)
    capability_sandbox.revoke(lease["id"])
    capability_sandbox.revoke(only_a["id"])


def test_同一动作再批一次可再消费一次_不陷入批准死循环():
    """消费过的条目若只走幂等短路，grant_operation 报「已授权」而 authorize 仍判
    consumed ⇒ 批准→再拦→再批准 永圈（2026-09-14 过期租约那轮同形事故）。"""
    lease = _lease("re-arm")
    h = fabric_loop.action_fingerprint(WRITE, {"path": "D:/w/a.txt", "content": "A"})
    for _ in range(2):
        capability_sandbox.grant_operation(lease["id"], WRITE, path="D:/w", action_hash=h)
        capability_sandbox.authorize(lease["id"], WRITE, path="D:/w", action_hash=h)
        with pytest.raises(PermissionError):
            capability_sandbox.authorize(lease["id"], WRITE, path="D:/w", action_hash=h)
    assert len([c for c in _caps(lease["id"]) if c.get("action_hash")]) == 1, "重新装填不扩授权面"
    capability_sandbox.revoke(lease["id"])


def test_旧式不带指纹的授权保持持续语义():
    """机械计划整计划批准 / 演示流程 / 越域读取都走这条：不传 action_hash ⇒ 不消费。
    本用例是「T3 不得把旧调用方一起收紧」的接线锁。"""
    lease = _lease("standing")
    capability_sandbox.grant_operation(lease["id"], WRITE, path="D:/w")
    for _ in range(3):
        capability_sandbox.authorize(lease["id"], WRITE, path="D:/w")
    capability_sandbox.authorize(lease["id"], WRITE, path="D:/w",
                                 action_hash=fabric_loop.action_fingerprint(WRITE, {"x": 1}))
    assert _caps(lease["id"])[0].get("consumed") is None
    capability_sandbox.revoke(lease["id"])


def test_full租约不因一次性语义消费_同一动作可连续放行():
    lease = _lease("full-once", mode=capability_sandbox.ACCESS_FULL)
    h = fabric_loop.action_fingerprint(WRITE, {"path": "D:/w/a.txt", "content": "A"})
    for _ in range(2):
        capability_sandbox.authorize(lease["id"], WRITE, path="D:/w", action_hash=h)
    assert all(not c.get("consumed") for c in _caps(lease["id"]))
    capability_sandbox.revoke(lease["id"])


def test_并发消费同一动作至多一次成功():
    lease = _lease("race")
    h = fabric_loop.action_fingerprint(WRITE, {"path": "D:/w/a.txt", "content": "A"})
    capability_sandbox.grant_operation(lease["id"], WRITE, path="D:/w", action_hash=h)
    barrier = threading.Barrier(4)
    results: list[str] = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            capability_sandbox.authorize(lease["id"], WRITE, path="D:/w", action_hash=h)
            outcome = "ok"
        except PermissionError:
            outcome = "denied"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1, results
    capability_sandbox.revoke(lease["id"])


def test_过期租约续期后指纹仍未消费_续期不新增授权():
    lease = capability_sandbox.grant("fabric:renew", [], ttl_seconds=600,
                                     mode=capability_sandbox.ACCESS_APPROVAL,
                                     now=lambda: 1000.0)
    h = fabric_loop.action_fingerprint(WRITE, {"path": "D:/w/a.txt", "content": "A"})
    capability_sandbox.grant_operation(lease["id"], WRITE, path="D:/w", action_hash=h,
                                       now=lambda: 1000.0)
    with pytest.raises(PermissionError):
        capability_sandbox.authorize(lease["id"], WRITE, path="D:/w", action_hash=h,
                                     now=lambda: 1e9)
    before = _caps(lease["id"])
    capability_sandbox.renew(lease["id"], now=lambda: 1e9)
    assert _caps(lease["id"]) == before, "续期只延时间，不得新增或改动授权条目"
    capability_sandbox.authorize(lease["id"], WRITE, path="D:/w", action_hash=h,
                                 now=lambda: 1e9)
    with pytest.raises(PermissionError):
        capability_sandbox.authorize(lease["id"], WRITE, path="D:/w", action_hash=h,
                                     now=lambda: 1e9)
    capability_sandbox.revoke(lease["id"])


# ── 循环层：批准 A 之后只有 A 能跑 ─────────────────────────────────────────

def _first_awaiting(work, lease_id):
    a = work / "a.txt"
    out = _run([{"tool": WRITE, "params": {"path": str(a), "content": "A"}}],
               lease_id=lease_id, output_dir=str(work))
    assert out.status == "awaiting_approval"
    assert out.pending_tool == WRITE
    assert out.pending_action_hash, "写类 awaiting 必须带出动作指纹"
    assert out.pending_params.get("path") == str(a)
    return a, out


def test_批准写a后改发写b_b不生成且重新awaiting(tmp_path):
    work = tmp_path / "作品"
    work.mkdir()
    lease = _lease("a-then-b")
    a, out1 = _first_awaiting(work, lease["id"])
    capability_sandbox.grant_operation(lease["id"], WRITE, path=str(work),
                                       action_hash=out1.pending_action_hash)

    b = work / "b.txt"
    out2 = _run([
        {"tool": WRITE, "params": {"path": str(a), "content": "A"}},
        {"tool": WRITE, "params": {"path": str(b), "content": "B"}},
        {"done": True, "reply": "两个都写了"},
    ], lease_id=lease["id"], output_dir=str(work),
        resume={"messages": _approved_messages(out1), "steps": out1.steps})
    assert a.is_file(), "批准的那个动作必须真跑"
    assert not b.exists(), "换路径=新动作，不得套用旧批准"
    assert out2.status == "awaiting_approval"
    assert out2.pending_params.get("path") == str(b)
    assert out2.pending_action_hash != out1.pending_action_hash
    capability_sandbox.revoke(lease["id"])


def test_批准同路径改内容后旧字节不变并重新awaiting(tmp_path):
    work = tmp_path / "作品"
    work.mkdir()
    lease = _lease("same-path-new-content")
    a, out1 = _first_awaiting(work, lease["id"])
    capability_sandbox.grant_operation(lease["id"], WRITE, path=str(work),
                                       action_hash=out1.pending_action_hash)

    out2 = _run([
        {"tool": WRITE, "params": {"path": str(a), "content": "A"}},
        {"tool": WRITE, "params": {"path": str(a), "content": "覆盖成别的"}},
        {"done": True, "reply": "改了"},
    ], lease_id=lease["id"], output_dir=str(work),
        resume={"messages": _approved_messages(out1), "steps": out1.steps})
    assert a.read_text(encoding="utf-8") == "A", "改内容=新动作，旧文件字节必须不动"
    assert out2.status == "awaiting_approval"
    capability_sandbox.revoke(lease["id"])


def test_批准A后模型首个动作就是B_B不得套用A的批准(tmp_path):
    """比「先重发 A 再发 B」更狠的一步：批准后模型不重发被批动作，直接换动作。
    若指纹只认工具名（不含实参），这一步 B 会在**消费之前**命中同一条 grant 直接跑掉。"""
    work = tmp_path / "作品"
    work.mkdir()
    lease = _lease("first-is-b")
    a, out1 = _first_awaiting(work, lease["id"])
    capability_sandbox.grant_operation(lease["id"], WRITE, path=str(work),
                                       action_hash=out1.pending_action_hash)

    secret = work / "另存为.txt"
    out2 = _run([
        {"tool": WRITE, "params": {"path": str(secret), "content": "没批过的东西"}},
        {"done": True, "reply": "写完"},
    ], lease_id=lease["id"], output_dir=str(work),
        resume={"messages": _approved_messages(out1), "steps": out1.steps})
    assert not secret.exists(), "换动作=没批过，第一个动作也不能跑"
    assert out2.status == "awaiting_approval"
    assert out2.pending_params.get("path") == str(secret)
    capability_sandbox.revoke(lease["id"])


def test_批准一次后重复同一动作第二次仍awaiting(tmp_path):
    """旧合同可以「批一次、循环里连写 N 次」；新合同批一次只放行一次。"""
    work = tmp_path / "作品"
    work.mkdir()
    lease = _lease("repeat-same")
    a, out1 = _first_awaiting(work, lease["id"])
    capability_sandbox.grant_operation(lease["id"], WRITE, path=str(work),
                                       action_hash=out1.pending_action_hash)

    out2 = _run([
        {"tool": WRITE, "params": {"path": str(a), "content": "A"}},
        {"tool": WRITE, "params": {"path": str(a), "content": "A"}},
        {"done": True, "reply": "写完"},
    ], lease_id=lease["id"], output_dir=str(work),
        resume={"messages": _approved_messages(out1), "steps": out1.steps})
    assert out2.status == "awaiting_approval", "重复同一动作也必须重新请求批准"
    assert out2.pending_action_hash == out1.pending_action_hash
    capability_sandbox.revoke(lease["id"])


def test_越域只读换另一文件时新路径重新审批(tmp_path):
    """读类走「按路径持续授权」，不是一次性：批准读 out1 后读 out2 必须另批一次。"""
    work = tmp_path / "作品"
    work.mkdir()
    outside = tmp_path / "域外1.txt"
    outside.write_text("一", encoding="utf-8")
    outside2 = tmp_path / "域外2.txt"
    outside2.write_text("二", encoding="utf-8")
    lease = _lease("out-read")

    out1 = _run([{"tool": "file.read_text", "params": {"path": str(outside)}}],
                lease_id=lease["id"], output_dir=str(work))
    assert out1.status == "awaiting_approval"
    assert out1.pending_paths == [str(outside)]
    assert not out1.pending_action_hash, "读类不绑一次性指纹"
    assert out1.pending_params.get("path") == str(outside), "待批动作的实参必须摊给用户看"
    capability_sandbox.grant_operation(lease["id"], "file.read_text", path=str(outside))

    out2 = _run([
        {"tool": "file.read_text", "params": {"path": str(outside)}},
        {"tool": "file.read_text", "params": {"path": str(outside2)}},
        {"done": True, "reply": "都读了"},
    ], lease_id=lease["id"], output_dir=str(work),
        resume={"messages": _approved_messages(out1), "steps": out1.steps})
    assert out2.status == "awaiting_approval", "另一条域外路径必须重新审批"
    assert out2.pending_paths == [str(outside2)]
    capability_sandbox.revoke(lease["id"])


def test_同一条域外路径重复读不因一次性语义停顿(tmp_path):
    work = tmp_path / "作品"
    work.mkdir()
    outside = tmp_path / "素材.txt"
    outside.write_text("内容", encoding="utf-8")
    lease = _lease("same-read-twice")
    out1 = _run([{"tool": "file.read_text", "params": {"path": str(outside)}}],
                lease_id=lease["id"], output_dir=str(work))
    capability_sandbox.grant_operation(lease["id"], "file.read_text", path=str(outside))
    out2 = _run([
        {"tool": "file.read_text", "params": {"path": str(outside), "max_chars": 100}},
        {"done": True, "reply": "读完"},
    ], lease_id=lease["id"], output_dir=str(work),
        resume={"messages": _approved_messages(out1), "steps": out1.steps})
    # 读类是持续授权：同一条路径可放行（max_chars 变化不改授权粒度）
    assert out2.status == "done", out2.error
    capability_sandbox.revoke(lease["id"])


def test_越域写在full模式同样被拒且与新合同不冲突(tmp_path):
    """T1 的写侧收紧优先于 T3：越域写不是「批不批」的问题，一次性语义也不放宽它。"""
    work = tmp_path / "作品"
    work.mkdir()
    outside = tmp_path / "域外.txt"
    lease = _lease("full-once-loop", mode=capability_sandbox.ACCESS_FULL)
    out = _run([
        {"tool": WRITE, "params": {"path": str(outside), "content": "x"}},
        {"done": True, "reply": "试过"},
    ], lease_id=lease["id"], output_dir=str(work), access_mode=capability_sandbox.ACCESS_FULL)
    assert "越出作品域" in out.steps[0]["error"]
    assert not outside.exists()
    capability_sandbox.revoke(lease["id"])


def test_full模式连续同一写不触发awaiting(tmp_path):
    work = tmp_path / "作品"
    work.mkdir()
    x1 = work / "x.txt"
    x2 = work / "y.txt"
    lease = _lease("full-repeat", mode=capability_sandbox.ACCESS_FULL)
    out = _run([
        {"tool": WRITE, "params": {"path": str(x1), "content": "1"}},
        {"tool": WRITE, "params": {"path": str(x1), "content": "1"}},
        {"tool": WRITE, "params": {"path": str(x2), "content": "2"}},
        {"done": True, "reply": "完成"},
    ], lease_id=lease["id"], output_dir=str(work),
        access_mode=capability_sandbox.ACCESS_FULL)
    # 第 2 步失败必须来自 handler 的「未授权覆盖」护栏，而不是审批停了一下
    assert out.status == "done", out.error
    assert "未授权覆盖" in out.steps[1]["error"], out.steps[1]
    assert x2.is_file(), "full 模式连续写不因一次性语义需要再批"
    capability_sandbox.revoke(lease["id"])


def test_handler抛错也算本次批准已用_重试需要新批准(tmp_path):
    work = tmp_path / "作品"
    work.mkdir()
    dir_target = work / "目录"      # 写进一个目录 ⇒ handler 必然失败
    dir_target.mkdir()
    lease = _lease("handler-error")
    params = {"path": str(dir_target), "content": "x"}
    h = fabric_loop.action_fingerprint(WRITE, dict(params))
    capability_sandbox.grant_operation(lease["id"], WRITE, path=str(work), action_hash=h)
    out = _run([
        {"tool": WRITE, "params": dict(params)},
        {"tool": WRITE, "params": dict(params)},
        {"done": True, "reply": "重试成功"},
    ], lease_id=lease["id"], output_dir=str(work))
    assert out.steps[0]["ok"] is False, "写入目录必须失败"
    assert out.status == "awaiting_approval", "失败的动作也占掉这一次批准"
    # 已消费状态必须落得住：重载盘面仍是 consumed
    reloaded = json.loads(capability_sandbox.LEASE_FILE.read_text(encoding="utf-8"))
    mine = [item for item in reloaded if item.get("id") == lease["id"]][0]
    assert [c for c in mine["capabilities"] if c.get("action_hash") == h][0]["consumed"] is True
    capability_sandbox.revoke(lease["id"])


def test_旧running断点续跑不丢已完成步骤(tmp_path):
    """旧断点（running，无 pending_tool）不带动作授权，但进度必须原样接住。"""
    work = tmp_path / "作品"
    work.mkdir()
    lease = _lease("legacy-running")
    legacy_steps = [{"step": 1, "tool": "file.list_dir", "params": {}, "ok": True},
                    {"step": 2, "tool": WRITE, "params": {"path": "旧步"}, "ok": True}]
    out = _run([{"done": True, "reply": "补写完成"}], lease_id=lease["id"],
               output_dir=str(work),
               resume={"messages": [{"role": "user", "content": "历史"}],
                       "steps": legacy_steps})
    assert out.status == "done"
    assert out.steps[:2] == legacy_steps, "续跑不得丢掉断点里的已完成步骤"
    capability_sandbox.revoke(lease["id"])


# ── 对话审批词（真实链路）──────────────────────────────────────────────────

def test_批准后续跑提示带上确切动作让模型逐字重发(tmp_path, monkeypatch):
    import app.services.agent_graph as ag
    import app.services.fabric_checkpoint as fc
    import app.services.plan_tasks as pt
    import app.services.task_progress_store as tps

    monkeypatch.setattr(pt, "_agent_access_mode", lambda: capability_sandbox.ACCESS_APPROVAL)
    store: dict = {}
    monkeypatch.setattr(tps, "load", lambda ns: store.get(ns, {}))
    monkeypatch.setattr(tps, "save", lambda ns, tasks, limit=100: store.update({ns: tasks}))

    work = tmp_path / "作品"
    work.mkdir()
    target = work / "note.txt"
    decisions = [
        {"tool": WRITE, "params": {"path": str(target), "content": "正文"}},
        {"tool": WRITE, "params": {"path": str(target), "content": "正文"}},
        {"done": True, "reply": "整理完成"},
    ]
    seen: list[str] = []

    def chat(base, key, model, system, user, **kw):
        seen.append(str(user))
        return json.dumps(decisions[min(len(seen) - 1, len(decisions) - 1)], ensure_ascii=False)

    ctx = {
        "chat_base": "b", "chat_key": "k", "chat_model": "m",
        "workspace_mode": "story", "has_mcp": False, "agent_cfg": None,
        "message": "根据这本小说制作合集卡", "output_dir": str(work),
        "repo_id": "work", "thread_id": "work", "chat_fn": chat,
    }
    from app.services import recipe_match, recipe_offer_store
    recipe_offer_store.save({
        "id": "seed", "fingerprint": recipe_match.fingerprint(ctx["message"]),
        "intent": ctx["message"], "output_dir": str(work), "thread_id": "work"})

    res = ag.plan_compiler_node({"user_text": ctx["message"], "images": [], "_ctx": ctx})
    assert "需要批准" in res["result_text"]
    pend = fc.pending(output_dir=str(work))
    assert pend and pend[0]["pending_action_hash"], "断点必须存下待批动作指纹"
    assert pend[0]["pending_params"]["path"] == str(target)

    res2 = af._fabric_approval_word("批准", ctx, str(work), [])
    assert "整理完成" in res2["result_text"], res2
    assert target.is_file()
    grant_prompt = [s for s in seen if "approval_granted" in s][-1]
    assert "逐字重发" in grant_prompt, "续跑提示必须要求模型原样重发被批动作"
    # user 参数整条是 JSON 数组，内层提示被二次转义（反斜杠翻倍），所以按文件名断言
    assert "note.txt" in grant_prompt and WRITE in grant_prompt, "提示里要带上确切的实参"
    capability_sandbox._reset_for_tests()


def test_旧awaiting断点缺指纹时不补通配授权并按新合同重批(tmp_path, monkeypatch):
    """迁移合同：T3 之前落盘的 awaiting 断点没有 action_hash ⇒
    不得替用户把「这个工具 + 作品根」一起批了（静默提权）；保留进度，让模型重发、重批。"""
    import app.services.agent_graph as ag
    import app.services.fabric_checkpoint as fc
    import app.services.plan_tasks as pt
    import app.services.task_progress_store as tps

    monkeypatch.setattr(pt, "_agent_access_mode", lambda: capability_sandbox.ACCESS_APPROVAL)
    store: dict = {}
    monkeypatch.setattr(tps, "load", lambda ns: store.get(ns, {}))
    monkeypatch.setattr(tps, "save", lambda ns, tasks, limit=100: store.update({ns: tasks}))

    work = tmp_path / "作品"
    work.mkdir()
    target = work / "note.txt"
    chat = _fake_chat([
        {"tool": WRITE, "params": {"path": str(target), "content": "x"}},
        {"tool": WRITE, "params": {"path": str(target), "content": "x"}},
        {"tool": WRITE, "params": {"path": str(target), "content": "x"}},
        {"done": True, "reply": "完成"},
    ])
    ctx = {
        "chat_base": "b", "chat_key": "k", "chat_model": "m",
        "workspace_mode": "story", "has_mcp": False, "agent_cfg": None,
        "message": "根据这本小说制作合集卡", "output_dir": str(work),
        "repo_id": "work", "thread_id": "work", "chat_fn": chat,
    }
    from app.services import recipe_match, recipe_offer_store
    recipe_offer_store.save({
        "id": "seed", "fingerprint": recipe_match.fingerprint(ctx["message"]),
        "intent": ctx["message"], "output_dir": str(work), "thread_id": "work"})

    ag.plan_compiler_node({"user_text": ctx["message"], "images": [], "_ctx": ctx})
    pend = fc.pending(output_dir=str(work))[0]
    # 模拟旧版本落盘的断点：剥掉 T3 新增的两个字段，并留一条已完成步骤做进度锚点
    legacy = {k: v for k, v in pend.items()
              if k not in ("pending_params", "pending_action_hash")}
    legacy["steps"] = [{"step": 1, "tool": "file.list_dir",
                        "params": {"path": str(work)}, "ok": True}]
    fc.save(legacy)

    res = af._fabric_approval_word("批准", ctx, str(work), [])
    assert "需要批准" in res["result_text"], res
    assert not target.exists(), "缺指纹的旧断点不得被补授权后直接写盘"
    lease_id = str(legacy["lease_id"])
    assert not [c for c in _caps(lease_id)
                if c.get("operation") == WRITE and not str(c.get("action_hash") or "")], \
        "旧断点不得被补授「operation + 作品根」这种通配授权"
    with pytest.raises(PermissionError):
        capability_sandbox.authorize(lease_id, WRITE, path=str(work))
    # 重批一次后即恢复正常链路
    pend2 = fc.pending(output_dir=str(work))[0]
    assert pend2.get("pending_action_hash"), "重新拦截必须按新合同带上指纹"
    assert pend2.get("steps") == legacy["steps"], "旧断点的已完成步骤必须原样带过来"
    res2 = af._fabric_approval_word("批准", ctx, str(work), [])
    assert "完成" in res2["result_text"], res2
    assert target.is_file()
    capability_sandbox._reset_for_tests()


# ── 审批文本：用户看到的必须是真动作 ───────────────────────────────────────

def _outcome(**kw) -> fabric_loop.FabricOutcome:
    base = {"status": "awaiting_approval", "error": "需要批准"}
    base.update(kw)
    return fabric_loop.FabricOutcome(**base)


def test_审批文本完整展示命令与实参():
    long_cmd = "Get-Content " + " -Tail 5 ".join(f"part{i}.txt" for i in range(40))
    assert len(long_cmd) > 240
    text = af._fabric_approval_text(_outcome(
        pending_tool="project.run_shell",
        pending_params={"command": long_cmd, "cwd": "D:/works/驭云录"},
        pending_action_hash="abc"))
    assert long_cmd in text, "命令被截断=用户批了一条自己没看全的命令"
    assert "D:/works/驭云录" in text
    assert "批准只放行这一个动作" in text


def test_审批文本对密钥脱敏并对超长正文明示仅展示摘要():
    secret = "sk-不该出现在界面上的密钥"
    body = "长正文" * 400
    text = af._fabric_approval_text(_outcome(
        pending_tool=WRITE,
        pending_params={"path": "D:/w/a.txt", "content": body, "chat_key": secret},
        pending_action_hash="abc"))
    assert secret not in text, "密钥必须脱敏"
    assert "密钥已隐藏" in text
    assert "仅展示摘要" in text, "截断必须明说，不能暗示用户已看到完整内容"
    assert f"原文 {len(body)} 字" in text


def test_审批文本对越域读取用持续授权措辞():
    text = af._fabric_approval_text(_outcome(
        pending_tool="file.read_text",
        pending_params={"path": "D:/外部/素材.txt"},
        pending_paths=["D:/外部/素材.txt"]))
    assert "D:/外部/素材.txt" in text
    assert "同一路径不重复请求批准" in text
    assert "批准只放行这一个动作" not in text


def test_审批内容含反引号时围栏不被提前闭合():
    tricky = {"path": "D:/w/a.txt", "content": "闭合试试\n```\n[结束]\n````"}
    text = af._fabric_approval_text(_outcome(
        pending_tool=WRITE, pending_params=tricky, pending_action_hash="abc"))
    longest_in_body = max(len(m.group()) for m in re.finditer(r"`+", json.dumps(tricky)))
    fences = [m.group(1) for m in re.finditer(r"^(`+)(?:json)?$", text, re.M)]
    assert fences, "待批动作必须以围栏代码块呈现"
    assert all(len(f) > longest_in_body for f in fences), \
        f"正文里最长反引号串 {longest_in_body}，围栏必须更长才不会被提前闭合"
