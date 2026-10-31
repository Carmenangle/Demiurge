"""资产缩略图派生层（P0-2 · Serpent artifacts 模式）。

原图留在落盘目录不动；缩略图（长边 512、JPEG q82）生成到**原图同目录 `.thumbs/`**，
文件名＝原图主干 + `_thumb.jpg`：
- 同目录派生 ⇒ 仓库迁移/换机器时随原图一起搬走；
- local-view 端点按扩展名白名单服务任意本地路径 ⇒ thumb 直接可被前端取，无需新端点。

幂等：thumb 已存在且不旧于原图（mtime）则跳过。坏图/不支持的类型/远程 URL 一律**静默
回落**（返回 None/空串，调用方用原图），绝不阻断入库主链。CPU 轻操作（Pillow 缩放，
典型 <200ms），在 index_generation 同步链内做；历史补生成走 `/rag/generate-thumbs`。

视频（2026-10-31）：走 ffmpeg 抽一帧当封面，抽完仍交给同一条 Pillow 归一链，
所以封面与静态图逐字节同规格（长边 512 / q82），前端 `<img>` 量到的宽高比＝视频真实
宽高比。定位不到 ffmpeg（手机端 / 未装 ComfyUI）⇒ 返回 None，前端保持原占位。
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app.services import view_urls

_log = logging.getLogger("uvicorn.error")

_THUMB_EDGE = 512
_JPEG_QUALITY = 82
_THUMB_DIR = ".thumbs"
# 媒体产出按类型分夹（2026-10-17 迁移）：老索引记 `<仓库>/<name>`，真实文件在
# `<仓库>/<类型>/<name>` ⇒ URL 解析要按这张表回退（见 local_path_of_url 注释）。
_KIND_DIRS = ("pictures", "videos", "audios")
# 静态图走 Pillow；视频走 ffmpeg 抽帧后并入同一条 Pillow 归一链（见模块 docstring）
_STATIC_EXTS = {"jpg", "jpeg", "png", "webp", "bmp", "gif"}
_VIDEO_EXTS = {"mp4", "webm", "mov", "mkv", "m4v", "avi"}
# 0.4s 往往还在黑场/淡入，先取 5s 更接近有效画面（阶梯与壁纸库同源，已真机验）
_FRAME_SEEK_LADDER = ("5", "1.5", "0.4", "0")
_FRAME_TIMEOUT_SECONDS = 8


def thumb_path_for(image_path: Path) -> Path:
    """原图 → 缩略图路径（同目录 `.thumbs/<主干>_thumb.jpg`）。"""
    return image_path.parent / _THUMB_DIR / f"{image_path.stem}_thumb.jpg"


def local_path_of_url(image_url: str) -> Path | None:
    """从 local-view?path=... 解出本机磁盘路径；非本机留存图/文件不存在返回 None。

    2026-10-18（用户实锤「反推打标报仅支持本机留存图片，但图明明在盘上」）：
    媒体产出早已按类型迁入 `<仓库>/pictures|videos|audios/`，但老索引仍记
    `<仓库>/<name>` ⇒ 原路径不存在时必须到同级类型夹找同名文件（与
    `rag_store.local_path_of` 同一张回退表）。此前这里只认精确路径，导致
    **打标 / 自动描述 / 补缩略图** 对 200/203 条老记录一律假报「远程图」。
    """
    if not image_url or "local-view" not in image_url:
        return None
    value = (parse_qs(urlparse(image_url).query).get("path") or [""])[0]
    path = Path(unquote(value)) if value else None
    if path is None:
        return None
    if path.is_file():
        return path
    for sub in _KIND_DIRS:
        alt = path.parent / sub / path.name
        if alt.is_file():
            return alt
    return None


def ensure_thumb(image_path: Path) -> Path | None:
    """确保缩略图存在并返回其路径；不需要/失败时返回 None（调用方回落原图）。"""
    try:
        if not image_path.is_file():
            return None
        extension = image_path.suffix.lower().lstrip(".")
        if extension not in _STATIC_EXTS and extension not in _VIDEO_EXTS:
            return None
        thumb = thumb_path_for(image_path)
        if thumb.is_file() and thumb.stat().st_mtime >= image_path.stat().st_mtime:
            return thumb  # 幂等：已存在且不旧于原图（重新生成覆盖同名图时 mtime 变 ⇒ 重生）
        thumb.parent.mkdir(parents=True, exist_ok=True)
        if extension not in _VIDEO_EXTS:
            return thumb if _write_thumb(image_path, thumb) else None
        frame = thumb.parent / f"{thumb.stem}_frame.tmp.jpg"
        try:
            if not extract_video_frame(image_path, frame):
                return None
            return thumb if _write_thumb(frame, thumb) else None
        finally:
            frame.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 缩略图失败绝不阻断入库，回落原图
        _log.warning("缩略图生成失败（回落原图） path=%s: %s", image_path, exc)
        return None


def _write_thumb(source: Path, thumb: Path) -> bool:
    """把一帧静态图归一成缩略图（长边 512 / JPEG q82，原子替换）。"""
    from PIL import Image, ImageOps

    with Image.open(source) as opened:
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
            return False
        tmp = thumb.with_suffix(".tmp")
        frame.save(tmp, format="JPEG", quality=_JPEG_QUALITY)
    tmp.replace(thumb)  # 原子替换（防并发/中断留残片）
    return True


def extract_video_frame(video_path: Path, dest: Path) -> bool:
    """ffmpeg 抽一帧到 `dest`（原分辨率，归一交给 `_write_thumb`）；定位不到 ffmpeg ⇒ False。"""
    from app.services.audio_merge import find_ffmpeg

    exe = find_ffmpeg()
    if not exe:
        _log.info("未定位到 ffmpeg，视频封面跳过（前端保持占位） path=%s", video_path)
        return False
    for seek in _FRAME_SEEK_LADDER:
        dest.unlink(missing_ok=True)
        command = [
            exe, "-y", "-loglevel", "error", "-ss", seek, "-i", str(video_path),
            "-frames:v", "1", "-update", "1", "-q:v", "2", str(dest),
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, timeout=_FRAME_TIMEOUT_SECONDS,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            return True
    return False


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
