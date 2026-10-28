"""用户本地存档：仓库列表 + 设置（含 API Key）持久化到 data/user_state.json。

此前这两块只存浏览器 localStorage，换浏览器/换机器就丢，后端 data 里的对话图片变孤儿。
现改为落盘到 backend/data（已被 .gitignore 排除、不进打包），前端启动时以此为准恢复。

隐私：user_state.json 含 API Key 等隐私，绝不上传（data 目录整体排除）。

分工（2026-10-24 审计中期项）：**读侧属主 = repo_meta**（load_user_state /
setting_str_from_state / user_state_path），本路由的三处内联自读已收口；写侧属主
仍在本路由（整体覆盖写 + selection 保留语义见 set_state）。测试注入接缝随读侧
改钉 `repo_meta.DATA_DIR`（见 test_user_state_ownership / test_user_state_selection）。
"""
import json
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.config import DATA_DIR
from app.services import repo_meta
from app.services.secret_fields import SECRET_KEYS

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

    return system_proxy.resolve(repo_meta.setting_str_from_state("proxyUrl"), search=search)


class UserState(BaseModel):
    # 前端结构自由（仓库列表、设置对象），后端只负责整体读写不解释内容
    repos: list | None = None
    settings: dict | None = None
    # 上次选中的大仓库/作品（前端结构：{repoId, workId, savedAt}）。桌面壳的 WebView2
    # 数据目录可能切换（受限上下文会回落到别的目录），selection 存后端才能跨目录/跨浏览器恢复。
    selection: dict | None = None


@router.get("")
def get_state() -> UserState:
    """读用户存档。缺失/损坏返回空（前端据此回退 localStorage）；读取经读侧属主 repo_meta。"""
    data = repo_meta.load_user_state()
    return UserState(
        repos=data.get("repos"),
        settings=data.get("settings"),
        selection=data.get("selection"),
    )


def _keep_stored_secrets(incoming: object, stored: object, key: str = "") -> object:
    """空密钥不覆盖盘上非空值（多客户端整对象覆盖写的写侧护栏）。

    localStorage 副本按 `stripSecrets` 清空了密钥，客户端启动回填一旦失败（后端忙/中继
    502）就会把这份空副本整份推回来；没有这层护栏，一次离线启动就能把盘上真 key 抹成
    空串（2026-10-28 实测：8 个模型 + embed + 各 token 全空，之后每轮生成 401）。
    非空值照常覆盖（换 key 生效）；模型条目按 id 对齐，被删掉的条目不会因此复活。
    密钥字段清单单一属主＝services/secret_fields（与配置包导出共用）。
    """
    if isinstance(incoming, dict):
        base = stored if isinstance(stored, dict) else {}
        merged = {k: _keep_stored_secrets(v, base.get(k), k) for k, v in incoming.items()}
        # 旧 APK / 旧前端的 Settings 可能还没有某个密钥字段：缺席也按「空」处理，
        # 否则换端升级期一次整份上传就会把新字段从盘上删掉。
        for name in SECRET_KEYS:
            old = base.get(name)
            if name not in merged and isinstance(old, str) and old.strip():
                merged[name] = old
        return merged
    if isinstance(incoming, list):
        pool = stored if isinstance(stored, list) else []
        by_id = {it["id"]: it for it in pool if isinstance(it, dict) and isinstance(it.get("id"), str)}
        kept = []
        for index, item in enumerate(incoming):
            key_of = item.get("id") if isinstance(item, dict) else None
            if isinstance(key_of, str):
                ref = by_id.get(key_of)
            else:
                ref = pool[index] if index < len(pool) else None
            kept.append(_keep_stored_secrets(item, ref, key))
        return kept
    if key in SECRET_KEYS and isinstance(stored, str) and stored.strip():
        if incoming is None or (isinstance(incoming, str) and not incoming.strip()):
            return stored
    return incoming


@router.post("")
def set_state(state: UserState) -> dict[str, bool]:
    """保存用户存档到 data/user_state.json。整体覆盖写。

    `selection` 未出现在请求里时沿用盘上旧值：启动早期 repos/settings 的合并上传
    不该把已记住的作品选择抹掉（旧前端也不带这个字段）。盘上旧值经读侧属主
    repo_meta.load_user_state 取（缺失/损坏即无旧值可保）。

    密钥同理「缺席≠删除」：空密钥不清空盘上非空值（见 _keep_stored_secrets），
    整个 settings 缺席时沿用盘上旧值。
    """
    existing = repo_meta.load_user_state()
    settings: object = state.settings
    if settings is None:
        settings = existing.get("settings")
    elif isinstance(settings, dict) and isinstance(existing.get("settings"), dict):
        settings = _keep_stored_secrets(settings, existing["settings"])
    payload: dict = {"repos": state.repos, "settings": settings}
    if "selection" in state.model_fields_set:
        payload["selection"] = state.selection
    elif "selection" in existing:
        payload["selection"] = existing.get("selection")
    p = repo_meta.user_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
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
    文件夹名保留 UUID 不动，只写标记文件——文件系统里一看便知对应哪个仓库。

    根校验与写/删类端点同口径（works_root_violation，2026-10-24 审计补齐）：
    必须等于配置的仓库文件夹根且已配置——本端点会向目录**写入**标记文件，
    不能例外于「防客户端指定任意目录」的合同。"""
    from app.services import repo_meta
    err = repo_meta.works_root_violation(req.output_dir)
    if err:
        raise HTTPException(status_code=400, detail=err)
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


@router.get("/wallpapers/scene-properties")
def wallpaper_scene_properties(source: str = "") -> dict:
    """场景壁纸的预设属性表（类型/范围/选项/当前值，已合并用户在 WE 里的覆盖）。"""
    from app.services import wallpaper_library
    try:
        return wallpaper_library.scene_properties(source)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/wallpapers/we-asset")
def wallpaper_we_asset(path: str = ""):
    """Wallpaper Engine 安装目录里的内置资产（字体 / 粒子贴图 / 材质）。

    scene 工程按相对路径引用它们，却不把这些文件打进 `scene.pkg`，实时渲染要另取一份。
    `path` 是 `assets/` 下的相对路径，首段限定在字体等素材子目录，越界与绝对路径 404。
    """
    from fastapi.responses import FileResponse
    from app.services import wallpaper_library
    try:
        asset = wallpaper_library.we_builtin_asset(path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        asset, media_type="application/octet-stream",
        headers={"Cache-Control": "public, max-age=604800"},
    )


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
