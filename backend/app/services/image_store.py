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
from app.services.pathnames import safe_dir, safe_seg


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
        # local-view 指向本机后端（127.0.0.1:8010），必须绕过系统代理——
        # Windows 系统代理（如 Clash 127.0.0.1:7897）会把 localhost 请求转发出去导致 502。
        # 外部 URL 保留默认 opener（走系统代理，中转通路依赖它）。
        if is_local_view_url(src):
            import urllib.request
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(src, timeout=30) as r:
                data = r.read(20 * 1024 * 1024)  # 最大 20 MB，防超大文件撑爆内存
        else:
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


def save_image_named(output_dir: str, repo_id: str, *,
                     filename: str, subfolder: str = "", type: str = "output",
                     url: str = COMFYUI_BASE_URL, dest_stem: str = "") -> str:
    """角色 LoRA 生图落盘：用可读名（角色_轮次_序号）存到 <repo>/ 根目录，同名覆盖。

    与 save_local 的差异：文件名由 dest_stem 决定（保留中文角色名），同一槽位
    重新生成时覆盖同名文件 → URL 不变、内容更新。取图失败抛 ComfyError。
    """
    if not output_dir:
        raise ComfyError("未配置输出图片路径", 400)
    from app.services import repo_meta
    base = repo_meta.repo_folder(output_dir, repo_id)
    fn = Path(filename).name
    ext = fn.rsplit(".", 1)[1] if "." in fn else "png"
    stem = safe_dir(dest_stem) or "image"
    dest = base / f"{stem}.{safe_dir(ext) or 'png'}"
    try:
        data, _ctype = comfyui_client.fetch_view(url, filename, type, subfolder, timeout=30)
    except ComfyError:
        raise ComfyError("取原图失败", 502)
    tmp = base / f".{dest.name}.{uuid4().hex}.tmp"
    tmp.write_bytes(data)
    try:
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return str(dest)


def save_audio_local(output_dir: str, repo_id: str, *,
                     filename: str, subfolder: str = "", type: str = "output",
                     url: str = COMFYUI_BASE_URL, dest_stem: str = "") -> str:
    """把生成的配音分条存到 <repo>/voices/，文件名用 dest_stem（角色_轮次_句号）。

    配音是作品语音资产，物理落 voices 子夹，与用户上传的参考音轨同目录；文件名保留
    中文角色名（safe_dir 只挡 Windows 非法字符），便于按角色/轮次检索。
    幂等：同名已存在直接返回旧路径（同一槽位重跑不重复落盘）。
    """
    if not output_dir:
        raise ComfyError("未配置输出路径", 400)
    from app.services import repo_meta
    base = repo_meta.repo_folder(output_dir, repo_id) / "voices"
    base.mkdir(parents=True, exist_ok=True)
    fn = Path(filename).name
    ext = fn.rsplit(".", 1)[1] if "." in fn else "flac"
    ext = safe_dir(ext) or "flac"
    stem = safe_dir(dest_stem) or "voice"
    dest = base / f"{stem}.{ext}"
    if dest.exists():
        return str(dest)
    data, _ctype = comfyui_client.fetch_view(url, filename, type, subfolder, timeout=30)
    tmp = base / f".{dest.name}.{uuid4().hex}.tmp"
    tmp.write_bytes(data)
    try:
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return str(dest)


# ===== 上网素材：联网搜索下载的图片，存到 outputDir/_web_materials/ =====


def web_materials_dir(output_dir: str) -> Path:
    """上网素材目录：outputDir/_web_materials/。"""
    return Path(output_dir) / "_web_materials"


def save_web_material(output_dir: str, src: str, source_url: str = "", title: str = "") -> dict:
    """把联网搜索到的图片下载到 _web_materials/，返回 {path, url, source_url, title, filename}。

    安全链（M1.3）：
    - SSRF 防护：由 _from_src → validate_media_url 保证（拒绝私网/metadata/localhost）
    - 大小限制：_from_src 硬上限 20MB
    - 魔数校验：验证字节流确实是声明格式的图片，防文件伪装
    - 原子写：先写临时文件再 rename，防并发/中断导致残缺文件
    - 扩展名矫正：data URI 声明的扩展名若与魔数不匹配，以魔数为准
    """
    if not output_dir:
        raise ComfyError("未配置输出图片路径", 400)
    d = web_materials_dir(output_dir)
    d.mkdir(parents=True, exist_ok=True)
    data, ext = _from_src(src)

    # 魔数校验：字节流必须是合法图片格式
    from app.services.image_magic import validate_image_bytes
    try:
        detected = validate_image_bytes(data)
        # 扩展名矫正：若声明扩展名与魔数不一致，以魔数为准
        ext = detected
    except ValueError as e:
        raise ComfyError(f"图片格式校验失败：{e}", 400)

    # 原子写：先写临时文件，再 rename（防并发/中断导致残缺文件）
    temp_path = d / (_next_seq_name(d, ext) + ".tmp")
    try:
        temp_path.write_bytes(data)
        temp_path.chmod(0o644)
        final_name = temp_path.stem  # 去掉 .tmp 后缀
        dest = d / final_name
        # 若同名文件已存在（极小概率），追加随机后缀
        if dest.exists():
            dest = d / _next_seq_name(d, ext)
        temp_path.rename(dest)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise

    from app.services import view_urls
    return {
        "path": str(dest),
        "url": view_urls.local_view(str(dest)),
        "source_url": source_url,
        "title": title or dest.name,
        "filename": dest.name,
    }


def list_web_materials(output_dir: str) -> list[dict]:
    """列出 _web_materials/ 下所有图片。"""
    if not output_dir:
        return []
    d = web_materials_dir(output_dir)
    if not d.exists():
        return []
    from app.services import view_urls
    items = []
    for f in sorted(d.iterdir(), key=lambda x: x.name, reverse=True):
        if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            items.append({
                "path": str(f),
                "url": view_urls.local_view(str(f)),
                "source_url": "",
                "title": f.name,
                "filename": f.name,
            })
    return items


def delete_web_material(output_dir: str, filename: str) -> bool:
    """删除 _web_materials/ 下的指定文件。"""
    if not output_dir:
        return False
    d = web_materials_dir(output_dir)
    safe_name = Path(filename).name
    target = d / safe_name
    if target.exists() and target.is_file():
        target.unlink()
        return True
    return False


# ===== 参考图：聊天上传到 <repo>/reference/ 的图片，供画布自动导入 =====


def list_reference_images(output_dir: str, repo_id: str) -> list[dict]:
    """列出 <repo_id>/reference/ 下所有图片文件。"""
    if not output_dir or not repo_id:
        return []
    from app.services import repo_meta
    base = repo_meta.repo_folder(output_dir, repo_id)
    d = base / "reference"
    if not d.exists():
        return []
    from app.services import view_urls
    items = []
    for f in sorted(d.iterdir(), key=lambda x: x.name, reverse=True):
        if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"):
            items.append({
                "path": str(f),
                "url": view_urls.local_view(str(f)),
                "title": f.name,
                "filename": f.name,
            })
    return items
