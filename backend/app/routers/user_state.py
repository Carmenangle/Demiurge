"""用户本地存档：仓库列表 + 设置（含 API Key）持久化到 data/user_state.json。

此前这两块只存浏览器 localStorage，换浏览器/换机器就丢，后端 data 里的对话图片变孤儿。
现改为落盘到 backend/data（已被 .gitignore 排除、不进打包），前端启动时以此为准恢复。

隐私：user_state.json 含 API Key 等隐私，绝不上传（data 目录整体排除）。
"""
import json
from pathlib import Path

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

from app.config import DATA_DIR

router = APIRouter()


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


class SyncMarkersRequest(BaseModel):
    output_dir: str


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
