"""M1.2 图片代理端点单测：协议校验/类型校验/大小校验。"""
import pytest

from app.services.image_proxy import fetch_remote_image, ImageProxyError, MAX_IMAGE_BYTES


def test_拒绝非http协议():
    with pytest.raises(ImageProxyError, match="http/https"):
        fetch_remote_image("file:///etc/passwd")
    # data: URI 原先漏过校验靠 httpx 抛 502；2026-10-24 起校验层（pinned_media_url）
    # 直接 400「只支持 http/https」——代理只做网络 fetch，data: 属调用方错误。
    with pytest.raises(ImageProxyError, match="http/https"):
        fetch_remote_image("data:image/png;base64,xxx")


def test_拒绝非图片内容类型(monkeypatch):
    import httpx

    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.headers = {"content-type": "text/html; charset=utf-8"}
            self.url = "https://example.com/not-an-image"
            self.history = ()

        def raise_for_status(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_bytes(self):
            yield b"<html></html>"

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, method, url, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(ImageProxyError, match="非图片内容类型"):
        fetch_remote_image("https://example.com/not-an-image")


def test_拒绝超过大小限制(monkeypatch):
    import httpx

    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.headers = {"content-type": "image/png"}
            self.url = "https://example.com/big.png"
            self.history = ()

        def raise_for_status(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_bytes(self):
            for _ in range(5):
                yield b"x" * 1024 * 1024
            yield b"x"  # 超了 1 byte

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, method, url, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(ImageProxyError, match="图片过大"):
        fetch_remote_image("https://example.com/big.png")


def test_成功拉取图片(monkeypatch):
    import httpx

    fake_data = b"\x89PNG\r\n\x1a\nfake-png-body"

    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.headers = {"content-type": "image/png"}
            self.url = "https://example.com/img.png"
            self.history = ()

        def raise_for_status(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_bytes(self):
            yield fake_data

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, method, url, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    data, ctype = fetch_remote_image("https://example.com/img.png")
    assert data == fake_data
    assert ctype == "image/png"


def test_网络异常返回502(monkeypatch):
    import httpx

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, method, url, **kwargs):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(ImageProxyError, match="图片拉取失败"):
        fetch_remote_image("https://example.com/not-reachable")


def test_默认max_bytes常量():
    assert MAX_IMAGE_BYTES == 5 * 1024 * 1024


def test_拒绝私网IP(monkeypatch):
    """SSRF 防护：拒绝指向内网 IP 的 URL（validate_media_url 拦截）"""
    with pytest.raises(ImageProxyError, match="私网"):
        fetch_remote_image("http://127.0.0.1:8188/view?filename=test.png")
    with pytest.raises(ImageProxyError, match="私网"):
        fetch_remote_image("http://192.168.1.1/img.png")
    with pytest.raises(ImageProxyError, match="私网"):
        fetch_remote_image("http://10.0.0.1/secret.png")


def test_拒绝metadata_IP(monkeypatch):
    """SSRF 防护：拒绝云实例 metadata 地址"""
    with pytest.raises(ImageProxyError, match="私网"):
        fetch_remote_image("http://169.254.169.254/latest/meta-data/")


def test_拒绝重定向到内网(monkeypatch):
    """SSRF 防护（逐跳校验，TOCTOU 修复）：302 指向内网地址时拦截且不发第二跳请求。

    旧实现 follow_redirects=True 先请求后审计 history，对内网的请求已经发出；
    新实现 follow_redirects=False，校验通过才发下一跳——FakeClient 的 stream
    被第二次调用时直接 fail 测试。
    """
    import httpx

    calls: list[str] = []

    class FakeRedirectResponse:
        def __init__(self):
            self.status_code = 302
            self.headers = {"location": "http://192.168.1.1/evil.png"}
            self.url = "https://safe-cdn.com/redirect-to-internal"

        def raise_for_status(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs.get("follow_redirects") is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, method, url, **kwargs):
            calls.append(url)
            if len(calls) > 1:
                raise AssertionError("重定向目标未校验就被请求（TOCTOU）")
            return FakeRedirectResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(ImageProxyError, match="重定向目标被拒"):
        fetch_remote_image("https://safe-cdn.com/redirect-to-internal")
    assert calls == ["https://safe-cdn.com/redirect-to-internal"]


def test_响应形态_nosniff全量_svg另加CSP_sandbox():
    """2026-10-25 审计补刀：代理响应形态单一属主 proxied_image_response。

    svg 可过 fetch 的 image/* 检查；没有 nosniff + CSP sandbox 时直航代理 URL
    即同源执行脚本（与 local_media 的 local-view 加固同口径，两端必须同形）。
    """
    from app.services.image_proxy import proxied_image_response

    ctype, headers = proxied_image_response("image/png")
    assert ctype == "image/png"
    assert headers == {"X-Content-Type-Options": "nosniff"}

    ctype, headers = proxied_image_response("image/svg+xml")
    assert ctype == "image/svg+xml"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Content-Security-Policy"] == "sandbox"

    # 非标准 image/svg（无 +xml）归一——未注册类型会诱使浏览器内容嗅探
    ctype, headers = proxied_image_response("image/svg")
    assert ctype == "image/svg+xml"
    assert headers["Content-Security-Policy"] == "sandbox"