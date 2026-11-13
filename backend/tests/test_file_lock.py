"""跨进程文件锁原语（2026-11-07 G 项）。

背景：`_SCHEMA_LOCK` / `state_store` 的锁都只在进程内生效，两个后端实例指向同一
`DATA_DIR` 时仍会并发建表/迁移或交错写用户档。锁原语必须同时满足：

1. 同一路径跨进程互斥（真实失败形态：DB 并发建库时 `duplicate column name`）；
2. 等待有超时，不能把第二个实例永久挂死；
3. 同一线程可重入（`delete_with_backup` → `backup` 这类嵌套调用不得自锁死）；
4. 不同路径互不阻塞。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

_HOLD_LOCK = r"""
import os
import time
from pathlib import Path

from app.file_lock import exclusive_path_lock

go = Path(os.environ["GO_PATH"])
while not go.exists():
    time.sleep(0.01)

lock = Path(os.environ["LOCK_PATH"])
log = Path(os.environ["LOG_PATH"])
with exclusive_path_lock(lock, timeout=20):
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"start {os.getpid()} {time.time()}\n")
    time.sleep(0.35)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"end {os.getpid()} {time.time()}\n")
"""


def _spawn_holder(tmp_path: Path, go: Path) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["LOCK_PATH"] = str(tmp_path / "shared.lock")
    env["LOG_PATH"] = str(tmp_path / "events.log")
    env["GO_PATH"] = str(go)
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONUTF8"] = "1"
    return subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _events(path: Path) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        kind, _pid, stamp = line.split()
        rows.append((kind, float(stamp)))
    return rows


def test_同一路径跨进程互斥(tmp_path: Path):
    """两个进程同时抢一把锁，临界区必须串行；旧代码没有锁原语 ⇒ 本例如红。"""
    go = tmp_path / "go"
    first = _spawn_holder(tmp_path, go)
    second = _spawn_holder(tmp_path, go)
    go.write_text("1", encoding="utf-8")
    for proc in (first, second):
        _out, err = proc.communicate(timeout=40)
        assert proc.returncode == 0, err

    events = _events(tmp_path / "events.log")
    assert [kind for kind, _ in events] == ["start", "end", "start", "end"], (
        f"临界区发生重叠：{events}"
    )


def test_等待超时抛LockTimeout(tmp_path: Path):
    from app.file_lock import LockTimeout, exclusive_path_lock

    lock = tmp_path / "timeout.lock"
    held = threading.Event()
    release = threading.Event()

    def _hold() -> None:
        with exclusive_path_lock(lock, timeout=5):
            held.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=_hold)
    holder.start()
    try:
        assert held.wait(timeout=5), "持锁线程未启动"
        started = time.monotonic()
        with pytest.raises(LockTimeout):
            with exclusive_path_lock(lock, timeout=0.15):
                pass
        assert time.monotonic() - started < 5, "超时没有按时返回"
    finally:
        release.set()
        holder.join(timeout=10)


def test_同一线程可重入(tmp_path: Path):
    """`delete_with_backup` 在持锁后还会调 `save/backup`，同线程嵌套不得自锁死。"""
    from app.file_lock import exclusive_path_lock

    lock = tmp_path / "reentrant.lock"
    with exclusive_path_lock(lock, timeout=2):
        with exclusive_path_lock(lock, timeout=2):
            pass


def test_不同路径互不阻塞(tmp_path: Path):
    from app.file_lock import exclusive_path_lock

    first = tmp_path / "a.lock"
    second = tmp_path / "b.lock"
    with exclusive_path_lock(first, timeout=2):
        with exclusive_path_lock(second, timeout=0.5):
            pass
