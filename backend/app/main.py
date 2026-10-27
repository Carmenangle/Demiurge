import asyncio
import logging
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from app.asyncio_quiet import install_connection_reset_filter
from app.db import init_db
from app.services import comfy_launcher
from app.services import comfy_watchdog
from app.services import local_auth, remote_gate
from app.services import request_guard
from app.services.workflow_build_tasks import start_worker as start_workflow_build_worker
from app.services.chat_agent_queue import start_worker as start_chat_agent_queue_worker
from app.services.plan_tasks import start_worker as start_plan_task_worker
from app.routers import ai, ai_providers, agents, artifacts, assets, attachments, characters, comfyui, docs, gif_sprite, gguf_importer, image_resize, jobs, loras, mcp, models, narrative, node_manager, palette, plans, preset, procedures, rag, regex, runs, scenario, skills, state, table, user_state, visual_ci, workflows, worldbook, activity, transfer, config_transfer

_LOG = logging.getLogger("uvicorn.error")


# 后端进程可能跑在受限上下文（低完整性/沙箱），它拉起的 ComfyUI 写不了安装树：
# AlekPet 的 `.install.lock` 会把冷启动拖到 600 秒。此类环境由 start-dev 在用户身份下
# 先拉起 ComfyUI（scripts/start_comfy.py），并设 LAF_COMFY_AUTOSTART=0 关掉这里的钩子。
_COMFY_AUTOSTART = os.environ.get("LAF_COMFY_AUTOSTART", "1") != "0"


def _autostart_comfyui() -> None:
    """autostart 线程体：按已保存配置拉起 ComfyUI；成败都登记到看门狗（失败要在面板可见）。

    此前 autostart() 的返回值被线程直接丢弃 ⇒ 启动失败时用户只看到「生图没反应」，
    原因（路径没配/启动失败）无从查起（2026-10-23 架构评审运行时稳健性短板 ②）。
    """
    result = comfy_launcher.autostart()
    if result.get("started"):
        comfy_watchdog.note_owned()
        return
    reason = str(result.get("reason") or "未知原因")
    _LOG.warning("ComfyUI 自动启动失败：%s", reason)
    comfy_watchdog.note_autostart_failed(reason)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """运行期唯一装配点（2026-10-24 卫生刀）：持久队列 worker 与看护设施只在 uvicorn
    起服务时拉起，import 本模块零副作用——runtime_entry 自检与测试进程都因此安全。

    - 三个队列 worker 无停止 API，测试进程由 tests/conftest.py 的「app.main 测试导入闸」
      把 start_worker 换成 no-op；lifespan 在测试里即使被触发也拉不起真 worker。
    - ComfyUI 看门狗：托管进程死亡后按已存配置自动重启（防重启风暴 + 面板留痕）；
      未配置/外部实例时全程 no-op。
    """
    start_workflow_build_worker()
    start_chat_agent_queue_worker()
    start_plan_task_worker()
    if _COMFY_AUTOSTART:
        threading.Thread(target=_autostart_comfyui, name="comfy-autostart", daemon=True).start()
    comfy_watchdog.start_watchdog()
    install_connection_reset_filter(asyncio.get_running_loop())
    yield


app = FastAPI(title="Local AI ComfyUI Frontend API", lifespan=_lifespan)
init_db()

# 默认只服务本机：这是单机桌面壳，含无鉴权的进程控制/文件读写接口（start/stop/interrupt/
# save-local/local-view 等）。CORS 只挡浏览器跨源，挡不住非浏览器客户端；故加回环门禁作纵深防御。
# 确需局域网访问时，设环境变量 LAF_ALLOW_REMOTE=1 放开（自担风险）。
# 对端 IP 之外还有两道闸（Origin/Host，属主 request_guard）：本机浏览器发起的跨站请求对端
# 同样是回环——form 编码 CSRF 能打到全部扁平模型 POST 端点（含 install-tracked 的
# clone+pip），DNS rebinding 能读走含密钥的 GET。判定语义与残留边界见该模块 docstring 与
# docs/security-hardening-origin-guard-and-svg-2026-10-24.md。
_ALLOW_REMOTE = os.environ.get("LAF_ALLOW_REMOTE", "") == "1"


@app.middleware("http")
async def _loopback_only(request: Request, call_next):
    verdict = request_guard.inbound_verdict(
        client_host=request.client.host if request.client else "",
        origin=request.headers.get("origin", ""),
        host_header=request.headers.get("host", ""),
        allow_remote=_ALLOW_REMOTE,
    )
    if verdict is not None:
        return JSONResponse(status_code=403, content={"detail": verdict})
    return await call_next(request)


# L2 联机同库：远程客户端能力闸（2026-10-26）。LAF_ALLOW_REMOTE 只放开可达性；
# 远端（非回环对端）还要过 remote_gate 的路由类表与「裸远端无令牌一律拒」互锁。
# PC 本机（回环对端）零变化。
@app.middleware("http")
async def _remote_capability_gate(request: Request, call_next):
    if _ALLOW_REMOTE and request.client:
        host = request.client.host or ""
        if remote_gate.is_remote(host):
            detail = remote_gate.verdict(
                request.url.path,
                request.query_params.get("path") if request.url.path == remote_gate.SCOPED_READ else None,
                token_enabled=bool(local_auth.env_token()),
            )
            if detail is not None:
                return JSONResponse(status_code=403, content={"detail": detail})
    return await call_next(request)


# 应用页 CSP（2026-10-24 XSS→密钥链第三环）：壳的 webview（location.replace 到本端口）与
# 浏览器直连的页面**都由本后端送达**——tauri.conf 的 csp 罩不到远程 http 页面，故 CSP 的
# 单一属主在这里，只挂 text/html 文档响应（API JSON 与媒体流不受影响）。
# 指令依据：script-src 'unsafe-inline' 是 scene 播放器 blob: 文档内联脚本所必需（vendor
# 文件不可改、blob 文档继承宿主 CSP；remote script 仍被 'self' 拦死、eval 不放行）；img/
# media 全同源（local-view 即本端口）加 data:/blob: 即可；ws 仅回环＝ComfyUI 进度 WS 直连
# （LAN ComfyUI 失去进度条为登记取舍）；object/base-uri/form-action/frame-ancestors 为
# 常规加固。注意壳侧注入脚本走 WebView2 ExecuteScript，不受页面 CSP 约束。
# frame-src 必须含回环 http：前端 ComfyUI 画布（ChatView/WorkflowCard/AIBuildView 等）
# 直接嵌 settings.comfyuiUrl（默认回环，端口真源 config.COMFYUI_DEFAULT_PORT）。曾只写
# blob: ⇒ 真机实测同源 http iframe 被拦成不透明 blocked page，画布全挂。LAN ComfyUI 的
# 画布嵌入同样被拦，与 connect-src 的 WS 取舍同口径登记。
_DOCUMENT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; "
    "connect-src 'self' blob: ws://127.0.0.1:* ws://localhost:* ws://[::1]:*; "
    "frame-src blob: http://127.0.0.1:* http://localhost:* http://[::1]:*; "
    "worker-src 'self' blob:; "
    "font-src 'self' data: blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


@app.middleware("http")
async def _csp_document_header(request: Request, call_next):
    response = await call_next(request)
    if (response.headers.get("content-type") or "").startswith("text/html"):
        response.headers.setdefault("Content-Security-Policy", _DOCUMENT_CSP)
    return response


# 出口媒体 URL 归一（治存量）：历史快照/消息正文/RAG 记录里写死了带本机 origin 的取图
# 地址，不迁移存档（用户定案「读时归一」），在发往客户端前收敛成根相对。
# 发射侧已由 view_urls 收口成新形态，这里只兜旧数据。
# 只碰 application/json：`text/event-stream` 由 sse.sse_response 逐帧归一（流式响应在此
# 整体读回会把增量下发缓冲成一次性），图片/文件流没有 JSON 体可改。
@app.middleware("http")
async def _normalize_media_urls(request: Request, call_next):
    # 本地 import：顶部 import 块会被并行改动整块重写（2026-10-24 实锤：加在顶部的
    # `view_urls`/`Response` 两行被冲掉，中间件运行期 NameError，连带打落下游闸测试）。
    from fastapi.responses import Response

    from app.services import view_urls

    response = await call_next(request)
    content_type = response.headers.get("content-type") or ""
    if "application/json" not in content_type:
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    normalized = view_urls.normalize_wire_bytes(body)
    # 一律重建响应：call_next 给的是 _StreamingResponse，body_iterator 只支持 async 迭代，
    # 原地塞回同步迭代器会让下发炸在 'async for' 上。content-length 交给 Response 重算
    # （字节数会变），背景任务手工带过去。
    headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
    out = Response(content=normalized, status_code=response.status_code,
                   headers=headers, media_type=content_type)
    out.background = response.background
    return out


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workflows.router, prefix="/api/workflows", tags=["workflows"])
app.include_router(runs.router, prefix="/api/runs", tags=["runs"])
app.include_router(plans.router, prefix="/api/plans", tags=["plans"])
app.include_router(ai.router, prefix="/api/ai", tags=["ai"])
app.include_router(ai_providers.router, prefix="/api/ai/providers", tags=["ai-providers"])
app.include_router(rag.router, prefix="/api/rag", tags=["rag"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(activity.router, prefix="/api/activity", tags=["activity"])
app.include_router(assets.router, prefix="/api/assets", tags=["assets"])
app.include_router(characters.router, prefix="/api/characters", tags=["characters"])
app.include_router(worldbook.router, prefix="/api/worldbook", tags=["worldbook"])
app.include_router(regex.router, prefix="/api/regex", tags=["regex"])
app.include_router(state.router, prefix="/api/state", tags=["state"])
app.include_router(narrative.router, prefix="/api/narrative", tags=["narrative"])
app.include_router(table.router, prefix="/api/tables", tags=["tables"])
app.include_router(preset.router, prefix="/api/preset", tags=["preset"])
app.include_router(loras.router, prefix="/api/loras", tags=["loras"])
app.include_router(gif_sprite.router, prefix="/api/gif-sprite", tags=["gif-sprite"])
app.include_router(palette.router, prefix="/api/palette", tags=["palette"])
app.include_router(image_resize.router, prefix="/api/image-resize", tags=["image-resize"])
app.include_router(comfyui.router, prefix="/api/comfyui", tags=["comfyui"])
app.include_router(models.router, prefix="/api/models", tags=["models"])
app.include_router(node_manager.router, prefix="/api/node-manager", tags=["node-manager"])
app.include_router(user_state.router, prefix="/api/user-state", tags=["user-state"])
app.include_router(attachments.router, prefix="/api/attachments", tags=["attachments"])
app.include_router(gguf_importer.router, prefix="/api/gguf", tags=["gguf-importer"])
app.include_router(visual_ci.router, prefix="/api/visual-ci", tags=["visual-ci"])
app.include_router(mcp.router, prefix="/api/mcp", tags=["mcp"])
app.include_router(skills.router, prefix="/api/skills", tags=["skills"])
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(scenario.router, prefix="/api/scenario", tags=["scenario"])
app.include_router(procedures.router, prefix="/api/procedures", tags=["procedures"])
app.include_router(docs.router, prefix="/api/docs", tags=["docs"])
app.include_router(artifacts.router, prefix="/api/artifacts", tags=["artifacts"])
app.include_router(transfer.router, prefix="/api/transfer", tags=["transfer"])
app.include_router(config_transfer.router, prefix="/api/config", tags=["config"])

# 手机独立模式（壳层设 LAF_MOBILE_REMOTE=1）：联机同库配置端点 + 反向代理。
# PC 后端不设该变量 ⇒ 不注册、不装中间件，行为与既有完全一致。
_MOBILE_REMOTE = os.environ.get("LAF_MOBILE_REMOTE", "") == "1"
if _MOBILE_REMOTE:
    from app.routers import mobile_remote as mobile_remote_api

    app.include_router(mobile_remote_api.router, prefix="/api/mobile", tags=["mobile"])


@app.get("/api/health")
def health() -> dict[str, object]:
    # running_jobs＝在途 ComfyUI 生成数（comfy_pending 是服务端真源：提交登记、终态
    # 删除、30 分钟 TTL 兜底）。手机壳层用它在「退后台 30s 暂停」与「前台服务保活」
    # 之间做闸（PLAN §7）；PC 诊断面同样可读。
    from app.services import comfy_pending

    return {"status": "ok", "running_jobs": len(comfy_pending.snapshot())}


# 新人引导「独立文档页」正文里的相对图片（如 ../assets/guide/x.png）指向仓库 docs/assets，
# 挂成静态目录供文档正文引用。必须在前端 dist 的 "/" 挂载之前注册，否则被它吞掉。
_DOCS_ASSETS = Path(__file__).resolve().parents[2] / "docs" / "assets"
if _DOCS_ASSETS.is_dir():
    app.mount("/docs-assets", StaticFiles(directory=_DOCS_ASSETS), name="docs-assets")

_FRONTEND_DIST = Path(os.environ.get("LAF_FRONTEND_DIST", "")).expanduser()


# 前端 dist 的缓存口径只有一个属主（2026-10-27 手机降级卡那轮补上）：`StaticFiles` 默认不发
# `Cache-Control` ⇒ 浏览器走启发式新鲜度，理论上能把换包前的 `index.html` 复用很久，而它引用的
# 旧 chunk 名已不在磁盘上 ⇒ 动态导入失败、页面掉进降级卡。文档禁缓存是这条链的根闸。
# 🔴 但这条链**不是**用户那张截图已证的归因：真机 0.30.8→0.30.13 真实换包**没有**出卡
# （见 `ARCHITECTURE.md` 条 73 ④）；这里按「正确性 + 纵深防御」保留，PC 与手机共用同一条路。
# assets/* 的文件名带内容 hash，永不冲突 ⇒ 给满一年 immutable。
def _static_cache_control(path: str) -> str:
    # StaticFiles 递进来的是 OS 归一后的相对路径：根是 "."，Windows 分隔符是 "\"，
    # ".."/"." 组件已被抹掉——口径按首段判，与平台无关。
    head = path.replace("\\", "/").strip("/").partition("/")[0]
    if not head or head == "." or head.endswith(".html"):
        return "no-store"
    if head == "assets":
        return "public, max-age=31536000, immutable"
    # 其余是 dist 根下的非哈希媒体（onboarding/textures/…，合起来约 100 MB）：
    # 文件名不换内容换，缓存要有限期；StaticFiles 自带 ETag，过期走 304 不重传字节。
    return "public, max-age=3600"


class _DistStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = _static_cache_control(path)
        return response


if str(_FRONTEND_DIST) not in {"", "."} and (_FRONTEND_DIST / "index.html").is_file():
    app.mount("/", _DistStaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")

# 联机同库反向代理必须装在 local_auth 内侧（add_middleware 后加者在外层）：
# 手机令牌闸始终最外层，代理只负责出站，绝不让未认证的本机调用借道打到 PC。
if _MOBILE_REMOTE:
    from app.services import mobile_remote

    mobile_remote.install(app)

# 移动端独立模式：壳层设了 LAF_LOCAL_TOKEN 才安装（未设为 no-op，PC 行为不变）。
# 放在静态挂载之后：install() 会把引导端点插到路由表最前，挂载不会吞掉它。
local_auth.install_from_env(app)
