"""手机端联机同库：配置存储、反向代理、令牌隔离与 WS 中继。

真行为断言优先：真 HTTP / 真 WS 假 PC；重点锁三条安全合同——
1. 手机本机令牌闸在最外层（未认证请求不触达 PC）；
2. 出站只带 PC 令牌，手机 cookie/令牌绝不外流；
3. /api/mobile/* 与 /api/health 永远本机处理。
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import websockets

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.routers import mobile_remote as mobile_remote_router
from app.services import local_auth, mobile_remote

PC_TOKEN = "pc-token-0123456789"
PHONE_TOKEN = "phone-token-0123456789"


class FakePC:
    """最小 PC 后端：记录收到的请求，/api/health 可切换令牌校验。

    除健康探针外还扮演两台联机体检要用到的电脑端点：`/api/user-state`（回一个
    `settings.comfyuiUrl`）与 `/api/comfyui/status?url=`（回 `running`）。这两个开关
    让测试能钉住「手机报的是电脑存的那个地址、按那个地址探的结果」。
    """

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.require_token = True
        self.comfyui_url = "http://192.0.2.9:8188"
        self.comfy_running = True
        self.user_state_status = 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 — http.server 约定
                outer.requests.append((self.path, dict(self.headers)))
                if outer.require_token and self.headers.get("X-Demiurge-Token") != PC_TOKEN:
                    body = b'{"detail":"missing token"}'
                    self._reply(401, body)
                    return
                if self.path.startswith("/api/health"):
                    body = b'{"status":"ok","running_jobs":0}'
                elif self.path.startswith("/api/user-state"):
                    if outer.user_state_status != 200:
                        self._reply(outer.user_state_status, b'{"detail":"boom"}')
                        return
                    body = json.dumps(
                        {"repos": [], "settings": {"comfyuiUrl": outer.comfyui_url}}).encode()
                elif self.path.startswith("/api/comfyui/status"):
                    body = json.dumps(
                        {"running": outer.comfy_running, "managed": False}).encode()
                else:
                    body = json.dumps({
                        "path": self.path,
                        "token": self.headers.get("X-Demiurge-Token") or "",
                        "cookie": self.headers.get("Cookie") or "",
                        "origin": self.headers.get("Origin") or "",
                    }).encode()
                self._reply(200, body)

            def _reply(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:  # noqa: ANN002 — 静音测试输出
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = int(self.httpd.server_address[1])
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=3)

    def paths(self) -> list[str]:
        return [path for path, _headers in self.requests]


class FakePCWS:
    """最小 PC 进度 WS：记录路径/令牌，回显收到的帧。"""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.tokens: list[str] = []
        self.port = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        started = threading.Event()

        async def _serve() -> None:
            self._stop = asyncio.Event()
            self._loop = asyncio.get_running_loop()

            async def _handler(ws) -> None:  # noqa: ANN001 — websockets 协议对象
                self.paths.append(str(ws.request.path))
                self.tokens.append(ws.request.headers.get("X-Demiurge-Token") or "")
                async for message in ws:
                    await ws.send(message)

            async with websockets.serve(_handler, "127.0.0.1", 0) as server:
                self.port = int(server.sockets[0].getsockname()[1])
                started.set()
                assert self._stop is not None
                await self._stop.wait()

        self._thread = threading.Thread(target=lambda: asyncio.run(_serve()), daemon=True)
        self._thread.start()
        assert started.wait(5), "fake PC ws server failed to start"

    def stop(self) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread is not None:
            self._thread.join(timeout=3)


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _write_config(tmp_path: Path, base_url: str, *, enabled: bool = True) -> None:
    (tmp_path / mobile_remote.CONFIG_NAME).write_text(json.dumps({
        "enabled": enabled, "base_url": base_url, "token": PC_TOKEN,
    }), encoding="utf-8")


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "local", "running_jobs": 0}

    @app.get("/api/mobile/remote")
    def remote() -> dict:
        return {"local": True}

    @app.get("/api/echo")
    def echo() -> dict:
        return {"local": True}  # 联机启用时这条应被代理走，不返回本地值

    mobile_remote.install(app)
    return app


def _status_app() -> FastAPI:
    """挂真路由（`/api/mobile/*`）+ 真代理中间件：验体检端点由本机应答、不被代理。"""
    app = FastAPI()
    app.include_router(mobile_remote_router.router, prefix="/api/mobile")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "local", "running_jobs": 0}

    mobile_remote.install(app)
    return app


@pytest.fixture()
def fake_pc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv(local_auth.TOKEN_ENV, PHONE_TOKEN)
    pc = FakePC()
    pc.start()
    try:
        yield {"pc": pc, "tmp": tmp_path}
    finally:
        pc.stop()


def test_配置保存与投影(fake_pc: dict):
    tmp = fake_pc["tmp"]
    saved = mobile_remote.save_config(
        base_url="http://192.0.2.10:8010/", token=PC_TOKEN, enabled=True)
    assert saved["base_url"] == "http://192.0.2.10:8010"
    public = mobile_remote.public_config()
    assert public["enabled"] and public["active"] and public["token_set"]
    assert "token" not in public and PC_TOKEN not in json.dumps(public)
    # 空 token = 保留现值
    saved = mobile_remote.save_config(
        base_url="http://192.0.2.10:8010", token="", enabled=True)
    assert saved["token"] == PC_TOKEN
    # 禁用保留地址与令牌，便于下次再连
    mobile_remote.save_config(
        base_url="http://192.0.2.10:8010", token="", enabled=False)
    cfg = mobile_remote.load_config()
    assert cfg["enabled"] is False and cfg["base_url"] == "http://192.0.2.10:8010"
    # 损坏文件 = 禁用态，不炸代理
    (tmp / mobile_remote.CONFIG_NAME).write_text("{", encoding="utf-8")
    assert mobile_remote.load_config()["enabled"] is False


@pytest.mark.parametrize("base_url,token,match", [
    ("http://127.0.0.1:8010", PC_TOKEN, "局域网地址"),
    ("http://localhost:8010", PC_TOKEN, "局域网地址"),
    ("http://192.0.2.10:8010/api", PC_TOKEN, "不要带路径"),
    ("ftp://192.0.2.10:8010", PC_TOKEN, "http"),
    ("", PC_TOKEN, "请填写电脑地址"),
    ("http://192.0.2.10:8010", "short", "至少 8 位"),
])
def test_启用时的地址与令牌校验(fake_pc: dict, base_url: str, token: str, match: str):
    with pytest.raises(mobile_remote.RemoteConfigError, match=match):
        mobile_remote.save_config(base_url=base_url, token=token, enabled=True)


def test_代理转发_附加PC令牌且剥离手机凭证(fake_pc: dict):
    pc = fake_pc["pc"]
    _write_config(fake_pc["tmp"], f"http://127.0.0.1:{pc.port}")
    client = TestClient(_app(), client=("127.0.0.1", 50000))
    response = client.get(
        "/api/echo?x=1",
        headers={"X-Demiurge-Token": PHONE_TOKEN, "Cookie": f"demiurge_token={PHONE_TOKEN}",
                 "Origin": "http://127.0.0.1:18010"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["path"] == "/api/echo?x=1"
    assert body["token"] == PC_TOKEN
    assert body["cookie"] == ""
    assert body["origin"] == ""


def test_本机端点与健康探针不走代理(fake_pc: dict):
    pc = fake_pc["pc"]
    _write_config(fake_pc["tmp"], f"http://127.0.0.1:{pc.port}")
    client = TestClient(_app(), client=("127.0.0.1", 50000))
    assert client.get("/api/health").json() == {"status": "local", "running_jobs": 0}
    assert client.get("/api/mobile/remote").json() == {"local": True}
    assert pc.requests == []


def test_手机令牌闸在最外层_未认证不触达PC(fake_pc: dict):
    pc = fake_pc["pc"]
    _write_config(fake_pc["tmp"], f"http://127.0.0.1:{pc.port}")
    app = _app()
    local_auth.install(app, PHONE_TOKEN)  # 后装 = 外层：手机令牌先于代理生效
    client = TestClient(app, client=("127.0.0.1", 50000))
    assert client.get("/api/echo").status_code == 401
    assert pc.requests == []
    assert client.get(
        "/api/echo", headers={"X-Demiurge-Token": PHONE_TOKEN}).status_code == 200
    assert len(pc.requests) == 1


def test_PC不可达返回502可读文案(fake_pc: dict):
    _write_config(fake_pc["tmp"], f"http://127.0.0.1:{_closed_port()}")
    client = TestClient(_app(), client=("127.0.0.1", 50000))
    response = client.get("/api/echo")
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "联机模式" in detail and "离线资源同步" in detail


def test_探活成功_令牌错误与不可达(fake_pc: dict):
    pc = fake_pc["pc"]
    ok = asyncio.run(mobile_remote.probe(
        base_url=f"http://127.0.0.1:{pc.port}", token=PC_TOKEN))
    assert ok["reachable"] is True and ok["health"]["status"] == "ok"
    assert isinstance(ok["latency_ms"], int)

    bad = asyncio.run(mobile_remote.probe(
        base_url=f"http://127.0.0.1:{pc.port}", token="wrong-token-123"))
    assert bad["reachable"] is False and "令牌" in bad["detail"]

    down = asyncio.run(mobile_remote.probe(
        base_url=f"http://127.0.0.1:{_closed_port()}", token=PC_TOKEN))
    assert down["reachable"] is False and "连不上电脑" in down["detail"]


def test_联机体检_未启用联机时不发任何请求(fake_pc: dict):
    pc = fake_pc["pc"]
    base = f"http://127.0.0.1:{pc.port}"
    _write_config(fake_pc["tmp"], base, enabled=False)
    out = asyncio.run(mobile_remote.online_status())
    assert out == {
        "active": False, "base_url": base, "reachable": False, "latency_ms": None,
        "comfyui_url": "", "comfyui_running": False, "detail": "",
    }
    assert pc.requests == []


def test_联机体检_电脑不可达时只报一个断点(fake_pc: dict):
    pc = fake_pc["pc"]
    _write_config(fake_pc["tmp"], f"http://127.0.0.1:{_closed_port()}")
    out = asyncio.run(mobile_remote.online_status())
    assert out["active"] is True and out["reachable"] is False
    assert "连不上电脑" in out["detail"]
    # 电脑都没应答，就不再追问它的 ComfyUI（免得一次故障报两个原因）
    assert out["comfyui_url"] == "" and out["comfyui_running"] is False
    assert pc.requests == []


def test_联机体检_按电脑存的地址探ComfyUI(fake_pc: dict):
    pc = fake_pc["pc"]
    pc.comfyui_url = "https://comfy.example.com"
    pc.comfy_running = False
    _write_config(fake_pc["tmp"], f"http://127.0.0.1:{pc.port}")
    down = asyncio.run(mobile_remote.online_status())
    assert down["reachable"] is True and down["comfyui_running"] is False
    assert down["comfyui_url"] == "https://comfy.example.com"
    assert "https://comfy.example.com" in down["detail"]
    # 探的是电脑自己存的那个地址，且由电脑侧执行探活（手机不重复一份白名单判据）
    assert any(path.startswith("/api/comfyui/status?url=https") for path in pc.paths())

    pc.comfy_running = True
    up = asyncio.run(mobile_remote.online_status())
    assert up["comfyui_running"] is True and up["detail"] == ""


def test_联机体检_电脑地址为空时如实报缺(fake_pc: dict):
    pc = fake_pc["pc"]
    pc.comfyui_url = ""
    _write_config(fake_pc["tmp"], f"http://127.0.0.1:{pc.port}")
    out = asyncio.run(mobile_remote.online_status())
    assert out["reachable"] is True and out["comfyui_running"] is False
    assert "没有填写 ComfyUI 地址" in out["detail"]


def test_联机体检_读不到电脑设置也不炸(fake_pc: dict):
    pc = fake_pc["pc"]
    pc.user_state_status = 500
    _write_config(fake_pc["tmp"], f"http://127.0.0.1:{pc.port}")
    out = asyncio.run(mobile_remote.online_status())
    assert out["reachable"] is True and out["comfyui_running"] is False
    assert "读不到电脑的设置" in out["detail"]


def test_联机体检端点由本机应答且不回传令牌(fake_pc: dict):
    pc = fake_pc["pc"]
    _write_config(fake_pc["tmp"], f"http://127.0.0.1:{pc.port}")
    app = _status_app()
    local_auth.install(app, PHONE_TOKEN)  # 后装 = 外层：手机令牌先于代理生效
    client = TestClient(app, client=("127.0.0.1", 50000))
    assert client.get("/api/mobile/online-status").status_code == 401
    response = client.get("/api/mobile/online-status",
                          headers={"X-Demiurge-Token": PHONE_TOKEN})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active"] is True and body["comfyui_running"] is True
    assert "/api/mobile/online-status" not in pc.paths()  # 永不被反向代理
    assert "token" not in body and PC_TOKEN not in json.dumps(body)


def test_WS中继带PC令牌(fake_pc: dict):
    ws_pc = FakePCWS()
    ws_pc.start()
    try:
        _write_config(fake_pc["tmp"], f"http://127.0.0.1:{ws_pc.port}")
        app = _app()
        local_auth.install(app, PHONE_TOKEN)
        client = TestClient(app, client=("127.0.0.1", 50000))
        with client.websocket_connect(
            "/api/comfyui/ws?clientId=c1",
            headers={"X-Demiurge-Token": PHONE_TOKEN},
        ) as ws:
            ws.send_text("hello")
            assert ws.receive_text() == "hello"
        assert ws_pc.paths and ws_pc.paths[0] == "/api/comfyui/ws?clientId=c1"
        assert ws_pc.tokens == [PC_TOKEN]
    finally:
        ws_pc.stop()


def test_代理路径判定():
    assert mobile_remote.should_proxy_path("/api/user-state")
    assert mobile_remote.should_proxy_path("/api/comfyui/local-view")
    assert mobile_remote.should_proxy_path("/api")
    assert not mobile_remote.should_proxy_path("/api/health")
    assert not mobile_remote.should_proxy_path("/api/mobile/remote")
    assert not mobile_remote.should_proxy_path("/api/mobile/comfy-host")
    assert not mobile_remote.should_proxy_path("/api/mobile/online-status")
    assert not mobile_remote.should_proxy_path("/docs-assets/x.png")
