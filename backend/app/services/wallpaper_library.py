"""液态玻璃壁纸：扫描用户壁纸库、生成缩略图，并管理「自定义壁纸」存储位置。

设计要点：
- 扫描/缩略图只读用户壁纸库：缩略图缓存写 `data/wallpaper_thumbs/`，绝不往壁纸目录塞文件；
- 自定义壁纸写入独立「存储位置」（默认 `data/wallpapers/`），更改位置时迁移其中的媒体文件；
- 视频缩略图 = ffmpeg 抽首帧（复用 audio_merge 的 ffmpeg 定位，便携 ComfyUI 也能命中）；
- 缓存键 = 路径 + mtime_ns + size，源文件更新自动失效；生成失败静默返回 None，
  前端回落为视频元素/占位。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from app.config import DATA_DIR
from app.services.audio_merge import find_ffmpeg

_log = logging.getLogger("uvicorn.error")

IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "avif", "tiff"}
# 只收浏览器能直接解码的容器，避免扫出一堆点开黑屏的壁纸
VIDEO_EXTS = {"mp4", "webm", "mov", "m4v", "ogv"}

_MAX_FILES = 20000
_MAX_DEPTH = 8
_PAGE_SIZE_MAX = 60
_THUMB_EDGE = 480
_THUMB_VERSION = "2"  # 取帧策略变更时递增，强制旧缓存失效

CACHE_DIR = DATA_DIR / "wallpaper_thumbs"

# 「自定义壁纸」存储位置：只收参考界面的三种格式；空设置=默认目录
DEFAULT_STORE_DIR = DATA_DIR / "wallpapers"
_STORE_EXTS = {"jpg", "jpeg", "png", "mp4"}
_STORE_FORMATS = ("jpg", "jpeg", "png", "mp4")
_STORE_NAME_MAX = 80
_STORE_MOVE_MAX = 2000

# Wallpaper Engine 的 Steam 创意工坊 AppID；作品目录下用 project.json 描述标题/类型/预览图
WE_WORKSHOP_APP_ID = "431960"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.Lock()
        return lock


def _kind_of(suffix: str) -> str | None:
    ext = suffix.lower().lstrip(".")
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    return None


def _walk_media(root: Path) -> tuple[list[tuple[Path, str]], bool]:
    """递归收集媒体文件（跳过隐藏目录/缓存目录），返回 (文件, 是否截断)。"""
    found: list[tuple[Path, str]] = []
    truncated = False
    root_depth = len(root.parts)
    for current, dirs, files in os.walk(root, followlinks=False):
        cur = Path(current)
        depth = len(cur.parts) - root_depth
        if depth >= _MAX_DEPTH:
            dirs[:] = []
        dirs[:] = sorted(
            d for d in dirs
            if not d.startswith(".") and not Path(current, d).is_symlink()
        )
        for name in sorted(files):
            if name.startswith("."):
                continue
            kind = _kind_of(Path(name).suffix)
            if kind is None:
                continue
            found.append((cur / name, kind))
            if len(found) >= _MAX_FILES:
                truncated = True
                return found, truncated
    return found, truncated


def _matches_query(query: str, *haystacks: str) -> bool:
    text = (query or "").strip().casefold()
    if not text:
        return True
    return any(text in (value or "").casefold() for value in haystacks)


def scan(
    folder: str, *, kind: str = "all", page: int = 1, page_size: int = 21,
    hidden: list[str] | tuple[str, ...] = (), hidden_only: bool = False,
    query: str = "",
) -> dict:
    """扫描壁纸文件夹并分页返回。kind: all/video/image；hidden 命中的路径不出现（或只看隐藏）。"""
    raw = (folder or "").strip().strip('"')
    if not raw:
        raise ValueError("请先填写壁纸存放地址")
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise ValueError(f"文件夹不存在或不可访问：{raw}")
    root = root.resolve()

    entries, truncated = _walk_media(root)
    if kind in ("video", "image"):
        entries = [e for e in entries if e[1] == kind]
    entries.sort(key=lambda item: item[0].relative_to(root).as_posix().casefold())

    hidden_keys = {
        os.path.normcase(str(Path(p.strip().strip('"')).resolve()))
        for p in hidden
        if isinstance(p, str) and p.strip()
    }
    hidden_entries = [e for e in entries if os.path.normcase(str(e[0])) in hidden_keys]
    visible_total = len(entries) - len(hidden_entries)
    selected = (
        hidden_entries if hidden_only
        else [e for e in entries if os.path.normcase(str(e[0])) not in hidden_keys]
    )
    selected = [
        e for e in selected
        if _matches_query(query, e[0].relative_to(root).as_posix(), e[0].stem)
    ]

    total = len(selected)
    size = max(1, min(_PAGE_SIZE_MAX, int(page_size or 21)))
    pages = max(1, (total + size - 1) // size)
    current = max(1, min(pages, int(page or 1)))
    start = (current - 1) * size
    items = []
    for path, item_kind in selected[start:start + size]:
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append({
            "path": str(path),
            "name": path.relative_to(root).with_suffix("").as_posix(),
            "kind": item_kind,
            "animated": item_kind == "video" or path.suffix.lower() == ".gif",
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
        })
    return {
        "folder": str(root),
        "query": (query or "").strip(),
        "total": total,
        "visible_total": visible_total,
        "hidden_total": len(hidden_entries),
        "page": current,
        "pages": pages,
        "page_size": size,
        "truncated": truncated,
        "items": items,
    }


def _registry_steam_roots() -> list[Path]:
    """Windows 注册表登记的 Steam 安装目录（HKCU 的 SteamPath / HKLM 的 InstallPath）。"""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows 分支
        return []
    lookups = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    )
    roots: list[Path] = []
    for hive, key, value in lookups:
        try:
            with winreg.OpenKey(hive, key) as handle:
                raw, _ = winreg.QueryValueEx(handle, value)
        except OSError:
            continue
        text = str(raw or "").strip()
        if text:
            roots.append(Path(text))
    return roots


def _vdf_library_paths(steam_root: Path) -> list[Path]:
    """解析 libraryfolders.vdf 里的 "path" 项（VDF 用 \\\\ 转义反斜杠）。"""
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    paths: list[Path] = []
    for match in re.finditer(r'"path"\s+"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE):
        raw = match.group(1).replace("\\\\", "\\").strip()
        if raw:
            paths.append(Path(raw))
    return paths


def steam_libraries() -> list[Path]:
    """Steam 安装目录 + 所有库目录（注册表 + libraryfolders.vdf，去重且只保留存在的）。"""
    candidates: list[Path] = list(_registry_steam_roots())
    steam_env = os.environ.get("STEAM_PATH", "").strip()
    if steam_env:
        candidates.append(Path(steam_env))
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
        base = os.environ.get(env, "").strip()
        if base:
            candidates.append(Path(base) / "Steam")
    for root in list(candidates):
        candidates.extend(_vdf_library_paths(root))

    seen: set[str] = set()
    libraries: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if not resolved.is_dir():
            continue
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        libraries.append(resolved)
    return libraries


def workshop_roots() -> list[Path]:
    """所有 Steam 库下的 Wallpaper Engine 创意工坊目录（steamapps/workshop/content/431960）。"""
    seen: set[str] = set()
    roots: list[Path] = []
    for library in steam_libraries():
        candidate = library / "steamapps" / "workshop" / "content" / WE_WORKSHOP_APP_ID
        try:
            if not candidate.is_dir():
                continue
        except OSError:
            continue
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        roots.append(candidate)
    return roots


def _child_file(base: Path, raw: str) -> Path | None:
    """把 project.json 里的相对路径解析为 base 内的真实文件（拒绝绝对路径/越界/不存在）。"""
    text = (raw or "").strip().strip('"').replace("\\", "/")
    if not text:
        return None
    rel = Path(text)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    try:
        base_resolved = base.resolve()
        resolved = (base / rel).resolve()
    except OSError:
        return None
    if resolved != base_resolved and base_resolved not in resolved.parents:
        return None
    return resolved if resolved.is_file() else None


_PREVIEW_EXTS = ("gif", "png", "jpg", "jpeg", "webp", "bmp")


def _workshop_preview(item_dir: Path, project: dict) -> Path | None:
    """作品自带预览图：project.json 的 preview，或工坊标准的 preview.*。

    工坊预览普遍只有 160~200px，仅作列表封面；scene/web 无法在浏览器渲染时
    它也是唯一可展示的画面，前端据此标注「低清预览」。
    """
    preview = _child_file(item_dir, str(project.get("preview") or ""))
    if preview is not None and _kind_of(preview.suffix) == "image":
        return preview
    for ext in _PREVIEW_EXTS:
        candidate = _child_file(item_dir, f"preview.{ext}")
        if candidate is not None:
            return candidate
    return None


def _workshop_media(
    item_dir: Path, project: dict, preview: Path | None,
) -> tuple[Path, bool] | None:
    """返回 (用于桌面展示的媒体, 是否只是静态预览)。

    视频/图片作品直接用 file；scene 的媒体在 `scene.pkg` 里（`project.json` 的 file 指向
    包内 `scene.json`，磁盘上不存在），web/application 也没有可直接展示的媒体文件，
    故这些一律回落作品预览图作为展示位；scene 能否页面内实时渲染由 `scene_pkg` 单独标注，
    由前端按该 id 取包解码，后端不解析包格式。
    """
    media = _child_file(item_dir, str(project.get("file") or ""))
    if media is not None and _kind_of(media.suffix) is not None:
        return media, False
    if preview is not None:
        return preview, True
    return None


_WE_SCENE_ID_RE = re.compile(r"^\d{1,20}$")


def scene_package(workshop_id: str) -> Path:
    """工坊 scene 作品的打包文件 `scene.pkg`（实时渲染的唯一数据入口）。

    `project.json` 的 `file` 指向包内的 `scene.json`，磁盘上并不存在，所以 scene 作品
    只有这一个可读的媒体；后端只负责把它原样交给前端解码，不解析包格式。
    id 必须是纯数字（Steam 发布 id），且落在已发现的工坊根下，越界无从构造。
    """
    ident = str(workshop_id or "").strip()
    if not _WE_SCENE_ID_RE.match(ident):
        raise ValueError("工坊作品 id 不合法")
    for root in workshop_roots():
        candidate = root / ident / "scene.pkg"
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    raise ValueError("该作品没有可用的 scene.pkg")


def has_scene_package(item_dir: Path, we_type: str) -> bool:
    """scene 类型且包文件存在时，前端才可能实时渲染；否则继续回落静态预览。"""
    if we_type != "scene":
        return False
    try:
        return (item_dir / "scene.pkg").is_file()
    except OSError:
        return False


def _image_size(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:  # noqa: BLE001 [EXC-留痕] 预览尺寸只做标注，读不出不阻断扫描
        return None


def _workshop_items() -> tuple[list[dict], list[Path]]:
    roots = workshop_roots()
    items: list[dict] = []
    seen: set[str] = set()
    for root in roots:
        try:
            children = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)
        except OSError:
            continue
        for item_dir in children:
            project_path = item_dir / "project.json"
            if not project_path.is_file():
                continue
            try:
                project = json.loads(project_path.read_text(encoding="utf-8-sig", errors="replace"))
            except (OSError, ValueError):
                continue
            if not isinstance(project, dict):
                continue
            preview = _workshop_preview(item_dir, project)
            media = _workshop_media(item_dir, project, preview)
            if media is None:
                continue
            path, preview_only = media
            kind = _kind_of(path.suffix)
            if kind is None:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            key = os.path.normcase(str(path))
            if key in seen:
                continue
            seen.add(key)
            title = str(project.get("title") or "").strip() or item_dir.name
            preview_size = _image_size(preview) if preview is not None else None
            items.append({
                "path": str(path),
                "name": title,
                "title": title,
                "folder": str(item_dir),
                "kind": kind,
                "animated": kind == "video" or path.suffix.lower() == ".gif",
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
                "source": "workshop",
                "workshop_id": item_dir.name,
                "we_type": str(project.get("type") or "").strip().lower(),
                "preview_only": preview_only,
                "scene_pkg": has_scene_package(item_dir, str(project.get("type") or "").strip().lower()),
                "preview": str(preview) if preview is not None else "",
                "preview_width": preview_size[0] if preview_size else 0,
                "preview_height": preview_size[1] if preview_size else 0,
            })
    items.sort(key=lambda item: (item["title"].casefold(), item["path"].casefold()))
    return items, roots


def scan_workshop(
    *, kind: str = "all", page: int = 1, page_size: int = 21,
    hidden: list[str] | tuple[str, ...] = (), hidden_only: bool = False,
    query: str = "",
) -> dict:
    """扫描 Wallpaper Engine 创意工坊（自动发现 Steam 库），分页语义与 scan 一致。"""
    items, roots = _workshop_items()
    if kind in ("video", "image"):
        items = [item for item in items if item["kind"] == kind]

    hidden_keys = {
        os.path.normcase(str(Path(p.strip().strip('"')).resolve()))
        for p in hidden
        if isinstance(p, str) and p.strip()
    }
    hidden_items = [item for item in items if os.path.normcase(item["path"]) in hidden_keys]
    visible_total = len(items) - len(hidden_items)
    selected = (
        hidden_items if hidden_only
        else [item for item in items if os.path.normcase(item["path"]) not in hidden_keys]
    )
    selected = [
        item for item in selected
        if _matches_query(query, item["title"], item["path"], item["workshop_id"])
    ]

    total = len(selected)
    size = max(1, min(_PAGE_SIZE_MAX, int(page_size or 21)))
    pages = max(1, (total + size - 1) // size)
    current = max(1, min(pages, int(page or 1)))
    start = (current - 1) * size
    return {
        "folder": str(roots[0]) if roots else "",
        "roots": [str(root) for root in roots],
        "found": bool(roots),
        "query": (query or "").strip(),
        "total": total,
        "visible_total": visible_total,
        "hidden_total": len(hidden_items),
        "page": current,
        "pages": pages,
        "page_size": size,
        "truncated": False,
        "items": selected[start:start + size],
    }


def _clean_path_text(raw: str) -> str:
    return (raw or "").strip().strip('"').strip()


def resolve_store_dir(raw: str, *, create: bool = False) -> Path:
    """解析自定义壁纸存储位置；空串=默认目录，支持 ~ 与环境变量。"""
    text = _clean_path_text(raw)
    if not text:
        path = DEFAULT_STORE_DIR
    else:
        path = Path(os.path.expandvars(os.path.expanduser(text)))
        if not path.is_absolute():
            raise ValueError("存储位置需为绝对路径，或使用 ~ 表示用户主目录")
    try:
        path = path.resolve()
    except OSError as exc:
        raise ValueError(f"存储位置无效：{text}") from exc
    if create:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"无法创建存储位置：{path}") from exc
        if not path.is_dir():
            raise ValueError("存储位置不是文件夹")
    return path


def _is_store_file(path: Path) -> bool:
    try:
        return path.is_file() and path.suffix.lower().lstrip(".") in _STORE_EXTS
    except OSError:
        return False


def count_store(root: Path) -> int:
    if not root.is_dir():
        return 0
    try:
        return sum(1 for child in root.iterdir() if _is_store_file(child))
    except OSError:
        return 0


def store_info(raw: str) -> dict:
    """返回解析后的绝对路径、默认目录与已上传数量。"""
    path = resolve_store_dir(raw)
    text = _clean_path_text(raw)
    return {
        "dir": str(path),
        "raw": text,
        "default": str(DEFAULT_STORE_DIR.resolve()),
        "is_default": not text,
        "count": count_store(path),
        "formats": list(_STORE_FORMATS),
    }


def _safe_store_name(filename: str) -> str:
    base = Path((filename or "").replace("\\", "/")).name
    suffix = Path(base).suffix.lower()
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(base).stem).strip(" .")[:_STORE_NAME_MAX]
    return f"{stem or 'wallpaper'}{suffix}"


def _unique_dest(root: Path, name: str) -> Path:
    dest = root / name
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    for index in range(2, 1000):
        candidate = root / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    return root / f"{uuid4().hex}{suffix}"


def save_upload(stream: BinaryIO, filename: str, raw_dir: str) -> dict:
    """把选择的自定义壁纸写入存储位置；重名自动加序号，空文件拒绝。"""
    suffix = Path((filename or "").replace("\\", "/")).suffix.lower().lstrip(".")
    if suffix not in _STORE_EXTS:
        raise ValueError("格式仅限 JPG / PNG / MP4")
    root = resolve_store_dir(raw_dir, create=True)
    dest = _unique_dest(root, _safe_store_name(filename))
    try:
        with dest.open("wb") as handle:
            shutil.copyfileobj(stream, handle)
    except OSError as exc:
        dest.unlink(missing_ok=True)
        raise ValueError(f"保存壁纸失败：{exc}") from exc
    if not dest.is_file() or dest.stat().st_size <= 0:
        dest.unlink(missing_ok=True)
        raise ValueError("上传文件为空")
    return {
        "ok": True,
        "path": str(dest),
        "name": Path((filename or "").replace("\\", "/")).name or dest.name,
        "count": count_store(root),
    }


def migrate_store(from_raw: str, to_raw: str) -> dict:
    """更改存储位置：迁移旧位置中的直接媒体文件，返回旧→新路径映射。"""
    source = resolve_store_dir(from_raw)
    target = resolve_store_dir(to_raw)
    if os.path.normcase(str(source)) == os.path.normcase(str(target)):
        return {
            "ok": True, "from": str(source), "to": str(target), "mapping": {},
            "moved": 0, "count": count_store(target), "skipped": [], "truncated": False,
        }
    if source in target.parents or target in source.parents:
        raise ValueError("新存储位置不能是原位置的父目录或子目录")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"无法创建存储位置：{target}") from exc
    if not target.is_dir():
        raise ValueError("存储位置不是文件夹")

    mapping: dict[str, str] = {}
    skipped: list[str] = []
    truncated = False
    if source.is_dir():
        try:
            files = sorted(
                (child for child in source.iterdir() if _is_store_file(child)),
                key=lambda child: child.name.casefold(),
            )
        except OSError as exc:
            raise ValueError(f"读取原存储位置失败：{exc}") from exc
        if len(files) > _STORE_MOVE_MAX:
            files = files[:_STORE_MOVE_MAX]
            truncated = True
        for file in files:
            dest = _unique_dest(target, file.name)
            try:
                shutil.move(str(file), str(dest))
            except OSError as exc:
                dest.unlink(missing_ok=True)
                skipped.append(f"{file.name}: {exc}")
                continue
            mapping[str(file)] = str(dest)
    return {
        "ok": True,
        "from": str(source),
        "to": str(target),
        "mapping": mapping,
        "moved": len(mapping),
        "count": count_store(target),
        "skipped": skipped,
        "truncated": truncated,
    }


def resolve_wallpaper(folder: str, path: str) -> Path:
    """校验 path 确实是 folder 内的媒体文件（缩略图端点用，防越界读文件）。"""
    root_raw = (folder or "").strip().strip('"')
    if not root_raw:
        raise ValueError("缺少壁纸存放地址")
    root = Path(root_raw).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("壁纸文件夹不存在")
    target = Path((path or "").strip().strip('"')).expanduser().resolve()
    if not target.is_file():
        raise ValueError("壁纸文件不存在")
    if target != root and root not in target.parents:
        raise ValueError("壁纸不在指定文件夹内")
    if _kind_of(target.suffix) is None:
        raise ValueError("不支持的壁纸格式")
    return target


def _cache_path(source: Path) -> Path:
    stat = source.stat()
    key = hashlib.sha1(
        f"{source}|{stat.st_mtime_ns}|{stat.st_size}|{_THUMB_VERSION}".encode("utf-8")
    ).hexdigest()
    return CACHE_DIR / f"{key}.jpg"


def thumbnail(source: Path) -> Path | None:
    """生成/复用缩略图；失败返回 None（调用方回落）。"""
    try:
        if not source.is_file():
            return None
        kind = _kind_of(source.suffix)
        if kind is None:
            return None
        dest = _cache_path(source)
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        with _lock_for(dest.name):
            if dest.is_file() and dest.stat().st_size > 0:
                return dest
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".tmp.jpg")
            ok = _image_thumb(source, tmp) if kind == "image" else _video_thumb(source, tmp)
            if not ok or not tmp.is_file() or tmp.stat().st_size == 0:
                tmp.unlink(missing_ok=True)
                return None
            tmp.replace(dest)
            return dest
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 缩略图失败不阻断壁纸库浏览
        _log.warning("壁纸缩略图生成失败（回落） path=%s: %s", source, exc)
        return None


def _image_thumb(source: Path, dest: Path) -> bool:
    from PIL import Image, ImageOps

    with Image.open(source) as opened:
        img = ImageOps.exif_transpose(opened)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.getchannel("A"))
            frame = bg
        else:
            frame = img if img.mode == "L" else img.convert("RGB")
        frame.thumbnail((_THUMB_EDGE, _THUMB_EDGE))
        if frame.width <= 0 or frame.height <= 0:
            return False
        frame.save(dest, format="JPEG", quality=82)
    return True


def _video_thumb(source: Path, dest: Path) -> bool:
    exe = find_ffmpeg()
    if not exe:
        return False
    # 0.4s 往往还在黑场/淡入（例如 60fps 长视频），先取 5s 更接近有效画面
    for seek in ("5", "1.5", "0.4", "0"):
        cmd = [
            exe, "-y", "-loglevel", "error",
            "-ss", seek, "-i", str(source),
            "-frames:v", "1", "-update", "1",
            "-vf", f"scale={_THUMB_EDGE}:-2",
            "-q:v", "4", str(dest),
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            return True
    return False
