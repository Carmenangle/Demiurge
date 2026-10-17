"""资产缩略图派生层（P0-2 · Serpent artifacts 模式）。

原图留在落盘目录不动；缩略图（长边 512、JPEG q82）生成到**原图同目录 `.thumbs/`**，
文件名＝原图主干 + `_thumb.jpg`：
- 同目录派生 ⇒ 仓库迁移/换机器时随原图一起搬走；
- local-view 端点按扩展名白名单服务任意本地路径 ⇒ thumb 直接可被前端取，无需新端点。

幂等：thumb 已存在且不旧于原图（mtime）则跳过。坏图/非静态图/远程 URL 一律**静默
回落**（返回 None/空串，调用方用原图），绝不阻断入库主链。CPU 轻操作（Pillow 缩放，
典型 <200ms），在 index_generation 同步链内做；历史补生成走 `/rag/generate-thumbs`。
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app.services import view_urls

_log = logging.getLogger("uvicorn.error")

_THUMB_EDGE = 512
_JPEG_QUALITY = 82
_THUMB_DIR = ".thumbs"
# 只处理静态图（视频缩略图需抽帧，超 P0-2 范围；GIF 由 Pillow 天然抽首帧）
_STATIC_EXTS = {"jpg", "jpeg", "png", "webp", "bmp", "gif"}


def thumb_path_for(image_path: Path) -> Path:
    """原图 → 缩略图路径（同目录 `.thumbs/<主干>_thumb.jpg`）。"""
    return image_path.parent / _THUMB_DIR / f"{image_path.stem}_thumb.jpg"


def local_path_of_url(image_url: str) -> Path | None:
    """从 local-view?path=... 解出本机磁盘路径；非本机留存图/文件不存在返回 None。"""
    if not image_url or "local-view" not in image_url:
        return None
    value = (parse_qs(urlparse(image_url).query).get("path") or [""])[0]
    path = Path(unquote(value)) if value else None
    return path if path and path.is_file() else None


def ensure_thumb(image_path: Path) -> Path | None:
    """确保缩略图存在并返回其路径；不需要/失败时返回 None（调用方回落原图）。"""
    try:
        if not image_path.is_file():
            return None
        if image_path.suffix.lower().lstrip(".") not in _STATIC_EXTS:
            return None
        thumb = thumb_path_for(image_path)
        if thumb.is_file() and thumb.stat().st_mtime >= image_path.stat().st_mtime:
            return thumb  # 幂等：已存在且不旧于原图（重新生成覆盖同名图时 mtime 变 ⇒ 重生）
        thumb.parent.mkdir(parents=True, exist_ok=True)
        from PIL import Image, ImageOps

        with Image.open(image_path) as opened:
            img = ImageOps.exif_transpose(opened)  # 按 EXIF 转正，否则手机图躺平
            if img.mode in ("RGBA", "LA", "P"):
                # 透明区域合成白底（直接 convert RGB 会变黑底，二次元图不可接受）
                img = img.convert("RGBA")
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.getchannel("A"))
                frame = bg
            else:
                frame = img if img.mode == "L" else img.convert("RGB")
            frame.thumbnail((_THUMB_EDGE, _THUMB_EDGE))
            if frame.width <= 0 or frame.height <= 0:
                return None
            tmp = thumb.with_suffix(".tmp")
            frame.save(tmp, format="JPEG", quality=_JPEG_QUALITY)
        tmp.replace(thumb)  # 原子替换（防并发/中断留残片）
        return thumb
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 缩略图失败绝不阻断入库，回落原图
        _log.warning("缩略图生成失败（回落原图） path=%s: %s", image_path, exc)
        return None


def thumb_url_if_ready(image_url: str) -> str:
    """只查不生成：本地图且缩略图文件已在（历史补生成后）则返回 URL，否则空串。"""
    path = local_path_of_url(image_url)
    if path is None:
        return ""
    thumb = thumb_path_for(path)
    if not thumb.is_file():
        return ""
    return view_urls.local_view(str(thumb))


def thumb_url_for(image_url: str) -> str:
    """本地图尽力生成缩略图并返回其 local-view URL；非本地/失败返回空串（回落原图）。"""
    path = local_path_of_url(image_url)
    if path is None:
        return ""
    thumb = ensure_thumb(path)
    return view_urls.local_view(str(thumb)) if thumb else ""


def remove_thumb(image_path: Path) -> None:
    """删原图时顺手删缩略图（防 `.thumbs/` 留孤儿）。"""
    try:
        thumb_path_for(image_path).unlink(missing_ok=True)
    except OSError as exc:
        _log.warning("缩略图清理失败 path=%s: %s", image_path, exc)
