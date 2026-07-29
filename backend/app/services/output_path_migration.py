"""输出图片根路径审查与资产迁移。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.config import CHROMA_DIR, DATA_DIR


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AssetRef:
    collection: str
    doc_id: str
    metadata: dict
    source: Path
    destination: Path
    old_url: str
    new_url: str


def _absolute(value: str) -> Path:
    return Path(os.path.abspath(os.path.expanduser((value or "").strip())))


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _relative_to(path: Path, root: Path) -> Path | None:
    try:
        if os.path.commonpath([_path_key(path), _path_key(root)]) != _path_key(root):
            return None
        rel = Path(os.path.relpath(path, root))
        return None if rel == Path(".") or ".." in rel.parts else rel
    except (ValueError, OSError):
        return None


def _roots(old_dir: str, new_dir: str) -> tuple[Path, Path]:
    if not (old_dir or "").strip() or not (new_dir or "").strip():
        raise MigrationError("原存放路径和新存放路径均不能为空")
    old_root, new_root = _absolute(old_dir), _absolute(new_dir)
    if _path_key(old_root) == _path_key(new_root):
        return old_root, new_root
    if _relative_to(new_root, old_root) is not None or _relative_to(old_root, new_root) is not None:
        raise MigrationError("新旧存放路径不能互相包含，请选择独立目录")
    return old_root, new_root


def _local_path(url: str) -> Path | None:
    if not url or "local-view" not in url:
        return None
    try:
        query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
        value = query.get("path", "")
        return _absolute(value) if value else None
    except (TypeError, ValueError):
        return None


def _with_local_path(url: str, path: Path) -> str:
    parsed = urlsplit(url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = [(key, str(path) if key == "path" else value) for key, value in pairs]
    if not any(key == "path" for key, _ in query):
        query.append(("path", str(path)))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _collections():
    import chromadb

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    for item in client.list_collections():
        name = item.name if hasattr(item, "name") else str(item)
        if name.startswith("repo_"):
            yield client.get_collection(name)


def _scan_asset_refs(old_root: Path, new_root: Path) -> list[AssetRef]:
    refs: list[AssetRef] = []
    try:
        collections = list(_collections())
    except Exception as exc:  # noqa: BLE001
        raise MigrationError(f"无法读取资产库索引：{exc}") from exc
    for collection in collections:
        try:
            data = collection.get(include=["metadatas"])
        except Exception as exc:  # noqa: BLE001
            raise MigrationError(f"无法读取资产库 {collection.name}：{exc}") from exc
        for index, metadata in enumerate(data.get("metadatas") or []):
            meta = dict(metadata or {})
            if meta.get("kind") != "generation":
                continue
            old_url = meta.get("image_url")
            source = _local_path(old_url) if isinstance(old_url, str) else None
            if source is None:
                continue
            relative = _relative_to(source, old_root)
            if relative is None:
                continue
            destination = new_root / relative
            refs.append(AssetRef(
                collection=collection.name,
                doc_id=(data.get("ids") or [])[index],
                metadata=meta,
                source=source,
                destination=destination,
                old_url=old_url,
                new_url=_with_local_path(old_url, destination),
            ))
    return refs


def _same_file(left: Path, right: Path) -> bool:
    if not left.is_file() or not right.is_file() or left.stat().st_size != right.stat().st_size:
        return False

    def digest(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()

    return digest(left) == digest(right)


def _unique_files(refs: list[AssetRef]) -> list[tuple[Path, Path]]:
    files: dict[str, tuple[Path, Path]] = {}
    for ref in refs:
        files.setdefault(_path_key(ref.source), (ref.source, ref.destination))
    return list(files.values())


def audit(old_dir: str, new_dir: str) -> dict[str, object]:
    old_root, new_root = _roots(old_dir, new_dir)
    if _path_key(old_root) == _path_key(new_root):
        return {"changed": False, "asset_count": 0, "file_count": 0,
                "missing_count": 0, "conflict_count": 0, "total_bytes": 0}
    refs = _scan_asset_refs(old_root, new_root)
    files = _unique_files(refs)
    existing_keys = {_path_key(source) for source, _ in files if source.is_file()}
    existing_refs = [ref for ref in refs if _path_key(ref.source) in existing_keys]
    existing_files = [(source, destination) for source, destination in files if source.is_file()]
    missing = len(files) - len(existing_files)
    conflicts = sum(
        1 for source, destination in existing_files
        if destination.exists() and not _same_file(source, destination)
    )
    total = sum(source.stat().st_size for source, _ in existing_files)
    return {
        "changed": True,
        "asset_count": len(existing_refs),
        "file_count": len(existing_files),
        "missing_count": missing,
        "conflict_count": conflicts,
        "total_bytes": total,
    }


def _copy_files(files: list[tuple[Path, Path]]) -> int:
    copied = 0
    for source, destination in files:
        if not source.is_file():
            raise MigrationError(f"资产文件不存在：{source}")
        if destination.exists():
            if _same_file(source, destination):
                continue
            raise MigrationError(f"新路径存在同名但内容不同的文件：{destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copy2(source, temp)
            if not _same_file(source, temp):
                raise MigrationError(f"资产文件复制校验失败：{source}")
            temp.replace(destination)
            copied += 1
        finally:
            if temp.exists():
                temp.unlink()
    return copied


def _update_index(refs: list[AssetRef]) -> list[tuple[object, list[str], list[dict]]]:
    import chromadb

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    grouped: dict[str, list[AssetRef]] = {}
    for ref in refs:
        grouped.setdefault(ref.collection, []).append(ref)
    applied: list[tuple[object, list[str], list[dict]]] = []
    try:
        for name, items in grouped.items():
            collection = client.get_collection(name)
            ids = [item.doc_id for item in items]
            old_metas = [item.metadata for item in items]
            new_metas = [{**item.metadata, "image_url": item.new_url} for item in items]
            collection.update(ids=ids, metadatas=new_metas)
            applied.append((collection, ids, old_metas))
    except Exception as exc:  # noqa: BLE001
        for collection, ids, old_metas in reversed(applied):
            try:
                collection.update(ids=ids, metadatas=old_metas)
            except Exception:
                pass
        raise MigrationError(f"更新资产库索引失败：{exc}") from exc
    return applied


def _rewrite_value(value, destinations: dict[str, Path]) -> tuple[object, int]:
    if isinstance(value, str):
        path = _local_path(value)
        destination = destinations.get(_path_key(path)) if path is not None else None
        return (_with_local_path(value, destination), 1) if destination is not None else (value, 0)
    if isinstance(value, list):
        changed, count = [], 0
        for item in value:
            next_value, n = _rewrite_value(item, destinations)
            changed.append(next_value)
            count += n
        return changed, count
    if isinstance(value, dict):
        changed, count = {}, 0
        for key, item in value.items():
            next_value, n = _rewrite_value(item, destinations)
            changed[key] = next_value
            count += n
        return changed, count
    return value, 0


def _rewrite_json_references(files: list[tuple[Path, Path]]) -> tuple[list[tuple[Path, bytes]], int]:
    destinations = {_path_key(source): destination for source, destination in files}
    candidates = [DATA_DIR / "user_state.json"]
    snapshots = DATA_DIR / "chat_snapshots"
    if snapshots.is_dir():
        candidates.extend(snapshots.glob("*.json"))
    backups: list[tuple[Path, bytes]] = []
    count = 0
    try:
        for path in candidates:
            if not path.is_file():
                continue
            raw = path.read_bytes()
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            changed, n = _rewrite_value(value, destinations)
            if not n:
                continue
            backups.append((path, raw))
            temp = path.with_suffix(path.suffix + ".migration.tmp")
            temp.write_text(json.dumps(changed, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
            count += n
    except Exception as exc:  # noqa: BLE001
        for path, raw in reversed(backups):
            path.write_bytes(raw)
        raise MigrationError(f"更新对话与仓库封面引用失败：{exc}") from exc
    return backups, count


def _rollback_index(applied: list[tuple[object, list[str], list[dict]]]) -> None:
    for collection, ids, old_metas in reversed(applied):
        try:
            collection.update(ids=ids, metadatas=old_metas)
        except Exception:
            pass


def migrate(old_dir: str, new_dir: str) -> dict[str, object]:
    old_root, new_root = _roots(old_dir, new_dir)
    if _path_key(old_root) == _path_key(new_root):
        return {"migrated_files": 0, "updated_assets": 0, "updated_references": 0,
                "delete_failures": 0, "skipped_missing": 0}
    all_refs = _scan_asset_refs(old_root, new_root)
    all_files = _unique_files(all_refs)
    existing_keys = {_path_key(source) for source, _ in all_files if source.is_file()}
    refs = [ref for ref in all_refs if _path_key(ref.source) in existing_keys]
    files = [(source, destination) for source, destination in all_files if source.is_file()]
    _copy_files(files)
    applied = _update_index(refs)
    backups: list[tuple[Path, bytes]] = []
    try:
        backups, reference_count = _rewrite_json_references(files)
    except Exception:
        _rollback_index(applied)
        raise

    delete_failures = 0
    for source, _ in files:
        try:
            source.unlink()
        except OSError:
            delete_failures += 1
    return {
        "migrated_files": len(files),
        "updated_assets": len(refs),
        "updated_references": reference_count,
        "delete_failures": delete_failures,
        "skipped_missing": len(all_files) - len(files),
    }
