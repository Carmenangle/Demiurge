"""完整世界状态快照与反事实分支。

快照是不可变的；恢复只允许写入空的新仓库，绝不覆盖正式时间线。
仓库文件包含消息、角色状态、纪要、表格、世界书、时序事实、角色认知、
Narrative CI 与视觉偏好；Chroma 中属于该仓库的集合另行导出。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import Any

from app.config import CHROMA_DIR, DATA_DIR
from app.services import repo_meta
from app.services.pathnames import safe_seg


SNAPSHOT_ROOT = DATA_DIR / "scenario_snapshots"
EXPERIMENT_ROOT = DATA_DIR / "scenario_experiments"
SNAPSHOT_VERSION = 2
_SNAPSHOT_LOCK = RLock()


def _snapshot_dir(repo_id: str, snapshot_id: str) -> Path:
    return SNAPSHOT_ROOT / safe_seg(repo_id, strip=False) / safe_seg(snapshot_id, strip=False)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _previous_file_index(repo_id: str) -> dict[str, dict[str, Any]]:
    snapshots = list_snapshots(repo_id)
    if not snapshots:
        return {}
    return {
        str(item.get("path") or ""): item
        for item in snapshots[0].get("files") or []
        if isinstance(item, dict) and item.get("path")
    }


def _copy_consistent(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".db":
        with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
        return
    if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm"}:
        try:
            os.link(source, target)
            return
        except OSError:
            pass
    shutil.copy2(source, target)


def _collection_names(repo_id: str) -> list[str]:
    rid = safe_seg(repo_id, strip=False)
    # rag_store/worldbook/visual_asset_index 各自拥有名称协议；这里仅负责快照 Adapter。
    return [f"repo_{rid}", f"worldbook_{rid}", f"asset_visual_{rid}"]


def _json_value(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return value


def _export_collections(repo_id: str, target: Path) -> list[dict[str, Any]]:
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    except Exception:
        return []
    exported: list[dict[str, Any]] = []
    for name in _collection_names(repo_id):
        try:
            collection = client.get_collection(name)
            raw = collection.get(include=["documents", "metadatas", "embeddings"])
        except Exception:
            continue
        embeddings = raw.get("embeddings")
        payload = {
            "name": name,
            "ids": raw.get("ids") or [],
            "documents": raw.get("documents") or [],
            "metadatas": raw.get("metadatas") or [],
            "embeddings": _json_value(embeddings if embeddings is not None else []),
        }
        out = target / f"{name}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        exported.append({"name": name, "count": len(payload["ids"]), "file": out.name})
    return exported


def _create_snapshot_locked(output_dir: str, repo_id: str, *, turn: int = 0,
                            label: str = "", dedupe_key: str = "") -> dict[str, Any]:
    if not output_dir or not repo_id:
        raise ValueError("缺少 output_dir 或 repo_id")
    if dedupe_key:
        existing = next((item for item in list_snapshots(repo_id)
                         if item.get("dedupe_key") == dedupe_key), None)
        if existing:
            return existing
    source = repo_meta.repo_folder(output_dir, repo_id)
    snapshot_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    root = _snapshot_dir(repo_id, snapshot_id)
    staging = root.with_name(f".{root.name}.tmp")
    payload_dir = staging / "payload"
    previous = _previous_file_index(repo_id)
    files: list[dict[str, Any]] = []
    try:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source)
            relative_key = relative.as_posix()
            target = payload_dir / relative
            source_stat = path.stat()
            _copy_consistent(path, target)
            cached = previous.get(relative_key) or {}
            hash_value = ""
            if (
                path.suffix.lower() != ".db"
                and int(cached.get("source_size") or -1) == source_stat.st_size
                and int(cached.get("source_mtime_ns") or -1) == source_stat.st_mtime_ns
            ):
                hash_value = str(cached.get("sha256") or "")
            files.append({
                "path": relative_key,
                "size": target.stat().st_size,
                "source_size": source_stat.st_size,
                "source_mtime_ns": source_stat.st_mtime_ns,
                "sha256": hash_value or _sha256(target),
            })
        collections = _export_collections(repo_id, staging / "collections")
        manifest = {
            "version": SNAPSHOT_VERSION,
            "snapshot_id": snapshot_id,
            "source_repo_id": repo_id,
            "source_folder": str(source),
            "created_at": int(time.time() * 1000),
            "turn": max(0, int(turn)),
            "label": label.strip(),
            "dedupe_key": dedupe_key.strip(),
            "files": files,
            "collections": collections,
        }
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        staging.replace(root)
        return manifest
    except Exception:
        if staging.is_dir():
            shutil.rmtree(staging)
        raise


def create_snapshot(output_dir: str, repo_id: str, *, turn: int = 0,
                    label: str = "", dedupe_key: str = "") -> dict[str, Any]:
    with _SNAPSHOT_LOCK:
        return _create_snapshot_locked(
            output_dir, repo_id, turn=turn, label=label, dedupe_key=dedupe_key,
        )


def list_snapshots(repo_id: str) -> list[dict[str, Any]]:
    base = SNAPSHOT_ROOT / safe_seg(repo_id, strip=False)
    items: list[dict[str, Any]] = []
    if not base.is_dir():
        return items
    for path in base.glob("*/manifest.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("source_repo_id") == repo_id:
                items.append(data)
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(items, key=lambda item: int(item.get("created_at") or 0), reverse=True)


def _replace_paths(value: Any, old: str, new: str, source_repo: str,
                   target_repo: str) -> Any:
    if isinstance(value, str):
        return value.replace(old, new) if old else value
    if isinstance(value, list):
        return [_replace_paths(v, old, new, source_repo, target_repo) for v in value]
    if isinstance(value, dict):
        updated = {k: _replace_paths(v, old, new, source_repo, target_repo)
                   for k, v in value.items()}
        for key in ("repo_id", "repoId", "thread_id", "threadId"):
            if updated.get(key) == source_repo:
                updated[key] = target_repo
        return updated
    return value


def _restore_collections(root: Path, manifest: dict[str, Any], target_repo_id: str,
                         source_folder: str, target_folder: str) -> int:
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    except Exception:
        return 0
    restored = 0
    source_repo_id = str(manifest["source_repo_id"])
    for entry in manifest.get("collections") or []:
        path = root / "collections" / str(entry.get("file") or "")
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        target_name = str(data.get("name") or "").replace(source_repo_id, target_repo_id)
        ids = [str(item).replace(source_repo_id, target_repo_id) for item in data.get("ids") or []]
        if not target_name or not ids:
            continue
        collection = client.get_or_create_collection(target_name)
        payload: dict[str, Any] = {"ids": ids}
        for key in ("documents", "metadatas", "embeddings"):
            values = data.get(key) or []
            if values:
                payload[key] = _replace_paths(
                    values, source_folder, target_folder, source_repo_id, target_repo_id,
                )
        collection.upsert(**payload)
        restored += len(ids)
    return restored


def _delete_collections(repo_id: str) -> None:
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    except Exception:
        return
    for name in _collection_names(repo_id):
        try:
            client.delete_collection(name)
        except Exception:
            continue


def _clear_branch_payload(output_dir: str, repo_id: str) -> None:
    target = repo_meta.repo_folder_path(output_dir, repo_id)
    if not target.is_dir():
        return
    for path in target.iterdir():
        if path.name == "_repo.json":
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    _delete_collections(repo_id)


def fork_snapshot(output_dir: str, source_repo_id: str, snapshot_id: str,
                  target_repo_id: str) -> dict[str, Any]:
    if not all((output_dir, source_repo_id, snapshot_id, target_repo_id)):
        raise ValueError("分支参数不完整")
    if source_repo_id == target_repo_id:
        raise ValueError("目标仓库不能是源仓库")
    root = _snapshot_dir(source_repo_id, snapshot_id)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("快照不存在")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("source_repo_id") != source_repo_id:
        raise ValueError("快照来源不匹配")
    target = repo_meta.repo_folder(output_dir, target_repo_id)
    occupied = [path for path in target.iterdir() if path.name != "_repo.json"]
    if occupied:
        raise FileExistsError("目标仓库不是空仓库，拒绝覆盖")
    payload = root / "payload"
    copied: list[Path] = []
    try:
        for source in sorted(payload.rglob("*")):
            if not source.is_file() or source.name == "_repo.json":
                continue
            out = target / source.relative_to(payload)
            _copy_consistent(source, out)
            copied.append(out)
        old_folder = str(manifest.get("source_folder") or "")
        for path in copied:
            if path.suffix.lower() not in {".json", ".jsonl", ".txt", ".md"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
                if path.suffix.lower() == ".json":
                    value = json.loads(text)
                    value = _replace_paths(
                        value, old_folder, str(target), source_repo_id, target_repo_id,
                    )
                    text = json.dumps(value, ensure_ascii=False, indent=2)
                elif old_folder:
                    text = text.replace(old_folder, str(target))
                path.write_text(text, encoding="utf-8")
            except (OSError, UnicodeError):
                continue
        repo_meta.write_repo_marker(target, target_repo_id)
        vectors = _restore_collections(root, manifest, target_repo_id, old_folder, str(target))
    except Exception:
        for path in reversed(copied):
            try:
                path.unlink()
            except OSError:
                pass
        _delete_collections(target_repo_id)
        raise
    return {"ok": True, "snapshot_id": snapshot_id, "source_repo_id": source_repo_id,
            "target_repo_id": target_repo_id, "files": len(copied), "vectors": vectors}


def create_experiment(output_dir: str, source_repo_id: str, snapshot_id: str,
                      branches: list[dict[str, str]], *, rounds: int = 2) -> dict[str, Any]:
    if not 2 <= rounds <= 5:
        raise ValueError("短程推演轮数必须为 2–5")
    if not 1 <= len(branches) <= 3:
        raise ValueError("需要 1–3 个候选分支")
    experiment_id = f"exp-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    created: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        for branch in branches:
            target = str(branch.get("target_repo_id") or "").strip()
            choice = str(branch.get("choice") or "").strip()
            if not target or not choice or target in seen:
                raise ValueError("每个候选必须有唯一 target_repo_id 与非空 choice")
            seen.add(target)
            result = fork_snapshot(output_dir, source_repo_id, snapshot_id, target)
            created.append({"choice": choice, "target_repo_id": target, "fork": result,
                            "status": "ready"})
    except Exception:
        for item in created:
            _clear_branch_payload(output_dir, str(item["target_repo_id"]))
        raise
    record = {
        "version": 1, "experiment_id": experiment_id, "source_repo_id": source_repo_id,
        "snapshot_id": snapshot_id, "rounds": rounds, "created_at": int(time.time() * 1000),
        "branches": created, "selected_repo_id": "",
    }
    EXPERIMENT_ROOT.mkdir(parents=True, exist_ok=True)
    (EXPERIMENT_ROOT / f"{experiment_id}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return record


def select_branch(experiment_id: str, target_repo_id: str) -> dict[str, Any]:
    path = EXPERIMENT_ROOT / f"{safe_seg(experiment_id, strip=False)}.json"
    if not path.is_file():
        raise FileNotFoundError("实验不存在")
    data = json.loads(path.read_text(encoding="utf-8"))
    if target_repo_id not in {item.get("target_repo_id") for item in data.get("branches") or []}:
        raise ValueError("目标不属于该实验")
    data["selected_repo_id"] = target_repo_id
    data["selected_at"] = int(time.time() * 1000)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
