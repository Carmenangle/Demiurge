"""跨进程独占文件锁（2026-11-07 G 项）。

锁文件必须独立于目标文件：目标会被 `replace()` 换成新文件，锁在目标上会在换文件后
失效。Windows 用 `msvcrt.locking` 锁首字节，POSIX 用 `fcntl.flock`；进程内再叠一把
按路径分桶的线程锁（Windows 上同进程的第二个句柄也会被 OS 锁拦住）。

同一线程嵌套获取同一路径时直接复用外层锁：`delete_with_backup` 持锁后还会调
`save` / `backup`，不这样做会自锁死。
"""
from __future__ import annotations

import importlib
import logging
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

_LOG = logging.getLogger("uvicorn.error")


class LockTimeout(TimeoutError):
    """在给定超时内没拿到锁；调用方应把它当「操作失败」，不能当成「没有并发」。"""


_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()
_HELD = threading.local()


def _thread_lock(key: str) -> threading.Lock:
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[key] = lock
        return lock


def _held_paths() -> set[str]:
    paths = getattr(_HELD, "paths", None)
    if paths is None:
        paths = set()
        _HELD.paths = paths
    return paths


def _acquire_os_lock(fileobj: BinaryIO, deadline: float | None) -> None:
    if os.name == "nt":
        import msvcrt

        while True:
            try:
                fileobj.seek(0)
                msvcrt.locking(fileobj.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if deadline is not None and time.monotonic() >= deadline:
                    raise LockTimeout(f"等待文件锁超时：{fileobj.name}") from None
                time.sleep(0.02)
    else:
        # importlib 走 Any：fcntl 的属性在 Windows 平台的 typeshed 里不存在，直接 import 会红 mypy
        fcntl = importlib.import_module("fcntl")
        while True:
            try:
                fcntl.flock(fileobj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError:
                if deadline is not None and time.monotonic() >= deadline:
                    raise LockTimeout(f"等待文件锁超时：{fileobj.name}") from None
                time.sleep(0.02)


def _release_os_lock(fileobj: BinaryIO) -> None:
    try:
        fileobj.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fileobj.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl = importlib.import_module("fcntl")
            fcntl.flock(fileobj.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        # 释放失败不能盖掉调用方的异常；句柄关闭时 OS 也会回收这把锁
        _LOG.warning("释放文件锁失败 %s：%s", fileobj.name, exc)


@contextmanager
def exclusive_path_lock(path: Path, *, timeout: float | None = 30.0) -> Iterator[None]:
    """独占 `path` 对应的锁文件；`timeout=None` 表示无限等待。"""
    key = os.path.normcase(str(Path(path).resolve()))
    held = _held_paths()
    if key in held:
        yield
        return

    lock = _thread_lock(key)
    deadline = None if timeout is None else time.monotonic() + timeout
    if deadline is None:
        acquired = lock.acquire()
    else:
        acquired = lock.acquire(timeout=max(0.0, deadline - time.monotonic()))
    if not acquired:
        raise LockTimeout(f"等待文件锁超时（进程内）：{path}")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as fileobj:
            fileobj.seek(0, os.SEEK_END)
            if fileobj.tell() == 0:
                fileobj.write(b"0")
                fileobj.flush()
            _acquire_os_lock(fileobj, deadline)
            held.add(key)
            try:
                yield
            finally:
                held.discard(key)
                _release_os_lock(fileobj)
    finally:
        lock.release()
