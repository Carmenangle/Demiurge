"""把图片留存到本地 outputDir（全分辨率，不降质）。

两种来源的字节获取与文件名推断集中于此；ComfyUI /view 取图仍走 comfyui_client。
纯逻辑（文件名清洗、data URI 解码、扩展名推断）可脱离 HTTP 单测。
"""
from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

from app.config import COMFYUI_BASE_URL
from app.services import comfyui_client
from app.services.comfyui_client import ComfyError
from app.services.pathnames import safe_seg


def _next_seq_name(base: Path, ext: str) -> str:
    """时间戳 + 随机后缀命名，如 20260703_153012_874321_a1b2c3d4.png。

    不再靠「扫描磁盘取 max+1」——那套机制三处脆弱：删末尾图后 max 回退→新图撞旧编号；
    删中间图留空洞；用户手动改名会干扰 max 计算。时间戳前缀单调递增、永不复用、
    完全不依赖磁盘现状，删任何图/手动改名都不影响后续命名。随机后缀再防同一微秒内并发撞名。
    字典序 = 时间序，前端按文件名排序仍从新到旧（且已额外落 created_at 作权威排序，见 index_generation）。
    """
    ext = safe_seg(ext) or "png"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return f"{ts}_{uuid4().hex[:8]}.{ext}"



def _from_src(src: str) -> tuple[bytes, str]:
    """通用模式：data URI 解码或外部 URL 下载，返回 (data, 扩展名)。"""
    if src.startswith("data:"):
        try:
            header, b64 = src.split(",", 1)
            data = base64.b64decode(b64)
        except Exception as e:
            raise ComfyError(f"解析 data URI 失败：{e}", 400)
        ext = "png"
        if "image/" in header:
            ext = header.split("image/")[1].split(";")[0] or "png"
        elif "video/" in header:
            ext = header.split("video/")[1].split(";")[0] or "mp4"
        return data, ext
    # 校验外部 URL 防 SSRF；本应用 local-view 代理地址豁免（已在后端可信路径落盘）
    from app.services.url_guard import is_local_view_url, validate_media_url
    if not is_local_view_url(src):
        try:
            validate_media_url(src)
        except ValueError as e:
            raise ComfyError(str(e), 400)
    try:
        with urlopen(src, timeout=30) as r:
            data = r.read(20 * 1024 * 1024)  # 最大 20 MB，防超大文件撑爆内存
    except ComfyError:
        raise
    except Exception as e:
        raise ComfyError(f"下载图片失败：{e}", 502)
    tail = Path(src.split("?")[0]).name
    ext = tail.rsplit(".", 1)[1] if "." in tail else "png"
    return data, ext


def save_local(
    output_dir: str,
    repo_id: str = "home",
    *,
    src: str = "",
    filename: str = "",
    subfolder: str = "",
    type: str = "output",
    url: str = COMFYUI_BASE_URL,
    subdir: str = "",
    idempotency_key: str = "",
) -> str:
    """存原图到 outputDir/<repo_id>/，返回落盘路径。

    - src 非空：通用模式（data URI / 外部 URL）。
    - 否则用 filename 从 ComfyUI /view 取原图。
    - subdir 非空：落到 <repo_id>/<subdir>/ 子文件夹（如用户上传的参考图 → reference/）。
    校验失败/取图失败抛 ComfyError，路由层转 HTTPException。
    """
    if not output_dir:
        raise ComfyError("未配置输出图片路径", 400)
    from app.services import repo_meta
    base = repo_meta.repo_folder(output_dir, repo_id)  # 文件夹名=仓库名(保中文)，并写 _repo.json
    if subdir:
        base = base / safe_seg(subdir)
        base.mkdir(parents=True, exist_ok=True)
    if idempotency_key and filename and not src:
        fn = Path(filename).name
        ext = fn.rsplit(".", 1)[1] if "." in fn else "png"
        suffix = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
        existing = base / f"workflow_{suffix}.{safe_seg(ext) or 'png'}"
        if existing.exists():
            return str(existing)
    if src:
        data, ext = _from_src(src)
    else:
        if not filename:
            raise ComfyError("缺少 filename 或 src", 400)
        try:
            data, _ctype = comfyui_client.fetch_view(url, filename, type, subfolder, timeout=30)
        except ComfyError:
            raise ComfyError("取原图失败", 502)
        fn = Path(filename).name
        ext = fn.rsplit(".", 1)[1] if "." in fn else "png"
    # 上传的参考图落 reference/ 子夹，与生成图（根目录）分开。子夹名做安全化防路径穿越。
    # 统一按本仓库文件夹自己的顺序编号命名——不沿用 ComfyUI 的 uid_编号(会随重启从头计数、
    # 删图后新图撞旧编号导致覆盖)。每个仓库独立编号，跨来源都不撞名。
    if idempotency_key:
        suffix = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
        dest = base / f"workflow_{suffix}.{safe_seg(ext) or 'png'}"
        if dest.exists():
            return str(dest)
        tmp = base / f".{dest.name}.{uuid4().hex}.tmp"
        tmp.write_bytes(data)
        try:
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                tmp.unlink()
    else:
        dest = base / _next_seq_name(base, ext)
        dest.write_bytes(data)
    return str(dest)
