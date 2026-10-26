"""手机端联机同库：本机后端 → PC 后端的反向代理（0.30.0，2026-10-26）。

形态：WebView 永远只加载手机本机后端（127.0.0.1:PORT）。启用「联机同库」后，本机
后端把 `/api/*`（除 `/api/mobile/*` 与 `/api/health`）反向代理到同一 Wi‑Fi 下的 PC
后端，并附加 PC 的 `LAF_LOCAL_TOKEN`。这样：

- **数据真源在 PC**：`/api/user-state` 也走代理，手机看到的是 PC 的设置/作品/会话；
  断开联机即回到手机本机数据（本机 user_state 未被改写）。
- **本机令牌不被绕过**：代理中间件装在 `local_auth` **内侧**，手机令牌仍由最外层
  中间件强制；出站请求剥掉手机 cookie/令牌，只带 PC 令牌。
- **PC 的能力闸照常生效**：PC 端 `remote_gate` 按 LAN 对端收敛端点（start/stop/
  models/config/… 一律 403），本模块不另做一份白名单。

已知边界（登记，不假装支持）：
- PC 的 ComfyUI 画布/预览 iframe 仍指向 PC 的 `comfyuiUrl`（多为 PC 本机回环的
  ComfyUI 端口），手机浏览器里的回环地址是手机自己 ⇒ 联机模式下画布不可用；生成请求本身仍由
  PC 后端执行。进度 WS 经本模块中继（手机前端 `VITE_COMFY_PROGRESS_PROXY=1`）。
- 联机配置是**设备本地**的（`data/remote_backend.json`），不随 PC 配置包同步。
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging

from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import websockets
from fastapi import FastAPI
from starlette.websockets import WebSocket

from app import config
from app.services import local_auth

_LOG = logging.getLogger("uvicorn.error")

CONFIG_NAME = "remote_backend.json"
PC_TOKEN_HEADER = local_auth.HEADER_NAME
LOCAL_TOKEN_COOKIE = local_auth.COOKIE_NAME

# 这些路径永远由手机本机处理：联机配置自身、健康探测（壳层暂停/保活判据）。
_EXCLUDE_PREFIXES = ("/api/mobile",)
_EXCLUDE_EXACT = ("/api/health",)

_WS_RELAY_PATH = "/api/comfyui/ws"  # 手机前端唯一使用的 WS（进度中继）

_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "trailers", "transfer-encoding", "upgrade",
})
_REQUEST_DROP = _HOP_BY_HOP | frozenset({
    "host", "cookie", "x-demiurge-token", "origin", "referer",
    "content-length", "accept-encoding",
})
_RESPONSE_DROP = _HOP_BY_HOP | frozenset({"set-cookie"})

_CONNECT_TIMEOUT = 8.0
_READ_TIMEOUT = 300.0


class RemoteConfigError(ValueError):
    """用户可见的联机配置错误（路由层转 400）。"""


def _config_path() -> Path:
    return Path(config.DATA_DIR) / CONFIG_NAME


def load_config() -> dict:
    """读联机配置。缺失/损坏/非对象一律返回禁用态（代理据此走本机路由）。"""
    disabled = {"enabled": False, "base_url": "", "token": ""}
    path = _config_path()
    if not path.is_file():
        return disabled
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return disabled
    if not isinstance(raw, dict):
        return disabled
    return {
        "enabled": bool(raw.get("enabled")),
        "base_url": str(raw.get("base_url") or "").strip().rstrip("/"),
        "token": str(raw.get("token") or "").strip(),
    }


def is_active(cfg: dict) -> bool:
    return bool(cfg.get("enabled") and cfg.get("base_url") and cfg.get("token"))


def normalize_base_url(raw: str) -> str:
    """校验并规范化 PC 地址：http(s)、无路径/参数/账号，且不能是回环（否则代理打回自己）。"""
    value = (raw or "").strip().rstrip("/")
    if not value:
        raise RemoteConfigError("请填写电脑地址（例如 http://192.168.1.6:<端口>）")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise RemoteConfigError("电脑地址必须以 http:// 或 https:// 开头")
    if not parsed.hostname:
        raise RemoteConfigError("电脑地址里没有主机名")
    if parsed.username or parsed.password:
        raise RemoteConfigError("电脑地址不要带账号密码")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise RemoteConfigError("电脑地址只填到端口，不要带路径或参数")
    host = parsed.hostname
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host in ("localhost",)
    if loopback:
        raise RemoteConfigError("请填写电脑的局域网地址（127.0.0.1 在手机上指向手机自己）")
    return f"{parsed.scheme}://{parsed.netloc}"


def save_config(*, base_url: str, token: str, enabled: bool) -> dict:
    """原子写联机配置；token 传空串表示保留现值（不提供「清空令牌」语义，禁用即断开）。"""
    current = load_config()
    token = (token or "").strip() or current["token"]
    if enabled:
        base_url = normalize_base_url(base_url)
        if len(token) < 8:
            raise RemoteConfigError("访问令牌不正确（至少 8 位；以 PC 启动脚本打印的为准）")
    else:
        base_url = (base_url or current["base_url"]).strip().rstrip("/")
        if base_url:
            base_url = normalize_base_url(base_url)
    payload = {"enabled": bool(enabled), "base_url": base_url, "token": token}
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return payload


def public_config(cfg: dict | None = None) -> dict:
    """给前端的状态投影：绝不返回令牌本体。"""
    cfg = cfg or load_config()
    return {
        "supported": True,
        "enabled": bool(cfg.get("enabled")),
        "active": is_active(cfg),
        "base_url": cfg.get("base_url", ""),
        "token_set": bool(cfg.get("token")),
    }


async def probe(*, base_url: str, token: str) -> dict:
    """探 PC 的 /api/health：返回 {reachable, detail, latency_ms, health}。"""
    import time

    started = time.perf_counter()
    url = f"{base_url.rstrip('/')}/api/health"
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=4.0), trust_env=False,
        ) as client:
            resp = await client.get(url, headers={PC_TOKEN_HEADER: token})
    except httpx.HTTPError as exc:
        return {
            "reachable": False,
            "detail": _connect_detail(exc),
            "latency_ms": None,
            "health": None,
        }
    latency = round((time.perf_counter() - started) * 1000)
    if resp.status_code == 401:
        return {"reachable": False, "detail": "电脑端令牌不正确",
                "latency_ms": latency, "health": None}
    if resp.status_code == 403:
        return {"reachable": False, "detail": _detail_of(resp) or "电脑端拒绝了此连接",
                "latency_ms": latency, "health": None}
    if resp.status_code != 200:
        return {"reachable": False, "detail": f"电脑返回 HTTP {resp.status_code}",
                "latency_ms": latency, "health": None}
    try:
        health = resp.json()
    except ValueError:
        health = None
    return {"reachable": True, "detail": "", "latency_ms": latency, "health": health}


def _detail_of(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return ""
    return str(data.get("detail") or "") if isinstance(data, dict) else ""


def _connect_detail(exc: Exception) -> str:
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ConnectError)):
        return (
            "连不上电脑：确认同一 Wi‑Fi、地址/端口正确、PC 已用 start-lan 脚本启动，"
            "并放行防火墙"
        )
    if isinstance(exc, httpx.ReadTimeout):
        return "电脑响应超时"
    return f"连接电脑失败（{exc.__class__.__name__}）"


def should_proxy_path(path: str) -> bool:
    if path in _EXCLUDE_EXACT:
        return False
    if any(path == prefix or path.startswith(prefix + "/") for prefix in _EXCLUDE_PREFIXES):
        return False
    return path == "/api" or path.startswith("/api/")


class RemoteProxyMiddleware:
    """HTTP/WS 双通道反向代理；未启用联机时逐字透传（PC 与手机离线态零变化）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            cfg = load_config()
            if is_active(cfg) and should_proxy_path(scope.get("path", "")):
                await self._http(scope, receive, send, cfg)
                return
        elif scope["type"] == "websocket":
            cfg = load_config()
            if is_active(cfg) and scope.get("path") == _WS_RELAY_PATH:
                await self._ws(scope, receive, send, cfg)
                return
        await self.app(scope, receive, send)

    async def _http(self, scope, receive, send, cfg: dict) -> None:
        method = scope.get("method", "GET")
        path = scope.get("path", "")
        target = cfg["base_url"] + path
        query = scope.get("query_string") or b""
        if query:
            target += "?" + query.decode("latin-1")

        headers = {
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in scope.get("headers", [])
            if key.decode("latin-1").lower() not in _REQUEST_DROP
        }
        headers[PC_TOKEN_HEADER] = cfg["token"]
        body = await _read_body(receive)

        timeout = httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False,
                                     trust_env=False) as client:
            request = client.build_request(method, target, headers=headers, content=body or None)
            try:
                response = await client.send(request, stream=True)
            except httpx.HTTPError as exc:
                await _send_json(send, 502, _proxy_error(cfg, exc))
                return
            try:
                await send({
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": _response_headers(response),
                })
                async for chunk in response.aiter_raw():
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            finally:
                await response.aclose()

    async def _ws(self, scope, receive, send, cfg: dict) -> None:
        ws = WebSocket(scope, receive, send)
        supplied = (
            ws.headers.get(local_auth.HEADER_NAME, "")
            or ws.cookies.get(LOCAL_TOKEN_COOKIE, "")
        )
        if not local_auth.verify_supplied(supplied):
            await ws.close(code=4401)
            return
        await ws.accept()

        query = (scope.get("query_string") or b"").decode("latin-1")
        upstream_url = ws_base_url(cfg["base_url"]) + _WS_RELAY_PATH
        if query:
            upstream_url += "?" + query
        try:
            async with websockets.connect(
                upstream_url,
                additional_headers={PC_TOKEN_HEADER: cfg["token"]},
                open_timeout=_CONNECT_TIMEOUT,
                ping_interval=20,
                ping_timeout=20,
                max_size=None,
                proxy=None,
            ) as upstream:
                await _relay_ws(ws, upstream)
        except Exception as exc:  # noqa: BLE001 — 上游不可达/协议错：收尾关闭，细节只进日志
            _LOG.info("mobile remote ws upstream failed: %s", exc)
            try:
                await ws.close(code=1011)
            except Exception:  # noqa: BLE001 — 客户端可能已断开
                pass


def ws_base_url(base_url: str) -> str:
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://"):]
    return "ws://" + base_url[len("http://"):]


async def _relay_ws(ws: WebSocket, upstream) -> None:
    async def client_to_upstream() -> None:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            if "text" in message:
                await upstream.send(message["text"])
            elif "bytes" in message:
                await upstream.send(message["bytes"])

    async def upstream_to_client() -> None:
        async for message in upstream:
            if isinstance(message, str):
                await ws.send_text(message)
            else:
                await ws.send_bytes(message)

    tasks = [
        asyncio.create_task(client_to_upstream(), name="mobile-remote-ws-down"),
        asyncio.create_task(upstream_to_client(), name="mobile-remote-ws-up"),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001 — 客户端已断开
            pass


async def _read_body(receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.request":
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        elif message["type"] == "http.disconnect":
            break
    return b"".join(chunks)


def _response_headers(response: httpx.Response) -> list[tuple[bytes, bytes]]:
    return [
        (key.encode("latin-1"), value.encode("latin-1"))
        for key, value in response.headers.multi_items()
        if key.lower() not in _RESPONSE_DROP
    ]


def _proxy_error(cfg: dict, exc: Exception) -> str:
    return (
        f"联机模式：{_connect_detail(exc)}。"
        f"（目标 {cfg['base_url']}；可在「设置 → 离线资源同步」断开联机回到本机数据）"
    )


async def _send_json(send, status: int, detail: str) -> None:
    payload = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(payload)).encode("ascii")),
        ],
    })
    await send({"type": "http.response.body", "body": payload, "more_body": False})


def install(app: FastAPI) -> None:
    """装到 local_auth 内侧（调用点必须在 install_from_env 之前，保证令牌闸最外层）。"""
    app.add_middleware(RemoteProxyMiddleware)
