"""作品包导入/导出薄路由（M5 换机迁移；逻辑单一属主 services/repo_transfer）。

- GET /export：FileResponse 附件下载——**故意走 GET + 同源 cookie**：WebView 的
  DownloadListener/DownloadManager 拿不到 fetch blob，真实 http URL 才能原生落盘；
  下载是只读动作，cookie 鉴权无 CSRF 面。
- POST /import：multipart 上传（WebView 侧由壳层 onShowFileChooser 文件桥供料）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.services import repo_transfer
from app.services.repo_transfer import TransferError

router = APIRouter()


@router.get("/export")
def export(repo_id: str):
    try:
        zip_path, filename = repo_transfer.export_repo(repo_id)
    except TransferError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return FileResponse(
        zip_path, filename=filename, media_type="application/zip",
        background=BackgroundTask(repo_transfer.cleanup_transfer_file, zip_path),
    )


@router.post("/import")
async def import_package(file: UploadFile):
    if not file.filename or not file.filename.endswith((".zip", ".demirepo.zip")):
        raise HTTPException(status_code=400, detail="请上传 .zip 作品包")
    dest = Path(repo_transfer.config.DATA_DIR) / "transfer" / f"import-{file.filename}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())
    try:
        result = repo_transfer.import_repo(dest)
    except TransferError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc))
    return result
