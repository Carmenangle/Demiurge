import ipaddress
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles


import threading

from app.db import init_db
from app.services import comfy_launcher
from app.services import comfy_watchdog
from app.services.workflow_build_tasks import start_worker as start_workflow_build_worker
from app.services.chat_agent_queue import start_worker as start_chat_agent_queue_worker
from app.services.plan_tasks import start_worker as start_plan_task_worker
from app.routers import ai, ai_providers, agents, artifacts, assets, attachments, characters, comfyui, docs, gif_sprite, gguf_importer, image_resize, jobs, loras, mcp, models, narrative, node_manager, palette, plans, preset, procedures, rag, regex, runs, scenario, skills, state, table, user_state, visual_ci, workflows, worldbook, activity

_LOG = logging.getLogger("uvicorn.error")

app = FastAPI(title="Local AI ComfyUI Frontend API")
init_db()
start_workflow_build_worker()
start_chat_agent_queue_worker()
start_plan_task_worker()


# 后端进程可能跑在受限上下文（低完整性/沙箱），它拉起的 ComfyUI 写不了安装树：
# AlekPet 的 `.install.lock` 会把冷启动拖到 600 秒。此类环境由 start-dev 在用户身份下
# 先拉起 ComfyUI（scripts/start_comfy.py），并设 LAF_COMFY_AUTOSTART=0 关掉这里的钩子。
_COMFY_AUTOSTART = os.environ.get("LAF_COMFY_AUTOSTART", "1") != "0"


def _autostart_comfyui() -> None:
    """按已保存配置在后台自动拉起 ComfyUI；成败都登记到看门狗（失败要在面板可见）。

    放在 startup 钩子而非模块级：runtime_entry 自检只 import 本模块，不应误起子进程。
    此前 autostart() 的返回值被线程直接丢弃 ⇒ 启动失败时用户只看到「生图没反应」，
    原因（路径没配/启动失败）无从查起（2026-10-23 架构评审运行时稳健性短板 ②）。
    """
    if not _COMFY_AUTOSTART:
        return

    def _run() -> None:
        result = comfy_launcher.autostart()
        if result.get("started"):
            comfy_watchdog.note_owned()
            return
        reason = str(result.get("reason") or "未知原因")
        _LOG.warning("ComfyUI 自动启动失败：%s", reason)
        comfy_watchdog.note_autostart_failed(reason)

    threading.Thread(target=_run, name="comfy-autostart", daemon=True).start()


@app.on_event("startup")
def _start_comfy_watchdog() -> None:
    """看门狗：托管进程死亡后按已存配置自动重启（防重启风暴 + 面板留痕）。

    未配置/外部实例时全程 no-op；开发链（LAF_COMFY_AUTOSTART=0，ComfyUI 由
    start-dev 在用户身份下拉起）同样安全——没托管过就不管。
    """
    comfy_watchdog.start_watchdog()

# 默认只服务本机：这是单机桌面壳，含无鉴权的进程控制/文件读写接口（start/stop/interrupt/
# save-local/local-view 等）。CORS 只挡浏览器跨源，挡不住非浏览器客户端；故加回环门禁作纵深防御。
# 确需局域网访问时，设环境变量 LAF_ALLOW_REMOTE=1 放开（自担风险）。
_ALLOW_REMOTE = os.environ.get("LAF_ALLOW_REMOTE", "") == "1"


@app.middleware("http")
async def _loopback_only(request: Request, call_next):
    if not _ALLOW_REMOTE:
        client = request.client.host if request.client else ""
        try:
            is_loopback = ipaddress.ip_address(client).is_loopback
        except ValueError:
            is_loopback = client in ("localhost", "")
        if not is_loopback:
            return JSONResponse(
                status_code=403,
                content={"detail": "仅允许本机访问（如需局域网访问请设 LAF_ALLOW_REMOTE=1）"},
            )
    return await call_next(request)


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


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# 新人引导「独立文档页」正文里的相对图片（如 ../assets/guide/x.png）指向仓库 docs/assets，
# 挂成静态目录供文档正文引用。必须在前端 dist 的 "/" 挂载之前注册，否则被它吞掉。
_DOCS_ASSETS = Path(__file__).resolve().parents[2] / "docs" / "assets"
if _DOCS_ASSETS.is_dir():
    app.mount("/docs-assets", StaticFiles(directory=_DOCS_ASSETS), name="docs-assets")

_FRONTEND_DIST = Path(os.environ.get("LAF_FRONTEND_DIST", "")).expanduser()
if str(_FRONTEND_DIST) not in {"", "."} and (_FRONTEND_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
