# -*- coding: utf-8 -*-
"""llm.py usage 回调测试：_collect_usage、on_usage 回调链路、backward compatibility。"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from app.services import llm


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
    assert stats["cache_hit_ratio"] == round(500 / 800, 4)


def test_collect_usage_empty():
    assert llm._collect_usage(None) == {}
    # 空 dict 也归一为全 0 统计（有意义的观察值）
    assert llm._collect_usage({}) == {
        "prompt_tokens": 0, "completion_tokens": 0,
        "cached_tokens": 0, "total_tokens": 0, "cache_hit_ratio": 0.0,
    }
    # 非 dict 类型返回 {}
    assert llm._collect_usage("nope") == {}


# ── build_model 签名 ──────────────────────────────────────────────────────────


def test_build_model_accepts_on_usage():
    import inspect
    sig = inspect.signature(llm.build_model)
    assert "on_usage" in sig.parameters
    params = list(sig.parameters.keys())
    assert params[-1] == "on_usage"


def test_chat_messages_accepts_on_usage():
    import inspect
    sig = inspect.signature(llm.chat_messages)
    assert "on_usage" in sig.parameters


def test_chat_messages_stream_accepts_on_usage():
    import inspect
    sig = inspect.signature(llm.chat_messages_stream)
    assert "on_usage" in sig.parameters


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
