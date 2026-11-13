"""checkpoint 内联图片外置（2026-11-12 P6-C）。

LangGraph checkpoint 每轮保存整段 state；用户上传的 `data:image/...;base64,` 曾被
原样写进 checkpoint，一段长剧情能撑到数 GB（实测单线程 3.16 GB）。checkpoint 里的
图片只用于旧会话回填与画布媒体来源，不参与模型上下文（模型历史只取文本），因此
写入前统一落盘成文件并换成根相对 local-view 地址：既保留可回取能力，又不再让
每轮 state 膨胀。
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re

from app.config import DATA_DIR
from app.services import view_urls
from app.services.pathnames import safe_seg

_LOG = logging.getLogger("uvicorn.error")

#: 外置图片落点；按线程分目录、按内容哈希命名（幂等，重复写入不产生新文件）。
CHECKPOINT_IMAGE_DIR = DATA_DIR / "chat_checkpoint_images"
#: 单张内联媒体上限（与 image_payload / image_store 同口径）。
MAX_INLINE_BYTES = 20 * 1024 * 1024
_MIME_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
    "image/webp": "webp", "image/gif": "gif", "image/bmp": "bmp",
    "image/avif": "avif", "video/mp4": "mp4", "video/webm": "webm",
    "audio/mpeg": "mp3", "audio/wav": "wav",
}
# 文本里内嵌的 data URI（markdown 图片/历史拼接）；只匹配 base64 形态。
_DATA_URI_TEXT = re.compile(
    r"data:(?:image|video|audio)/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\r\n]+",
)


def _ext(mime: str) -> str:
    mime = (mime or "").split(";")[0].strip().lower()
    return _MIME_EXT.get(mime, (mime.split("/")[-1] or "bin").replace("+", "_"))


def _save(data: bytes, thread_id: str, ext: str) -> str:
    digest = hashlib.sha256(data).hexdigest()
    folder = CHECKPOINT_IMAGE_DIR / safe_seg(thread_id or "home", "home", strip=False)
    path = folder / f"{digest[:32]}.{safe_seg(ext) or 'bin'}"
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    return view_urls.local_view(str(path))


def externalize(src: str, thread_id: str) -> str:
    """data URI → 落盘文件 + local-view 根相对地址；非 data URI 原样返回。

    解码失败/超限时返回空串，由调用方丢弃该图——宁可少一张历史图，也不让坏数据
    把每轮 checkpoint 重新撑大。
    """
    if not isinstance(src, str) or not src.startswith("data:"):
        return src
    try:
        header, payload = src.split(",", 1)
        mime = header[5:].split(";")[0] or "image/png"
        data = base64.b64decode(payload, validate=False)
        if not data:
            raise ValueError("解码为空")
        if len(data) > MAX_INLINE_BYTES:
            raise ValueError(f"超过 {MAX_INLINE_BYTES} 字节上限：{len(data)}")
        return _save(data, thread_id, _ext(mime))
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 外置失败丢图但不阻断落盘
        _LOG.warning("checkpoint 内联媒体外置失败 thread=%s: %s", thread_id, exc)
        return ""


def externalize_images(thread_id: str, images: list[str] | None) -> list[str] | None:
    """批量外置图片列表；全部失败返回 None（保持「无图」语义）。"""
    if not images:
        return images
    out = [url for url in (externalize(src, thread_id) for src in images) if url]
    return out or None


def externalize_text(text: str, thread_id: str) -> str:
    """替换正文里内嵌的 data URI；没有内嵌图时原样返回（零成本）。"""
    if not text or "data:" not in text:
        return text
    return _DATA_URI_TEXT.sub(lambda match: externalize(match.group(0), thread_id), text)
