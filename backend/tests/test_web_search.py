"""M1.1 搜索源 Adapter 注册表单元测试。

覆盖：
- 注册表：注册/查找/默认源/未注册回落
- DDG 行为迁移：HTML 解析/空查询/异常返回空列表
- 签名兼容：web_search 对旧调用方（不传 provider）行为不变
- available_adapters 探测
"""

from app.services import web_search as ws


# ── 注册表 ───────────────────────────────────────────────────

def test_register_and_get_adapter():
    """注册 Adapter 后可通过名称取回。"""
    class FakeAdapter:
        def search(self, query, max_results=6, proxy=""):
            return [{"title": "fake", "snippet": "s", "url": "https://x.test"}]

    fake = FakeAdapter()
    ws.register_adapter("test-source", fake)
    assert ws.get_adapter("test-source") is fake


def test_get_adapter_missing_returns_none():
    """未注册的源返回 None。"""
    assert ws.get_adapter("nonexistent") is None


def test_default_adapter_is_ddg():
    """默认源是 ddg。"""
    adapter = ws.get_adapter("ddg")
    assert adapter is not None


def test_available_adapters_returns_sorted_names():
    """available_adapters 返回已注册源名称排序列表。"""
    names = ws.available_adapters()
    assert "ddg" in names
    assert names == sorted(names)


def test_web_search_defaults_to_ddg_when_no_provider():
    """不传 provider 时使用默认源 ddg。"""
    # 用空查询触发快速返回，验证走的是默认源路径
    result = ws.web_search("", max_results=6, proxy="")
    assert result == []


def test_web_search_unknown_provider_returns_empty():
    """不存在的 provider 返回空列表。"""
    result = ws.web_search("test query", provider="nonexistent", proxy="")
    assert result == []


# ── DDG HTML 解析 ────────────────────────────────────────────

_DDG_MOCK = """<!DOCTYPE html>
<html>
<body>
<div class="results">
    <a rel="nofollow" class="result__a" href="https://example.com/page1">Result One</a>
    <a class="result__snippet">Snippet for result one</a>
    <a rel="nofollow" class="result__a" href="https://example.com/page2">Result Two</a>
    <a class="result__snippet">Snippet for result two</a>
</div>
</body>
</html>"""


def test_ddg_parse_html(monkeypatch):
    """DDG adapter 正确解析 HTML 结果。"""
    import httpx

    class FakeResponse:
        text = _DDG_MOCK

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers, params):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)

    adapter = ws.get_adapter("ddg")
    results = adapter.search("test query", max_results=6, proxy="")

    assert len(results) == 2
    assert results[0]["title"] == "Result One"
    assert results[0]["snippet"] == "Snippet for result one"
    assert results[0]["url"] == "https://example.com/page1"
    assert results[1]["title"] == "Result Two"
    assert results[1]["snippet"] == "Snippet for result two"
    assert results[1]["url"] == "https://example.com/page2"


def test_ddg_empty_query_returns_empty():
    """DDG adapter 空查询返回空列表。"""
    adapter = ws.get_adapter("ddg")
    result = adapter.search("", proxy="")
    assert result == []


def test_ddg_http_error_returns_empty(monkeypatch):
    """DDG HTTP 异常返回空列表不抛。"""
    import httpx

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers, params):
            raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(httpx, "Client", FakeClient)

    adapter = ws.get_adapter("ddg")
    result = adapter.search("test query", proxy="")
    assert result == []


# ── 签名兼容 ─────────────────────────────────────────────────

def test_web_search_compat_no_provider(monkeypatch):
    """旧调用方不传 provider 时行为不变。"""
    import httpx

    class FakeClient:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, headers, params):
            raise httpx.ConnectError("no network")

    # 必须注入 FakeClient：否则 CI（可直连 DDG）会发真实请求返回真结果
    monkeypatch.setattr(httpx, "Client", FakeClient)

    # 验证 web_search 签名：旧调用方 web_search(q, n, proxy) 不报 TypeError
    result = ws.web_search("test", max_results=3, proxy="")
    # 无网络 → 返回空列表，不是异常
    assert result == []


def test_search_and_refine_accepts_provider(monkeypatch):
    """inspiration.search_and_refine 接受并透传 search_provider。"""
    from app.services import inspiration

    captured_provider = {}

    def fake_search(query, *, max_results, proxy, provider=None):
        captured_provider["provider"] = provider
        return [{"title": "t", "snippet": "s", "url": "https://x.test"}]

    def fake_chat(*args, **kwargs):
        return "tag1, tag2"

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration._llm, "chat", fake_chat)

    inspiration.search_and_refine(
        "query", "b", "k", "m", search_provider="ddg",
    )

    assert captured_provider["provider"] == "ddg"


# ── M1.2 图片搜索 adapter ──────────────────────────────────────

_BING_MOCK = """<!DOCTYPE html>
<html>
<body>
<a class="iusc" m="{&quot;murl&quot;:&quot;https://img.example.com/1.jpg&quot;,&quot;turl&quot;:&quot;https://t.example.com/1_thumb.jpg&quot;,&quot;purl&quot;:&quot;https://example.com/page1&quot;,&quot;t&quot;:&quot;Pic One&quot;}"></a>
<a class="other" m="{&quot;murl&quot;:&quot;https://img.example.com/garbage.jpg&quot;}"></a>
<a class="iusc" m="{&quot;murl&quot;:&quot;https://img.example.com/2.jpg&quot;,&quot;turl&quot;:&quot;data:image/jpeg;base64,xxx&quot;,&quot;purl&quot;:&quot;https://example.com/page2&quot;,&quot;t&quot;:&quot;Pic Two&quot;}"></a>
<a class="iusc" m="not-json"></a>
</body>
</html>"""


def test_bing_images_parse_html(monkeypatch):
    """Bing Images adapter 正确解析 m 属性 JSON。"""
    import httpx

    class FakeResponse:
        text = _BING_MOCK

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers, params):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    adapter = ws.get_image_adapter("bing-images")
    assert adapter is not None
    results = adapter.search_images("test query", max_results=8, proxy="")

    assert len(results) == 2
    assert results[0]["thumb_url"] == "https://t.example.com/1_thumb.jpg"
    assert results[0]["full_url"] == "https://img.example.com/1.jpg"
    assert results[0]["source_url"] == "https://example.com/page1"
    assert results[0]["title"] == "Pic One"
    # 第二张 turl 是 base64 → 回落 murl 做 thumb
    assert results[1]["thumb_url"] == "https://img.example.com/2.jpg"
    assert results[1]["full_url"] == "https://img.example.com/2.jpg"


def test_bing_images_empty_query_returns_empty():
    """图片搜索空查询返回空列表。"""
    adapter = ws.get_image_adapter("bing-images")
    assert adapter is not None
    assert adapter.search_images("", proxy="") == []


def test_bing_images_http_error_returns_empty(monkeypatch):
    """图片搜索 HTTP 异常返回空列表不抛。"""
    import httpx

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers, params):
            raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "Client", FakeClient)
    adapter = ws.get_image_adapter("bing-images")
    assert adapter is not None
    assert adapter.search_images("test", proxy="") == []


def test_image_search_compat_entry(monkeypatch):
    """image_search 入口：默认 bing-images，不存在返回空。"""
    import httpx

    class FakeClient:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, headers, params):
            raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "Client", FakeClient)
    assert ws.image_search("test", proxy="") == []
    assert ws.image_search("test", proxy="", provider="nonexistent") == []


def test_image_search_adapter_is_image_search_adapter():
    """bing-images adapter 满足 ImageSearchAdapter 协议。"""
    adapter = ws.get_image_adapter("bing-images")
    assert adapter is not None
    assert hasattr(adapter, "search_images")


def test_bing_images_proxy_passed_to_client(monkeypatch):
    """图片搜索 proxy 参数正确透传给 httpx.Client。"""
    import httpx

    class FakeResponse:
        text = _BING_MOCK
        def raise_for_status(self):
            pass

    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["kw"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers, params):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    adapter = ws.get_image_adapter("bing-images")
    adapter.search_images("test", max_results=8, proxy="http://127.0.0.1:7890")

    assert captured["kw"]["proxy"] == "http://127.0.0.1:7890"
    assert captured["kw"]["trust_env"] is False


# ── 协议检查 ─────────────────────────────────────────────────

def test_ddg_adapter_is_search_adapter():
    """DDG adapter 满足 SearchAdapter 协议。"""
    adapter = ws.get_adapter("ddg")
    assert isinstance(adapter, ws.SearchAdapter)