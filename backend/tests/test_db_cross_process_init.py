"""两个后端实例指向同一 `DATA_DIR` 时，建库/补列必须串行（2026-11-07 G 项）。

真实失败形态（本文件的种子）：`_schema_ready` + `_SCHEMA_LOCK` 都只在进程内，
两个进程同时首连时都会读到「`workflow_build_tasks` 还没有 `worker_id`」，随后
一个进程的 `alter table ... add column` 会撞上另一个已提交的同名列 ⇒
`sqlite3.OperationalError: duplicate column name: worker_id`，其中一个实例启动失败。

用例在子进程里包一层「读到列清单后等两方都到齐」的代理，把竞态窗口放大到必现；
修复后第二个进程会先等 `<db>.init.lock`，进入时列已存在，不再重复 ALTER。
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

_CHILD = r"""
import os
import time
from pathlib import Path

import app.db as db

race_dir = Path(os.environ["RACE_DIR"])
race_dir.mkdir(parents=True, exist_ok=True)
state = {"delayed": False}
real_raw = db._raw_connection


class _Proxy:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *args):
        result = self._conn.execute(sql, *args)
        if not state["delayed"] and sql == "pragma table_info(workflow_build_tasks)":
            state["delayed"] = True
            (race_dir / f"ready-{os.getpid()}").write_text("1", encoding="utf-8")
            go = race_dir / "go"
            deadline = time.time() + 20.0
            while time.time() < deadline and not go.exists():
                time.sleep(0.01)
        return result

    def __getattr__(self, name):
        return getattr(self._conn, name)


db._raw_connection = lambda: _Proxy(real_raw())
db.init_db()
print("INIT_OK")
"""


def _spawn(data_dir: Path, race_dir: Path, env: dict[str, str]) -> subprocess.Popen[str]:
    child_env = env.copy()
    child_env["LAF_DATA_DIR"] = str(data_dir)
    child_env["LAF_COMFY_AUTOSTART"] = "0"
    child_env["RACE_DIR"] = str(race_dir)
    child_env["PYTHONPATH"] = str(BACKEND)
    child_env["PYTHONUTF8"] = "1"
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        cwd=BACKEND,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_两进程并发建库都成功且结构完整(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    # 老库：workflow_build_tasks 还没有 worker_id / lease_expires_at ⇒ 两个进程都要走补列。
    # 新建库的 CREATE TABLE 已带这两列，不会触发 ALTER，复现不出竞态。
    connection = sqlite3.connect(data_dir / "app.db")
    try:
        connection.execute(
            """
            create table workflow_build_tasks (
                id text primary key,
                session_id text not null,
                mode text not null,
                need text not null,
                payload text not null,
                status text not null,
                result text not null default '',
                error text not null default '',
                created_at integer not null,
                updated_at integer not null
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    race_dir = tmp_path / "race"
    env = os.environ.copy()
    procs = [_spawn(data_dir, race_dir, env) for _ in range(2)]
    # 无锁时两进程都会到齐；有锁时只有第一个能进，等到 5s 后放行即可。
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if len(list(race_dir.glob("ready-*"))) >= 2:
            break
        time.sleep(0.02)
    (race_dir / "go").write_text("1", encoding="utf-8")
    results = [proc.communicate(timeout=90) for proc in procs]

    for proc, (out, err) in zip(procs, results, strict=True):
        assert proc.returncode == 0, (
            f"并发建库失败（rc={proc.returncode}）\nstdout={out}\nstderr={err}"
        )
        assert "INIT_OK" in out

    db_path = data_dir / "app.db"
    connection = sqlite3.connect(db_path)
    try:
        columns = {
            row[1]
            for row in connection.execute("pragma table_info(workflow_build_tasks)")
        }
        version = connection.execute("pragma user_version").fetchone()[0]
    finally:
        connection.close()
    assert {"worker_id", "lease_expires_at"} <= columns
    assert version == 2, "结构档位必须打到当前档"
