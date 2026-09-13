# -*- coding: utf-8 -*-
"""llm.py usage 回调测试：_collect_usage、on_usage 回调链路、backward compatibility。"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from app.services import llm


@pytest.fixture(autouse=True)
def _reset_stream_usage_flag(monkeypatch):
    """隔离流式观测开关（进程级单例）：默认启用、不带环境变量覆盖。"""
    monkeypatch.setattr(llm, "_stream_usage_disabled", False)
    monkeypatch.delenv("DEMIURGE_STREAM_USAGE", raising=False)


# ── _collect_usage ─────────────────────────────────────────────────────────────


def test_collect_usage_openai_format():
    raw = {
        "prompt_tokens": 1200,
        "completion_tokens": 350,
        "total_tokens": 1550,
    }
    stats = llm._collect_usage(raw)
    assert stats["prompt_tokens"] == 1200
    assert stats["completion_tokens"] == 350
    assert stats["cached_tokens"] == 0
    assert stats["uncached_prompt_tokens"] == 1200
    assert stats["total_tokens"] == 1550
    assert stats["cache_hit_ratio"] == 0.0


def test_collect_usage_cached():
    raw = {
        "prompt_tokens": 1200,
        "completion_tokens": 200,
        "cached_tokens": 900,
        "total_tokens": 1400,
    }
    stats = llm._collect_usage(raw)
    assert stats["cached_tokens"] == 900
    assert stats["uncached_prompt_tokens"] == 300
    assert stats["cache_hit_ratio"] == round(900 / 1200, 4)


def test_collect_usage_prompt_details_cached():
    """部分中转把缓存命中放在 prompt_tokens_details.cached_tokens。"""
    raw = {
        "prompt_tokens": 800,
        "completion_tokens": 150,
        "total_tokens": 950,
        "prompt_tokens_details": {"cached_tokens": 500},
    }
    stats = llm._collect_usage(raw)
    assert stats["cached_tokens"] == 500
    assert stats["uncached_prompt_tokens"] == 300
    assert stats["cache_hit_ratio"] == round(500 / 800, 4)


def test_collect_usage_deepseek_hit_miss():
    """DeepSeek 官方字段：prompt_tokens 已含 hit（= hit + miss），未命中须用 miss 而非相减。

    2026-09-12 P0 新增。若按 OpenAI 口径相减会得到 0 未命中（200-800<0），把成本算成 0。
    """
    raw = {
        "prompt_tokens": 1000,
        "completion_tokens": 50,
        "prompt_cache_hit_tokens": 800,
        "prompt_cache_miss_tokens": 200,
        "total_tokens": 1050,
    }
    stats = llm._collect_usage(raw)
    assert stats["prompt_tokens"] == 1000
    assert stats["cached_tokens"] == 800
    assert stats["uncached_prompt_tokens"] == 200
    assert stats["cache_hit_ratio"] == 0.8


def test_collect_usage_deepseek_只有hit无miss():
    """只有 hit、没有 miss 字段时，按「prompt 已含缓存」相减。"""
    stats = llm._collect_usage({
        "prompt_tokens": 1000, "completion_tokens": 10,
        "prompt_cache_hit_tokens": 640,
    })
    assert stats["cached_tokens"] == 640
    assert stats["uncached_prompt_tokens"] == 360


def test_collect_usage_claude原生口径():
    """Claude 原生：input_tokens **不含**缓存部分，总量要自己加回来，写入单列。"""
    raw = {
        "input_tokens": 100,
        "output_tokens": 40,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 200,
        "total_tokens": 1240,
    }
    stats = llm._collect_usage(raw)
    assert stats["prompt_tokens"] == 1200          # 100 + 900 + 200
    assert stats["uncached_prompt_tokens"] == 100
    assert stats["cached_tokens"] == 900
    assert stats["cache_write_tokens"] == 200
    assert stats["completion_tokens"] == 40
    assert stats["cache_hit_ratio"] == round(900 / 1200, 4)


def test_collect_usage_langchain标准形态():
    """LangChain usage_metadata 形态：input/output_tokens + input_token_details.cache_read。"""
    raw = {
        "input_tokens": 1200, "output_tokens": 30, "total_tokens": 1230,
        "input_token_details": {"cache_read": 700},
    }
    stats = llm._collect_usage(raw)
    assert stats["prompt_tokens"] == 1200          # 标准形态：input_tokens 已是总量
    assert stats["cached_tokens"] == 700
    assert stats["uncached_prompt_tokens"] == 500
    assert stats["completion_tokens"] == 30


def test_collect_usage_empty():
    assert llm._collect_usage(None) == {}
    # 空 dict 也归一为全 0 统计（有意义的观察值）
    assert llm._collect_usage({}) == {
        "prompt_tokens": 0, "completion_tokens": 0,
        "cached_tokens": 0, "uncached_prompt_tokens": 0, "cache_write_tokens": 0,
        "total_tokens": 0, "cache_hit_ratio": 0.0,
    }
    # 非 dict 类型返回 {}
    assert llm._collect_usage("nope") == {}


# ── build_model 签名 ──────────────────────────────────────────────────────────


def test_build_model_不得再有死参on_usage():
    """`build_model` 曾声明 on_usage 却从未引用（死参数），调用方会误以为观测已接通。

    2026-09-12 P0 删除；usage 只能从 invoke/stream 的**响应**取，构造函数挂不上钩子。
    此用例把「不得回归」锁死，并锁住新的 stream_usage 开关在位。
    """
    import inspect
    sig = inspect.signature(llm.build_model)
    assert "on_usage" not in sig.parameters
    assert "stream_usage" in sig.parameters
    assert list(sig.parameters.keys())[-1] == "stream_usage"


def test_build_model_stream_usage注入stream_options(monkeypatch):
    """stream_usage=True 时必须把 stream_options 并进请求体（不显式要，网关不回 usage）。"""
    captured: dict = {}

    def fake_init(model, **kw):
        captured.update(kw)
        captured["_model"] = model
        return object()

    import langchain.chat_models as lcm
    monkeypatch.setattr(lcm, "init_chat_model", fake_init)

    llm.build_model("https://api.example.com", "k", "m", streaming=True, stream_usage=True)
    assert captured["model_kwargs"] == {"stream_options": {"include_usage": True}}

    captured.clear()
    llm.build_model("https://api.example.com", "k", "m", streaming=True)
    assert "model_kwargs" not in captured


def test_chat_messages_accepts_on_usage():
    import inspect
    sig = inspect.signature(llm.chat_messages)
    assert "on_usage" in sig.parameters


def test_chat_messages_stream_accepts_on_usage():
    import inspect
    sig = inspect.signature(llm.chat_messages_stream)
    assert "on_usage" in sig.parameters


def test_chat_accepts_on_usage():
    """2026-09-12 P0：chat 是 fabric 自由循环每条决策走的通道，必须能上报 usage。"""
    import inspect
    assert "on_usage" in inspect.signature(llm.chat).parameters


# ── on_usage 回调链路（mock）──────────────────────────────────────────────────


def test_chat_messages_calls_on_usage_on_success():
    """chat_messages 成功后把 usage 传给 on_usage 回调。"""
    collected: list[dict] = []

    def collector(stats: dict) -> None:
        collected.append(stats)

    fake_msg = MagicMock()
    fake_msg.content = "hello"
    fake_msg.usage_metadata = {
        "prompt_tokens": 100, "completion_tokens": 20,
        "cached_tokens": 0, "total_tokens": 120,
    }

    with patch.object(llm, "build_model") as mock_build, \
         patch.object(llm, "_payload", return_value=[("human", "hi")]):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_msg
        mock_build.return_value = mock_llm

        result = llm.chat_messages(
            "http://example.com/v1", "key", "gpt-4o-mini",
            [{"role": "user", "content": "hi"}],
            on_usage=collector,
        )

        assert result == "hello"
        assert len(collected) == 1
        assert collected[0]["prompt_tokens"] == 100
        assert collected[0]["completion_tokens"] == 20


def test_chat_messages_calls_on_usage_on_failure():
    """chat_messages 调用失败时不调 on_usage。"""
    collected: list[dict] = []

    def collector(stats: dict) -> None:
        collected.append(stats)

    with patch.object(llm, "build_model") as mock_build, \
         patch.object(llm, "_payload", return_value=[("human", "hi")]):
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("boom")
        mock_build.return_value = mock_llm

        with pytest.raises(RuntimeError, match="boom"):
            llm.chat_messages(
                "http://example.com/v1", "key", "gpt-4o-mini",
                [{"role": "user", "content": "hi"}],
                on_usage=collector,
            )
        assert collected == []


def test_chat_messages_no_callback_noop():
    """on_usage=None 时正常执行，不报错。"""
    fake_msg = MagicMock()
    fake_msg.content = "world"
    fake_msg.usage_metadata = {"prompt_tokens": 50, "completion_tokens": 10}

    with patch.object(llm, "build_model") as mock_build, \
         patch.object(llm, "_payload", return_value=[("human", "hi")]):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_msg
        mock_build.return_value = mock_llm

        result = llm.chat_messages(
            "http://example.com/v1", "key", "gpt-4o-mini",
            [{"role": "user", "content": "hi"}],
        )
        assert result == "world"


# ── 流式 usage（2026-09-12 P0）────────────────────────────────────────────────


class _Chunk:
    def __init__(self, content, usage=None, finish=""):
        self.content = content
        self.usage_metadata = usage
        self.response_metadata = {"finish_reason": finish} if finish else {}


class _StreamModel:
    """按脚本吐块的替身：末块 content 为空、只带 usage（真实网关的收尾块形态）。"""

    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = 0

    def stream(self, _payload):
        self.calls += 1
        for chunk in self._chunks:
            yield chunk


def test_流式末块usage不被空delta丢掉():
    """usage 常挂在 content 为空的收尾块上——采集若写在 `if not delta: continue` 之后必丢。

    2026-09-12 P0 修的正是这个次序 bug；此前流式路径的命中率恒为 0。
    """
    collected: list[dict] = []
    chunks = [
        _Chunk("正文"),
        _Chunk("", usage={
            "prompt_tokens": 1000, "completion_tokens": 20, "total_tokens": 1020,
            "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 200,
        }, finish="stop"),
    ]
    deltas: list[str] = []

    with patch.object(llm, "build_model", return_value=_StreamModel(chunks)), \
         patch.object(llm, "_payload", return_value=[("human", "hi")]):
        out = llm.chat_messages_stream(
            "http://example.com/v1", "key", "m", [{"role": "user", "content": "hi"}],
            deltas.append, on_usage=collected.append,
        )

    assert out == "正文"
    assert deltas == ["正文"]
    assert len(collected) == 1
    assert collected[0]["cached_tokens"] == 800
    assert collected[0]["uncached_prompt_tokens"] == 200
    assert collected[0]["cache_hit_ratio"] == 0.8


def test_流式默认请求stream_options():
    """流式通道默认要 usage（否则网关不回）；build_model 收到 stream_usage=True。"""
    seen: list[bool] = []

    def fake_build(*_a, **kw):
        seen.append(bool(kw.get("stream_usage")))
        return _StreamModel([_Chunk("ok", finish="stop")])

    with patch.object(llm, "build_model", side_effect=fake_build), \
         patch.object(llm, "_payload", return_value=[("human", "hi")]):
        llm.chat_messages_stream(
            "http://example.com/v1", "key", "m", [{"role": "user", "content": "hi"}],
            lambda _d: None,
        )
    assert seen == [True]


def test_流式stream_options被拒自动降级且不吃重试预算():
    """上游明确拒绝 stream_options → 关开关、不带该字段重建、本次重试**不计入预算**。

    retries=1：若降级消耗预算，整条流会直接失败——这正是要防的。
    """
    seen: list[bool] = []
    first = {"raised": False}

    class _RejectOnceStream(_StreamModel):
        def stream(self, payload):
            if not first["raised"]:
                first["raised"] = True
                raise RuntimeError(
                    "400 Bad Request: extra fields not permitted: stream_options")
            return super().stream(payload)

    model = _RejectOnceStream([_Chunk("ok", finish="stop")])

    def fake_build(*_a, **kw):
        seen.append(bool(kw.get("stream_usage")))
        return model

    with patch.object(llm, "build_model", side_effect=fake_build), \
         patch.object(llm, "_payload", return_value=[("human", "hi")]):
        out = llm.chat_messages_stream(
            "http://example.com/v1", "key", "m", [{"role": "user", "content": "hi"}],
            lambda _d: None, retries=1,
        )

    assert out == "ok"
    assert seen == [True, False]          # 第二次重建时已关掉开关
    assert llm._stream_usage_disabled is True


def test_流式内容审核400不误判成stream_options被拒():
    """只有「提到该字段 + 提到不支持」才算拒绝，否则关掉开关会白白丢掉观测。"""
    assert llm._looks_like_stream_options_rejected(
        RuntimeError("400 invalid request: content policy violation")) is False
    assert llm._looks_like_stream_options_rejected(
        RuntimeError("400: unsupported parameter: stream_options")) is True


def test_流式usage开关可被环境变量关闭(monkeypatch):
    monkeypatch.setenv("DEMIURGE_STREAM_USAGE", "0")
    assert llm._stream_usage_enabled() is False
    monkeypatch.setenv("DEMIURGE_STREAM_USAGE", "1")
    assert llm._stream_usage_enabled() is True


# ── 向后兼容 ──────────────────────────────────────────────────────────────────


def test_chat_no_on_usage_kwarg():
    """chat() 不传 on_usage，旧调用链不受影响。"""
    fake_msg = MagicMock()
    fake_msg.content = "reply"

    with patch.object(llm, "build_model") as mock_build, \
         patch.object(llm, "_payload", return_value=[("system", "sys"), ("human", "usr")]):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_msg
        mock_build.return_value = mock_llm

        result = llm.chat(
            "http://example.com/v1", "key", "gpt-4o-mini",
            system="sys", user="usr",
        )
        assert result == "reply"


def test_chat透传on_usage():
    """chat() 传了 on_usage 时必须真的收到（fabric 自由循环每条决策走的就是它）。"""
    collected: list[dict] = []
    fake_msg = MagicMock()
    fake_msg.content = "reply"
    fake_msg.usage_metadata = {"prompt_tokens": 10, "completion_tokens": 2}

    with patch.object(llm, "build_model") as mock_build, \
         patch.object(llm, "_payload", return_value=[("system", "s"), ("human", "u")]):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_msg
        mock_build.return_value = mock_llm

        out = llm.chat(
            "http://example.com/v1", "key", "m", system="s", user="u",
            on_usage=collected.append,
        )

    assert out == "reply"
    assert len(collected) == 1
    assert collected[0]["prompt_tokens"] == 10

# ── _is_local_url（本地端点绕过系统代理）────────────────────────────────────


def test_is_local_url_localhost():
    assert llm._is_local_url("http://localhost:11434/v1") is True
    assert llm._is_local_url("http://127.0.0.1:8010/api") is True
    assert llm._is_local_url("http://[::1]:8080/v1") is True


def test_is_local_url_private_net():
    assert llm._is_local_url("http://192.168.1.5:8080/v1") is True
    assert llm._is_local_url("http://10.0.0.2:8080/v1") is True
    assert llm._is_local_url("http://172.20.0.2:8080/v1") is True


def test_is_local_url_public_false():
    assert llm._is_local_url("https://api.openai.com/v1") is False
    assert llm._is_local_url("https://open.bigmodel.cn/api/paas/v4") is False
    assert llm._is_local_url("") is False
