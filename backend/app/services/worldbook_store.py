"""独立世界书落盘：每本世界书 = worldbookDir 下的一个 <安全名>.json（ST 世界书格式）。

区别于 character_store 的卡内嵌世界书（那随卡落在卡文件夹）：这里是用户单独导入的、
可跨卡复用的独立世界书，落在设置里的「世界书文件夹」(worldbookDir)。

- 名称取自上传文件名（ST 世界书 JSON 内部无 name 字段）。
- 同名冲突：save 时目标已存在且 overwrite=False → 抛 FileExistsError（调用方决定覆盖）。
- 卡内嵌世界书导入若与已存独立世界书同名，也复用同一冲突语义（见 routers）。

只做文件读写；条目解析/检索是 worldbook.py 的事，格式校验在导入路由。
"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import re

from app.services.pathnames import safe_dir, safe_seg

REPO_WORLDBOOK_FILE = "worldbook.json"


@dataclass
class WorldbookSummary:
    name: str
    file: str
    entries: int


def _path(base: str, name: str) -> Path:
    # safe_dir 保留中文可读性（世界书名常为中文），只挡 Windows 非法字符，与 character_store 一致
    return Path(base) / f"{safe_dir(name)}.json"


def exists(base: str, name: str) -> bool:
    return _path(base, name).is_file()


def _entry_count(book: dict[str, Any]) -> int:
    raw = book.get("entries")
    if isinstance(raw, dict):
        return len(raw)
    if isinstance(raw, list):
        return len(raw)
    return 0


def save(base: str, name: str, book: dict[str, Any], *, overwrite: bool = False) -> WorldbookSummary:
    """把世界书 JSON 写入 <base>/<安全名>.json。同名且 overwrite=False → FileExistsError。"""
    if not base:
        raise ValueError("未设置世界书文件夹路径")
    p = _path(base, name)
    if p.is_file() and not overwrite:
        raise FileExistsError(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    return WorldbookSummary(name=name, file=p.name, entries=_entry_count(book))


def list_books(base: str) -> list[WorldbookSummary]:
    root = Path(base)
    if not root.is_dir():
        return []
    out: list[WorldbookSummary] = []
    for child in sorted(root.glob("*.json")):
        try:
            book = json.loads(child.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(book, dict):
            continue
        out.append(WorldbookSummary(name=child.stem, file=child.name, entries=_entry_count(book)))
    return out


def read_book(base: str, name: str) -> dict[str, Any] | None:
    p = _path(base, name)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def delete_book(base: str, name: str) -> bool:
    p = _path(base, name)
    if not p.is_file():
        return False
    p.unlink()
    return not p.is_file()


def repo_snapshot_path(base: str, repo_id: str) -> Path:
    """当前小仓库的世界书快照，与 state/tables 一样按 repo_id 物理隔离。"""
    return Path(base) / safe_seg(repo_id, strip=False) / REPO_WORLDBOOK_FILE


def read_repo_snapshot(base: str, repo_id: str) -> dict[str, Any] | None:
    if not (base and repo_id):
        return None
    p = repo_snapshot_path(base, repo_id)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def save_repo_snapshot(base: str, repo_id: str, book: dict[str, Any]) -> bool:
    """把 worldbook dict 写回小仓库快照文件。快照不存在 → False。"""
    if not (base and repo_id):
        return False
    p = repo_snapshot_path(base, repo_id)
    if not p.parent.exists():
        return False
    p.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def _raw_entries(book: dict[str, Any]) -> list[dict[str, Any]]:
    raw = book.get("entries")
    values = raw.values() if isinstance(raw, dict) else raw
    return [deepcopy(item) for item in (values or []) if isinstance(item, dict)]


def ensure_repo_snapshot(base: str, repo_id: str,
                         source_books: list[dict[str, Any]]) -> dict[str, Any] | None:
    """首次使用时合并来源并快照；已有快照永不被源库覆盖。"""
    existing = read_repo_snapshot(base, repo_id)
    if existing is not None:
        return existing
    entries: list[dict[str, Any]] = []
    for book in source_books:
        if isinstance(book, dict):
            entries.extend(_raw_entries(book))
    if not source_books or not (base and repo_id):
        return None
    snapshot = {"entries": entries}
    p = repo_snapshot_path(base, repo_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return deepcopy(snapshot)


def _entry_matches(item: dict[str, Any], query: str) -> bool:
    if not query:
        return False
    terms = [str(item.get("comment") or "").strip()]
    keys = item.get("keys") or item.get("key") or []
    if isinstance(keys, str):
        keys = [keys]
    terms.extend(str(term).strip() for term in keys if str(term).strip())
    return any(term and term in query for term in terms)


def repo_snapshot_context(base: str, repo_id: str, *, query: str = "",
                          max_chars: int = 20_000,
                          allowed_indices: set[int] | frozenset[int] | None = None) -> str:
    """给 Curator 的带稳定 index 条目视图；限制长度避免重复灌入整本大书。"""
    book = read_repo_snapshot(base, repo_id)
    if not book:
        return ""
    from app.services import worldbook_edit
    indexed = worldbook_edit.list_entries(book)
    if allowed_indices is not None:
        indexed = [item for item in indexed if item["index"] in allowed_indices]
    if query:
        indexed.sort(key=lambda item: not _entry_matches(item, query))
    selected: list[dict[str, Any]] = []
    for item in indexed:
        candidate = json.dumps([*selected, item], ensure_ascii=False, separators=(",", ":"))
        if len(candidate) > max_chars:
            if not selected:
                clipped = dict(item)
                clipped["content"] = str(clipped.get("content") or "")[:max(0, max_chars - 256)]
                selected.append(clipped)
            break
        selected.append(item)
    return json.dumps(selected, ensure_ascii=False, separators=(",", ":"))


def repo_visual_profiles(base: str, repo_id: str, query: str, *, max_chars: int = 4_000) -> str:
    """提取本轮命中角色的稳定视觉锚点，供主模型与当前状态合并。"""
    book = read_repo_snapshot(base, repo_id)
    if not book or not query:
        return ""
    from app.services import worldbook_edit
    profiles: list[str] = []
    for item in worldbook_edit.list_entries(book):
        content = str(item.get("content") or "")
        comment = str(item.get("comment") or "")
        if not _entry_matches(item, query) or not ("角色卡·" in comment or "【角色卡·" in content):
            continue
        name = comment.split("角色卡·", 1)[-1].strip() or "角色"
        anchors = re.findall(r"^【(?:外貌|身材|穿着)】[^\r\n]*", content, flags=re.M)
        if not anchors:
            continue
        candidate = f"{name}：" + "；".join(anchors)
        if sum(len(value) for value in profiles) + len(candidate) > max_chars:
            break
        profiles.append(candidate)
    return "\n".join(profiles)


def _merge_character_dynamic(existing: str, update: str) -> str:
    marker = "【剧情进展·动态】"
    base = existing.split(marker, 1)[0].rstrip()
    dynamic = update.split(marker, 1)[-1].strip()
    return f"{base}\n\n{marker}\n{dynamic}"


def apply_repo_ops(base: str, repo_id: str, ops: list[dict[str, Any]], *,
                   allowed_update_indices: set[int] | frozenset[int] | None = None,
                   rejections: list[dict[str, Any]] | None = None) -> int:
    """对当前小仓库快照执行 Curator 增改；更新需 evidence，删除类 op 一律拒绝。

    角色卡条目（comment 含「角色卡·」或正文带【角色卡·】头）只允许按剧情进度改正文
    动态段（_merge_character_dynamic）；身份键（comment/keys/constant/enabled）不随
    ops 变更并上报 rejections——改名/改键会让 repo_visual_profiles 的「角色卡·」+
    【外貌】锚识别失配断粮（2026-08-31 实锤：舞姬恋↔舞柔条目连不上 → 空泛提示词）。
    拒绝明细写入 rejections（调用方负责 trace），不抛错不中断整批。
    """
    book = read_repo_snapshot(base, repo_id)
    if not book:
        return 0
    from app.services import worldbook_edit
    applied = 0
    for op in ops:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op") or "").strip()
        if kind in {"worldbook_delete", "delete", "worldbook_remove", "remove"}:
            if rejections is not None:
                record: dict[str, Any] = {"op": kind, "reason": "delete_forbidden"}
                if op.get("index") is not None:
                    record["index"] = op.get("index")
                rejections.append(record)
            continue
        text = str(op.get("text") or "").strip()
        patch: dict[str, Any] = {"content": text}
        if "title" in op:
            patch["comment"] = str(op.get("title") or "").strip()
        if "keys" in op and isinstance(op.get("keys"), list):
            patch["keys"] = op["keys"]
        if "constant" in op:
            patch["constant"] = bool(op["constant"])
        if "enabled" in op:
            patch["enabled"] = bool(op["enabled"])
        if kind == "worldbook_add" and text:
            worldbook_edit.add_entry(book, patch)
            applied += 1
        elif kind == "worldbook_update" and text and str(op.get("evidence") or "").strip():
            try:
                index = int(op.get("index"))
            except (TypeError, ValueError):
                continue
            if allowed_update_indices is not None and index not in allowed_update_indices:
                continue
            entries = worldbook_edit.list_entries(book)
            if 0 <= index < len(entries):
                current = entries[index]
                content = str(current.get("content") or "")
                comment = str(current.get("comment") or "")
                if "角色卡·" in comment or "【角色卡·" in content:
                    merged = _merge_character_dynamic(content, text)
                    stripped = sorted(key for key in patch if key != "content")
                    patch = {"content": merged}
                    if rejections is not None and stripped:
                        rejections.append({
                            "op": kind, "index": index,
                            "reason": "character_identity_keys_forbidden",
                            "fields": stripped,
                        })
            if worldbook_edit.update_entry(book, index, patch):
                applied += 1
    if applied:
        p = repo_snapshot_path(base, repo_id)
        p.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    return applied
