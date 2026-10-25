"""本机 API 令牌鉴权（单一属主）：只在移动端独立模式启用，桌面零变化。

威胁模型：Android 上任意应用都能连 `127.0.0.1:<port>`，回环不是信任边界。壳层生成
32 字节随机令牌存 app 私有 prefs，经环境变量交给本进程；浏览器侧用
HttpOnly + SameSite=Strict cookie（`<img>`/`<video>`/SSE 带不了自定义头），
本进程自取图与 Java 探测走 `X-Demiurge-Token` 头。

开关语义：`LAF_LOCAL_TOKEN` 未设置/空 ⇒ 不安装中间件，PC 行为与既有测试完全不变。
设置后除引导端点外的**所有**路径都要令牌，包括静态前端与 `/api/health`。

引导端点 `GET /__auth?token=…&next=/`：校验 query 令牌（WebView 首次导航带不了头，
也还没有 cookie），下发 cookie 后 303 回站内路径。`next` 只接受以单个 `/` 开头的站内
路径，`//host` 与绝对 URL 一律收敛到 `/`，避免开放重定向。
"""
from __future__ import annotations

import hmac
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

TOKEN_ENV = "LAF_LOCAL_TOKEN"
HEADER_NAME = "X-Demiurge-Token"
COOKIE_NAME = "demiurge_token"
BOOTSTRAP_PATH = "/__auth"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365

_UNAUTHORIZED_DETAIL = "缺少或错误的本机访问令牌"


def env_token() -> str:
    """当前进程的令牌；未启用时返回空串。"""
    return os.environ.get(TOKEN_ENV, "").strip()


def _matches(supplied: str, expected: str) -> bool:
    # 先转 bytes：compare_digest 对含非 ASCII 的 str 会抛 TypeError，任何本地应用都能
    # 发一个奇怪的头把中间件打成 500（拒绝服务面）。
    return hmac.compare_digest(supplied.encode("utf-8", "ignore"), expected.encode("utf-8"))


def outbound_headers() -> dict[str, str]:
    """本进程向自己发起 HTTP 时要带的头；未启用时为空字典（调用方可无脑透传）。"""
    token = env_token()
    return {HEADER_NAME: token} if token else {}


def _bootstrap(request: Request, expected: str) -> Response:
    supplied = request.query_params.get("token", "")
    if not _matches(supplied, expected):
        return JSONResponse(status_code=401, content={"detail": _UNAUTHORIZED_DETAIL})
    target = request.query_params.get("next", "/")
    if not target.startswith("/") or target.startswith("//"):
        target = "/"
    response = RedirectResponse(url=target, status_code=303)
    response.set_cookie(
        key=COOKIE_NAME,
        value=expected,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


def install(app: FastAPI, token: str) -> None:
    """给 app 装上令牌中间件与引导端点（token 必须非空；调用方负责判定开关）。"""

    @app.middleware("http")
    async def _require_local_token(request: Request, call_next):
        if request.url.path == BOOTSTRAP_PATH:
            return await call_next(request)
        supplied = request.headers.get(HEADER_NAME, "") or request.cookies.get(COOKIE_NAME, "")
        if _matches(supplied, token):
            return await call_next(request)
        return JSONResponse(status_code=401, content={"detail": _UNAUTHORIZED_DETAIL})

    async def _bootstrap_route(request: Request) -> Response:
        return _bootstrap(request, token)

    app.add_api_route(BOOTSTRAP_PATH, _bootstrap_route, methods=["GET"], include_in_schema=False)
    # app.main 在 import 期已把 StaticFiles 挂到 "/"，后注册的路由会被它吞掉；
    # 引导端点必须插到路由表最前（与 mobile_server 的探针路由同一坑）。
    routes = app.router.routes
    routes.insert(0, routes.pop())


def install_from_env(app: FastAPI) -> bool:
    """按环境变量决定是否安装；返回是否启用。"""
    token = env_token()
    if not token:
        return False
    install(app, token)
    return True
