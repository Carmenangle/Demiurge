"""用户本地存档：仓库列表 + 设置（含 API Key）持久化到 data/user_state.json。

此前这两块只存浏览器 localStorage，换浏览器/换机器就丢，后端 data 里的对话图片变孤儿。
现改为落盘到 backend/data（已被 .gitignore 排除、不进打包），前端启动时以此为准恢复。

隐私：user_state.json 含 API Key 等隐私，绝不上传（data 目录整体排除）。
"""
import json
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel

from app.config import DATA_DIR
from app.services import repo_meta

router = APIRouter()
@router.get("/proxy-status")
def proxy_status() -> dict:
    """实时检测设置里的代理地址是否在本机监听（继承全局模式用）。"""
    import socket
    from urllib.parse import urlparse

    proxy_url = ""
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        settings = data.get("settings") or {}
        if isinstance(settings, dict):
            proxy_url = str(settings.get("proxyUrl") or "").strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        return {"listening": False, "address": ""}
    if not proxy_url:
        return {"listening": False, "address": ""}
    try:
        parsed = urlparse(proxy_url if "//" in proxy_url else f"//{proxy_url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 0
    except ValueError:
        return {"listening": False, "address": proxy_url}
    if port <= 0:
        return {"listening": False, "address": proxy_url}
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return {"listening": True, "address": proxy_url}
    except OSError:
        return {"listening": False, "address": proxy_url}


def _state_path() -> Path:
    return DATA_DIR / "user_state.json"


class UserState(BaseModel):
    # 前端结构自由（仓库列表、设置对象），后端只负责整体读写不解释内容
    repos: list | None = None
    settings: dict | None = None


@router.get("")
def get_state() -> UserState:
    """读用户存档。缺失/损坏返回空（前端据此回退 localStorage）。"""
    p = _state_path()
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return UserState(repos=data.get("repos"), settings=data.get("settings"))
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            pass
    return UserState()


@router.post("")
def set_state(state: UserState) -> dict[str, bool]:
    """保存用户存档到 data/user_state.json。整体覆盖写。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"repos": state.repos, "settings": state.settings}
    _state_path().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True}


class RenameRequest(BaseModel):
    repo_id: str
    old_name: str = ""
    new_name: str
    output_dir: str = ""


@router.post("/rename-folder")
def rename_folder(req: RenameRequest) -> dict:
    """仓库改名：重命名输出文件夹 + 重写快照/RAG 里的图片路径。前端 renameRepo 时调用。"""
    from app.services import repo_meta
    return repo_meta.rename_folder(req.output_dir, req.repo_id, req.old_name, req.new_name)


class DeleteFolderRequest(BaseModel):
    repo_id: str
    name: str = ""
    output_dir: str = ""


@router.post("/delete-folder")
def delete_folder(req: DeleteFolderRequest) -> dict:
    """删仓库时清理其作品文件夹（快照卡/世界书/persona/会话/图）。只删作品自有文件夹，不碰源库。"""
    from app.services import repo_meta
    return repo_meta.delete_folder(req.output_dir, req.repo_id, req.name)


class SyncMarkersRequest(BaseModel):
    output_dir: str


class CanvasLayoutRequest(BaseModel):
    repo_id: str
    output_dir: str = ""
    nodes: dict | None = None
    edges: list | None = None
    viewport: dict | None = None
    # 灵感卡（角色卡 / 世界书条目 / 预设 / 表格行 各自一张；可被剧情对话引用）
    inspiration_cards: list | None = None
    # 参考图（文件夹拖入画布的图片节点，独立于灵感卡）
    reference_images: list | None = None
    # 已删除节点黑名单：删除的投影节点 id（gen-<generationId>），投影时过滤防止复活
    deleted_ids: list | None = None


@router.get("/canvas-layout")
def canvas_layout_get(repo_id: str, output_dir: str = "") -> dict:
    """读取作品画布布局（canvas.json：布局/连线/视口/灵感卡/参考图/删除黑名单）。缺失返回空结构。"""
    from app.services import canvas_store
    return canvas_store.load_layout(output_dir, repo_id)


@router.post("/canvas-layout")
def canvas_layout_save(req: CanvasLayoutRequest) -> dict[str, bool]:
    """保存作品画布布局到 canvas.json。只写布局/连线/视口/灵感卡/参考图/删除黑名单，不碰 generation/快照/角色卡。"""
    from app.services import canvas_store
    canvas_store.save_layout(req.output_dir, req.repo_id, {
        "nodes": req.nodes or {},
        "edges": req.edges or [],
        "viewport": req.viewport or {"x": 0, "y": 0, "scale": 1},
        "inspiration_cards": req.inspiration_cards or [],
        "reference_images": req.reference_images or [],
        "deleted_ids": req.deleted_ids or [],
    })
    return {"ok": True}


@router.post("/sync-markers")
def sync_markers(req: SyncMarkersRequest) -> dict[str, int]:
    """扫描 output_dir 下的 UUID 子文件夹，按当前仓库列表补/更新 _repo.json。
    文件夹名保留 UUID 不动，只写标记文件——文件系统里一看便知对应哪个仓库。"""
    from app.services import repo_meta
    out = Path(req.output_dir)
    if not out.is_dir():
        return {"written": 0}
    n = 0
    for d in out.iterdir():
        if d.is_dir() and repo_meta.repo_name(d.name):  # 仅当能查到仓库名才标注
            repo_meta.write_repo_marker(d, d.name)
            n += 1
    return {"written": n}


@router.post("/upload-bg")
async def upload_bg(file: UploadFile = File(...)) -> dict:
    """上传对话背景图，存到 data/backgrounds/，返回本地绝对路径（前端填进 chatBgPath，走 local-view 读）。"""
    from uuid import uuid4
    bg_dir = DATA_DIR / "backgrounds"
    bg_dir.mkdir(parents=True, exist_ok=True)
    name = file.filename or "bg.png"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "png"
    if ext not in ("png", "jpg", "jpeg", "webp", "gif", "bmp"):
        ext = "png"
    dest = bg_dir / f"{uuid4().hex}.{ext}"
    data = await file.read()
    dest.write_bytes(data)
    return {"ok": True, "path": str(dest)}


@router.post("/upload-voice")
async def upload_voice(
    file: UploadFile = File(...),
    repo_id: str = Form("home"),
    output_dir: str = Form(""),
) -> dict:
    """上传参考音轨到 <repo>/voices/，返回本地绝对路径（填进 characterVoices[角色].voiceRef，走 local-view 读）。

    音轨是作品专属资产（每作品每角色一个），物理落仓库 voices 子夹，与 reference/ 对齐。
    """
    from uuid import uuid4
    folder = repo_meta.repo_folder(output_dir, repo_id)
    voices_dir = folder / "voices"
    voices_dir.mkdir(parents=True, exist_ok=True)
    name = file.filename or "voice.wav"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "wav"
    if ext not in ("wav", "mp3", "flac", "ogg", "m4a", "aac", "opus"):
        ext = "wav"
    dest = voices_dir / f"{uuid4().hex}.{ext}"
    data = await file.read()
    dest.write_bytes(data)
    return {"ok": True, "path": str(dest)}
