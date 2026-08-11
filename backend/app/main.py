import ipaddress
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import threading

from app.db import init_db
from app.services import comfy_launcher
from app.services.workflow_build_tasks import start_worker as start_workflow_build_worker
from app.services.chat_agent_queue import start_worker as start_chat_agent_queue_worker
from app.routers import ai, ai_providers, agents, assets, characters, comfyui, gif_sprite, image_resize, loras, mcp, models, narrative, node_manager, palette, preset, procedures, rag, regex, runs, scenario, skills, state, table, user_state, workflows, worldbook

app = FastAPI(title="Local AI ComfyUI Frontend API")
init_db()
start_workflow_build_worker()
start_chat_agent_queue_worker()


@app.on_event("startup")
def _autostart_comfyui() -> None:
    """服务真正开始接收请求时，按已保存配置在后台自动拉起 ComfyUI。
    放在 startup 钩子而非模块级：runtime_entry 自检只 import 本模块，不应误起子进程。"""
    threading.Thread(target=comfy_launcher.autostart, daemon=True).start()

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
app.include_router(ai.router, prefix="/api/ai", tags=["ai"])
app.include_router(ai_providers.router, prefix="/api/ai/providers", tags=["ai-providers"])
app.include_router(rag.router, prefix="/api/rag", tags=["rag"])
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
app.include_router(mcp.router, prefix="/api/mcp", tags=["mcp"])
app.include_router(skills.router, prefix="/api/skills", tags=["skills"])
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(scenario.router, prefix="/api/scenario", tags=["scenario"])
app.include_router(procedures.router, prefix="/api/procedures", tags=["procedures"])


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


_FRONTEND_DIST = Path(os.environ.get("LAF_FRONTEND_DIST", "")).expanduser()
if str(_FRONTEND_DIST) not in {"", "."} and (_FRONTEND_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
