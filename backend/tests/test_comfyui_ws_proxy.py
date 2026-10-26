"""ComfyUI 进度 WS 代理：真 socket 中继行为（上游用本地假 ComfyUI WS 服务）。

假上游服务用 websockets（仅测试依赖，requirements-dev 声明；生产代理走
websocket-client——手机 wheelhouse 102 pin 离线构建，不引入新运行依赖）。
安全断言优先于连通性：白名单外目标拒绝、令牌缺失拒绝（WS 不经过 http 中间件，
这条闸只在端点内）、令牌不外流由「代理出站不带头」的实现保证并抽查。
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest
import websockets

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.routers import comfyui as comfyui_router
from app.services.local_auth import COOKIE_NAME, HEADER_NAME


class FakeComfy:
    """最小 ComfyUI /ws：连接即按序下发预设帧，并记录对端路径与来帧。"""

    def __init__(self, frames: list[str | bytes] | None = None) -> None:
        self.frames = frames or []
        self.paths: list[str] = []
        self.received: list[str | bytes] = []
        self.port = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._conns: set[object] = set()

    def start(self) -> None:
        started = threading.Event()

        async def _serve() -> None:
            self._stop = asyncio.Event()
            self._loop = asyncio.get_running_loop()
            async with websockets.serve(self._handler, "127.0.0.1", 0) as server:
                self.port = server.sockets[0].getsockname()[1]
                started.set()
                assert self._stop is not None
                await self._stop.wait()

        self._thread = threading.Thread(target=lambda: asyncio.run(_serve()), daemon=True)
        self._thread.start()
        assert started.wait(5), "fake comfy ws server failed to start"

    def stop(self) -> None:
        # 先关干净活跃连接再停 loop，避免 websockets 内部清理任务撞上「loop is closed」
        if self._loop is not None and self._stop is not None:
            for conn in list(self._conns):
                try:
                    asyncio.run_coroutine_threadsafe(_close_conn(conn), self._loop).result(2)
                except Exception:  # noqa: BLE001 — 清理尽力而为
                    pass
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread is not None:
            self._thread.join(timeout=3)

    async def _handler(self, ws) -> None:  # noqa: ANN001 — websockets 协议对象，版本间类型不稳
        self._conns.add(ws)
        try:
            self.paths.append(str(ws.request.path))
            for frame in self.frames:
                await ws.send(frame)
            async for message in ws:
                self.received.append(message)
        finally:
            self._conns.discard(ws)


async def _close_conn(conn: object) -> None:
    closer = getattr(conn, "close", None)
    if closer is not None:
        await closer()


@pytest.fixture()
def client() -> TestClient:
    """回环对端：默认 TestClient 地址是 "testclient"（非回环），会被能力闸判成远端。"""
    app = FastAPI()
    app.include_router(comfyui_router.router, prefix="/api/comfyui")
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def remote_client() -> TestClient:
    """远端对端（L2 联机）：能力闸必须与 http 路径同判据，不能靠「不共享中间件」漏过。"""
    app = FastAPI()
    app.include_router(comfyui_router.router, prefix="/api/comfyui")
    return TestClient(app, client=("192.168.1.5", 51000))


def _closed_port() -> int:
    """拿一个确定没有监听的回环端口（bind 后立刻释放）。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_中继上游文本帧且clientId透传(client: TestClient) -> None:
    fake = FakeComfy(frames=['{"type":"status","data":{"sid":"c1"}}'])
    fake.start()
    try:
        with client.websocket_connect(
            f"/api/comfyui/ws?url=http://127.0.0.1:{fake.port}&clientId=c1"
        ) as ws:
            assert ws.receive_text() == '{"type":"status","data":{"sid":"c1"}}'
    finally:
        fake.stop()
    assert any("clientId=c1" in path for path in fake.paths), fake.paths


def test_中继上游二进制帧(client: TestClient) -> None:
    fake = FakeComfy(frames=[b"\x89PNG-previews-bytes"])
    fake.start()
    try:
        with client.websocket_connect(
            f"/api/comfyui/ws?url=http://127.0.0.1:{fake.port}"
        ) as ws:
            assert ws.receive_bytes() == b"\x89PNG-previews-bytes"
    finally:
        fake.stop()


def test_客户端到上游方向的帧也中继(client: TestClient) -> None:
    fake = FakeComfy()
    fake.start()
    try:
        with client.websocket_connect(
            f"/api/comfyui/ws?url=http://127.0.0.1:{fake.port}"
        ) as ws:
            ws.send_text("client-hello")
            deadline = time.time() + 5
            while not fake.received and time.time() < deadline:
                time.sleep(0.05)
            assert fake.received == ["client-hello"]
    finally:
        fake.stop()


def test_白名单外目标一律拒绝(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/comfyui/ws?url=http://evil.example:8188"):
            pass  # pragma: no cover — 到这里就是失败


def test_目标url解析_回环拼ws并带clientId_白名单外抛错() -> None:
    from app.services.comfyui_ws_proxy import _target_ws_url

    assert _target_ws_url("http://127.0.0.1:8188/", "c 1") == (
        "ws://127.0.0.1:8188/ws?clientId=c%201"
    )
    with pytest.raises(ValueError):
        _target_ws_url("http://evil.example:8188", "")


def test_上游建连失败时客户端以1011关闭(client: TestClient) -> None:
    port = _closed_port()
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/api/comfyui/ws?url=http://127.0.0.1:{port}") as ws:
            # 已 accept 的连接上，服务端关闭要在 receive 侧才能观察到
            ws.receive_text()
    assert exc.value.code == 1011


def test_令牌启用时无凭据拒绝_头或cookie放行(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAF_LOCAL_TOKEN", "t0k-test")
    fake = FakeComfy(frames=['{"type":"status"}'])
    fake.start()
    try:
        # 无凭据：拒绝（WS 不经过 http 中间件，这条闸只在端点内）
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/api/comfyui/ws?url=http://127.0.0.1:{fake.port}"
            ):
                pass
        # 头放行
        with client.websocket_connect(
            f"/api/comfyui/ws?url=http://127.0.0.1:{fake.port}",
            headers={HEADER_NAME: "t0k-test"},
        ) as ws:
            assert ws.receive_text() == '{"type":"status"}'
        # 同源 cookie 放行（浏览器 WebSocket 带不了自定义头，只能靠 cookie）
        with client.websocket_connect(
            f"/api/comfyui/ws?url=http://127.0.0.1:{fake.port}",
            headers={"Cookie": f"{COOKIE_NAME}=t0k-test"},
        ) as ws:
            assert ws.receive_text() == '{"type":"status"}'
        assert len(fake.paths) == 2, fake.paths
    finally:
        fake.stop()


# ── L2 联机：WS 升级不经过 http 中间件，能力闸必须在端点内自做 ──

def test_裸远端WS在建连上游前以4403关闭(
    remote_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """裸 LAF_ALLOW_REMOTE（未设令牌）的远端：三件套缺一即拒，且不拨号上游。"""
    monkeypatch.delenv("LAF_LOCAL_TOKEN", raising=False)
    fake = FakeComfy(frames=['{"type":"status"}'])
    fake.start()
    try:
        with pytest.raises(WebSocketDisconnect) as exc:
            with remote_client.websocket_connect(
                f"/api/comfyui/ws?url=http://127.0.0.1:{fake.port}"
            ):
                pass
        assert exc.value.code == 4403
        time.sleep(0.2)  # 给「错误地起了中继线程」留出暴露窗口
        assert fake.paths == [], fake.paths
    finally:
        fake.stop()


def test_远端令牌三件套齐备时WS放行(
    remote_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LAF_LOCAL_TOKEN", "t0k-l2")
    fake = FakeComfy(frames=['{"type":"status"}'])
    fake.start()
    try:
        with remote_client.websocket_connect(
            f"/api/comfyui/ws?url=http://127.0.0.1:{fake.port}",
            headers={HEADER_NAME: "t0k-l2"},
        ) as ws:
            assert ws.receive_text() == '{"type":"status"}'
        assert len(fake.paths) == 1, fake.paths
    finally:
        fake.stop()


def test_回环对端不受能力闸影响_裸ALLOW_REMOTE也放行(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """闸按「对端是否回环」二分：PC 本机行为逐字不变（PC 端不开 WS 代理同理）。"""
    monkeypatch.delenv("LAF_LOCAL_TOKEN", raising=False)
    monkeypatch.setenv("LAF_ALLOW_REMOTE", "1")
    fake = FakeComfy(frames=['{"type":"status"}'])
    fake.start()
    try:
        with client.websocket_connect(
            f"/api/comfyui/ws?url=http://127.0.0.1:{fake.port}"
        ) as ws:
            assert ws.receive_text() == '{"type":"status"}'
    finally:
        fake.stop()
