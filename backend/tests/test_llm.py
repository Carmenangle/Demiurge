"""llm 深模块：多消息通道 chat_messages 的 role 映射与空条目跳过。"""
from __future__ import annotations

import time

import pytest

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
    # 2026-10-19：claude 模型的 system 头会挂 cache_control(ttl=1h)，内容是块数组；
    # 合并/交替/末轮不重复这些原语义一条不动。
    assert captured[0][0][0] == "system"
    head = captured[0][0][1]
    assert isinstance(head, list) and head[0]["type"] == "text"
    assert head[0]["text"] == "系统一\n\n系统二"
    assert head[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert captured[0][1:] == [
        ("human", "旧问题"),
        ("ai", "旧回答"),
        ("human", "<user last input>\n\n</user last input>\n\n继续剧情"),
    ]
    assert sum(
        (c if isinstance(c, str) else c[0]["text"]).count("继续剧情")
        for _, c in captured[0]
    ) == 1


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
    # 2026-10-19：claude 模型（无显式 profile ⇒ 按模型名推断）system 头挂 cache_control
    assert captured[0][0][0] == "system"
    head = captured[0][0][1]
    assert isinstance(head, list) and head[0]["text"] == "甲\n\n乙"
    assert head[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert captured[0][1:] == [
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


def test_chat_messages_stream_回调异常原样穿透不包装(monkeypatch):
    """2026-09-04 A 方案：on_delta 是调用方代码（流式层在其中查 cancel_event 抛
    RoleplayInterrupted）。若被包成 RuntimeError，普通对话节点会把用户停止显示成
    「回答失败」。回调异常必须原样上抛（不进 retry、不换包装）。"""
    calls: list = []

    class _Abort(RuntimeError):
        pass

    def boom(delta):
        calls.append(delta)
        raise _Abort("用户已停止生成")

    monkeypatch.setattr(_llm, "build_model", lambda *a, **k: _MetaModel([
        _MetaResp("第一段"), _MetaResp("第二段"),
    ]))
    with pytest.raises(_Abort, match="用户已停止生成"):
        _llm.chat_messages_stream(
            "b", "k", "m", [{"role": "user", "content": "问"}], boom,
        )
    # 只吐了第一段就中断；未吞包、未重试（否则会再吐一次第一段）
    assert calls == ["第一段"]


class _BreakAfterModel:
    """吐一段后抛断流：模拟中转缓冲式下发中途 ReadTimeout 掐断（2026-10-20 真机形态）。"""

    def __init__(self, first, error):
        self._first = first
        self._error = error

    def stream(self, payload):
        yield _MetaResp(self._first)
        raise self._error


def test_chat_messages_stream_中途断流携带已收增量(monkeypatch):
    """2026-10-20 实锤（turn f77e68b1）：中转中途 `The read operation timed out`，
    已付费的思考/正文增量此前被整段丢弃（`parts` 是局部变量）⇒ 上层只能整段重掷。
    `StreamInterrupted` 必须把已收增量原样带出——那是拼接续写的唯一素材。"""
    monkeypatch.setattr(_llm, "build_model", lambda *a, **k: _BreakAfterModel(
        "已写出的半段", RuntimeError("The read operation timed out")))
    with pytest.raises(_llm.StreamInterrupted) as excinfo:
        _llm.chat_messages_stream(
            "b", "k", "m", [{"role": "user", "content": "问"}], lambda _d: None,
            retries=1,  # 断流且已有增量 → 不重试，直接抛
        )
    assert excinfo.value.partial == "已写出的半段"
    assert "调用对话模型失败" in str(excinfo.value)
    # 旧捕获点（4xx 判死 / 普通 except）不能因为换了类型而漏网
    assert isinstance(excinfo.value, RuntimeError)


def test_cap_max_tokens_按模型上限min收敛():
    """2026-09-04 用户定案：请求上限 > 模型上限 → 按模型来（min）；未登记不钳。"""
    # 命中前缀（带日期/下划线后缀变体）
    assert _llm.cap_max_tokens("deepseek-v4-flash-0731", 604000) == 393216
    assert _llm.cap_max_tokens("deepseek-v4-flash", 604000) == 393216
    assert _llm.cap_max_tokens("deepseek-v4-flash_0731", 604000) == 393216
    # 请求上限更小 → 按请求原样
    assert _llm.cap_max_tokens("deepseek-v4-flash-0731", 20000) == 20000
    # 未登记模型 → 不钳
    assert _llm.cap_max_tokens("gpt-5.6-terra", 604000) == 604000
    # None / 空模型 防御
    assert _llm.cap_max_tokens("deepseek-v4-flash-0731", None) is None
    assert _llm.cap_max_tokens("", 500) == 500


def test_chat_messages_超限max_tokens按模型收敛(monkeypatch):
    captured: dict = {}

    def fake_build(base_url, api_key, model, **kw):
        captured.update(kw)
        return _FakeModel([])

    monkeypatch.setattr(_llm, "build_model", fake_build)
    _llm.chat_messages(
        "b", "k", "deepseek-v4-flash-0731",
        [{"role": "user", "content": "问"}], max_tokens=604000,
    )
    assert captured["max_tokens"] == 393216


def test_chat_messages_stream_超限max_tokens按模型收敛(monkeypatch):
    captured: dict = {}

    def fake_build(base_url, api_key, model, **kw):
        captured.update(kw)
        return _FakeModel([])

    monkeypatch.setattr(_llm, "build_model", fake_build)
    _llm.chat_messages_stream(
        "b", "k", "deepseek-v4-flash-0731",
        [{"role": "user", "content": "问"}], lambda _d: None, max_tokens=604000,
    )
    assert captured["max_tokens"] == 393216


def test_is_client_rejected_4xx判死_瞬时仍可重试():
    """2026-09-04：4xx（429 除外）是确定性错误判死；429/5xx/超时仍是瞬时可重试。"""
    client = RuntimeError("Error code: 400 - {'code': 'LITELLM_ERROR', "
                          "'message': 'max_tokens should be less or equal to 393216'}")
    assert _llm.is_client_rejected(client)
    assert _llm.is_client_rejected(RuntimeError("Error code: 401 - invalid api key"))
    assert _llm.is_client_rejected(RuntimeError("Error code: 422 - unsupported field"))
    assert not _llm.is_client_rejected(RuntimeError("Error code: 429 - rate limit"))
    assert not _llm.is_client_rejected(RuntimeError("Error code: 502 - upstream_error"))
    assert not _llm.is_client_rejected(RuntimeError("connection error"))


def test_带代理失败时回退直连再试一次(monkeypatch):
    """2026-10-20 用户实锤「表格调用的模型不应该按照设置的机制来吗，既然能对话，表格理应能生成」：

    仓库对话链**从不把代理发给后端**（前端 `chatProxyUrl` 只定义不传）⇒ 走
    `build_model(proxy="")` 的「完全默认构造」⇒ 一直能连通；
    而手动填表/纪要索引显式带了 `proxy` ⇒ 注入代理 http_client ⇒ 代理不可用就
    `Connection error`，于是「能聊不能填表」。
    修法：代理调用重试用尽仍失败时，去掉代理按**与对话完全相同**的方式再试一次。
    """
    import langchain.chat_models  # noqa: F401  （确保依赖可用；实际被 monkeypatch 顶掉）

    built: list[str] = []

    class _FakeModel:
        def __init__(self, proxy: str, ok: bool):
            self.proxy, self.ok = proxy, ok

        def invoke(self, _payload):
            built.append(self.proxy)
            if not self.ok:
                raise RuntimeError("Connection error.")
            return type("R", (), {"content": "回填结果", "usage_metadata": {}})()

    def fake_init(**_kw):
        # 用 http_client/http_async_client 区分是否注入代理（显式代理时 build_model 会传）
        return _FakeModel(_kw.get("_probe_proxy", ""), _kw.get("_probe_proxy", "") == "")

    monkeypatch.setattr(_llm, "build_model", lambda *a, **k: (
        built.append(k.get("proxy") or "") or
        _FakeModel(k.get("proxy") or "", not (k.get("proxy") or ""))
    ))

    out = _llm.chat_messages(
        "http://x", "k", "m", [{"role": "user", "content": "hi"}],
        proxy="http://127.0.0.1:53564", retries=1,
    )
    assert out == "回填结果"
    # 首次必带代理；最终必有一次「去代理」的成功尝试
    assert built[0] == "http://127.0.0.1:53564"
    assert built[-1] == ""
    assert len(built) >= 2


def test_四xx类错误不得触发去代理重试(monkeypatch):
    """参数/鉴权类错误去掉代理也一样错，重试只会白烧一次调用。"""
    built: list[str] = []

    class _Fail:
        def invoke(self, _payload):
            raise RuntimeError("Error code: 400 - invalid max_tokens")

    monkeypatch.setattr(_llm, "build_model", lambda *a, **k: (
        built.append(k.get("proxy") or "") or _Fail()
    ))

    try:
        _llm.chat_messages(
            "http://x", "k", "m", [{"role": "user", "content": "hi"}],
            proxy="http://127.0.0.1:53564", retries=1,
        )
    except RuntimeError as exc:
        assert "400" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("4xx 必须上抛")
    assert built == ["http://127.0.0.1:53564"]   # 只试了一次，没有去代理重试


def test_chat_messages_非瞬时错误只试一次_摘要记本层1次(monkeypatch):
    """2026-10-20 观测补口：582 这类错误不在 `_is_transient` 白名单（只认 502/503/504…）

    ⇒ 我们这一层**一次都不重试**。真机后果：网关日志看到 3 发请求，其中 2 发其实
    是 SDK（`max_retries` 默认 2）发的，用户却以为是我们在重试。
    锁：**非瞬时 ⇒ 本层尝试次数恒为 1**，且摘要里写明「非瞬时」。
    """
    calls: list[int] = []

    class _Fail:
        def invoke(self, _payload):
            calls.append(1)
            raise RuntimeError("Error code: 582 - provider unavailable")

    monkeypatch.setattr(_llm, "build_model", lambda *a, **k: _Fail())

    with pytest.raises(RuntimeError) as excinfo:
        _llm.chat_messages(
            "http://x", "k", "m", [{"role": "user", "content": "hi"}], retries=2,
        )
    assert calls == [1]                       # 我们这层没重试
    msg = str(excinfo.value)
    assert "本层尝试 1 次" in msg
    assert "#1" in msg and "非瞬时" in msg


def test_chat_messages_瞬时错误退避重试_摘要记本层2次(monkeypatch):
    """对照上一条：命中 `_is_transient` 才会消耗我们自己的重试预算，且每次都进摘要。"""
    import time as _time

    calls: list[int] = []

    class _Fail:
        def invoke(self, _payload):
            calls.append(1)
            raise RuntimeError("Error code: 502 - upstream_error")

    monkeypatch.setattr(_llm, "build_model", lambda *a, **k: _Fail())
    monkeypatch.setattr(_time, "sleep", lambda _s: None)   # 退避不等，测试不慢

    with pytest.raises(RuntimeError) as excinfo:
        _llm.chat_messages(
            "http://x", "k", "m", [{"role": "user", "content": "hi"}], retries=2,
        )
    assert calls == [1, 1]
    msg = str(excinfo.value)
    assert "本层尝试 2 次" in msg
    assert "#1" in msg and "#2" in msg and "瞬时" in msg


def test_去代理直连仍失败_摘要记直连那次(monkeypatch):
    """去代理直连是**额外**的一次尝试，也必须进摘要（否则又是一笔看不见的发数）。"""
    built: list[str] = []

    class _Fail:
        def invoke(self, _payload):
            raise RuntimeError("Connection error.")

    monkeypatch.setattr(_llm, "build_model", lambda *a, **k: (
        built.append(k.get("proxy") or "") or _Fail()
    ))

    with pytest.raises(RuntimeError) as excinfo:
        _llm.chat_messages(
            "http://x", "k", "m", [{"role": "user", "content": "hi"}],
            proxy="http://127.0.0.1:53564", retries=1,
        )
    assert built == ["http://127.0.0.1:53564", ""]
    assert "#直连" in str(excinfo.value)


def test_失败摘要不得翻转瞬时与4xx判定(monkeypatch):
    """摘要是我们自己拼的字面，绝不能让「非瞬时」变「瞬时」（否则会多烧重试）、
    也不能让 4xx 判死失效。错误文本里原有的字面不算新增，故用中性错误文本验证。
    """
    class _Fail:
        def invoke(self, _payload):
            raise RuntimeError("boom")

    monkeypatch.setattr(_llm, "build_model", lambda *a, **k: _Fail())

    with pytest.raises(RuntimeError) as excinfo:
        _llm.chat_messages(
            "http://x", "k", "m", [{"role": "user", "content": "hi"}], retries=2,
        )
    raised = excinfo.value
    assert not _llm._is_transient(raised)      # 摘要没把「非瞬时」变成「瞬时」
    assert not _llm.is_client_rejected(raised)  # 也没凭空造出 4xx


# ── 2026-10-21：取消必须能打断「无增量窗口」（中转缓冲式下发） ──────────────


class _SlowAfterFirst:
    """先吐一段，然后安静数秒再吐下一段：模拟中转缓冲式下发（首字后长时间静默）。"""

    def __init__(self, quiet_seconds: float = 3.0):
        self._quiet = quiet_seconds
        self.calls = 0

    def stream(self, _payload):
        self.calls += 1
        yield _MetaResp("第一段")
        time.sleep(self._quiet)
        yield _MetaResp("第二段")


class _FakeHttpClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _patch_cancel_build(monkeypatch, model):
    captured: dict = {}
    fake_client = _FakeHttpClient()

    def fake_build(*_a, **kw):
        captured.update(kw)
        return model

    monkeypatch.setattr(_llm, "build_model", fake_build)
    monkeypatch.setattr(_llm, "_make_http_client", lambda *_a, **_k: fake_client)
    return captured, fake_client


def test_chat_messages_stream_无增量窗口内取消立即生效(monkeypatch):
    """2026-10-21 用户实锤：点停止后上游仍跑到 61.4s（网关记「客户端断开」，
    $1.3031、输出 0）。根因：取消只在 on_delta 回调里查，而中转缓冲式下发时
    首字 40~80s 才到 ⇒ 这段时间停止信号无人消费，后端阻塞在 next()，thread
    准入不释放（前端 60s 后提示「后端仍在收尾生成任务」）。
    锁：无增量期间 should_abort 也生效——不等下一段、不重试、强制断开连接。
    """
    model = _SlowAfterFirst(quiet_seconds=3.0)
    _captured, client = _patch_cancel_build(monkeypatch, model)
    started = time.monotonic()

    def should_abort() -> bool:
        return time.monotonic() - started > 0.3

    deltas: list[str] = []
    with pytest.raises(_llm.StreamAborted):
        _llm.chat_messages_stream(
            "http://x", "k", "m", [{"role": "user", "content": "问"}], deltas.append,
            retries=3, should_abort=should_abort,
        )
    elapsed = time.monotonic() - started
    assert deltas == ["第一段"]      # 已收到的增量照常交付，不丢
    assert elapsed < 2.0            # 3s 静默里就中断，不等第二段
    assert model.calls == 1         # 取消不重试（重试=再花一次钱）
    assert client.closed is True    # 专属连接被强制关闭（上游停止生成）
    assert isinstance(_llm.StreamAborted("x"), RuntimeError)  # 旧捕获点不误伤


def test_chat_messages_stream_正常完成也关闭专属客户端(monkeypatch):
    """每次流式尝试的专属 client 用完即弃（正常完成同样关闭，不泄漏 keep-alive）。"""
    captured, client = _patch_cancel_build(monkeypatch, _FakeModel([]))
    out = _llm.chat_messages_stream(
        "b", "k", "m", [{"role": "user", "content": "问"}], lambda _d: None,
        should_abort=lambda: False,
    )
    assert out == "流式"
    assert captured["http_client"] is client
    assert client.closed is True


def test_chat_messages_stream_未传取消探针不注入专属客户端(monkeypatch):
    """不传 should_abort 的既有调用方逐字节保持旧行为（不新建 client）。"""
    created: list = []
    captured: dict = {}

    def fake_build(*_a, **kw):
        captured.update(kw)
        return _FakeModel([])

    monkeypatch.setattr(_llm, "build_model", fake_build)
    monkeypatch.setattr(_llm, "_make_http_client", lambda *_a, **_k: created.append(1))
    out = _llm.chat_messages_stream(
        "b", "k", "m", [{"role": "user", "content": "问"}], lambda _d: None,
    )
    assert out == "流式"
    assert created == []
    assert captured.get("http_client") is None
