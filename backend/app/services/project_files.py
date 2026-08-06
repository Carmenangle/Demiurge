"""编辑模式的作品文件能力：所有路径严格限制在当前小仓库目录内。"""
from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

from app.services import repo_meta

MAX_TEXT_BYTES = 1_048_576
MAX_PNG_BYTES = 20 * 1024 * 1024
MAX_LIST_ENTRIES = 500
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ProjectFileError(ValueError):
    """作品文件请求不合法或无法完成。"""


def project_root(repo_id: str) -> Path:
    output_dir = repo_meta.output_dir_from_state()
    if not output_dir:
        raise ProjectFileError("请先在设置中配置仓库文件夹")
    if not (repo_id or "").strip() or repo_id == "home":
        raise ProjectFileError("编辑模式需要先选择一个作品")
    return repo_meta.repo_folder(output_dir, repo_id).resolve()


def _relative_path(path: str, *, allow_root: bool = False) -> Path:
    raw = (path or "").strip()
    if not raw:
        if allow_root:
            return Path()
        raise ProjectFileError("文件路径不能为空")
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.drive or PureWindowsPath(raw).anchor:
        raise ProjectFileError("只允许使用作品目录内的相对路径")
    if any(part in {"..", "."} for part in candidate.parts):
        raise ProjectFileError("路径不得包含 . 或 ..")
    return candidate


def _resolve(root: Path, path: str, *, allow_root: bool = False) -> Path:
    relative = _relative_path(path, allow_root=allow_root)
    target = (root / relative).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ProjectFileError("路径超出当前作品目录") from exc
    return target


def _require_text_file(path: Path) -> None:
    if not path.is_file():
        raise ProjectFileError("文件不存在")
    size = path.stat().st_size
    if size > MAX_TEXT_BYTES:
        raise ProjectFileError(f"文件超过 {MAX_TEXT_BYTES} 字节限制")


def _read_utf8(path: Path) -> str:
    _require_text_file(path)
    data = path.read_bytes()
    if b"\x00" in data:
        raise ProjectFileError("不支持读取二进制文件")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProjectFileError("文件不是 UTF-8 文本") from exc


def list_files(root: Path, path: str = "", *, recursive: bool = True) -> list[str]:
    base = _resolve(root, path, allow_root=True)
    if not base.is_dir():
        raise ProjectFileError("目录不存在")
    iterator = base.rglob("*") if recursive else base.iterdir()
    items: list[str] = []
    for item in iterator:
        resolved = item.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        if not item.is_file():
            continue
        items.append(item.relative_to(root).as_posix())
        if len(items) >= MAX_LIST_ENTRIES:
            break
    return sorted(items)


def read_text(root: Path, path: str) -> str:
    return _read_utf8(_resolve(root, path))


def file_exists(root: Path, path: str) -> bool:
    return _resolve(root, path).is_file()


def write_text(root: Path, path: str, content: str) -> int:
    data = content.encode("utf-8")
    if len(data) > MAX_TEXT_BYTES:
        raise ProjectFileError(f"内容超过 {MAX_TEXT_BYTES} 字节限制")
    target = _resolve(root, path)
    if target.exists() and not target.is_file():
        raise ProjectFileError("目标不是文件")
    parent = target.parent.resolve(strict=False)
    try:
        parent.relative_to(root)
    except ValueError as exc:
        raise ProjectFileError("目标目录超出当前作品目录") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes(data)
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return len(data)


def read_png(root: Path, path: str) -> bytes:
    target = _resolve(root, path)
    if not target.is_file():
        raise ProjectFileError("PNG 文件不存在")
    data = target.read_bytes()
    if len(data) > MAX_PNG_BYTES:
        raise ProjectFileError(f"PNG 超过 {MAX_PNG_BYTES} 字节限制")
    if not data.startswith(PNG_SIGNATURE):
        raise ProjectFileError("文件不是有效的 PNG")
    return data


def write_png(root: Path, path: str, data: bytes) -> int:
    if len(data) > MAX_PNG_BYTES:
        raise ProjectFileError(f"PNG 超过 {MAX_PNG_BYTES} 字节限制")
    if not data.startswith(PNG_SIGNATURE):
        raise ProjectFileError("附件不是有效的 PNG")
    target = _resolve(root, path)
    if target.suffix.casefold() != ".png":
        raise ProjectFileError("图片目标必须使用 .png 扩展名")
    if target.exists() and not target.is_file():
        raise ProjectFileError("目标不是文件")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes(data)
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return len(data)


def replace_text(
    root: Path, path: str, old_text: str, new_text: str, *, replace_all: bool = False,
) -> int:
    if not old_text:
        raise ProjectFileError("待替换文本不能为空")
    target = _resolve(root, path)
    original = _read_utf8(target)
    count = original.count(old_text)
    if count == 0:
        raise ProjectFileError("文件中未找到待替换文本")
    if count > 1 and not replace_all:
        raise ProjectFileError(f"待替换文本出现 {count} 次；请提供更精确的文本或允许全部替换")
    updated = original.replace(old_text, new_text, -1 if replace_all else 1)
    write_text(root, path, updated)
    return count if replace_all else 1
