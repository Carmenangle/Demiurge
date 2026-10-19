"""WAL 首次切换的并发竞态（2026-10-18 真机复现，批量视觉描述并发写全失败）。

症状：`pragma journal_mode=WAL` 被多个新连接同时执行 ⇒ 抢建 -wal/-shm，
输的连接后续 UPDATE 报 "attempt to write a readonly database"。表现是
「后台任务显示 done，但资产库里看不到描述」——写进了 Chroma、没进主表。
"""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from app import db as dbmod


def _write(i: int) -> bool:
    conn = dbmod.get_connection()
    try:
        with conn:
            conn.execute("insert into t(v) values (?)", (f"row{i}",))
        return True
    except sqlite3.OperationalError as exc:
        return f"ERR {exc}"
    finally:
        conn.close()


def test_concurrent_first_writes_all_succeed(tmp_path, monkeypatch):
    """-wal 不存在（干净关闭后）时的并发首写：不得有 readonly 失败。"""
    path = tmp_path / "wal.db"
    monkeypatch.setattr(dbmod, "DB_PATH", path)
    dbmod._wal_ready = False

    conn = dbmod.get_connection()
    conn.execute("create table t(v text)")
    conn.commit()
    conn.close()
    for suffix in ("-wal", "-shm"):          # 造出「干净关闭、-wal 已被清理」的现场
        side = tmp_path / f"wal.db{suffix}"
        if side.exists():
            side.unlink()
    dbmod._wal_ready = False

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_write, range(8)))
    assert results == [True] * 8, f"并发首写出现失败：{results}"


def test_write_with_retry_recovers_from_readonly(tmp_path, monkeypatch):
    """readonly 是瞬时 WAL 竞态 ⇒ 重开连接重试即可；非 readonly 的错不吞、直接抛。"""
    path = tmp_path / "retry.db"
    monkeypatch.setattr(dbmod, "DB_PATH", path)
    dbmod._wal_ready = False
    conn = dbmod.get_connection()
    conn.execute("create table t(v text)")
    conn.commit()
    conn.close()

    calls = {"n": 0}

    class _Boom:
        def execute(self, sql, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise sqlite3.OperationalError("attempt to write a readonly database")
            return None

        def close(self):
            return None

    def _flaky(*_a, **_k):
        return _Boom()

    monkeypatch.setattr(dbmod, "get_connection", _flaky)
    assert dbmod.write_with_retry(lambda c: c.execute("insert into t(v) values('a')")) is None
    assert calls["n"] == 2

    def _hard(*_a, **_k):
        raise sqlite3.OperationalError("no such column: x")

    monkeypatch.setattr(dbmod, "get_connection", _hard)
    try:
        dbmod.write_with_retry(lambda c: None)
    except sqlite3.OperationalError as exc:
        assert "no such column" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("非 readonly 的错必须原样抛出，不能被重试吞掉")


def test_wal_pragma_runs_once_per_process(tmp_path, monkeypatch):
    """切换只做一次（锁 + 标志位）：后续连接不再重复 pragma，避免再次抢锁。"""
    path = tmp_path / "once.db"
    monkeypatch.setattr(dbmod, "DB_PATH", path)
    dbmod._wal_ready = False
    calls: list[str] = []

    class _Spy(sqlite3.Connection):
        def execute(self, sql, *a, **k):  # 只记录 pragma，行为照旧
            if "journal_mode" in str(sql):
                calls.append(str(sql))
            return super().execute(sql, *a, **k)

    real_connect = sqlite3.connect
    monkeypatch.setattr(
        sqlite3, "connect",
        lambda *a, **k: real_connect(*a, **k, factory=_Spy) if "factory" not in k
        else real_connect(*a, **k),
    )
    for _ in range(3):
        dbmod.get_connection().close()
    assert len(calls) == 1, f"WAL pragma 执行了 {len(calls)} 次（应只 1 次）：{calls}"
