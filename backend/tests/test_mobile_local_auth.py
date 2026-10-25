"""M4：本机令牌鉴权（app.services.local_auth）与后端自取图的令牌头。

桌面不设 LAF_LOCAL_TOKEN ⇒ 中间件不安装，PC 行为零变化。移动端壳层注入 32 字节
随机令牌后，同一台手机上能连 127.0.0.1 的其它应用也拿不到 API 数据（回环门禁只
挡浏览器中介，不挡本机原生进程）。

自取图 4 处（generation_store / image_gen / image_store / visual_ci）走的是本机
HTTP API：启用令牌后必须带同一个头，否则会被自己的中间件判 401——这里用真实
回环 HTTP 服务验证「有令牌成功、无令牌失败」，不靠 mock 自证。
"""
from __future__ import annotations

import base64
import http.server
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.services import generation_store, image_gen, image_store, local_auth, visual_ci

_TOKEN = "test-token-32-bytes-0123456789abcdef"
_PNG = b"\x89PNG\r\n\x1a\n" + b"fakepng"


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/ping")
    def ping() -> dict[str, str]:
        return {"ok": "yes"}

    return app


def _installed() -> tuple[FastAPI, TestClient]:
    app = _app()
    assert local_auth.install_from_env(app) is True
    return app, TestClient(app)


# ---------------------------------------------------------------------------
# 中间件与引导端点
# ---------------------------------------------------------------------------


def test_未设令牌时不安装中间件(monkeypatch):
    monkeypatch.delenv(local_auth.TOKEN_ENV, raising=False)
    app = _app()
    assert local_auth.install_from_env(app) is False
    client = TestClient(app)
    assert client.get("/api/ping").status_code == 200
    assert client.get(local_auth.BOOTSTRAP_PATH).status_code == 404  # 引导端点也不存在


def test_设了令牌后无凭证或错凭证一律401(monkeypatch):
    monkeypatch.setenv(local_auth.TOKEN_ENV, _TOKEN)
    _, client = _installed()
    for headers in ({}, {local_auth.HEADER_NAME: "wrong-token"}):
        resp = client.get("/api/ping", headers=headers)
        assert resp.status_code == 401
        assert "令牌" in resp.json()["detail"]


def test_头或cookie任一命中即放行(monkeypatch):
    monkeypatch.setenv(local_auth.TOKEN_ENV, _TOKEN)
    _, client = _installed()
    assert client.get("/api/ping",
                      headers={local_auth.HEADER_NAME: _TOKEN}).status_code == 200
    client.cookies.set(local_auth.COOKIE_NAME, _TOKEN)
    assert client.get("/api/ping").status_code == 200


def test_引导端点种HttpOnly严格同站cookie并303到站内(monkeypatch):
    monkeypatch.setenv(local_auth.TOKEN_ENV, _TOKEN)
    _, client = _installed()
    resp = client.get(local_auth.BOOTSTRAP_PATH,
                      params={"token": _TOKEN, "next": "/chat?x=1"},
                      follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/chat?x=1"
    cookie = resp.headers["set-cookie"].lower()
    assert cookie.startswith(f"{local_auth.COOKIE_NAME}={_TOKEN}".lower())
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    # 壳层「二次启动先直连」依赖跨进程存活的持久 cookie：session cookie 不会被
    # CookieManager.flush() 落盘，重启即失效 ⇒ Max-Age 是承重字段，必须锁。
    assert "max-age" in cookie
    assert f"max-age={local_auth.COOKIE_MAX_AGE}" in cookie
    # 只锁存在性不够：Max-Age 偏短 ⇒ 每隔一段就回落引导 ⇒ 令牌重新进 URL query，
    # 正好废掉本轮加固。prefs 里的令牌本身不过期，cookie 就该长有效。
    assert local_auth.COOKIE_MAX_AGE >= 60 * 60 * 24 * 30
    # 303 的 Set-Cookie 已进 jar：后续同源请求无需再带任何头
    assert client.get("/api/ping").status_code == 200


@pytest.mark.parametrize("next_value", ["//evil.example/x", "https://evil.example/x", "", "relative"])
def test_引导端点next只允许站内绝对路径(monkeypatch, next_value):
    monkeypatch.setenv(local_auth.TOKEN_ENV, _TOKEN)
    _, client = _installed()
    resp = client.get(local_auth.BOOTSTRAP_PATH,
                      params={"token": _TOKEN, "next": next_value},
                      follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_引导端点错令牌401(monkeypatch):
    monkeypatch.setenv(local_auth.TOKEN_ENV, _TOKEN)
    _, client = _installed()
    resp = client.get(local_auth.BOOTSTRAP_PATH, params={"token": "nope"})
    assert resp.status_code == 401


def test_非ascii令牌头不把中间件打成500(monkeypatch):
    """compare_digest 对含非 ASCII 的 str 抛 TypeError——本机任意进程发个怪头就能让
    所有请求 500（拒绝服务面），故必须先 encode 成 bytes 再比。"""
    monkeypatch.setenv(local_auth.TOKEN_ENV, _TOKEN)
    _, client = _installed()
    resp = client.get("/api/ping",
                      headers={local_auth.HEADER_NAME: "令牌".encode("utf-8")})
    assert resp.status_code == 401


def test_引导端点不被根静态挂载吞掉(monkeypatch, tmp_path):
    """app.main 在 import 期就把前端 dist 挂在 "/"，引导端点必须插到路由表最前。"""
    (tmp_path / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    app = FastAPI()
    app.mount("/", StaticFiles(directory=tmp_path, html=True), name="frontend")
    monkeypatch.setenv(local_auth.TOKEN_ENV, _TOKEN)
    assert local_auth.install_from_env(app) is True
    resp = TestClient(app).get(local_auth.BOOTSTRAP_PATH,
                               params={"token": _TOKEN}, follow_redirects=False)
    assert resp.status_code == 303


def test_outbound_headers随环境切换(monkeypatch):
    monkeypatch.delenv(local_auth.TOKEN_ENV, raising=False)
    assert local_auth.outbound_headers() == {}
    monkeypatch.setenv(local_auth.TOKEN_ENV, _TOKEN)
    assert local_auth.outbound_headers() == {local_auth.HEADER_NAME: _TOKEN}


# ---------------------------------------------------------------------------
# 自取图：真实回环 HTTP 服务，要求令牌头
# ---------------------------------------------------------------------------


@pytest.fixture
def token_server(monkeypatch):
    seen: list[dict[str, str]] = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append({k.lower(): v for k, v in self.headers.items()})
            if self.headers.get(local_auth.HEADER_NAME) != _TOKEN:
                self.send_response(401)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(_PNG)))
            self.end_headers()
            self.wfile.write(_PNG)

        def log_message(self, fmt, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    # view_urls.fetchable 在调用期读 config.BACKEND_BASE_URL，改这里即可让
    # 根相对 local-view 拼到本测试服务（is_local_view_url 只看回环 + 端点路径）
    monkeypatch.setattr("app.config.BACKEND_BASE_URL", origin)
    try:
        yield origin, seen
    finally:
        server.shutdown()
        server.server_close()


def _fetch_generation_store(url: str) -> bytes:
    return generation_store._download_capped(url, timeout=5, max_bytes=1024 * 1024)


def _fetch_image_gen(url: str) -> bytes:
    data, _name, _mime = image_gen._load_image_bytes(url)
    return data


def _fetch_image_store(url: str) -> bytes:
    data, _ext = image_store._from_src(url)
    return data


def _fetch_visual_ci(url: str) -> bytes:
    uri = visual_ci._to_data_uri(url)
    assert uri.startswith("data:image/png;base64,")
    return base64.b64decode(uri.split(",", 1)[1])


_SELF_FETCH_SITES = [
    pytest.param(_fetch_generation_store, id="generation_store"),
    pytest.param(_fetch_image_gen, id="image_gen"),
    pytest.param(_fetch_image_store, id="image_store"),
    pytest.param(_fetch_visual_ci, id="visual_ci"),
]


@pytest.mark.parametrize("fetch", _SELF_FETCH_SITES)
def test_自取图必须带上令牌头(monkeypatch, token_server, fetch):
    monkeypatch.setenv(local_auth.TOKEN_ENV, _TOKEN)
    origin, seen = token_server
    assert fetch(f"{origin}/api/comfyui/local-view?path=x.png") == _PNG
    assert seen[0][local_auth.HEADER_NAME.lower()] == _TOKEN


def test_缺令牌时自取图会被自己的闸拒绝(monkeypatch, token_server):
    """反向锁：证明这个头是承重的，不是「加了也无所谓」。"""
    monkeypatch.delenv(local_auth.TOKEN_ENV, raising=False)
    origin, seen = token_server
    import httpx

    with pytest.raises(httpx.HTTPStatusError):
        _fetch_generation_store(f"{origin}/api/comfyui/local-view?path=x.png")
    assert local_auth.HEADER_NAME.lower() not in seen[0]


def test_自取图模块必须同时带令牌头_静态扫描():
    """约束优先落为测试：凡引用回环自取拼装/判定（`fetchable(` / `is_local_view_url`）的
    模块，必须同时引用 `outbound_headers`——否则新增链路会静默绕过令牌，只在真机上 401。

    扫描面**只有** `backend/app/**/*.py`（运行态发送方）：`tests/`、`scripts/` 不在面内，
    测试里拿 `fetchable(` 造 URL 不会误报。豁免按文件名给出属主模块（view_urls 拼 URL、
    url_guard 做判定），它们自身不发请求；新增属主需同步进 `owners`。"""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    owners = {"view_urls.py", "url_guard.py"}
    scanned: list[str] = []
    offenders: list[str] = []
    for path in sorted(app_dir.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "fetchable(" not in text and "is_local_view_url" not in text:
            continue
        if path.name in owners:
            continue
        scanned.append(path.name)
        if "outbound_headers" not in text:
            offenders.append(path.name)
    assert len(scanned) >= 4, scanned  # 4 条既有链路都在扫描面内
    assert offenders == []
