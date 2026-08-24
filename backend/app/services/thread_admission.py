"""线程准入：同一 thread 只允许一个活动运行，并持有其协作取消信号。

从 agent_runner 抽出，让「谁在跑、能不能再开、如何取消」成为独立可测的真源。
- admit：抢占式登记，重复登记同一 thread 抛 RunAlreadyActive。
- release：带所有权校验，只有登记它的那次运行能撤销自己（防旧运行误删新登记）。
- request_cancel：向活动运行发协作取消信号（set 其 cancel_event）。
- 看门狗：admission 记录登记时间，超过 ADMISSION_STALE_MS 视为泄漏（运行崩溃未 release）
  自动清理，避免残留登记永久阻塞该 thread 的后续消息（含聊天队列 worker 认领）。
零重依赖(仅 threading/dataclass)，供后台运行与对话维护共用。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass


class RunAlreadyActive(RuntimeError):
    pass


@dataclass
class Admission:
    thread_id: str
    cancel_event: threading.Event
    admitted_at: float = 0.0


_active: dict[str, Admission] = {}
_lock = threading.Lock()
_log = logging.getLogger(__name__)

# 一次运行超过该时长视为泄漏（生成任务异常崩溃未 release）。
# 前端流式单轮通常数十秒内；即使长时间生成也不应超过 30 分钟。
ADMISSION_STALE_MS = 30 * 60 * 1000


def _prune_stale(now: float) -> None:
    """清理超时未释放的登记（崩溃泄漏兜底）。持有 _lock 时调用。"""
    stale = [
        tid for tid, admission in _active.items()
        if (now - admission.admitted_at) * 1000 > ADMISSION_STALE_MS
    ]
    for tid in stale:
        _active.pop(tid, None)
        _log.info("pruned stale admission for thread %s (>%.0f min)", tid, ADMISSION_STALE_MS / 60_000)


def is_active(thread_id: str) -> bool:
    """该 thread 是否有活动运行。"""
    with _lock:
        _prune_stale(time.monotonic())
        return thread_id in _active


def active_threads() -> list[str]:
    """当前有活动运行的所有 thread（供后台面板列出正在跑的仓库对话）。"""
    with _lock:
        _prune_stale(time.monotonic())
        return list(_active.keys())


def admit(thread_id: str, cancel_event: threading.Event) -> Admission:
    """登记一次运行；同一 thread 已有活动运行时抛 RunAlreadyActive。"""
    admission = Admission(thread_id, cancel_event, time.monotonic())
    with _lock:
        _prune_stale(time.monotonic())
        if thread_id in _active:
            raise RunAlreadyActive("该对话已有生成任务正在运行")
        _active[thread_id] = admission
    return admission


def release(admission: Admission) -> None:
    """撤销登记；仅当当前登记就是这次运行才移除（防串台，幂等）。"""
    with _lock:
        if _active.get(admission.thread_id) is admission:
            _active.pop(admission.thread_id, None)


def request_cancel(thread_id: str) -> bool:
    """向该 thread 的活动运行发协作取消信号；命中返回 True，无活动运行返回 False。"""
    with _lock:
        admission = _active.get(thread_id)
    if admission is None:
        return False
    admission.cancel_event.set()
    return True
