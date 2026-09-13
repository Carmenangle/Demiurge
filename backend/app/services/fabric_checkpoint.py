"""approval 自由循环断点续跑的 checkpoint 存储（2026-09-06）。

approval 模式下智能编造（固化02/03 内容生成型任务）走自由循环：模型逐步调用能力，
durable/expensive 步骤由 capability_sandbox 租约拦截返回 awaiting_approval。本模块
把「已完成消息历史 + 步骤轨迹 + 租约」持久化为 checkpoint，用户批准/取消后由
plan_compiler_node 续跑或丢弃。存储复用 task_progress_store（后台活动面板同源快照，
dict{id: entry} 形态——2026-09-06 实锤：误转 list 后 save 内部 tasks.items() 崩溃）。
"""
from __future__ import annotations

import time
from typing import Any

from app.services import task_progress_store

NAMESPACE = "fabric_checkpoints"
MAX_ENTRIES = 20


def _now() -> float:
    """updated_at 存 epoch 秒（数字）：task_progress_store.save 内部用
    float(updated_at) 排序，字符串（2026-09-06 实锤：格式化成
    "2026-09-06T12:47:07+0800" 导致 could not convert string to float）。"""
    return time.time()


def _load() -> dict[str, dict[str, Any]]:
    tasks = task_progress_store.load(NAMESPACE)
    if not isinstance(tasks, dict):
        return {}
    # 兼容旧版字符串 updated_at（2026-09-06）：统一归 0，避免 save 排序 float() 崩溃
    for _t in tasks.values():
        if isinstance(_t, dict) and not isinstance(_t.get("updated_at"), (int, float)):
            _t["updated_at"] = 0.0
    return tasks


def save(entry: dict[str, Any]) -> dict[str, Any]:
    """保存/更新一条 checkpoint（按 id 覆盖，保持 dict 形态）。"""
    tasks = _load()
    entry = dict(entry)
    entry.setdefault("id", f"fabric-{int(time.time() * 1000)}")
    entry["updated_at"] = _now()
    tasks[entry["id"]] = entry
    if len(tasks) > MAX_ENTRIES:
        for _old in sorted(tasks, key=lambda k: float(tasks[k].get("updated_at") or 0.0))[:len(tasks) - MAX_ENTRIES]:
            tasks.pop(_old, None)
    task_progress_store.save(NAMESPACE, tasks)
    return dict(entry)


_RESUMABLE = ("awaiting_approval", "running")


def pending(output_dir: str = "", thread_id: str = "") -> list[dict[str, Any]]:
    """按作品目录/会话筛选可恢复的 checkpoint（awaiting_approval/running，新→旧）。
    2026-09-07：running 也纳入——中断（step_limit/error/删除消息）后给流程重试的资本。"""
    out = []
    for t in _load().values():
        if t.get("status") not in _RESUMABLE:
            continue
        if output_dir and t.get("output_dir") != output_dir:
            continue
        if thread_id and t.get("thread_id") != thread_id:
            continue
        out.append(dict(t))
    out.sort(key=lambda t: t.get("updated_at") or "", reverse=True)
    return out


def resumable(output_dir: str = "", thread_id: str = "") -> dict[str, Any] | None:
    """取最近一个可恢复断点（running 优先，其次 awaiting_approval），无则 None。"""
    cps = pending(output_dir=output_dir, thread_id=thread_id)
    if not cps:
        return None
    cps.sort(key=lambda t: (t.get("status") == "running", t.get("updated_at") or ""), reverse=True)
    return cps[0] if cps else None


def get(task_id: str) -> dict[str, Any] | None:
    return _load().get(task_id)


def delete(task_id: str) -> None:
    tasks = _load()
    tasks.pop(task_id, None)
    task_progress_store.save(NAMESPACE, tasks)
