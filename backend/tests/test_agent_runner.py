import threading
import time

import pytest

from app.services import agent_runner
from app.services.agent_contracts import RunContext


def _wait_idle(thread_id: str, timeout: float = 2.0) -> bool:
    """等后台 worker 收尾完成（释放准入）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not agent_runner.is_running(thread_id):
            return True
        time.sleep(0.01)
    return not agent_runner.is_running(thread_id)


def test_same_thread_run_is_rejected(monkeypatch):
    gate = threading.Event()

    def stream(context):
        gate.wait(1)
        yield {"done": True}

    monkeypatch.setattr(agent_runner.agent_graph, "stream_multi_agent", stream)
    monkeypatch.setattr(agent_runner.generation_store, "persist_text", lambda *a, **k: None)
    monkeypatch.setattr(agent_runner.chat_memory, "append_turn", lambda *a, **k: None)
    context = RunContext(thread_id="thread", message="one")
    queue = agent_runner.run_multi_stream(context)
    with pytest.raises(agent_runner.RunAlreadyActive):
        agent_runner.run_multi_stream(RunContext(thread_id="thread", message="two"))
    gate.set()
    list(agent_runner.drain(queue))


def test_runner_commits_turn_once(monkeypatch):
    monkeypatch.setattr(agent_runner.agent_graph, "stream_multi_agent", lambda context: iter([
        {"delta": "hello"}, {"done": True},
    ]))
    persisted = []
    turns = []
    monkeypatch.setattr(agent_runner.generation_store, "persist_text", lambda *a, **k: persisted.append((a, k)))
    monkeypatch.setattr(agent_runner.chat_memory, "append_turn", lambda *a, **k: turns.append((a, k)))

    events = list(agent_runner.drain(agent_runner.run_multi_stream(
        RunContext(thread_id="t2", message="question", message_id="m1")
    )))

    assert events == [{"delta": "hello"}, {"done": True}]
    assert len(persisted) == 1
    assert len(turns) == 1
    assert turns[0][0][1:4] == ("question", [], "hello")
    assert agent_runner.is_running("t2") is False


def test_cancel_only_targets_active_run(monkeypatch):
    def stream(context):
        context.cancel_event.wait(1)
        yield {"interrupted": True}

    monkeypatch.setattr(agent_runner.agent_graph, "stream_multi_agent", stream)
    monkeypatch.setattr(agent_runner.generation_store, "persist_text", lambda *a, **k: None)
    monkeypatch.setattr(agent_runner.chat_memory, "append_turn", lambda *a, **k: None)
    context = RunContext(thread_id="cancel-me", message="x")
    agent_runner.run_multi_stream(context)
    assert agent_runner.cancel("missing") is False
    assert agent_runner.cancel("cancel-me") is True
    assert context.cancel_event.is_set()
    context.cancel_event.set()  # 放行 worker 收尾
    _wait_idle("cancel-me")


def test_worker_finalizes_even_if_client_stops_draining(monkeypatch):
    # 模拟客户端中途断开：不消费队列，worker 仍须完成 persist + append_turn + 释放所有权
    finished = threading.Event()

    def stream(context):
        yield {"delta": "half"}

    persisted = []
    turns = []
    monkeypatch.setattr(agent_runner.agent_graph, "stream_multi_agent", stream)
    monkeypatch.setattr(agent_runner.generation_store, "persist_text",
                        lambda *a, **k: persisted.append((a, k)))

    def append_turn(*a, **k):
        turns.append((a, k))
        finished.set()

    monkeypatch.setattr(agent_runner.chat_memory, "append_turn", append_turn)

    agent_runner.run_multi_stream(RunContext(thread_id="drop", message="q", message_id="m"))
    # 不调用 drain（客户端已断）
    assert finished.wait(2)
    assert len(persisted) == 1
    assert len(turns) == 1
    assert _wait_idle("drop")


def test_thread_freed_after_run_allows_readmit(monkeypatch):
    monkeypatch.setattr(agent_runner.agent_graph, "stream_multi_agent",
                        lambda context: iter([{"done": True}]))
    monkeypatch.setattr(agent_runner.generation_store, "persist_text", lambda *a, **k: None)
    monkeypatch.setattr(agent_runner.chat_memory, "append_turn", lambda *a, **k: None)

    list(agent_runner.drain(agent_runner.run_multi_stream(
        RunContext(thread_id="reuse", message="one"))))
    assert _wait_idle("reuse")
    # 同 thread 运行结束后可立即再次开启，不被旧登记卡住
    list(agent_runner.drain(agent_runner.run_multi_stream(
        RunContext(thread_id="reuse", message="two"))))
    assert _wait_idle("reuse")


def test_worker_error_still_finalizes(monkeypatch):
    def boom(context):
        raise RuntimeError("stream exploded")
        yield  # pragma: no cover

    persisted = []
    monkeypatch.setattr(agent_runner.agent_graph, "stream_multi_agent", boom)
    monkeypatch.setattr(agent_runner.generation_store, "persist_text",
                        lambda *a, **k: persisted.append(1))
    monkeypatch.setattr(agent_runner.chat_memory, "append_turn", lambda *a, **k: None)

    events = list(agent_runner.drain(agent_runner.run_multi_stream(
        RunContext(thread_id="err", message="q", message_id="m"))))

    assert events == [{"error": "stream exploded"}]
    assert len(persisted) == 1          # 异常路径仍收尾
    assert _wait_idle("err")            # 仍释放所有权
