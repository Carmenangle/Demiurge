"""用户本地存档：仓库列表 + 设置（含 API Key）持久化到 data/user_state.json。

此前这两块只存浏览器 localStorage，换浏览器/换机器就丢，后端 data 里的对话图片变孤儿。
现改为落盘到 backend/data（已被 .gitignore 排除、不进打包），前端启动时以此为准恢复。

隐私：user_state.json 含 API Key 等隐私，绝不上传（data 目录整体排除）。
"""
import json
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.config import DATA_DIR
from app.services import repo_meta

router = APIRouter()
@router.get("/proxy-status")
def proxy_status(search: bool = False) -> dict:
    """「继承全局」实时检测：先读**系统代理**（WinINET/环境变量），再回退应用内手填地址。

    2026-09-10 升级（原实现只 TCP 探设置里那一个固定地址，换端口/系统代理开着也判「未开启」）：
    判定逻辑全部收敛到 `services/system_proxy`；本端点只负责取用户存档里的手填地址并透出结果。

    search=True 时额外真跑一次最小联网搜索，用于区分「端口在听」与「真能搜到」
    ——按需触发，避免每次启动都等网络超时。
    """
    from app.services import system_proxy

    manual = ""
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        settings = data.get("settings") or {}
        if isinstance(settings, dict):
            manual = str(settings.get("proxyUrl") or "").strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        manual = ""
    return system_proxy.resolve(manual, search=search)


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


class WallpaperScanRequest(BaseModel):
    folder: str = ""
    kind: str = "all"          # all / video / image
    page: int = 1
    page_size: int = 21
    hidden: list[str] | None = None   # 已隐藏路径：正常列表过滤掉，hidden_only 时只看它们
    hidden_only: bool = False
    query: str = ""            # 名称搜索（相对路径/文件名，不区分大小写）


class WallpaperStoreMoveRequest(BaseModel):
    from_dir: str = ""
    to_dir: str = ""           # 空串=恢复默认目录；支持 ~ 表示用户主目录


class WallpaperWorkshopRequest(BaseModel):
    kind: str = "all"
    page: int = 1
    page_size: int = 21
    hidden: list[str] | None = None
    hidden_only: bool = False
    query: str = ""            # 作品标题 / 创意工坊 id 搜索，不区分大小写


@router.post("/wallpapers/scan")
def scan_wallpapers(req: WallpaperScanRequest) -> dict:
    """递归扫描液态玻璃壁纸文件夹（图片 + 浏览器可播放视频），分页返回。"""
    from app.services import wallpaper_library
    try:
        return wallpaper_library.scan(
            req.folder, kind=req.kind, page=req.page, page_size=req.page_size,
            hidden=req.hidden or (), hidden_only=req.hidden_only, query=req.query,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/wallpapers/thumb")
def wallpaper_thumb(folder: str, path: str):
    """壁纸缩略图（图片缩放 / 视频首帧），缓存到 data/wallpaper_thumbs/。"""
    from fastapi.responses import FileResponse
    from app.services import wallpaper_library
    try:
        source = wallpaper_library.resolve_wallpaper(folder, path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    thumb = wallpaper_library.thumbnail(source)
    if thumb is None:
        raise HTTPException(status_code=404, detail="缩略图生成失败")
    return FileResponse(
        thumb, media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.post("/wallpapers/workshop")
def scan_workshop_wallpapers(req: WallpaperWorkshopRequest) -> dict:
    """自动发现 Steam 库并扫描 Wallpaper Engine 创意工坊（431960）壁纸。"""
    from app.services import wallpaper_library
    return wallpaper_library.scan_workshop(
        kind=req.kind, page=req.page, page_size=req.page_size,
        hidden=req.hidden or (), hidden_only=req.hidden_only, query=req.query,
    )


@router.get("/wallpapers/scene-pkg")
def wallpaper_scene_pkg(source: str = "", workshop_id: str = ""):
    """把 scene 作品的 `scene.pkg` 原样交给前端实时渲染器解码（后端不解析包格式）。

    `source` 是通用来源标识：`workshop:<id>`（或旧前端传的纯数字）/ `local:<绝对路径>`。
    `workshop_id` 只为兼容旧前端保留，新代码一律走 `source`。
    """
    from fastapi.responses import FileResponse
    from app.services import wallpaper_library
    ident = (source or "").strip() or (workshop_id or "").strip()
    try:
        pkg = wallpaper_library.scene_package(ident)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        pkg, media_type="application/octet-stream",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/wallpapers/workshop/scene-pkg")
def workshop_scene_pkg(workshop_id: str):
    """旧路径（工坊 id 单参）；保留一版避免老前端 404，内部与上者同源。"""
    return wallpaper_scene_pkg(source="", workshop_id=workshop_id)


@router.get("/wallpapers/store")
def wallpaper_store(dir: str = "") -> dict:
    """自定义壁纸存储位置：解析 ~、返回绝对路径与已上传数量。"""
    from app.services import wallpaper_library
    try:
        return wallpaper_library.store_info(dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/wallpapers/upload")
def wallpaper_upload(file: UploadFile = File(...), dir: str = Form("")) -> dict:
    """上传自定义壁纸到存储位置（仅 JPG/PNG/MP4，重名自动加序号）。"""
    from app.services import wallpaper_library
    try:
        return wallpaper_library.save_upload(file.file, file.filename or "", dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/wallpapers/store/migrate")
def wallpaper_store_migrate(req: WallpaperStoreMoveRequest) -> dict:
    """更改存储位置：迁移旧位置中的壁纸文件，返回路径映射供前端改写列表。"""
    from app.services import wallpaper_library
    try:
        return wallpaper_library.migrate_store(req.from_dir, req.to_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/upload-bg")
async def upload_bg(file: UploadFile = File(...)) -> dict:
    """上传对话/液态玻璃壁纸，存到 data/backgrounds/。

    图片供 chatBgPath；视频供液态玻璃主题的独立壁纸，走 local-view（支持 Range 拖动进度）。
    """
    from uuid import uuid4
    bg_dir = DATA_DIR / "backgrounds"
    bg_dir.mkdir(parents=True, exist_ok=True)
    name = file.filename or "bg.png"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "png"
    if ext not in ("png", "jpg", "jpeg", "webp", "gif", "bmp", "mp4", "webm", "mov", "m4v", "ogv"):
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
