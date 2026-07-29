"""线程准入：同一 thread 只允许一个活动运行，并持有其协作取消信号。

从 agent_runner 抽出，让「谁在跑、能不能再开、如何取消」成为独立可测的真源。
- admit：抢占式登记，重复登记同一 thread 抛 RunAlreadyActive。
- release：带所有权校验，只有登记它的那次运行能撤销自己（防旧运行误删新登记）。
- request_cancel：向活动运行发协作取消信号（set 其 cancel_event）。
零重依赖(仅 threading/dataclass)，供后台运行与对话维护共用。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass


class RunAlreadyActive(RuntimeError):
    pass


@dataclass
class Admission:
    thread_id: str
    cancel_event: threading.Event


_active: dict[str, Admission] = {}
_lock = threading.Lock()


def is_active(thread_id: str) -> bool:
    """该 thread 是否有活动运行。"""
    with _lock:
        return thread_id in _active


def active_threads() -> list[str]:
    """当前有活动运行的所有 thread（供后台面板列出正在跑的仓库对话）。"""
    with _lock:
        return list(_active.keys())


def admit(thread_id: str, cancel_event: threading.Event) -> Admission:
    """登记一次运行；同一 thread 已有活动运行时抛 RunAlreadyActive。"""
    admission = Admission(thread_id, cancel_event)
    with _lock:
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
