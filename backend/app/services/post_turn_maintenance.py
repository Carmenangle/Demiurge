"""剧情正文交付后的低优先级维护队列。

同一作品串行维护，避免表格、纪要和世界书并发写；不同作品可并行。
维护失败只记日志，不得重新占用或破坏已经完成的前台对话。
"""
from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass


_LOG = logging.getLogger(__name__)


@dataclass
class _OwnerWorker:
    jobs: "queue.Queue[Callable[[], None]]"
    thread: threading.Thread


_workers: dict[str, _OwnerWorker] = {}
_lock = threading.Lock()


def submit(owner: str, job: Callable[[], None]) -> None:
    """把维护工作交给 owner 专属串行队列，并立即返回。"""
    key = owner or "__global__"
    with _lock:
        worker = _workers.get(key)
        if worker is None:
            jobs: "queue.Queue[Callable[[], None]]" = queue.Queue()
            thread = threading.Thread(
                target=_run, args=(key, jobs),
                name=f"post-turn-{key[:12]}", daemon=True,
            )
            worker = _OwnerWorker(jobs=jobs, thread=thread)
            _workers[key] = worker
            jobs.put(job)
            thread.start()
            return
        worker.jobs.put(job)


def _run(owner: str, jobs: "queue.Queue[Callable[[], None]]") -> None:
    while True:
        try:
            job = jobs.get(timeout=60)
        except queue.Empty:
            with _lock:
                current = _workers.get(owner)
                if current is not None and current.jobs is jobs and jobs.empty():
                    _workers.pop(owner, None)
                    return
            continue
        try:
            job()
        except Exception:  # noqa: BLE001
            _LOG.exception("剧情后维护失败 owner=%s", owner)
        finally:
            jobs.task_done()
