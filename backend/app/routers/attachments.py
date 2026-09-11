"""对话附件端点：multipart 上传 + file_id 流式下载（历史回放只读卡片）。

- POST /api/attachments/upload    multipart: file + thread_id → {file_id, name, mime, size}
- GET  /api/attachments/{file_id} 按 file_id 流式下载原文件（Content-Disposition 附件）
"""
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.services import attachment_store

router = APIRouter()


@router.post("/upload")
async def upload_attachment(
    file: UploadFile = File(...),
    thread_id: str = Form("home"),
) -> dict:
    """上传对话附件到会话级目录；返回 file_id 元信息（前端渲染卡片、随消息透传 agent）。"""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件不能作为附件")
    name = (file.filename or "").strip() or "attachment.bin"
    mime = file.content_type or ""
    try:
        meta = attachment_store.save_upload(thread_id, name, mime, data)
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    return {"ok": True, **meta}


@router.get("/{file_id}")
def download_attachment(file_id: str) -> FileResponse:
    """按 file_id 流式下载原文件；历史回放附件卡点击下载用。"""
    path = attachment_store.resolve(file_id)
    if path is None:
        raise HTTPException(status_code=404, detail="附件不存在或已删除")
    # 文件名取 file_id- 之后的安全段；mime 用保存时记录？存储层未存 mime 索引，
    # 回放卡片已带 mime 元信息，这里按原文件后缀兜底 octet-stream，浏览器可另存。
    name = path.name.split("-", 1)[-1] if "-" in path.name else path.name
    return FileResponse(path, media_type="application/octet-stream", filename=name)
