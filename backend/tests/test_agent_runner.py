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


def test_runner在模型启动前把用户消息写入权威快照(monkeypatch):
    persisted = []

    def stream(context):
        assert persisted == [(
            "turn", "user-1", "不能丢失的用户消息", ["reference.png"],
        )]
        yield {"done": True}

    monkeypatch.setattr(agent_runner.agent_graph, "stream_multi_agent", stream)
    monkeypatch.setattr(
        agent_runner.generation_store, "persist_user_message",
        lambda *args: persisted.append(args), raising=False,
    )
    monkeypatch.setattr(agent_runner.generation_store, "persist_text", lambda *a, **k: None)
    monkeypatch.setattr(agent_runner.chat_memory, "append_turn", lambda *a, **k: None)

    list(agent_runner.drain(agent_runner.run_multi_stream(RunContext(
        thread_id="turn", message="不能丢失的用户消息", images=["reference.png"],
        user_message_id="user-1", message_id="assistant-1",
    ))))

    assert persisted == [("turn", "user-1", "不能丢失的用户消息", ["reference.png"])]


def test_runner接收节点实时增量并以最终文本替换落盘(monkeypatch):
    def stream(context):
        context.stream_sink({"delta": "生成"})
        context.stream_sink({"delta": "中"})
        yield {"replace": "最终正文"}
        yield {"done": True}

    monkeypatch.setattr(agent_runner.agent_graph, "stream_multi_agent", stream)
    persisted = []
    monkeypatch.setattr(agent_runner.generation_store, "persist_text",
                        lambda *a, **k: persisted.append((a, k)))
    monkeypatch.setattr(agent_runner.chat_memory, "append_turn", lambda *a, **k: None)

    events = list(agent_runner.drain(agent_runner.run_multi_stream(
        RunContext(thread_id="live", message="q", message_id="m", stream_output=True),
    )))

    assert events == [
        {"delta": "生成"}, {"delta": "中"}, {"replace": "最终正文"}, {"done": True},
    ]
    assert persisted[0][0][2] == "最终正文"


def test_runner非流式也提供即时媒体事件通道(monkeypatch):
    def stream(context):
        assert context.stream_sink is not None
        context.stream_sink({"replace": "最终正文"})
        context.stream_sink({
            "illustrate_request": {"prompt": "完整提示词", "motion": 0, "actors": []},
            "id": "slot-1",
        })
        yield {"done": True}

    monkeypatch.setattr(agent_runner.agent_graph, "stream_multi_agent", stream)
    monkeypatch.setattr(agent_runner.generation_store, "persist_text", lambda *a, **k: None)
    monkeypatch.setattr(agent_runner.generation_store, "persist_media_slot", lambda *a, **k: None)
    monkeypatch.setattr(agent_runner.chat_memory, "append_turn", lambda *a, **k: None)

    events = list(agent_runner.drain(agent_runner.run_multi_stream(
        RunContext(thread_id="ready", message="q", message_id="m", stream_output=False),
    )))
    assert events[:2] == [
        {"replace": "最终正文"},
        {"illustrate_request": {"prompt": "完整提示词", "motion": 0, "actors": []}, "id": "slot-1"},
    ]


def test_runner即时事件先持久化正文和媒体槽(monkeypatch):
    def stream(context):
        context.stream_sink({"replace": "最终正文"})
        context.stream_sink({
            "illustrate_request": {
                "prompt": "完整提示词", "motion": 0, "actors": [], "offset": 2,
            },
            "id": "slot-1",
        })
        yield {"done": True}

    persisted = []
    monkeypatch.setattr(agent_runner.agent_graph, "stream_multi_agent", stream)
    monkeypatch.setattr(
        agent_runner.generation_store, "persist_text",
        lambda *args, **kwargs: persisted.append(("text", args, kwargs)),
    )
    monkeypatch.setattr(
        agent_runner.generation_store, "persist_media_slot",
        lambda *args, **kwargs: persisted.append(("slot", args, kwargs)),
    )
    monkeypatch.setattr(agent_runner.chat_memory, "append_turn", lambda *a, **k: None)

    list(agent_runner.drain(agent_runner.run_multi_stream(
        RunContext(thread_id="durable", message="q", message_id="bot"),
    )))

    assert persisted[0] == ("text", ("durable", "bot", "最终正文"), {})
    assert persisted[1] == ("slot", ("durable", "bot", "slot-1", 2), {})


def test_runner用同一turn_id记录首尾事件(monkeypatch):
    monkeypatch.setattr(agent_runner.agent_graph, "stream_multi_agent",
                        lambda context: iter([{"delta": "答复"}, {"done": True}]))
    monkeypatch.setattr(agent_runner.generation_store, "persist_text", lambda *a, **k: None)
    monkeypatch.setattr(agent_runner.chat_memory, "append_turn", lambda *a, **k: None)
    captured = []
    monkeypatch.setattr(agent_runner.run_trace, "emit",
                        lambda ctx, event, **data: captured.append((ctx.turn_id, event, data)))
    context = RunContext(
        thread_id="trace-thread", message="中文输入",
        illustrate=True, comfy_illustrate=True,
    )

    list(agent_runner.drain(agent_runner.run_multi_stream(context)))

    assert [item[1] for item in captured] == ["turn.started", "turn.completed"]
    assert {item[0] for item in captured} == {context.turn_id}
    assert captured[0][2]["raw_input"] == "中文输入"
    assert captured[0][2]["user_message_id"] == ""
    assert captured[0][2]["illustrate"] is True
    assert captured[0][2]["comfy_illustrate"] is True
    assert captured[1][2]["assistant_output"] == "答复"


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


def test_finalize_persistence_error_still_releases_thread_and_ends_stream(monkeypatch):
    monkeypatch.setattr(
        agent_runner.agent_graph, "stream_multi_agent",
        lambda context: iter([{"delta": "已生成"}, {"done": True}]),
    )
    monkeypatch.setattr(
        agent_runner.generation_store, "persist_text",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk failure")),
    )
    monkeypatch.setattr(agent_runner.run_trace, "emit", lambda *a, **k: None)

    events = list(agent_runner.drain(agent_runner.run_multi_stream(
        RunContext(thread_id="finalize-error", message="q", message_id="m"),
    )))

    assert events == [{"delta": "已生成"}, {"done": True}]
    assert _wait_idle("finalize-error")
