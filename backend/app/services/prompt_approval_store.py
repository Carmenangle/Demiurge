"""按对话线程持久化待审核提示词，保证生成副作用必须经过用户确认。"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from app.config import DATA_DIR

APPROVALS_FILE = DATA_DIR / "prompt_approvals.json"
_LOCK = threading.RLock()


def _load() -> dict[str, dict]:
    if not APPROVALS_FILE.is_file():
        return {}
    try:
        data = json.loads(APPROVALS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return {}


def _write(data: dict[str, dict]) -> None:
    APPROVALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(APPROVALS_FILE) + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(APPROVALS_FILE)


def _items(thread_id: str, raw: object) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    # 兼容旧格式：thread_id 直接对应单条审批。
    if "stage" in raw:
        item = dict(raw)
        item_id = str(item.get("id") or uuid.uuid5(uuid.NAMESPACE_URL, f"prompt-approval:{thread_id}"))
        item["id"] = item_id
        item.setdefault("created_at", 0)
        return {item_id: item}
    out: dict[str, dict] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            item = dict(value)
            item["id"] = str(item.get("id") or key)
            item.setdefault("created_at", 0)
            out[item["id"]] = item
    return out


def get(thread_id: str, approval_id: str | None = None) -> dict | None:
    with _LOCK:
        items = _items(thread_id, _load().get(thread_id))
        if approval_id:
            item = items.get(approval_id)
        else:
            item = max(items.values(), key=lambda value: value.get("created_at", 0), default=None)
        return dict(item) if isinstance(item, dict) else None


def list_all(thread_id: str) -> list[dict]:
    with _LOCK:
        items = _items(thread_id, _load().get(thread_id))
        return [dict(item) for item in sorted(
            items.values(), key=lambda value: value.get("created_at", 0),
        )]


def set(thread_id: str, approval: dict) -> dict:
    item = dict(approval)
    item["id"] = str(item.get("id") or uuid.uuid4())
    item.setdefault("created_at", time.time())
    with _LOCK:
        data = _load()
        items = _items(thread_id, data.get(thread_id))
        items[item["id"]] = item
        data[thread_id] = items
        _write(data)
    return item


def clear(thread_id: str, approval_id: str | None = None) -> None:
    with _LOCK:
        data = _load()
        if thread_id not in data:
            return
        if not approval_id:
            del data[thread_id]
        else:
            items = _items(thread_id, data.get(thread_id))
            items.pop(approval_id, None)
            if items:
                data[thread_id] = items
            else:
                del data[thread_id]
        _write(data)
