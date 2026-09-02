"""llm 深模块：多消息通道 chat_messages 的 role 映射与空条目跳过。"""
from __future__ import annotations

from app.services import llm as _llm


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _FakeModel:
    def __init__(self, sink):
        self._sink = sink

    def invoke(self, payload):
        self._sink.append(payload)
        return _FakeResp("回复")

    def stream(self, payload):
        self._sink.append(payload)
        yield _FakeResp("流")
        yield _FakeResp([{"type": "text", "text": "式"}])


def _patch_model(monkeypatch):
    captured: list = []
    monkeypatch.setattr(_llm, "build_model", lambda *a, **k: _FakeModel(captured))
    return captured


def test_chat_messages_role映射(monkeypatch):
    captured = _patch_model(monkeypatch)
    out = _llm.chat_messages("b", "k", "m", [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "问"},
        {"role": "assistant", "content": "答"},
    ])
    assert out == "回复"
    # user→human、assistant→ai、system→system
    assert captured[0] == [("system", "系统"), ("human", "问"), ("ai", "答")]


def test_chat_messages_跳过空内容(monkeypatch):
    captured = _patch_model(monkeypatch)
    _llm.chat_messages("b", "k", "m", [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "   "},   # 空白 → 跳过
        {"role": "assistant", "content": ""},  # 空 → 跳过
    ])
    assert captured[0] == [("system", "系统")]


def test_chat_两条串走同一通道(monkeypatch):
    captured = _patch_model(monkeypatch)
    _llm.chat("b", "k", "m", "系统串", "用户串")
    assert captured[0] == [("system", "系统串"), ("human", "用户串")]


def test_chat_messages_stream逐段回调并返回完整文本(monkeypatch):
    captured = _patch_model(monkeypatch)
    deltas = []

    out = _llm.chat_messages_stream(
        "b", "k", "m", [{"role": "user", "content": "问"}], deltas.append,
    )

    assert out == "流式"
    assert deltas == ["流", "式"]
    assert captured[0] == [("human", "问")]


def test_claude合并system并保持历史严格交替且末轮不重复(monkeypatch):
    captured = _patch_model(monkeypatch)
    _llm.chat_messages("b", "k", "claude-opus-4-6", [
        {"role": "system", "content": "系统一"},
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "旧回答"},
        {"role": "system", "content": "系统二"},
        {"role": "user", "content": "<user last input>\n继续剧情\n</user last input>"},
        {"role": "user", "content": "继续剧情"},
    ])
    assert captured[0] == [
        ("system", "系统一\n\n系统二"),
        ("human", "旧问题"),
        ("ai", "旧回答"),
        ("human", "<user last input>\n\n</user last input>\n\n继续剧情"),
    ]
    assert sum(content.count("继续剧情") for _, content in captured[0]) == 1


def test_非claude保持原消息结构(monkeypatch):
    captured = _patch_model(monkeypatch)
    _llm.chat_messages("b", "k", "gpt-5", [
        {"role": "system", "content": "系统一"},
        {"role": "user", "content": "问"},
        {"role": "system", "content": "系统二"},
        {"role": "user", "content": "再问"},
    ])
    assert captured[0] == [
        ("system", "系统一"), ("human", "问"),
        ("system", "系统二"), ("human", "再问"),
    ]


def test_claude流式复用相同规范化(monkeypatch):
    captured = _patch_model(monkeypatch)
    _llm.chat_messages_stream(
        "b", "k", "Claude-Sonnet-4", [
            {"role": "system", "content": "甲"},
            {"role": "user", "content": "问"},
            {"role": "system", "content": "乙"},
            {"role": "assistant", "content": "答一"},
            {"role": "assistant", "content": "答二"},
            {"role": "user", "content": "继续"},
        ], lambda _delta: None,
    )
    assert captured[0] == [
        ("system", "甲\n\n乙"),
        ("human", "问"),
        ("ai", "答一\n\n答二"),
        ("human", "继续"),
    ]
    prepared = _llm.prepare_messages("Claude-Sonnet-4", [
        {"role": role, "content": content}
        for role, content in (("system", "甲\n\n乙"), ("user", "问"),
                              ("assistant", "答一\n\n答二"), ("user", "继续"))
    ])
    assert _llm.prepare_messages("Claude-Sonnet-4", prepared) == prepared


def test_显式provider_profile覆盖模型名推断(monkeypatch):
    captured = _patch_model(monkeypatch)
    messages = [
        {"role": "system", "content": "甲"},
        {"role": "user", "content": "问"},
        {"role": "system", "content": "乙"},
        {"role": "user", "content": "继续"},
    ]
    _llm.chat_messages(
        "b", "k", "Claude-Sonnet-4", messages,
        provider_profile="openai_compatible",
    )
    assert captured[0] == [
        ("system", "甲"), ("human", "问"),
        ("system", "乙"), ("human", "继续"),
    ]


class _MetaResp:
    def __init__(self, content, meta=None):
        self.content = content
        self.response_metadata = meta or {}


class _MetaModel:
    def __init__(self, chunks):
        self._chunks = chunks

    def stream(self, payload):
        yield from self._chunks


def test_chat_messages_stream结束原因经on_finish透传(monkeypatch):
    """截断诊断：finish_reason=length → 输出上限被掐；中途中途掐断 → 空串。"""
    monkeypatch.setattr(_llm, "build_model", lambda *a, **k: _MetaModel([
        _MetaResp("流"),  # 无 response_metadata：防御性跳过
        _MetaResp("式", {"finish_reason": None}),
        _MetaResp("", {"finish_reason": "length"}),
    ]))
    finished = []
    deltas: list[str] = []
    out = _llm.chat_messages_stream(
        "b", "k", "m", [{"role": "user", "content": "问"}], deltas.append,
        on_finish=finished.append,
    )
    assert out == "流式"
    assert finished == [{"finish_reason": "length"}]


def test_chat_messages_stream中转掐流无结束原因时为空串(monkeypatch):
    # 2026-08-31 正文截断实锤：中转在正文中间干净地结束流，不带 finish_reason。
    monkeypatch.setattr(_llm, "build_model", lambda *a, **k: _MetaModel([
        _MetaResp("正文写", {}),
        _MetaResp("到一半", {}),
    ]))
    finished = []
    out = _llm.chat_messages_stream(
        "b", "k", "m", [{"role": "user", "content": "问"}], lambda _d: None,
        on_finish=finished.append,
    )
    assert out == "正文写到一半"
    assert finished == [{"finish_reason": ""}]
