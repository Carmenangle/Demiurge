"""`image_agent.stream_agent` 的流式行为：事件次序 / 取消 / 异常降级 / 历史注入。

接缝：`stream_agent` 内部调模块级 `_build` ⇒ patch `image_agent._build`
即可注入替身 agent；替身拿到**真实 sink/insp 列表**，在产 chunk 的同时写它们，
从而覆盖「图/灵感卡即时透出先于文本」「异常时先补发产物」等次序约束。

红线：不碰 checkpointer 库（_build 已被替身）、不触真实 LLM/生图接口。
"""

import threading
import types

from langchain_core.messages import AIMessage, HumanMessage

from app.services import chat_memory, image_agent


class _AgentStub:
    """替身 agent：build 时记录 sink/insp 与调用入参，stream 按 events 产出。

    events 是「步骤」列表：元素为 ("chunk", chunk, meta) / ("img", rec) /
    ("insp", card) / ("raise", exc)；img/insp 在产出下一个 chunk 前写入真实 sink，
    复现「工具节点先落产物、chunk 随后到达」的真实时序。
    """

    def __init__(self, events):
        self.events = events
        self.sink: list = []
        self.insp: list = []
        self.payload = None
        self.config = None

    def stream(self, payload, config, stream_mode=None):
        self.payload = payload
        self.config = config
        for ev in self.events:
            kind = ev[0]
            if kind == "chunk":
                yield ev[1], ev[2]
            elif kind == "img":
                self.sink.append(ev[1])
            elif kind == "insp":
                self.insp.append(ev[1])
            elif kind == "raise":
                raise ev[1]


def _patch_build(monkeypatch, events, memory_mode="external_turn", history=None, **overrides):
    stub = _AgentStub(events)
    seen_kwargs: dict = {}

    def _fake_build(chat_base, chat_key, chat_model, gen_base, gen_key, gen_model,
                    sink, size, **kw):
        seen_kwargs.update(kw)
        seen_kwargs["size"] = size
        stub.sink = sink                      # stream_agent 传进来的真实 sink
        stub.insp = kw.get("insp_sink")
        return stub

    monkeypatch.setattr(image_agent, "_build", _fake_build)
    if memory_mode == "external_turn":
        monkeypatch.setattr(chat_memory, "get_history", lambda tid: (history or []))
    kwargs = dict(
        thread_id="t1", message="画一张图", images=None,
        chat_base="cb", chat_key="ck", chat_model="cm",
        gen_base="gb", gen_key="gk", gen_model="gm",
        memory_mode=memory_mode,
    )
    kwargs.update(overrides)
    return list(image_agent.stream_agent(**kwargs)), stub, seen_kwargs


def test_文本增量只透模型节点(monkeypatch):
    """tool 节点（工具内部 llm）的 token 被跳过，model/agent 节点的文本透出。"""
    chunk = lambda text: types.SimpleNamespace(content=text)  # noqa: E731
    events = [("chunk", chunk("工具内"), {"langgraph_node": "tools"}),
              ("chunk", chunk("你"), {"langgraph_node": "model"}),
              ("chunk", chunk("好"), {"langgraph_node": "model"})]

    events_out, _stub, _kw = _patch_build(monkeypatch, events)

    assert events_out == [{"delta": "你"}, {"delta": "好"}]


def test_灵感卡与图片即时透出且先于模型文本(monkeypatch):
    """工具节点 chunk 到达时：先灵感卡、再图，最后才是总结文本（前端不死等）。"""
    chunk = lambda text: types.SimpleNamespace(content=text)  # noqa: E731
    rec = {"id": "img-1", "url": "saved.png", "regeneration": {"kind": "ai-image"}}
    card = {"id": "c1", "title": "灵感卡"}
    events = [("insp", card),
              ("img", rec),
              ("chunk", chunk("已生成"), {"langgraph_node": "tools"}),
              ("chunk", chunk("完成"), {"langgraph_node": "model"})]

    events_out, _stub, _kw = _patch_build(monkeypatch, events)

    assert events_out == [
        {"inspiration": card},
        {"image": "saved.png", "image_id": "img-1", "regeneration": rec["regeneration"]},
        {"delta": "完成"},
    ]


def test_尾部兜底补发未透出的产物(monkeypatch):
    """全程无 model 节点 chunk（如总结轮被跳过）也不丢图——循环外兜底 flush。"""
    rec = {"id": "img-2", "url": "saved2.png", "regeneration": None}
    events = [("img", rec), ("chunk", types.SimpleNamespace(content="x"),
                              {"langgraph_node": "tools"})]

    events_out, _stub, _kw = _patch_build(monkeypatch, events)

    assert events_out == [{"image": "saved2.png", "image_id": "img-2",
                           "regeneration": None}]


def test_取消置位_停止产出并补发已生成产物(monkeypatch):
    chunk = lambda text: types.SimpleNamespace(content=text)  # noqa: E731
    rec = {"id": "img-3", "url": "saved3.png", "regeneration": None}
    card = {"id": "c2", "title": "卡"}
    events = [("insp", card), ("img", rec), ("chunk", chunk("x"), {"langgraph_node": "model"})]
    cancel = threading.Event()
    cancel.set()

    events_out, _stub, _kw = _patch_build(monkeypatch, events, cancel_event=cancel)

    assert events_out == [
        {"image": "saved3.png", "image_id": "img-3", "regeneration": None},
        {"inspiration": card},
        {"interrupted": True},
    ]


def test_异常但已有产物_降级提示不丢图(monkeypatch):
    rec = {"id": "img-4", "url": "saved4.png", "regeneration": None}
    events = [("img", rec), ("raise", RuntimeError("模型总结挂了"))]

    events_out, _stub, _kw = _patch_build(monkeypatch, events)

    assert events_out[0] == {"image": "saved4.png", "image_id": "img-4",
                             "regeneration": None}
    assert "内容已生成" in events_out[1]["delta"]
    assert "模型总结挂了" in events_out[1]["delta"]


def test_异常且无产物_报真错误(monkeypatch):
    events = [("raise", RuntimeError("连不上网关"))]

    events_out, _stub, _kw = _patch_build(monkeypatch, events)

    assert events_out == [{"error": "连不上网关"}]


def test_带图输入构造多模态消息(monkeypatch):
    chunk = lambda text: types.SimpleNamespace(content=text)  # noqa: E731
    events = [("chunk", chunk("好"), {"langgraph_node": "model"})]

    events_out, stub, kw = _patch_build(
        monkeypatch, events, images=["http://a/1.png", "http://a/2.png"])

    assert events_out == [{"delta": "好"}]
    assert kw["has_images"] is True
    human = stub.payload["messages"][-1]
    assert human.content[0] == {"type": "text", "text": "画一张图"}
    assert [b["image_url"]["url"] for b in human.content[1:]] == [
        "http://a/1.png", "http://a/2.png"]


def test_external_turn历史注入在输入之前(monkeypatch):
    """external_turn 模式：把 checkpoint 历史按角色还原成消息，拼在本轮输入前。"""
    history = [
        {"role": "user", "content": "上一问"},
        {"role": "assistant", "content": "上一答"},
    ]
    chunk = lambda text: types.SimpleNamespace(content=text)  # noqa: E731
    events = [("chunk", chunk("好"), {"langgraph_node": "model"})]

    events_out, stub, _kw = _patch_build(
        monkeypatch, events, memory_mode="external_turn", history=history)

    assert events_out == [{"delta": "好"}]
    msgs = stub.payload["messages"]
    assert [type(m) for m in msgs] == [HumanMessage, AIMessage, HumanMessage]
    assert [m.content for m in msgs] == ["上一问", "上一答", "画一张图"]


def test_入参透传_build_与repo_id回退thread_id(monkeypatch):
    events = [("chunk", types.SimpleNamespace(content=""), {"langgraph_node": "model"})]

    _events_out, _stub, kw = _patch_build(
        monkeypatch, events, repo_id="", size="1536x1024", agent_id="a1",
        style_template="国风厚涂", image_quality="low")

    # repo_id 留空 → stream_agent 侧回退为 thread_id（作品域随会话）
    assert kw["repo_id"] == "t1"
    assert kw["size"] == "1536x1024"
    assert kw["agent_id"] == "a1"
    assert kw["style_template"] == "国风厚涂"
    assert kw["image_quality"] == "low"


def test_空模型回复不产空delta(monkeypatch):
    events = [("chunk", types.SimpleNamespace(content=""), {"langgraph_node": "model"}),
              ("chunk", types.SimpleNamespace(content=None), {"langgraph_node": "model"})]

    events_out, _stub, _kw = _patch_build(monkeypatch, events)

    assert events_out == []
