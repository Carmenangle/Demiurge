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


# ── prompt cache：显式 cache_control(ttl=1h)（2026-10-19）─────────────────
# 用户实锤：8 笔请求缓存创建 291,657 tokens、读取仅 61,438（4.75 倍），白付约 $6.7。
# 根因＝Anthropic 默认 5 分钟 TTL 扛不住剧情对话间隔。这里显式把寿命拉到 1 小时。


def _msgs() -> list[dict]:
    return [
        {"role": "system", "content": "静态头（跨轮不变）"},
        {"role": "user", "content": "第一轮"},
        {"role": "assistant", "content": "回复"},
        {"role": "system", "content": "【世界设定·本轮召回（每轮重排）】动态块"},
        {"role": "user", "content": "第二轮"},
    ]


def test_inject_prompt_cache_claude_wraps_head_only():
    out = llm._inject_prompt_cache(_msgs(), is_claude=True)
    head = out[0]
    assert isinstance(head["content"], list)
    assert head["content"][0]["type"] == "text"
    assert head["content"][0]["text"] == "静态头（跨轮不变）"
    assert head["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # 只有第一条 system 被包；动态块与对话原样
    assert out[3]["content"] == "【世界设定·本轮召回（每轮重排）】动态块"
    assert out[1] == {"role": "user", "content": "第一轮"}
    assert len([m for m in out if isinstance(m.get("content"), list)]) == 1


def test_inject_prompt_cache_skips_non_claude():
    out = llm._inject_prompt_cache(_msgs(), is_claude=False)
    assert out[0]["content"] == "静态头（跨轮不变）"


def test_inject_prompt_cache_respects_disable(monkeypatch):
    monkeypatch.setenv("LAF_PROMPT_CACHE", "0")
    out = llm._inject_prompt_cache(_msgs(), is_claude=True)
    assert out[0]["content"] == "静态头（跨轮不变）"


def test_inject_prompt_cache_no_double_wrap():
    """内容已是块数组时只补 cache_control，不重复包 text（否则提示词结构被破坏）。"""
    msgs = [{"role": "system", "content": [
        {"type": "text", "text": "已是块"}, {"type": "text", "text": "第二块"}]}]
    out = llm._inject_prompt_cache(msgs, is_claude=True)
    blocks = out[0]["content"]
    assert len(blocks) == 2
    assert blocks[0]["text"] == "已是块"
    assert blocks[1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_payload_injects_cache_for_claude_model():
    """_payload 对 claude 模型生效（无显式 profile ⇒ 按模型名推断）。"""
    payload = llm._payload("claude-opus-4-8", _msgs())
    head_content = payload[0][1]
    assert isinstance(head_content, list)
    assert head_content[0]["cache_control"]["ttl"] == "1h"


def test_payload_explicit_openai_profile_overrides_model_name():
    """显式 openai_compatible 必须压过模型名里的 claude——否则给不该缓存的请求也挂断点。

    这条是回归锁：2026-10-19 首版把两个条件用 `or` 拼，被本测试抓出来后改成
    与 prepare_messages 逐字同构的「显式 profile 优先」。
    """
    payload = llm._payload("claude-opus-4-8", _msgs(), provider_profile="openai_compatible")
    assert payload[0][1] == "静态头（跨轮不变）"


# ── 稳定前缀断点（2026-10-19 第二刀）──────────────────────────────────────
# 只挂 system 头只能覆盖 2,883 字，而实测稳定前缀有 36,942 字（占上下文 75.5%）。
# 这里把「断点落在最后一条静态预设块、绝不落到每轮块上」钉死。


def _roleplay_shape() -> list[dict]:
    """仿真真实 roleplay 结构：静态头 → 对话 → 预设静态块 → 本轮输入 → 动态块。"""
    return [
        {"role": "system", "content": "【输出结构】静态头，里面写着「本轮剧情导致的状态变化」"},  # 0
        {"role": "user", "content": "第一轮输入"},                                                # 1
        {"role": "assistant", "content": "第一轮回复"},                                            # 2
        {"role": "system", "content": "预设块 A（静态）"},                                          # 3
        {"role": "system", "content": "预设块 B：分析<user last input>中主人输入"},                 # 4 ← 旧版会误判
        {"role": "system", "content": "* 升华描写。</Order>"},                                     # 5 ← 应落在这里
        {"role": "user", "content": "以下为用户最新输入。 <user last input> 新剧情"},               # 6 ← 首个动态
        {"role": "system", "content": "【世界设定·本轮召回（每轮重排）】…"},                        # 7 动态
        {"role": "system", "content": "[输出纪律] 静态但夹在动态之后"},                             # 8
        {"role": "system", "content": "【本轮用户安排·最高优先级】…"},                              # 9 动态
        {"role": "user", "content": "新剧情"},                                                     # 10
    ]


def test_stable_prefix_tail_落在最后一条静态预设块():
    assert llm._stable_prefix_tail(_roleplay_shape()) == 5


def test_stable_prefix_tail_不被静态提示词里的裸标签骗到():
    """预设 COT 指南里写了「分析<user last input>…」——那是静态指令，不能当动态块。"""
    msgs = [
        {"role": "system", "content": "头"},
        {"role": "system", "content": "指南：在这里分析<user last input>中主人输入内容。"},
        {"role": "system", "content": "末尾静态块"},
        {"role": "user", "content": "以下为用户最新输入。 <user last input> 正文"},
        {"role": "system", "content": "【世界设定·本轮召回（每轮重排）】…"},
    ]
    # 触发点是 index 3（带「以下为」前缀的包装），回退到 index 2
    assert llm._stable_prefix_tail(msgs) == 2


def test_stable_prefix_tail_无标记时返回负一():
    """world/table_maintenance 那类 2 条消息请求没有每轮块 ⇒ 只留头断点，绝不猜。"""
    assert llm._stable_prefix_tail([
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "问题"},
    ]) == -1


def _roleplay_shape_with_history() -> list[dict]:
    """仿真 2026-10-21 的真实 roleplay 布局（预设把 <history> 排在 Order/COT 静态块之前）。

    实测 trace（37 条）：静态头+问候对+预设静态块 → `<history>` 开标签行 → 上一轮
    剧情轮（user+assistant）→ Order/COT/格式等**静态**块 → 本轮召回/用户安排 → 本轮输入。
    """
    return [
        {"role": "system", "content": "【输出结构】静态头"},                                     # 0
        {"role": "user", "content": "你是谁？"},                                                  # 1 静态问候
        {"role": "assistant", "content": "<GrayWill> 我是灰魂…"},                                 # 2 静态问候
        {"role": "system", "content": "【世界设定（相关条目）】静态锚点段"},                        # 3
        {"role": "system", "content": "预设静态块（含 COT 指南，提到 <history> 是历史记录）"},      # 4 ← 只提到、不以标签结尾
        {"role": "system", "content": "预设静态块末尾"},                                          # 5 ← 应落在这里
        {"role": "system", "content": "- <history>是历史推演记录及其他补充设定\n<history>"},       # 6 ← 每轮边界（开标签行）
        {"role": "user", "content": "办婚礼，进行婚纱甜蜜 sex 环节"},                               # 7 上一轮 user
        {"role": "assistant", "content": "腓特烈跪在@我@身前…（上一轮正文）"},                      # 8 上一轮 assistant
        {"role": "system", "content": "</history>"},                                              # 9
        {"role": "system", "content": "* 升华描写。</Order>"},                                      # 10 静态但排在历史之后
        {"role": "user", "content": "以下为用户最新输入。 <user last input> 正常推进"},             # 11 本轮输入
        {"role": "system", "content": "【世界设定·本轮召回（每轮重排）】…"},                        # 12 动态
        {"role": "user", "content": "正常推进时间甜蜜过日子"},                                      # 13
    ]


def test_stable_prefix_tail_历史块也算每轮边界():
    """2026-10-21 实锤：跨回合首异在 `<history>`（上一轮剧情），不是一个回合的重试。

    旧逻辑只认显式每轮短语 ⇒ 断点落到历史之后（msg10），缓存块每轮都变、永远读不中，
    只剩 system 头能命中（账单实测命中率 8.3%）。修后断点落在历史块之前（msg5）。"""
    assert llm._stable_prefix_tail(_roleplay_shape_with_history()) == 5


def test_stable_prefix_tail_静态指令提到history不算边界():
    """静态块里**提到** `<history>`（不以标签结尾）不得当边界——与裸标签教训同源。"""
    msgs = [
        {"role": "system", "content": "头"},
        {"role": "system", "content": "指南：把内容写进 <history> 段落里"},  # 提到但不以标签结尾
        {"role": "system", "content": "静态块末尾"},                          # ← 应落在这里
        {"role": "system", "content": "- <history>是历史记录\n<history>"},     # 真正的开标签行
        {"role": "user", "content": "上一轮输入"},
        {"role": "system", "content": "【世界设定·本轮召回（每轮重排）】…"},
    ]
    assert llm._stable_prefix_tail(msgs) == 2


def test_inject_prompt_cache_挂两个断点且不碰动态块():
    out = llm._inject_prompt_cache(_roleplay_shape(), is_claude=True)
    marked = [
        i for i, m in enumerate(out)
        if isinstance(m.get("content"), list)
        and any(isinstance(b, dict) and "cache_control" in b for b in m["content"])
    ]
    assert marked == [0, 5]
    # 动态块必须是原样字符串，不能被包成块数组
    assert out[6]["content"] == "以下为用户最新输入。 <user last input> 新剧情"
    assert out[7]["content"] == "【世界设定·本轮召回（每轮重排）】…"
    assert out[9]["content"] == "【本轮用户安排·最高优先级】…"


def test_inject_prompt_cache_覆盖率显著提升():
    """第二个断点必须把缓存面从「system 头」扩到「最后一条静态预设块」。"""
    msgs = _roleplay_shape()
    out = llm._inject_prompt_cache(msgs, is_claude=True)
    head_only = len(llm._content_text(out[0]))
    with_tail = sum(len(llm._content_text(m)) for m in out[:6])
    assert with_tail > head_only * 3


def test_inject_prompt_cache_无每轮块时只挂头():
    out = llm._inject_prompt_cache([
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "问题"},
    ], is_claude=True)
    marked = [
        i for i, m in enumerate(out)
        if isinstance(m.get("content"), list)
        and any(isinstance(b, dict) and "cache_control" in b for b in m["content"])
    ]
    assert marked == [0]


def test_payload_no_cache_for_non_claude():
    payload = llm._payload("gpt-5.6-terra", _msgs(), provider_profile="openai_compatible")
    assert payload[0][1] == "静态头（跨轮不变）"


def test_inject_prompt_cache_does_not_mutate_input():
    """不得就地改调用方传入的 messages（上层会复用同一列表）。"""
    msgs = _msgs()
    before = msgs[0]["content"]
    llm._inject_prompt_cache(msgs, is_claude=True)
    assert msgs[0]["content"] == before


# ── 流式读超时（2026-10-20 从 30s 放宽到 120s）──────────────────────────
# 实锤：中转缓冲式下发，首字 43s、段间空档 62/68/80s；30s 会把**正常生成**的请求断开
# （网关侧记「客户端断开」，3 笔 × $2.7735，每笔已产出 4,000 output tokens）。


def test_流式读超时默认120秒(monkeypatch):
    monkeypatch.delenv("LAF_STREAM_READ_TIMEOUT", raising=False)
    assert llm._stream_read_timeout() == 120.0


def test_流式读超时可被环境变量覆盖(monkeypatch):
    monkeypatch.setenv("LAF_STREAM_READ_TIMEOUT", "200")
    assert llm._stream_read_timeout() == 200.0


def test_流式读超时非法值回落默认(monkeypatch):
    for bad in ("abc", "0", "-5", ""):
        monkeypatch.setenv("LAF_STREAM_READ_TIMEOUT", bad)
        assert llm._stream_read_timeout() == 120.0, bad
