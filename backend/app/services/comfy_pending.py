"""ComfyUI 在途生成登记（2026-10-19）。

改动前「正在出图」只存在浏览器 `localStorage`（`laf_pending_gen_<threadId>`）：
换浏览器、清缓存、换设备打开就看不到任何在跑任务，删除仓库前的守卫也查不到。
这里补一份**服务端**登记：提交成功后写入、终态（完成/取消/失败）删除，
供「后台活动」面板跨端读取。

分工（防两个真源漂移）：
- 本表只回答「prompt_id 属于哪个 thread、何时提交」；
- 任务结果、归档、判重仍归前端 `WorkflowGenerationRuntime` 与后端 `generation_store`。
- **不做 ComfyUI 主动探活**：面板轮询链不得依赖 ComfyUI 网络（会拖垮 1.5s 轮询）。
  前端崩溃未清的情况由 30 分钟 TTL 兜底（与前端 `STALE_MS` 对齐）。
"""
from __future__ import annotations

import threading
import time

from app.services import task_progress_store

# 与前端 comfyBackgroundActivity.STALE_MS 一致：超过即视为陈旧，不再展示。
TTL_MS = 30 * 60 * 1000
# 合理 epoch 秒下界（2001-09-09）：更小的值视为「毫秒/脏数据」，回退到服务端时间。
_EPOCH_FLOOR = 1_000_000_000
_NAMESPACE = "comfy-pending"

_TASKS: dict[str, dict] = task_progress_store.load(_NAMESPACE)
_LOCK = threading.Lock()


def _persist_locked() -> None:
    task_progress_store.save(_NAMESPACE, _TASKS)


def track(prompt_id: str, thread_id: str, created_at: float = 0.0) -> dict:
    """登记一个在途 prompt（幂等：同 prompt_id 覆盖）。

    `created_at` 只接受「合理的 epoch 秒」：客户端若误传毫秒（1.7e12）或 0，
    统一回退到服务端当前时间——否则 TTL 永远算不出过期，僵尸条目会留在面板上。
    """
    pid = str(prompt_id or "").strip()
    if not pid:
        raise ValueError("prompt_id 不能为空")
    now = time.time()
    created = float(created_at or 0)
    if not (_EPOCH_FLOOR <= created <= now + 60):
        created = now
    entry = {
        "prompt_id": pid,
        "thread_id": str(thread_id or "home"),
        "created_at": created,
        "updated_at": now,
    }
    with _LOCK:
        _TASKS[pid] = entry
        _persist_locked()
    return dict(entry)


def finish(prompt_id: str) -> bool:
    """终态清除（完成/取消/失败/超时都走这里）。返回是否确实有这条。"""
    pid = str(prompt_id or "").strip()
    with _LOCK:
        removed = _TASKS.pop(pid, None) is not None
        if removed:
            _persist_locked()
    return removed


def _prune_locked(now: float) -> None:
    stale = [
        pid for pid, item in _TASKS.items()
        if (now - float(item.get("created_at") or 0)) * 1000 > TTL_MS
    ]
    for pid in stale:
        _TASKS.pop(pid, None)
    if stale:
        _persist_locked()


def snapshot(now: float | None = None) -> list[dict]:
    """在途列表（新的在前），顺手清掉超过 TTL 的僵尸条目。"""
    current = time.time() if now is None else now
    with _LOCK:
        _prune_locked(current)
        items = [dict(item) for item in _TASKS.values()]
    items.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
    return items


def clear() -> None:
    """测试/排障用：清空全部登记。"""
    with _LOCK:
        _TASKS.clear()
        _persist_locked()
