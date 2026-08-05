"""后台任务进度快照：原子落盘、容量限制和重启中断归一。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import DATA_DIR

STORE_DIR = DATA_DIR / "task_progress"


def _path(namespace: str) -> Path:
    if not namespace.replace("-", "").replace("_", "").isalnum():
        raise ValueError("无效的任务进度命名空间")
    return STORE_DIR / f"{namespace}.json"


def load(namespace: str) -> dict[str, dict[str, Any]]:
    path = _path(namespace)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(task_id): dict(value)
        for task_id, value in raw.items()
        if isinstance(value, dict)
    }


def save(namespace: str, tasks: dict[str, dict[str, Any]], limit: int = 100) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        tasks.items(),
        key=lambda item: float(item[1].get("updated_at") or item[1].get("created") or 0),
        reverse=True,
    )[:max(1, limit)]
    path = _path(namespace)
    temp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        temp.write_text(
            json.dumps(dict(ordered), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def mark_interrupted(
    tasks: dict[str, dict[str, Any]],
    *,
    running_statuses: set[str],
    message: str = "应用重启，任务已中断，请重新执行。",
) -> bool:
    changed = False
    for task in tasks.values():
        status = str(task.get("status") or "")
        if status not in running_statuses and not bool(task.get("running")):
            continue
        task.update({
            "status": "error",
            "running": False,
            "finished": True,
            "phase": "interrupted",
            "speed_bps": 0,
            "error": message,
            "message": message,
        })
        changed = True
    return changed
