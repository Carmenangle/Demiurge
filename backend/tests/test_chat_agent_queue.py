import sqlite3
import threading

from app.services import chat_agent_queue as queue
from app.services import thread_admission


SCHEMA = """
create table chat_agent_queue (
    id text primary key, thread_id text not null, need text not null,
    payload text not null, status text not null, error text not null default '',
    created_at integer not null, updated_at integer not null,
    worker_id text not null default '', lease_expires_at integer not null default 0
)
"""


def _connection_factory(path):
    def connect():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection
    return connect


def _prepare(tmp_path, monkeypatch):
    path = tmp_path / "queue.db"
    with _connection_factory(path)() as connection:
        connection.execute(SCHEMA)
    monkeypatch.setattr(queue, "get_connection", _connection_factory(path))
    monkeypatch.setattr(queue, "_now", lambda: 100)
    monkeypatch.setattr(queue, "_WORKER_ID", "test-worker")
    return path


def test_enqueue_hides_payload_and_secret(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(queue, "start_worker", lambda: None)

    created = queue.enqueue({
        "thread_id": "repo-1", "message": "画只猫", "api_key": "secret", "images": [],
    })
    assert created["status"] == "queued"
    assert created["thread_id"] == "repo-1"
    assert created["need"] == "画只猫"
    assert "payload" not in created
    assert "secret" not in str(created)


def test_claim_is_atomic_and_recovers_only_expired_lease(tmp_path, monkeypatch):
    path = _prepare(tmp_path, monkeypatch)
    with _connection_factory(path)() as connection:
        connection.executemany(
            "insert into chat_agent_queue values(?,?,?,?,?,?,?,?,?,?)",
            [
                ("live", "r", "l", "{}", "running", "", 1, 1, "old-worker", 101),
                ("expired", "r", "e", "{}", "running", "", 2, 2, "dead-worker", 99),
            ],
        )
    claimed = queue._claim_next()
    assert claimed["id"] == "expired"
    assert claimed["worker_id"] == "test-worker"
    assert queue._claim_next() is None
    assert queue.get("live")["status"] == "running"


def test_release_claim_returns_to_queue(tmp_path, monkeypatch):
    path = _prepare(tmp_path, monkeypatch)
    with _connection_factory(path)() as connection:
        connection.execute(
            "insert into chat_agent_queue values(?,?,?,?,?,?,?,?,?,?)",
            ("t", "r", "n", "{}", "queued", "", 1, 1, "", 0),
        )
    claimed = queue._claim_next()
    assert claimed["status"] == "running"
    monkeypatch.setattr(queue._WAKE, "wait", lambda timeout=None: None)
    queue._release_claim("t")
    assert queue.get("t")["status"] == "queued"
    with _connection_factory(path)() as connection:
        row = connection.execute("select worker_id from chat_agent_queue where id='t'").fetchone()
    assert row["worker_id"] == ""


def test_cancel_only_removes_queued_not_running(tmp_path, monkeypatch):
    path = _prepare(tmp_path, monkeypatch)
    with _connection_factory(path)() as connection:
        connection.executemany(
            "insert into chat_agent_queue values(?,?,?,?,?,?,?,?,?,?)",
            [
                ("queued", "r", "q", "{}", "queued", "", 1, 1, "", 0),
                ("running", "r", "run", "{}", "running", "", 2, 2, "w", 999),
            ],
        )
    assert queue.cancel("queued")["status"] == "cancelled"
    # running 已交给 agent_runner，队列 cancel 不动它
    assert queue.cancel("running")["status"] == "running"
    assert queue.cancel("missing") is None


def test_cleanup_keeps_active_and_only_recent_terminal_rows(tmp_path, monkeypatch):
    path = _prepare(tmp_path, monkeypatch)
    with _connection_factory(path)() as connection:
        connection.executemany(
            "insert into chat_agent_queue values(?,?,?,?,?,?,?,?,?,?)",
            [
                ("queued", "r", "q", "{}", "queued", "", 1, 1, "", 0),
                ("old", "r", "o", "{}", "done", "", 2, 10, "", 0),
                ("new-1", "r", "1", "{}", "done", "", 3, 91, "", 0),
                ("new-2", "r", "2", "{}", "error", "e", 4, 92, "", 0),
                ("new-3", "r", "3", "{}", "cancelled", "", 5, 93, "", 0),
            ],
        )
    monkeypatch.setattr(queue, "TASK_RETENTION_MS", 50)
    monkeypatch.setattr(queue, "TASK_RETENTION_LIMIT", 2)
    assert queue.cleanup_finished() == 2
    assert {item["id"] for item in queue.list_tasks()} == {"queued", "new-2", "new-3"}


def test_list_tasks_filters_thread_and_orders_active_first(tmp_path, monkeypatch):
    path = _prepare(tmp_path, monkeypatch)
    with _connection_factory(path)() as connection:
        connection.executemany(
            "insert into chat_agent_queue values(?,?,?,?,?,?,?,?,?,?)",
            [
                ("done", "repo-1", "d", "{}", "done", "", 1, 1, "", 0),
                ("queued", "repo-1", "q", "{}", "queued", "", 2, 2, "", 0),
                ("other", "repo-2", "x", "{}", "queued", "", 3, 3, "", 0),
            ],
        )
    ids = [item["id"] for item in queue.list_tasks("repo-1")]
    assert ids == ["queued", "done"]


def test_execute_headless_reuses_agent_runner(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    captured = {}

    def fake_run(context):
        captured["thread_id"] = context.thread_id
        captured["message"] = context.message
        return "Q"

    monkeypatch.setattr(queue.agent_runner, "run_multi_stream", fake_run)
    monkeypatch.setattr(queue.agent_runner, "drain", lambda q: iter([{"delta": "x"}]))

    queue._execute({"thread_id": "repo-1", "message": "画只猫", "images": []})
    assert captured == {"thread_id": "repo-1", "message": "画只猫"}


def test_execute_lets_active_thread_stay_queued(tmp_path, monkeypatch):
    path = _prepare(tmp_path, monkeypatch)
    with _connection_factory(path)() as connection:
        connection.execute(
            "insert into chat_agent_queue values(?,?,?,?,?,?,?,?,?,?)",
            ("t", "repo-1", "n", '{"thread_id": "repo-1", "message": "m"}',
             "queued", "", 1, 1, "", 0),
        )

    def busy(_context):
        raise queue.agent_runner.RunAlreadyActive("该对话已有生成任务正在运行")

    monkeypatch.setattr(queue.agent_runner, "run_multi_stream", busy)
    monkeypatch.setattr(queue._WAKE, "wait", lambda timeout=None: None)
    claimed = queue._claim_next()
    try:
        queue._execute(__import__("json").loads(claimed["payload"]))
        raise AssertionError("应抛 RunAlreadyActive")
    except queue.agent_runner.RunAlreadyActive:
        queue._release_claim(claimed["id"])
    assert queue.get("t")["status"] == "queued"


def test_active_threads_lists_running_repos():
    event = threading.Event()
    admission = thread_admission.admit("repo-active", event)
    try:
        assert "repo-active" in thread_admission.active_threads()
    finally:
        thread_admission.release(admission)
    assert "repo-active" not in thread_admission.active_threads()
