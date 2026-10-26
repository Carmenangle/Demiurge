"""ComfyUI 进度 WebSocket 代理：把「浏览器直连 ComfyUI /ws」改为经本后端中继。

为什么存在（M7 手机端联机）：CSP `connect-src` 只放行回环 WS、Android 网安配置明文
仅回环且只约束 WebView 栈——前端直连 LAN PC 的 ComfyUI 进度流被双重阻断；后端
Python socket 不受两者约束。PC 端默认不走此路径（前端构建期开关
VITE_COMFY_PROGRESS_PROXY，PC 构建不设 ⇒ 直连行为逐字不变）。

安全合同：
- 目标 url 与全部 /api/comfyui 端点同口径过 url_guard.validate_comfyui_url
  （回环 + COMFYUI_ALLOWED_HOSTS 白名单）；代理绝不中继白名单外地址。
- WebSocket 连接不经过 local_auth 的 http 中间件——令牌校验在本端点内自做
  （X-Demiurge-Token 头或同源 cookie，与 http 路径等价）；未启用令牌时零校验。
- WS 升级同样不经过 main 的 http 中间件（回环门禁/能力闸都是 @app.middleware("http")），
  ⇒ 远程对端在这里自做 remote_gate 判定（与 main._remote_capability_gate 同判据）：
  裸 LAF_ALLOW_REMOTE（未设令牌）的远端一律 4403，且**在建连上游之前**就关掉。
- 出站连接绝不携带本机令牌（outbound_headers 只用于回环自取图；令牌不得流向
  ComfyUI 的访问面）。

生命周期：一条客户端连接 ⇋ 一条上游连接。上游断开/建连失败 ⇒ 关闭客户端连接，
**不在代理内重连**（单次重连是前端 subscribeProgress 的既有职责，完成判定另有
/history 轮询兜底）；空闲 600 s 双向无帧则关闭。上游连接由专用线程持有
（websocket-client 是同步实现，不能在事件循环线程里阻塞 IO），帧经
loop.call_soon_threadsafe 推回事件循环。
"""
from __future__ import annotations

import asyncio
import re
import threading
from urllib.parse import quote

import websocket as ws_client  # pip 包名 websocket-client；手机 wheelhouse 已 pin

from fastapi import WebSocket

from app.config import COMFYUI_BASE_URL
from app.services import local_auth, remote_gate
from app.services.url_guard import validate_comfyui_url

UPSTREAM_CONNECT_TIMEOUT = 5.0
UPSTREAM_RECV_POLL = 1.0
IDLE_TIMEOUT = 600.0

_RELAY_PATH = "/api/comfyui/ws"  # remote_gate 判定用的路由字面量（非 DENY/SCOPE 类）

_CLOSE_UNAUTHORIZED = 4401  # 令牌缺失/错误（http 侧对应 401）
_CLOSE_FORBIDDEN_TARGET = 4403  # 目标地址不在白名单 / 远程对端未过能力闸（http 侧 403）
_CLOSE_UPSTREAM = 1011  # 上游建连失败或异常断开


def _target_ws_url(comfy_url: str, client_id: str) -> str:
    """http(s) ComfyUI 基址 → ws(s) /ws 端点；白名单外地址抛 ValueError。"""
    base = validate_comfyui_url(comfy_url).rstrip("/")
    target = re.sub(r"^http", "ws", base) + "/ws"
    if client_id:
        target += f"?clientId={quote(client_id, safe='')}"
    return target


async def relay(ws: WebSocket, *, url: str = "", client_id: str = "") -> None:
    """把一条客户端 WS 连接中继到 ComfyUI /ws；路由层保持零逻辑。"""
    if not local_auth.verify_supplied(
        ws.headers.get(local_auth.HEADER_NAME, "")
        or ws.cookies.get(local_auth.COOKIE_NAME, "")
    ):
        await ws.close(code=_CLOSE_UNAUTHORIZED)
        return
    if remote_gate.is_remote(ws.client.host if ws.client else ""):
        detail = remote_gate.verdict(
            _RELAY_PATH, None, token_enabled=bool(local_auth.env_token()))
        if detail is not None:
            await ws.close(code=_CLOSE_FORBIDDEN_TARGET)
            return
    try:
        target = _target_ws_url(url or COMFYUI_BASE_URL, client_id)
    except ValueError:
        await ws.close(code=_CLOSE_FORBIDDEN_TARGET)
        return
    await ws.accept()

    loop = asyncio.get_running_loop()
    frames: asyncio.Queue[str | bytes | None] = asyncio.Queue()
    upstream_ready = asyncio.Event()  # 上游建连完成即置位（失败则 None 帧先行，主循环收尾）
    stop = threading.Event()
    upstream: dict[str, ws_client.WebSocket] = {}  # 线程侧建连后回填；事件循环侧只读

    def _offer(item: str | bytes | None) -> None:
        try:
            loop.call_soon_threadsafe(frames.put_nowait, item)
        except RuntimeError:  # noqa: BLE001 — 事件循环已随连接收尾关闭（测试环境常见）
            pass

    def _pump_upstream() -> None:
        try:
            conn = ws_client.create_connection(
                target, timeout=UPSTREAM_CONNECT_TIMEOUT, enable_multithread=True)
        except Exception:  # noqa: BLE001 — 上游不可达：整条中继结束，细节不上报客户端
            _offer(None)
            return
        upstream["conn"] = conn
        loop.call_soon_threadsafe(upstream_ready.set)
        conn.settimeout(UPSTREAM_RECV_POLL)
        while not stop.is_set():
            try:
                _offer(conn.recv())
            except ws_client.WebSocketTimeoutException:
                continue
            except Exception:  # noqa: BLE001 — 上游断开/协议错：与建连失败同语义
                break
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        _offer(None)

    threading.Thread(target=_pump_upstream, daemon=True, name="comfy-ws-proxy-upstream").start()

    async def _client_to_upstream() -> None:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            conn = upstream.get("conn")
            if conn is None:
                # 上游尚未建连完成：等建连后**重发当前帧**（continue 会把它丢掉）。
                # 失败路径由 None 帧结束下游任务并连带取消本任务。
                await upstream_ready.wait()
                conn = upstream.get("conn")
                if conn is None:
                    return
            payload = msg.get("text") if "text" in msg else msg.get("bytes")
            if payload is None:
                continue
            await loop.run_in_executor(None, conn.send, payload)

    async def _upstream_to_client() -> None:
        while True:
            try:
                frame = await asyncio.wait_for(frames.get(), timeout=IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                return  # 空闲超时：由 finally 统一关闭
            if frame is None:
                await ws.close(code=_CLOSE_UPSTREAM)
                return
            if isinstance(frame, str):
                await ws.send_text(frame)
            else:
                await ws.send_bytes(frame)

    # 双向泵并发：任一侧结束（客户端断开 / 上游断开 / 空闲超时）即整条收尾，
    # 否则客户端先走时上游连接会被白握到空闲超时。
    pump_task = asyncio.create_task(_client_to_upstream())
    downstream_task = asyncio.create_task(_upstream_to_client())
    try:
        await asyncio.wait({pump_task, downstream_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop.set()
        for task in (pump_task, downstream_task):
            task.cancel()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001 — 客户端已先行断开时 close 会抛
            pass
