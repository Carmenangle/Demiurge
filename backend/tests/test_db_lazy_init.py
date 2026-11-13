"""`init_db` 移出 import 的合同：启动显式建库 + 连接懒初始化（2026-11-07 E 项）。

真实失败形态：`import app.main` 就建库/跑结构闸，于是「导入失败」与「启动失败」混在一起，
不跑 lifespan 的端点测试也必须先手工 init 才不会撞空库。修法是：
- `_lifespan` 首步显式 `init_db()`（启动快速失败）；
- `get_connection()` 按当前 `DB_PATH` 幂等补建（键是路径，不是全局布尔）；
- 两者日志能区分「启动装配」与「首次连接补建」。
"""
from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _run_backend(code: str, data_dir: Path) -> subprocess.CompletedProcess[str]:
    """在隔离子进程里跑（LAF_DATA_DIR 指 tmp），避免 import app.main 碰真实 backend/data。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["LAF_DATA_DIR"] = str(data_dir)
    env["LAF_COMFY_AUTOSTART"] = "0"
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _tables(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
    finally:
        connection.close()


def _bind(monkeypatch, path: Path):
    from app import db

    monkeypatch.setattr(db, "DB_PATH", path)
    return db


def test_首次连接自动补建结构(tmp_path, monkeypatch):
    """不跑 lifespan 的端点/脚本直接取连接，也必须拿到完整结构而不是空库。"""
    path = tmp_path / "fresh.db"
    db = _bind(monkeypatch, path)

    connection = db.get_connection()
    try:
        assert "assets" in _tables(path)
        assert "plan_tasks" in _tables(path)
    finally:
        connection.close()


def test_同一DB_PATH只初始化一次(tmp_path, monkeypatch):
    db = _bind(monkeypatch, tmp_path / "once.db")
    gate_calls: list[int] = []
    monkeypatch.setattr(db, "_schema_gate", lambda: gate_calls.append(1) or True)

    db.get_connection().close()
    db.get_connection().close()

    assert gate_calls == [1], "同一 DB_PATH 的结构初始化只允许跑一次"


def test_换DB_PATH会重新补建(tmp_path, monkeypatch):
    """缓存键必须是路径：测试/脚本换 DB_PATH 后不能复用「已初始化」的旧判断。"""
    db = _bind(monkeypatch, tmp_path / "a.db")
    db.get_connection().close()

    second = tmp_path / "b.db"
    monkeypatch.setattr(db, "DB_PATH", second)
    db.get_connection().close()

    assert "assets" in _tables(second)


def test_日志能区分启动装配与首次连接补建(tmp_path, monkeypatch, caplog):
    db = _bind(monkeypatch, tmp_path / "startup.db")
    caplog.set_level(logging.INFO, logger="uvicorn.error")

    db.init_db()
    assert "启动装配" in caplog.text

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "lazy.db")
    db.get_connection().close()
    assert "首次连接补建" in caplog.text


def test_导入main不建库(tmp_path):
    result = _run_backend("import app.main\n", tmp_path / "probe-import")
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "probe-import" / "app.db").exists(), (
        "import app.main 不得建库/跑结构闸；建库必须发生在 lifespan 或首次连接"
    )


def test_lifespan建库失败则启动失败(tmp_path):
    """lifespan 不得吞掉 init_db 异常——否则会带病启动，问题被懒初始化悄悄掩盖。"""
    code = """
import asyncio
import app.main as m

def boom():
    raise RuntimeError("boom-init-db")

m.init_db = boom

async def run():
    async with m._lifespan(m.app):
        pass

try:
    asyncio.run(run())
except RuntimeError as exc:
    if "boom-init-db" not in str(exc):
        raise
    print("OK")
else:
    raise SystemExit("lifespan swallowed init_db failure")
"""
    result = _run_backend(code, tmp_path / "probe-lifespan")
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
