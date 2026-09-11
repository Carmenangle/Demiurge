import sqlite3
import threading
import time

from app import db
from app.services import build_session_store
from app.services import workflow_build_tasks as tasks


SCHEMA = """
create table workflow_build_tasks (
    id text primary key, session_id text not null, mode text not null, need text not null,
    payload text not null, status text not null, result text not null default '',
    error text not null default '', created_at integer not null, updated_at integer not null,
    worker_id text not null default '', lease_expires_at integer not null default 0
)
"""


def _connection_factory(path):
    def connect():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection
    return connect


def test_persistent_task_lifecycle_hides_payload(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    with _connection_factory(path)() as connection:
        connection.execute(SCHEMA)
    monkeypatch.setattr(tasks, "get_connection", _connection_factory(path))
    monkeypatch.setattr(tasks, "start_worker", lambda: None)
    monkeypatch.setattr(tasks, "_now", lambda: 100)

    created = tasks.enqueue({
        "session_id": "session-1", "mode": "direct", "need": "搭一个文生图工作流",
        "api_key": "secret", "current_graph": {}, "history": [],
    })
    assert created["status"] == "queued"
    assert "payload" not in created
    assert "secret" not in str(created)

    tasks._set_status(created["id"], "done", result={"ok": True, "graph": {"1": {}}})
    restored = tasks.get(created["id"])
    assert restored["status"] == "done"
    assert restored["result"]["graph"] == {"1": {}}


def test_cancel_and_start_does_not_reset_live_claim(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    with _connection_factory(path)() as connection:
        connection.execute(SCHEMA)
        connection.execute(
            "insert into workflow_build_tasks values(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("running", "s", "direct", "need", "{}", "running", "", "", 1, 1,
             "another-worker", 999),
        )
        connection.execute(
            "insert into workflow_build_tasks values(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("queued", "s", "direct", "later", "{}", "queued", "", "", 2, 2, "", 0),
        )
    monkeypatch.setattr(tasks, "get_connection", _connection_factory(path))
    monkeypatch.setattr(tasks, "_WORKER", None)
    monkeypatch.setattr(tasks, "_now", lambda: 100)

    class Worker:
        def __init__(self, **_kwargs):
            self.started = False
        def is_alive(self):
            return self.started
        def start(self):
            self.started = True

    monkeypatch.setattr(tasks.threading, "Thread", Worker)
    tasks.start_worker()
    assert tasks.get("running")["status"] == "running"

    cancelled = tasks.cancel("queued")
    assert cancelled["status"] == "cancelled"
    assert tasks.get("queued")["status"] == "cancelled"


def test_claim_is_atomic_and_recovers_only_expired_lease(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    with _connection_factory(path)() as connection:
        connection.execute(SCHEMA)
        connection.executemany(
            "insert into workflow_build_tasks values(?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("live", "s", "direct", "live", "{}", "running", "", "", 1, 1,
                 "old-worker", 101),
                ("expired", "s", "direct", "expired", "{}", "running", "", "", 2, 2,
                 "dead-worker", 99),
            ],
        )
    monkeypatch.setattr(tasks, "get_connection", _connection_factory(path))
    monkeypatch.setattr(tasks, "_now", lambda: 100)
    monkeypatch.setattr(tasks, "_WORKER_ID", "test-worker")

    claimed = tasks._claim_next()
    assert claimed["id"] == "expired"
    assert claimed["worker_id"] == "test-worker"
    assert tasks._claim_next() is None
    assert tasks.get("live")["status"] == "running"


def test_cleanup_keeps_active_and_only_recent_terminal_rows(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    with _connection_factory(path)() as connection:
        connection.execute(SCHEMA)
        rows = [
            ("queued", "s", "direct", "q", "{}", "queued", "", "", 1, 1, "", 0),
            ("old", "s", "direct", "o", "{}", "done", "", "", 2, 10, "", 0),
            ("new-1", "s", "direct", "1", "{}", "done", "", "", 3, 91, "", 0),
            ("new-2", "s", "direct", "2", "{}", "error", "", "e", 4, 92, "", 0),
            ("new-3", "s", "direct", "3", "{}", "cancelled", "", "", 5, 93, "", 0),
        ]
        connection.executemany(
            "insert into workflow_build_tasks values(?,?,?,?,?,?,?,?,?,?,?,?)", rows,
        )
    monkeypatch.setattr(tasks, "get_connection", _connection_factory(path))
    monkeypatch.setattr(tasks, "_now", lambda: 100)
    monkeypatch.setattr(tasks, "TASK_RETENTION_MS", 50)
    monkeypatch.setattr(tasks, "TASK_RETENTION_LIMIT", 2)

    assert tasks.cleanup_finished() == 2
    assert {item["id"] for item in tasks.list_tasks()} == {"queued", "new-2", "new-3"}


def test_claim_completion_persists_session_before_done(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    with _connection_factory(path)() as connection:
        connection.execute(SCHEMA)
        connection.execute(
            "insert into workflow_build_tasks values(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("task", "session", "direct", "need", "{}", "queued", "", "", 1, 1, "", 0),
        )
    monkeypatch.setattr(tasks, "get_connection", _connection_factory(path))
    monkeypatch.setattr(tasks, "_now", lambda: 100)
    monkeypatch.setattr(tasks, "_WORKER_ID", "test-worker")
    seen = []

    def persist(*args, **kwargs):
        seen.append((args, kwargs, tasks.get("task")["status"]))
        return True

    monkeypatch.setattr(tasks.build_session_store, "apply_task_result", persist)
    claimed = tasks._claim_next()
    assert tasks._persist_and_finish(claimed, "done", result={"ok": True}) is True
    assert seen[0][2] == "running"
    assert seen[0][0][:4] == ("session", "task", "direct", "need")
    assert tasks.get("task")["status"] == "done"


def test_cancelled_claim_cannot_persist_late_result(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    with _connection_factory(path)() as connection:
        connection.execute(SCHEMA)
        connection.execute(
            "insert into workflow_build_tasks values(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("task", "session", "direct", "need", "{}", "queued", "", "", 1, 1, "", 0),
        )
    monkeypatch.setattr(tasks, "get_connection", _connection_factory(path))
    monkeypatch.setattr(tasks, "_now", lambda: 100)
    monkeypatch.setattr(tasks, "_WORKER_ID", "test-worker")
    persisted = []
    monkeypatch.setattr(
        tasks.build_session_store, "apply_task_result",
        lambda *args, **kwargs: persisted.append((args, kwargs)),
    )

    claimed = tasks._claim_next()
    assert tasks.cancel("task")["status"] == "cancelled"
    assert tasks._persist_and_finish(claimed, "done", result={"ok": True}) is False
    assert persisted == []
    assert tasks.get("task")["status"] == "cancelled"


def test_cancel_and_persist_are_serialized(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    with _connection_factory(path)() as connection:
        connection.execute(SCHEMA)
        connection.execute(
            "insert into workflow_build_tasks values(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("task", "session", "direct", "need", "{}", "queued", "", "", 1, 1, "", 0),
        )
    monkeypatch.setattr(tasks, "get_connection", _connection_factory(path))
    monkeypatch.setattr(tasks, "_now", lambda: 100)
    monkeypatch.setattr(tasks, "_WORKER_ID", "test-worker")
    entered = threading.Event()
    release = threading.Event()

    def persist(*_args, **_kwargs):
        entered.set()
        assert release.wait(2)
        return True

    monkeypatch.setattr(tasks.build_session_store, "apply_task_result", persist)
    claimed = tasks._claim_next()
    persist_thread = threading.Thread(
        target=tasks._persist_and_finish, args=(claimed, "done", {"ok": True}),
    )
    persist_thread.start()
    assert entered.wait(2)
    cancel_thread = threading.Thread(target=tasks.cancel, args=("task",))
    cancel_thread.start()
    time.sleep(0.05)
    assert cancel_thread.is_alive()
    release.set()
    persist_thread.join(2)
    cancel_thread.join(2)
    assert not persist_thread.is_alive() and not cancel_thread.is_alive()
    assert tasks.get("task")["status"] == "done"


def test_retry_after_session_write_is_idempotent(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    session_dir = tmp_path / "sessions"
    with _connection_factory(path)() as connection:
        connection.execute(SCHEMA)
        connection.execute(
            "insert into workflow_build_tasks values(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("task", "session", "direct", "need", "{}", "running", "", "", 1, 1,
             "test-worker", 200),
        )
    monkeypatch.setattr(tasks, "get_connection", _connection_factory(path))
    monkeypatch.setattr(tasks, "_now", lambda: 100)
    monkeypatch.setattr(tasks, "_WORKER_ID", "test-worker")
    monkeypatch.setattr(build_session_store, "SESS_DIR", session_dir)
    result = {"ok": True, "graph": {"1": {}}}
    build_session_store.save_session("session", "S", [], {})
    build_session_store.apply_task_result("session", "task", "direct", "need", result=result)
    with _connection_factory(path)() as connection:
        task = connection.execute(
            "select * from workflow_build_tasks where id='task'"
        ).fetchone()

    assert tasks._persist_and_finish(task, "done", result=result) is True
    messages = build_session_store.get_session("session")["msgs"]
    assert [msg.get("task_id") for msg in messages].count("task") == 1


def test_init_db_migrates_legacy_task_table(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    with _connection_factory(path)() as connection:
        connection.execute(
            """
            create table workflow_build_tasks (
                id text primary key, session_id text not null, mode text not null,
                need text not null, payload text not null, status text not null,
                result text not null default '', error text not null default '',
                created_at integer not null, updated_at integer not null
            )
            """
        )
    monkeypatch.setattr(db, "get_connection", _connection_factory(path))

    db.init_db()

    with _connection_factory(path)() as connection:
        columns = {
            row["name"] for row in connection.execute("pragma table_info(workflow_build_tasks)")
        }
    assert {"worker_id", "lease_expires_at"} <= columns
