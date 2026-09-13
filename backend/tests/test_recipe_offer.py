"""事前固化询问：断点存储 / 答复词续跑 / 主管强制路由 / 抑制草稿卡（2026-09-10）。

设计要点（都落在这份用例里）：
- 询问**必须**用独立命名空间：混进 `fabric_checkpoints` 会让「批准」被当成 durable 授权；
- 答复短词**必须**零 LLM 强制路由回 plan 节点，否则会被主管判成普通对话、询问永远悬空；
- 「直接跑」必须抑制 done 时的草稿卡，否则问完照样弹「保留/不保留」，询问就是纯噪声。
"""
from app.services import agent_graph as ag
from app.services import capability_sandbox, fabric_loop, recipe_offer_store, task_progress_store

_OFFER = {"id": "o1", "fingerprint": "fp", "intent": "整理设定文档",
          "delivery_intent": "整理设定文档", "output_dir": "D:/x", "thread_id": "t1"}


class _Done:
    """自由循环 done 结果的最小替身（用于绕开真实 LLM）。"""

    status = "done"
    reply = "已完成"
    steps: list = []
    messages: list = []
    pending_tool = ""
    _started_at = 0.0


def _iso(monkeypatch, tmp_path):
    """隔离存储：绝不碰用户真实 `backend/data/task_progress/`。"""
    monkeypatch.setattr(task_progress_store, "STORE_DIR", tmp_path)


def test_询问存储往返与筛选(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    saved = recipe_offer_store.save(dict(_OFFER))
    got = recipe_offer_store.pending(output_dir="D:/x", thread_id="t1")
    assert got is not None and got["id"] == saved["id"]
    # 作品/会话不符都不该命中（否则别的作品会被问一句不相干的固化）
    assert recipe_offer_store.pending(output_dir="D:/y") is None
    assert recipe_offer_store.pending(thread_id="t2") is None


def test_过期询问不再拦人(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    saved = recipe_offer_store.save(dict(_OFFER))
    task_progress_store.save(recipe_offer_store.NAMESPACE,
                             {saved["id"]: dict(saved, updated_at=0.0)})
    assert recipe_offer_store.pending(output_dir="D:/x", thread_id="t1") is None


def test_答复要则固化(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    recipe_offer_store.save(dict(_OFFER))
    ans = ag._recipe_offer_answer("要", {"thread_id": "t1"}, "D:/x")
    assert ans is not None and ans["answer"] == "solidify"
    # 回放的是**询问时存下的原始任务文本**，不是「要」这两个字
    assert ans["intent"] == "整理设定文档"
    assert recipe_offer_store.pending(output_dir="D:/x", thread_id="t1") is None


def test_答复直接跑则跳过(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    recipe_offer_store.save(dict(_OFFER))
    ans = ag._recipe_offer_answer("直接跑", {"thread_id": "t1"}, "D:/x")
    assert ans is not None and ans["answer"] == "skip"
    assert ans["intent"] == "整理设定文档"
    assert recipe_offer_store.pending(output_dir="D:/x", thread_id="t1") is None


def test_非答复词不消费询问(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    recipe_offer_store.save(dict(_OFFER))
    assert ag._recipe_offer_answer("帮我看看这个剧本", {"thread_id": "t1"}, "D:/x") is None
    assert recipe_offer_store.pending(output_dir="D:/x", thread_id="t1") is not None


def test_无询问时答复词不劫持对话(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    assert ag._recipe_offer_answer("要", {"thread_id": "t1"}, "D:/x") is None
    # 主管侧的直通车同理：没有待答询问 + 词不匹配 → 不强制路由
    assert ag._recipe_offer_pending({"thread_id": "t1"}, "D:/x") is None


def test_主管把固化询问答复零LLM路由回plan(monkeypatch, tmp_path):
    """短词「直接跑」若交给 LLM 主管，多半被判成普通对话 → 询问永远悬空。"""
    _iso(monkeypatch, tmp_path)
    recipe_offer_store.save(dict(_OFFER))
    out = ag.supervisor_node({
        "user_text": "直接跑", "images": [],
        "_ctx": {"output_dir": "D:/x", "thread_id": "t1"},
    })
    assert out["route"] == "plan"
    assert any("固化询问答复" in line for line in out["trace"])


def test_同一请求不重复询问(monkeypatch, tmp_path):
    """用户没理会询问、又原样重发同一条指令 → 直接放行去跑，不再弹第二次。"""
    from app.services import plan_tasks as _pt
    _iso(monkeypatch, tmp_path)
    monkeypatch.setattr(fabric_loop, "run_loop", lambda **_kw: _Done())
    monkeypatch.setattr(_pt, "_agent_access_mode", lambda: "full")
    task = "生成一份角色外貌综合文档"
    ctx = {"output_dir": str(tmp_path), "chat_fn": lambda *_a, **_k: "{}",
           "chat_base": "b", "chat_key": "k", "chat_model": "m"}
    capability_sandbox._reset_for_tests()
    try:
        first = ag.plan_compiler_node(
            {"user_text": task, "images": [], "_ctx": dict(ctx, message=task)})
        assert "要不要为它固化成一条" in first["result_text"]
        second = ag.plan_compiler_node(
            {"user_text": task, "images": [], "_ctx": dict(ctx, message=task)})
        assert "要不要为它固化成一条" not in second["result_text"]
    finally:
        for lease in capability_sandbox.active():
            capability_sandbox.revoke(lease["id"])
        capability_sandbox._reset_for_tests()


def test_直接跑抑制草稿卡(monkeypatch, tmp_path):
    """答「直接跑」→ done 时不再产出草稿卡，否则问完照样弹「保留/不保留」。"""
    events: list = []
    monkeypatch.setattr(ag.run_trace, "emit",
                        lambda _ctx, event, **data: events.append((event, data)))
    out = ag._fabric_finalize(_Done(), "整理设定文档", [], {"_suppress_recipe": True},
                              str(tmp_path))
    assert "[[recipe:" not in out["result_text"]
    assert any(e == "fabric.solidified" and d.get("status") == "suppressed"
               for e, d in events)


def test_未抑制时不走抑制分支(monkeypatch, tmp_path):
    events: list = []
    monkeypatch.setattr(ag.run_trace, "emit",
                        lambda _ctx, event, **data: events.append((event, data)))
    ag._fabric_finalize(_Done(), "整理设定文档", [], {}, str(tmp_path))
    statuses = [d.get("status") for e, d in events if e == "fabric.solidified"]
    assert statuses and statuses[0] != "suppressed"
