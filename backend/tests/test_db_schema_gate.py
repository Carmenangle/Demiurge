"""`app.db` 的结构档位闸（2026-11-02 P1）。

`init_db` 每次都跑全量 DDL，所以「库比本代码新」时它必须**知道自己不该动**：降级回来、或两个
版本并跑同一目录时，新库里有旧代码不认识的列/索引，照常跑 DDL 会静默改坏结构。闸的口径两条：

- `档位 <= 当前` ⇒ 就地迁移（历史库全是 0 档，DDL 全是 `IF NOT EXISTS` + 补列，天然前向兼容），
  **不备份**；
- `档位 > 当前` ⇒ 先做**能打开的**备份再建新；备份失败 ⇒ 一条 DDL 都不跑。

备份走 sqlite 在线备份 API：WAL 模式下单拷 `.db` 会漏掉还没落主文件的页，那种副本根本打不开，
而它存在的唯一理由就是「建新之前保住旧数据」。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app import db as dbmod


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "app.db"
    monkeypatch.setattr(dbmod, "DB_PATH", path)
    dbmod._wal_ready = False
    return path


def _version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("pragma user_version").fetchone()[0])
    finally:
        conn.close()


def _tables(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute(
            "select name from sqlite_master where type='table'")}
    finally:
        conn.close()


def _seed_old_db(path: Path, version: int) -> None:
    """造一个「别的版本写出的库」：带档位、带一张本代码不认识的表和一行真数据。"""
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("create table future_only (payload text)")
        conn.execute("insert into future_only values ('不得丢的数据')")
    conn.close()
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(f"pragma user_version={version}")
    conn.close()


def test_新库建完就打上档位(db_path: Path):
    dbmod.init_db()

    assert db_path.is_file()
    assert _version(db_path) == dbmod._DB_VERSION
    assert "assets" in _tables(db_path)
    assert list(db_path.parent.glob("app.db.bak-*")) == []


def test_零档旧库就地升档不动数据(db_path: Path):
    """历史装机库全是 0 档 ⇒ 必须走加法迁移，不许备份、更不许建新。"""
    _seed_old_db(db_path, 0)

    dbmod.init_db()

    assert _version(db_path) == dbmod._DB_VERSION
    tables = _tables(db_path)
    assert "future_only" in tables, "就地迁移不得丢既有表"
    assert "assets" in tables, "就地迁移要补齐本代码需要的表"
    assert list(db_path.parent.glob("app.db.bak-*")) == []

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("select payload from future_only").fetchone()[0] == "不得丢的数据"
    finally:
        conn.close()


def test_建新前的备份是打得开读得出数据的(db_path: Path):
    """WAL 库的备份必须能打开——这是走在线备份 API 而不是拷文件的全部理由。"""
    conn = sqlite3.connect(db_path)
    conn.execute("pragma journal_mode=WAL")
    with conn:
        conn.execute("create table t(v text)")
        conn.execute("insert into t values ('还没落主文件的页)')")
    conn.close()

    backup = dbmod._backup_db()

    assert backup is not None
    assert backup.name.startswith("app.db.bak-")
    conn = sqlite3.connect(backup)
    try:
        assert conn.execute("select v from t").fetchone()[0] == "还没落主文件的页)"
    finally:
        conn.close()


def test_更高档位先备份再建新(db_path: Path):
    _seed_old_db(db_path, dbmod._DB_VERSION + 1)

    dbmod.init_db()

    backups = sorted(db_path.parent.glob("app.db.bak-*"))
    assert len(backups) == 1
    # 备份必须是**打得开、读得出旧数据**的库：WAL 下裸拷贝做不到，所以走 backup API
    conn = sqlite3.connect(backups[0])
    try:
        assert conn.execute("select payload from future_only").fetchone()[0] == "不得丢的数据"
        assert int(conn.execute("pragma user_version").fetchone()[0]) == dbmod._DB_VERSION + 1
    finally:
        conn.close()

    assert _version(db_path) == dbmod._DB_VERSION
    assert "future_only" not in _tables(db_path), "建新后的库不该带着新档的表"
    assert "assets" in _tables(db_path)


def test_更高档且备份失败则一条DDL都不跑(db_path: Path, monkeypatch):
    _seed_old_db(db_path, dbmod._DB_VERSION + 1)
    monkeypatch.setattr(dbmod, "_backup_db", lambda: None)

    dbmod.init_db()

    assert not list(db_path.parent.glob("app.db.bak-*"))
    assert _version(db_path) == dbmod._DB_VERSION + 1, "拒动库时档位不该被改写"
    assert _tables(db_path) == {"future_only"}, "备份失败时不得建任何本代码的表"


def test_备份名跟随时间戳格式(db_path: Path, monkeypatch):
    import time

    _seed_old_db(db_path, dbmod._DB_VERSION + 1)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    backup = dbmod._backup_db()

    assert backup == db_path.with_name(f"app.db.bak-{stamp}")
    assert backup.is_file()


def test_重复建库幂等(db_path: Path):
    """`init_db` 每次启动都跑，第二次不得把库跑坏（档位、表集、数据都不动）。"""
    dbmod.init_db()
    tables = _tables(db_path)
    with dbmod.get_connection() as conn:
        conn.execute("insert into ai_providers values ('p','n','t','u','k','m',1,1,'a','b')")

    dbmod.init_db()

    assert _tables(db_path) == tables
    assert _version(db_path) == dbmod._DB_VERSION
    with dbmod.get_connection() as conn:
        assert conn.execute(
            "select count(*) from ai_providers").fetchone()[0] == 1
