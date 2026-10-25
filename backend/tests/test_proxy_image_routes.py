"""两个远程图片代理路由的响应形态回归（2026-10-25 审计补刀）。

svg 可经 fetch_remote_image 的 `image/*` 检查落到前端，而 2026-10-24 的 svg 加固
只落在 local-view（local_media.LocalMedia.headers）——代理路由裸回 Response ⇒
直接导航到一个恶意 svg 的代理 URL ＝ 脚本在应用同源（回环端口）执行。

形态单一属主＝`image_proxy.proxied_image_response`（nosniff 全量、svg 另加
CSP sandbox、非标准 image/svg 归一 image/svg+xml）。本文件锁的是**两个路由的
接线**：ai_text image-proxy 与 comfyui web-materials/proxy-image 缺一边即红。

mini app 挂真实路由（同 test_csp_header 的顺序无关手法）：不 import app.main，
不受 pytest-randomly 导入顺序影响。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import ai, comfyui

_app = FastAPI()
_app.include_router(ai.router, prefix="/api/ai")
_app.include_router(comfyui.router, prefix="/api/comfyui")
_client = TestClient(_app)


def _patch_image_fetch(monkeypatch, data: bytes, ctype: str) -> None:
    from app.services import image_proxy
    monkeypatch.setattr(image_proxy, "fetch_remote_image",
                        lambda url, proxy="": (data, ctype))


# ── /api/ai/image-proxy（灵感卡缩略图/原图中转） ──────────────────

def test_image_proxy_svg带CSP_sandbox与nosniff(monkeypatch):
    """直航恶意 svg 的代理 URL：脚本必须被 CSP sandbox 禁掉（同 local-view 口径）。"""
    _patch_image_fetch(monkeypatch, b"<svg onload=alert(1)>", "image/svg+xml")
    r = _client.get("/api/ai/image-proxy", params={"url": "https://evil.test/x.svg"})
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["content-type"].startswith("image/svg+xml")


def test_image_proxy_非标准svg类型归一(monkeypatch):
    """服务端声明 image/svg（无 +xml）也必须归一并加 sandbox——未注册类型会诱使嗅探。"""
    _patch_image_fetch(monkeypatch, b"<svg/>", "image/svg")
    r = _client.get("/api/ai/image-proxy", params={"url": "https://evil.test/x.svg"})
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["content-security-policy"] == "sandbox"


def test_image_proxy_位图只有nosniff无CSP(monkeypatch):
    _patch_image_fetch(monkeypatch, b"\x89PNG\r\n", "image/png")
    r = _client.get("/api/ai/image-proxy", params={"url": "https://cdn.test/x.png"})
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in r.headers


# ── /api/comfyui/web-materials/proxy-image（图站「查看大图」中转） ──

def _patch_material_fetch(monkeypatch, data: bytes, ctype: str) -> None:
    from app.services import material_sources
    monkeypatch.setattr(material_sources, "fetch_image_bytes",
                        lambda url, proxy="": (data, ctype))


def test_web_materials代理_svg同样带sandbox(monkeypatch):
    """图站 host 白名单只是收窄面不是替代：同形路由必须吃同一响应形态属主。"""
    _patch_material_fetch(monkeypatch, b"<svg onload=alert(1)>", "image/svg+xml")
    r = _client.get("/api/comfyui/web-materials/proxy-image",
                    params={"url": "https://cdn.donmai.us/x.svg"})
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["cache-control"] == "public, max-age=3600"


def test_web_materials代理_位图保留缓存头(monkeypatch):
    _patch_material_fetch(monkeypatch, b"\x89PNG\r\n", "image/png")
    r = _client.get("/api/comfyui/web-materials/proxy-image",
                    params={"url": "https://cdn.donmai.us/x.png"})
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in r.headers
    assert r.headers["cache-control"] == "public, max-age=3600"
