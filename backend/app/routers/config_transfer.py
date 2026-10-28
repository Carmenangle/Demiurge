"""离线资源包导入/导出薄路由（逻辑单一属主 services/config_bundle）。

- GET /export：FileResponse 附件下载（同 transfer：WebView 拿不到 fetch blob，
  真实 http URL 才能原生落盘；只读动作，同源 cookie 鉴权无 CSRF 面）。
- POST /export/save：PC 桌面壳没有下载处理器，由后端直接存进系统下载目录。
- POST /reveal：在资源管理器中定位刚导出的包（只认下载目录内的文件）。
- POST /import：multipart 上传（WebView 侧由壳层 onShowFileChooser 文件桥供料）。
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app import config
from app.services import config_bundle
from app.services.config_bundle import ConfigBundleError

router = APIRouter()


def _bundle_response(*, include_secrets: bool) -> FileResponse:
    try:
        zip_path, filename = config_bundle.export_bundle(include_secrets=include_secrets)
    except ConfigBundleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(
        zip_path, filename=filename, media_type="application/zip",
        background=BackgroundTask(config_bundle.cleanup_bundle_file, zip_path),
    )


@router.get("/export")
def export(include_secrets: bool = False):
    return _bundle_response(include_secrets=include_secrets)


@router.get("/pull")
def pull():
    """联机同库·在线同步的拉包口：远端客户端专用，**恒不含密钥**。

    `/api/config` 前缀整体是 PC 独占能力（remote_gate DENY：导出会把模型密钥、作品与
    素材一起打走）。本条是唯一精确放行的子路径，所以签名里**绝不接受** include_secrets
    ——密钥只能由手机本机自备，不经局域网传输。
    """
    return _bundle_response(include_secrets=False)


@router.post("/export/save")
def export_save(include_secrets: bool = False):
    try:
        return config_bundle.save_bundle(include_secrets=include_secrets)
    except ConfigBundleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class RevealRequest(BaseModel):
    path: str = ""


@router.post("/reveal")
def reveal(req: RevealRequest):
    try:
        opened = config_bundle.reveal_export(req.path)
    except ConfigBundleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": opened}


@router.get("/inventory")
def inventory() -> dict[str, object]:
    return config_bundle.inventory()


@router.post("/import")
async def import_package(file: UploadFile, overwrite: bool = False):
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 .zip 配置包")
    dest = Path(config.DATA_DIR) / "transfer" / f"config-import-{uuid4().hex}.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 含作品仓库的包可达数百 MB：分块落盘，不能整包读进内存（手机端会 OOM）。
    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            out.write(chunk)
    try:
        # 解包+复制是数百 MB 的同步重 IO：留在事件循环里会把 /api/health 饿死，
        # 而壳层 supervisor 是「5s 一跳、连三跳就 restart_uvicorn」⇒ 导入跑到仓库阶段
        # 会被自家看门狗打断（素材阶段在前、先落地，故现象是「只有素材进来了」）。
        return await run_in_threadpool(config_bundle.import_bundle, dest, overwrite=overwrite)
    except ConfigBundleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        dest.unlink(missing_ok=True)
