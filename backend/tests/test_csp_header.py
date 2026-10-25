"""应用页 CSP 响应头回归（2026-10-24 XSS→密钥链第三环）。

CSP 单一属主＝`main._csp_document_header`：壳（location.replace 到本端口）与浏览器直连
的页面都由后端送达，tauri.conf 的 csp 罩不到远程 http 页面。本文件是 app.main 的第二个
导入方——env 预置与 worker 中性化已上收 tests/conftest.py 的「app.main 测试导入闸」。

⚠ 为什么用 mini app 注入而不是 TestClient(app)：pytest-randomly 会打乱收集顺序，若
test_request_guard 先被收集则 app.main 在本文件设 LAF_FRONTEND_DIST 之前已导入 ⇒
静态挂载不存在 ⇒ "/" 404。注入真实中间件函数到 mini app 既测准行为又不依赖导入顺序
（真实 app 上的接线用 user_middleware 形态锁兜住）。
"""
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from app.main import _csp_document_header, app

_MINI = Starlette(routes=[
    Route("/", lambda r: HTMLResponse("<html><body>ok</body></html>")),
    Route("/api/health", lambda r: JSONResponse({"status": "ok"})),
    Route("/media", lambda r: Response(b"\x89PNG", media_type="image/png")),
])
_MINI.add_middleware(BaseHTTPMiddleware, dispatch=_csp_document_header)
_client = TestClient(_MINI, raise_server_exceptions=True)


def test_html文档带CSP头():
    r = _client.get("/")
    assert r.status_code == 200
    assert (r.headers.get("content-type") or "").startswith("text/html")
    assert r.headers.get("content-security-policy") == _csp_document_header.__globals__["_DOCUMENT_CSP"]


def test_api与媒体响应不带CSP头():
    assert "content-security-policy" not in _client.get("/api/health").headers
    assert "content-security-policy" not in _client.get("/media").headers


def test_已有CSP头不被覆盖():
    # setdefault 语义：上游（未来代理/显式设置）若已给 CSP，本中间件不越权改写
    routes = [Route("/own", lambda r: HTMLResponse(
        "<html></html>", headers={"Content-Security-Policy": "default-src 'none'"}))]
    own = Starlette(routes=routes)
    own.add_middleware(BaseHTTPMiddleware, dispatch=_csp_document_header)
    r = TestClient(own).get("/own")
    assert r.headers["content-security-policy"] == "default-src 'none'"


def test_真实app已接线CSP中间件():
    # 形态锁：@app.middleware("http") 注册形态 = BaseHTTPMiddleware(dispatch=函数本体)，
    # 换成别的实现（如重写 _loopback_only 合并）必须同步更新本测试与手册 G/10。
    wired = any(
        m.cls is BaseHTTPMiddleware and m.kwargs.get("dispatch") is _csp_document_header
        for m in app.user_middleware
    )
    assert wired


def test_CSP关键指令在位():
    csp = _csp_document_header.__globals__["_DOCUMENT_CSP"]
    for must in (
        "script-src 'self' 'unsafe-inline'",  # scene 播放器 blob 内联脚本必需
        "connect-src 'self' blob: ws://127.0.0.1:*",  # ComfyUI 进度 WS 仅回环
        "frame-src blob: http://127.0.0.1:*",  # scene 播放器 blob + ComfyUI 画布回环 http
        "font-src 'self' data: blob:",  # scene 文字字体（包内 / WE 内置）全走 blob URL
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
    ):
        assert must in csp, must


def test_font_src放行blob_scene文字字体():
    # 2026-10-25 真机实锤：scene 文字层的 FontFace 一律用 blob URL（包内 Quicksand 与
    # WE 安装目录的 spincycle 都先取字节再建 blob）。CSP 只有 'self' data: 时三条
    # FontFace 全 error ⇒ 日期/星期/时钟退系统字：SUN 的 3D 字形没了，回退字体的行距
    # 又更短，星期块下沿压到时钟上。dev vite 不发 CSP，所以 e2e 绿而生产红——本用例
    # 把「生产页面必须放行 blob 字体」钉在 CSP 单一属主上。
    csp = _csp_document_header.__globals__["_DOCUMENT_CSP"]
    directive = next(
        (d.strip() for d in csp.split(";") if d.strip().startswith("font-src ")),
        "",
    )
    assert directive, "font-src 指令缺失"
    assert "blob:" in directive.split()[1:], directive


def _frame_src_sources(csp: str) -> list[str]:
    for directive in csp.split(";"):
        parts = directive.strip().split()
        if parts and parts[0] == "frame-src":
            return parts[1:]
    return []


def _host_source_matches(sources: list[str], url: str) -> bool:
    scheme, _, rest = url.partition("://")
    host = rest.partition(":")[0].strip("[]").lower()
    for src in sources:
        src_scheme, sep, authority = src.partition("://")
        if not sep or src_scheme != scheme:
            continue
        src_port = authority.rpartition(":")[2]
        src_host = authority.partition(":")[0].strip("[]").lower()
        if src_port == "*" and src_host == host:
            return True
    return False


def test_frame_src放行回环http_ComfyUI画布():
    # 2026-10-24 审计回归:前端 ComfyUI 画布 iframe（ChatView/WorkflowCard/AIBuildView 等）
    # 直接嵌 settings.comfyuiUrl（默认 http://127.0.0.1:8188）。曾误写成 frame-src blob:
    # ⇒ 真机实测同源 http iframe 被拦成不透明 blocked page，画布全挂。本用例锁定画布
    # 必需的回环 http 主机源在位——收窄回 blob: 即红；LAN 主机仍拦（登记取舍）。
    sources = _frame_src_sources(_csp_document_header.__globals__["_DOCUMENT_CSP"])
    for url in ("http://127.0.0.1:8188", "http://localhost:8188", "http://[::1]:8188"):
        assert _host_source_matches(sources, url), url
    assert not _host_source_matches(sources, "http://192.168.1.5:8188")
