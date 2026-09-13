"""任务会话登记（上下文合同 P1，2026-09-11）。

讨论/委派型任务**首次命中时登记**为任务会话：接续轮的判定先查登记（显式状态，
不从对话猜词），材料（附件 fileId / 原始任务句 / 产物路径）每轮从登记重建——
任务通道与对话流分离后，「换一种说法就漏判→掉回剧情楼层→隔轮失忆」在结构上
不再可能（2026-09-11 实锤：「不用ComfyUI的提示词…」无任何讨论动词，三个词表
判定全不命中 → 掉回 roleplay，灵感卡/规范/附件注入整段失效）。

与 `fabric_checkpoints` / `recipe_offers` 物理隔离（同 recipe_offer_store 的理由：
混进 `fabric_checkpoint` 命名空间，对话里的「批准」词会被当成别的语义授权）。
存储复用 `task_progress_store`（dict{id: entry} 形态，测试隔离由
`tests/conftest.py::_isolate_task_progress_store` autouse 统一收口）。
"""
from __future__ import annotations

import time
from typing import Any

from app.services import task_progress_store

NAMESPACE = "task_sessions"
MAX_ENTRIES = 50
# 会话有效期：超过这个秒数无活动视为结束（隔天回来不该被昨天的讨论劫持路由）。
TTL_SECONDS = 6 * 3600.0
MAX_MATERIALS = 10


def _id(thread_id: str) -> str:
    return f"task:{thread_id or 'sess'}"


def save(entry: dict[str, Any]) -> dict[str, Any]:
    """登记/更新任务会话（按 thread_id 一线程一活跃任务）。

    合并语义（接续轮重发登记时不丢首链信息）：
    - ``intent`` 随本轮更新，但 ``created_intent`` 保留**首次**任务句（任务卡
      ①「任务目标」每轮用它重建——接续轮的本轮原文只是短修正）；
    - ``materials`` 按 file_id 并集追加（上限 MAX_MATERIALS，新材料追加在尾）。
    """
    tasks = task_progress_store.load(NAMESPACE)
    if not isinstance(tasks, dict):
        tasks = {}
    entry = dict(entry)
    thread_id = str(entry.get("thread_id") or "")
    entry["id"] = _id(thread_id)
    prev = tasks.get(entry["id"])
    if isinstance(prev, dict):
        if prev.get("created_intent"):
            entry.setdefault("created_intent", prev["created_intent"])
        if not entry.get("created_intent"):
            entry["created_intent"] = entry.get("intent") or ""
        merged: list[dict[str, Any]] = [
            m for m in (prev.get("materials") or []) if isinstance(m, dict)]
        seen = {str(m.get("file_id") or "") for m in merged}
        for m in entry.get("materials") or []:
            if isinstance(m, dict) and str(m.get("file_id") or "") not in seen:
                seen.add(str(m.get("file_id") or ""))
                merged.append(dict(m))
        entry["materials"] = merged[-MAX_MATERIALS:]
        for key in ("product_paths",):
            old = [p for p in (prev.get(key) or []) if isinstance(p, str)]
            new = [p for p in (entry.get(key) or []) if isinstance(p, str)]
            entry[key] = list(dict.fromkeys(old + new))[-MAX_MATERIALS:]
    else:
        entry.setdefault("created_intent", entry.get("intent") or "")
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


def active(output_dir: str = "", thread_id: str = "") -> dict[str, Any] | None:
    """取该会话**未过期**的活跃任务；无则 None。"""
    if not thread_id:
        return None
    entry = _load().get(_id(thread_id))
    if not isinstance(entry, dict):
        return None
    if time.time() - float(entry.get("updated_at") or 0.0) > TTL_SECONDS:
        return None
    if output_dir and entry.get("output_dir") != output_dir:
        return None
    return dict(entry)


def clear(thread_id: str) -> None:
    tasks = _load()
    tasks.pop(_id(thread_id), None)
    task_progress_store.save(NAMESPACE, tasks)
