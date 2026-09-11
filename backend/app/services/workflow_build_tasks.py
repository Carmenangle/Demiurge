"""AI 搭工作流后台任务：SQLite 持久化 + 租约式 FIFO worker。

任务参数和结果落本地 app.db；SQLite 事务保证多进程只认领一次，租约过期后自动恢复。
模型调用仍复用 workflow_builder，路由只负责 HTTP 适配。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from uuid import uuid4

from app.db import get_connection
from app.services import build_session_store, llm, workflow_builder
from app.services.rag_backend import EmbedConfig

_WAKE = threading.Condition()
_WORKER: threading.Thread | None = None
_CONTROLLERS: dict[str, threading.Event] = {}
_WORKER_ID = f"{os.getpid()}:{uuid4().hex}"

LEASE_MS = 60_000
HEARTBEAT_SECONDS = 10
TASK_RETENTION_MS = 7 * 24 * 60 * 60 * 1000
TASK_RETENTION_LIMIT = 200
TERMINAL_STATUSES = ("done", "error", "cancelled")


def _now() -> int:
    return int(time.time() * 1000)


def _build_chat(base_url: str, api_key: str, model: str, system: str, user: str,
                temperature: float = 0.7, proxy: str = "", retries: int = 1) -> str:
    return llm.chat(base_url, api_key, model, system, user, temperature, proxy=proxy, retries=1)


def start_worker() -> None:
    """启动当前进程的 worker；不同进程通过数据库原子认领协调。"""
    global _WORKER
    cleanup_finished()
    with _WAKE:
        if _WORKER and _WORKER.is_alive():
            _WAKE.notify_all()
            return
        _WORKER = threading.Thread(target=_worker_loop, name="workflow-build-worker", daemon=True)
        _WORKER.start()


def _worker_loop() -> None:
    while True:
        try:
            task = _claim_next()
        except sqlite3.OperationalError:
            with _WAKE:
                _WAKE.wait(timeout=0.25)
            continue
        if task is None:
            with _WAKE:
                _WAKE.wait(timeout=1.0)
            continue
        task_id = str(task["id"])
        cancel = threading.Event()
        _CONTROLLERS[task_id] = cancel
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=_heartbeat_loop,
            args=(task_id, heartbeat_stop),
            name=f"workflow-build-heartbeat-{task_id[:8]}",
            daemon=True,
        )
        heartbeat.start()
        try:
            result = _execute(json.loads(task["payload"]), cancel)
            if cancel.is_set():
                _finish_claim(task, "cancelled", error="已停止")
            else:
                _persist_and_finish(task, "done", result=result)
        except Exception as exc:  # noqa: BLE001 - worker must persist failures and continue queue
            if cancel.is_set():
                _finish_claim(task, "cancelled", error="已停止")
            else:
                _persist_and_finish(task, "error", error=str(exc))
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1)
            _CONTROLLERS.pop(task_id, None)


def _claim_next():
    """在 SQLite 写事务中认领一个任务，避免多 worker 重复执行。"""
    now = _now()
    with get_connection() as connection:
        connection.execute("begin immediate")
        row = connection.execute(
            """
            select id from workflow_build_tasks
            where status='queued' or (status='running' and lease_expires_at<=?)
            order by created_at asc limit 1
            """,
            (now,),
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        changed = connection.execute(
            """
            update workflow_build_tasks
            set status='running', worker_id=?, lease_expires_at=?, updated_at=?
            where id=? and (status='queued' or (status='running' and lease_expires_at<=?))
            """,
            (_WORKER_ID, now + LEASE_MS, now, row["id"], now),
        ).rowcount
        if changed != 1:
            connection.rollback()
            return None
        claimed = connection.execute(
            "select * from workflow_build_tasks where id=?", (row["id"],)
        ).fetchone()
        connection.commit()
        return claimed


def _heartbeat_loop(task_id: str, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_SECONDS):
        now = _now()
        try:
            with get_connection() as connection:
                changed = connection.execute(
                    """
                    update workflow_build_tasks set lease_expires_at=?, updated_at=?
                    where id=? and status='running' and worker_id=?
                    """,
                    (now + LEASE_MS, now, task_id, _WORKER_ID),
                ).rowcount
        except sqlite3.OperationalError:
            continue
        if changed != 1:
            return


def _persist_and_finish(task, status: str, result: dict | None = None, error: str = "") -> bool:
    task_id = str(task["id"])
    result_json = json.dumps(result, ensure_ascii=False) if result is not None else ""
    with get_connection() as connection:
        connection.execute("begin immediate")
        owned = connection.execute(
            "select 1 from workflow_build_tasks where id=? and status='running' and worker_id=?",
            (task_id, _WORKER_ID),
        ).fetchone()
        if owned is None:
            connection.rollback()
            return False
        build_session_store.apply_task_result(
            str(task["session_id"]), task_id, str(task["mode"]), str(task["need"]),
            result=result, error=error,
        )
        changed = connection.execute(
            """
            update workflow_build_tasks
            set status=?, result=?, error=?, updated_at=?, worker_id='', lease_expires_at=0
            where id=? and status='running' and worker_id=?
            """,
            (status, result_json, error, _now(), task_id, _WORKER_ID),
        ).rowcount
        if changed != 1:
            connection.rollback()
            return False
        connection.commit()
    cleanup_finished()
    return True


def _finish_claim(task, status: str, result: object | None = None, error: str = "") -> bool:
    task_id = str(task["id"])
    result_json = json.dumps(result, ensure_ascii=False) if result is not None else ""
    with get_connection() as connection:
        changed = connection.execute(
            """
            update workflow_build_tasks
            set status=?, result=?, error=?, updated_at=?, worker_id='', lease_expires_at=0
            where id=? and status='running' and worker_id=?
            """,
            (status, result_json, error, _now(), task_id, _WORKER_ID),
        ).rowcount
    if changed == 1:
        cleanup_finished()
        return True
    return False


def _set_status(task_id: str, status: str, result: object | None = None, error: str = "") -> None:
    result_json = json.dumps(result, ensure_ascii=False) if result is not None else ""
    with get_connection() as connection:
        connection.execute(
            """
            update workflow_build_tasks
            set status=?, result=?, error=?, updated_at=?, worker_id='', lease_expires_at=0
            where id=?
            """,
            (status, result_json, error, _now(), task_id),
        )
    if status in TERMINAL_STATUSES:
        cleanup_finished()


def cleanup_finished() -> int:
    """清除过期终态任务，并限制近期终态记录总量。"""
    now = _now()
    placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
    with get_connection() as connection:
        cursor = connection.execute(
            f"""
            delete from workflow_build_tasks
            where status in ({placeholders}) and (
                updated_at < ? or id not in (
                    select id from workflow_build_tasks
                    where status in ({placeholders})
                    order by updated_at desc, created_at desc limit ?
                )
            )
            """,
            (*TERMINAL_STATUSES, now - TASK_RETENTION_MS,
             *TERMINAL_STATUSES, TASK_RETENTION_LIMIT),
        )
        return cursor.rowcount


def _execute(payload: dict, cancel: threading.Event) -> dict:
    if cancel.is_set():
        raise RuntimeError("已停止")
    cfg = EmbedConfig(
        base_url=payload.get("embed_base_url", ""),
        api_key=payload.get("embed_api_key", ""),
        embed_model=payload.get("embed_model", "embedding-3"),
        mode=payload.get("embed_mode", "remote"),
        model_dir=payload.get("embed_model_dir", ""),
        reranker_dir=payload.get("reranker_model_dir", ""),
    )
    common = dict(
        base_url=payload.get("base_url", ""), api_key=payload.get("api_key", ""),
        model=payload.get("model", ""), proxy=payload.get("proxy", ""),
        cfg=cfg, need=payload.get("need", ""), comfy_url=payload.get("comfy_url", ""),
        current_graph=payload.get("current_graph") or {}, history=payload.get("history") or [],
    )
    mode = payload.get("mode")
    if mode == "plan":
        return workflow_builder.build_plan(_build_chat, **common)
    if mode == "module":
        return workflow_builder.build_module(_build_chat, max_retries=2, **common)
    if mode == "workflow":
        return workflow_builder.build_graph(
            _build_chat, workflow_dir=payload.get("workflow_dir", ""), name="",
            max_retries=4, save=False, **common,
        )
    if mode == "direct":
        return workflow_builder.build_direct(_build_chat, **common)
    raise ValueError(f"未知工作流搭建模式：{mode}")


def enqueue(payload: dict) -> dict:
    task_id = uuid4().hex
    now = _now()
    with get_connection() as connection:
        connection.execute(
            "insert into workflow_build_tasks(id,session_id,mode,need,payload,status,created_at,updated_at) values(?,?,?,?,?,?,?,?)",
            (task_id, payload.get("session_id", "draft"), payload.get("mode", "direct"), payload.get("need", ""),
             json.dumps(payload, ensure_ascii=False), "queued", now, now),
        )
    start_worker()
    with _WAKE:
        _WAKE.notify_all()
    return get(task_id) or {}


def list_tasks(session_id: str = "", limit: int = 100) -> list[dict]:
    query = "select * from workflow_build_tasks"
    params: list[object] = []
    if session_id:
        query += " where session_id=?"
        params.append(session_id)
    query += " order by case when status in ('queued','running') then 0 else 1 end, created_at desc limit ?"
    params.append(max(1, min(limit, 200)))
    with get_connection() as connection:
        rows = connection.execute(query, params).fetchall()
    return [_public(row) for row in rows]


def get(task_id: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute("select * from workflow_build_tasks where id=?", (task_id,)).fetchone()
    return _public(row) if row else None


def cancel(task_id: str) -> dict | None:
    controller = _CONTROLLERS.get(task_id)
    if controller:
        controller.set()
    with get_connection() as connection:
        changed = connection.execute(
            """
            update workflow_build_tasks
            set status='cancelled', error='已取消', updated_at=?,
                worker_id='', lease_expires_at=0
            where id=? and status in ('queued','running')
            """,
            (_now(), task_id),
        ).rowcount
        if changed == 0 and connection.execute(
            "select 1 from workflow_build_tasks where id=?", (task_id,)
        ).fetchone() is None:
            return None
    cleanup_finished()
    with _WAKE:
        _WAKE.notify_all()
    return get(task_id)


def _public(row) -> dict:
    result = {}
    if row["result"]:
        try:
            result = json.loads(row["result"])
        except json.JSONDecodeError:
            result = {}
    return {
        "id": row["id"], "session_id": row["session_id"], "mode": row["mode"],
        "need": row["need"], "status": row["status"], "result": result,
        "error": row["error"], "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
