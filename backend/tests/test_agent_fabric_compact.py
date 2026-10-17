"""历史自动压缩的摘要器行为（2026-10-14 A+C 之 C1）。

实锤背景（当日诊断）：慢中压对话模型下 `_llm.chat` 默认 200s 读超时 ×2 次重试，
用户每轮「发送→就绪」要等 6–10 分钟（全卡在 `_history_summarizer`）；且摘要器
无 trace 事件、不响应取消，卡死阶段完全盲跑。本文件锁死修后形态：
- 超时收紧为 60s、单次尝试（快速失败退机械裁剪）；
- cancel_event 置位则不发起 LLM 请求；
- 无论成败都发 history.compact trace 事件（含耗时与降级原因）。
"""

from app.services import agent_fabric
from app.services.agent_contracts import ModelConfig, RunContext


def _ctx() -> RunContext:
    return RunContext(
        thread_id="t", message="m",
        chat=ModelConfig(base_url="https://relay.test/v1", api_key="k", model="m"),
    )


def _record_events(monkeypatch) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        agent_fabric.run_trace, "emit",
        lambda ctx, event, **data: events.append((event, data)),
    )
    return events


def test_摘要收紧超时且单次尝试(monkeypatch):
    """元凶实锤：默认 200s×2 ⇒ 最坏 400s+。修后 60s×1，快速失败退机械裁剪。"""
    seen: dict = {}

    def fake_chat(_base, _key, _model, _system, _user, **kw):
        seen.update(kw)
        return "摘要正文"

    monkeypatch.setattr(agent_fabric._llm, "chat", fake_chat)
    fn = agent_fabric._history_summarizer(_ctx())

    assert fn([{"role": "user", "content": "旧历史"}]) == "摘要正文"
    assert seen["retries"] == 1
    assert seen["timeout"] == 60


def test_摘要已取消则不发起请求(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("已取消还发起 LLM 请求")

    monkeypatch.setattr(agent_fabric._llm, "chat", boom)
    ctx = _ctx()
    ctx.cancel_event.set()
    fn = agent_fabric._history_summarizer(ctx)

    assert fn([{"role": "user", "content": "旧历史"}]) is None


def test_摘要成功发trace事件(monkeypatch):
    events = _record_events(monkeypatch)
    monkeypatch.setattr(agent_fabric._llm, "chat", lambda *a, **k: "摘要正文")
    fn = agent_fabric._history_summarizer(_ctx())

    fn([{"role": "user", "content": "旧历史"}])

    compact = [d for name, d in events if name == "history.compact"]
    assert len(compact) == 1
    assert compact[0]["outcome"] == "ok"
    assert compact[0]["elapsed_ms"] >= 0


def test_摘要失败留痕并返回None退机械(monkeypatch):
    events = _record_events(monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("中转 502")

    monkeypatch.setattr(agent_fabric._llm, "chat", boom)
    fn = agent_fabric._history_summarizer(_ctx())

    assert fn([{"role": "user", "content": "旧历史"}]) is None
    compact = [d for name, d in events if name == "history.compact"]
    assert len(compact) == 1
    assert compact[0]["outcome"] == "failed"
    assert "502" in compact[0]["error"]


def test_摘要取消也留痕(monkeypatch):
    events = _record_events(monkeypatch)
    monkeypatch.setattr(
        agent_fabric._llm, "chat",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("取消后不得调用")),
    )
    ctx = _ctx()
    ctx.cancel_event.set()
    fn = agent_fabric._history_summarizer(ctx)

    assert fn([{"role": "user", "content": "旧历史"}]) is None
    compact = [d for name, d in events if name == "history.compact"]
    assert len(compact) == 1
    assert compact[0]["outcome"] == "cancelled"


def test_无对话模型配置时仍无摘要器():
    """缺 base/model ⇒ 返回 None（机械裁剪路径），与改造前一致。"""
    ctx = RunContext(thread_id="t", message="m")
    assert agent_fabric._history_summarizer(ctx) is None
