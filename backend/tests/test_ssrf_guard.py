"""SSRF 修复回归：重定向逐跳校验（校验通过才发下一跳请求）。

背景：三处「后端主动 fetch 用户 URL」的链路曾带同一类漏洞——
首跳校验通过后，下载器自动跟随重定向且重定向目标不校验，外部 URL
可 302 跳到私网/metadata 地址把内网响应拉回本地。修复后统一合同：
follow_redirects 关闭 + 每跳 validate_media_url（loopback local-view 豁免）。
"""
import httpx
import pytest

from app.services import image_store, visual_ci
from app.services.comfyui_client import ComfyError
from app.services.image_proxy import ImageProxyError, fetch_remote_image


def _redirect_client_factory(calls: list[str], redirect_map: dict[int, str]):
    """构造 Fake httpx.Client：第 N 次请求按 redirect_map 返回重定向或 200。

    redirect_map: {请求序号(1-based): Location}；不在表内 → 200 image/png。
    第二跳起若目标未过校验，测试通过 AssertionError 失败（不应被请求到）。
    """

    class FakeResp:
        def __init__(self, status: int, headers: dict | None = None, url: str = ""):
            self.status_code = status
            self.headers = headers or {}
            self.url = url

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("err", request=None, response=None)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_bytes(self):
            yield b"png"

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs.get("follow_redirects") is False, "必须关闭自动重定向"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, method, url, **kwargs):
            calls.append(str(url))
            n = len(calls)
            if n in redirect_map:
                return FakeResp(302, {"location": redirect_map[n]}, url)
            return FakeResp(200, {"content-type": "image/png"}, url)

        def get(self, url, **kwargs):
            calls.append(str(url))
            n = len(calls)
            if n in redirect_map:
                return FakeResp(302, {"location": redirect_map[n]}, str(url))
            return FakeResp(200, {"content-type": "image/png"}, str(url))

    return FakeClient


def test_image_store_拒绝重定向到私网(monkeypatch):
    """外部 URL 302 → 169.254.169.254：第二跳不得发出，直接拒绝。"""
    calls: list[str] = []
    monkeypatch.setattr(
        httpx, "Client",
        _redirect_client_factory(calls, {1: "http://169.254.169.254/latest/meta-data/"}),
    )
    with pytest.raises(ComfyError, match="重定向目标被拒"):
        image_store._download_external_url("https://cdn.example.com/a.png")
    assert len(calls) == 1  # 只有首跳被请求


def test_image_store_拒绝重定向到本机环回(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        httpx, "Client",
        _redirect_client_factory(calls, {1: "http://127.0.0.1:8010/api/comfyui/local-view?path=x"}),
    )
    with pytest.raises(ComfyError, match="重定向目标被拒"):
        image_store._download_external_url("https://cdn.example.com/b.png")
    assert len(calls) == 1


def test_image_store_公网重定向链放行(monkeypatch):
    """公网→公网 重定向链正常跟随，最终拿到字节。"""
    calls: list[str] = []
    monkeypatch.setattr(
        httpx, "Client",
        _redirect_client_factory(calls, {1: "https://cdn2.example.com/b.png"}),
    )
    data = image_store._download_external_url("https://cdn1.example.com/a.png")
    assert data == b"png"
    assert calls == [
        "https://cdn1.example.com/a.png",
        "https://cdn2.example.com/b.png",
    ]


def test_image_store_重定向循环超限(monkeypatch):
    calls: list[str] = []
    loop = {i: "/next" for i in range(1, 7)}
    monkeypatch.setattr(httpx, "Client", _redirect_client_factory(calls, loop))
    with pytest.raises(ComfyError, match="重定向跳数过多"):
        image_store._download_external_url("https://loop.example.com/a")


def test_image_proxy_拒绝重定向到私网且不发第二跳(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        httpx, "Client",
        _redirect_client_factory(calls, {1: "http://192.168.1.1/evil.png"}),
    )
    with pytest.raises(ImageProxyError, match="重定向目标被拒"):
        fetch_remote_image("https://safe-cdn.com/redirect-to-internal")
    assert calls == ["https://safe-cdn.com/redirect-to-internal"]


def test_visual_ci_拒绝私网直连(monkeypatch):
    """_to_data_uri 对非 local-view 的私网 URL 直接拒绝（返回空跳过 VLM）。"""
    import httpx as _httpx

    def _boom(*_a, **_k):
        raise AssertionError("私网 URL 不应发起网络请求")

    monkeypatch.setattr(_httpx, "get", _boom)
    assert visual_ci._to_data_uri("http://169.254.169.254/latest/meta-data/") == ""
    assert visual_ci._to_data_uri("http://192.168.1.1/secret.png") == ""
