"""事前固化询问的待确认断点（2026-09-10）。

为什么不复用 `fabric_checkpoint`：
`fabric_checkpoint._RESUMABLE = ("awaiting_approval", "running")`，而
`_fabric_approval_word` 会把 `fabric_checkpoint.pending()` 的第一条当成「待批准的
工具」去 `grant_operation`。固化询问若混进那个命名空间，用户回一句「批准」就会被
误当成 durable 能力授权。两者语义完全不同，必须物理隔离。

存储复用 `task_progress_store`（与 fabric_checkpoint 同源的 dict{id: entry} 形态）。
"""
from __future__ import annotations

import time
from typing import Any

from app.services import task_progress_store

NAMESPACE = "recipe_offers"
MAX_ENTRIES = 20
# 询问有效期：超过这个秒数视为过期（跨天/隔了很久的旧询问不该再拦人）。
TTL_SECONDS = 1800.0


def save(entry: dict[str, Any]) -> dict[str, Any]:
    """保存/更新一条询问（按 id 覆盖，保持 dict 形态）。"""
    tasks = task_progress_store.load(NAMESPACE)
    if not isinstance(tasks, dict):
        tasks = {}
    entry = dict(entry)
    entry.setdefault("id", f"offer-{int(time.time() * 1000)}")
    entry["updated_at"] = time.time()
    tasks[entry["id"]] = entry
    if len(tasks) > MAX_ENTRIES:
        for _old in sorted(tasks, key=lambda k: float(tasks[k].get("updated_at") or 0.0)
                           )[:len(tasks) - MAX_ENTRIES]:
            tasks.pop(_old, None)
    task_progress_store.save(NAMESPACE, tasks)
    return dict(entry)


def _load() -> dict[str, dict[str, Any]]:
    tasks = task_progress_store.load(NAMESPACE)
    if not isinstance(tasks, dict):
        return {}
    return {k: v for k, v in tasks.items() if isinstance(v, dict)}


def pending(output_dir: str = "", thread_id: str = "") -> dict[str, Any] | None:
    """取最近一条**未过期**的待确认询问（按作品目录/会话筛选）。无则 None。"""
    now = time.time()
    out: list[dict[str, Any]] = []
    for t in _load().values():
        if output_dir and t.get("output_dir") != output_dir:
            continue
        if thread_id and t.get("thread_id") != thread_id:
            continue
        if now - float(t.get("updated_at") or 0.0) > TTL_SECONDS:
            continue
        out.append(t)
    if not out:
        return None
    out.sort(key=lambda t: float(t.get("updated_at") or 0.0), reverse=True)
    return dict(out[0])


def get(offer_id: str) -> dict[str, Any] | None:
    return _load().get(offer_id)


def delete(offer_id: str) -> None:
    tasks = _load()
    tasks.pop(offer_id, None)
    task_progress_store.save(NAMESPACE, tasks)
