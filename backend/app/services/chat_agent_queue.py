"""仓库对话排队消息：SQLite 持久化 + 租约式 FIFO worker。

只承接「忙时排队、还没发出」的后续消息；一旦认领即交回 agent_runner 跑完并落盘，
不在此保存第二份运行态。同 thread 已有活动运行时让位等待，保证串行。
刷新/重开浏览器后排队消息仍会被 worker 重新认领执行——这是与前端内存队列的本质差异。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from uuid import uuid4

from app.db import get_connection
from app.services import agent_runner
from app.services.agent_contracts import ModelConfig, RunContext

_WAKE = threading.Condition()
_WORKER: threading.Thread | None = None
_WORKER_ID = f"{os.getpid()}:{uuid4().hex}"

LEASE_MS = 60_000
HEARTBEAT_SECONDS = 10
TASK_RETENTION_MS = 7 * 24 * 60 * 60 * 1000
TASK_RETENTION_LIMIT = 200
TERMINAL_STATUSES = ("done", "error", "cancelled")


def _now() -> int:
    return int(time.time() * 1000)


def start_worker() -> None:
    """启动当前进程的 worker；不同进程通过数据库原子认领协调。"""
    global _WORKER
    cleanup_finished()
    with _WAKE:
        if _WORKER and _WORKER.is_alive():
            _WAKE.notify_all()
            return
        _WORKER = threading.Thread(target=_worker_loop, name="chat-agent-queue-worker", daemon=True)
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
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=_heartbeat_loop,
            args=(task_id, heartbeat_stop),
            name=f"chat-agent-queue-heartbeat-{task_id[:8]}",
            daemon=True,
        )
        heartbeat.start()
        try:
            _execute(json.loads(task["payload"]))
            _finish(task_id, "done")
        except agent_runner.RunAlreadyActive:
            # 该 thread 正被其他运行占用（前端首条流式或另一 worker）：让位，退回排队稍后重试。
            _release_claim(task_id)
        except Exception as exc:  # noqa: BLE001 - worker 必须落盘失败并继续队列
            _finish(task_id, "error", error=str(exc))
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1)


def _claim_next():
    """在 SQLite 写事务中认领一个排队消息，避免多 worker 重复执行。"""
    now = _now()
    with get_connection() as connection:
        connection.execute("begin immediate")
        row = connection.execute(
            """
            select id from chat_agent_queue
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
            update chat_agent_queue
            set status='running', worker_id=?, lease_expires_at=?, updated_at=?
            where id=? and (status='queued' or (status='running' and lease_expires_at<=?))
            """,
            (_WORKER_ID, now + LEASE_MS, now, row["id"], now),
        ).rowcount
        if changed != 1:
            connection.rollback()
            return None
        claimed = connection.execute(
            "select * from chat_agent_queue where id=?", (row["id"],)
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
                    update chat_agent_queue set lease_expires_at=?, updated_at=?
                    where id=? and status='running' and worker_id=?
                    """,
                    (now + LEASE_MS, now, task_id, _WORKER_ID),
                ).rowcount
        except sqlite3.OperationalError:
            continue
        if changed != 1:
            return


def _finish(task_id: str, status: str, error: str = "") -> None:
    with get_connection() as connection:
        connection.execute(
            """
            update chat_agent_queue
            set status=?, error=?, updated_at=?, worker_id='', lease_expires_at=0
            where id=? and status='running' and worker_id=?
            """,
            (status, error, _now(), task_id, _WORKER_ID),
        )
    cleanup_finished()


def _release_claim(task_id: str) -> None:
    """让位：把自己认领的 running 退回 queued，稍后重试（不计为终态）。"""
    with get_connection() as connection:
        connection.execute(
            """
            update chat_agent_queue
            set status='queued', worker_id='', lease_expires_at=0, updated_at=?
            where id=? and status='running' and worker_id=?
            """,
            (_now(), task_id, _WORKER_ID),
        )
    # 稍等再唤醒，避免忙等占用运行中的 thread。
    with _WAKE:
        _WAKE.wait(timeout=1.0)
        _WAKE.notify_all()


def _execute(payload: dict) -> None:
    """构造 RunContext 交回 agent_runner headless 跑完（复用现有落盘/记忆/审批落盘）。"""
    mask = payload.get("image_mask")
    context = RunContext(
        thread_id=payload.get("thread_id", "home"),
        message=payload.get("message", ""),
        images=payload.get("images") or [],
        image_mask=mask if isinstance(mask, dict) else None,
        chat=ModelConfig(payload.get("base_url", ""), payload.get("api_key", ""), payload.get("model", "")),
        generation=ModelConfig(
            payload.get("gen_base_url", ""), payload.get("gen_api_key", ""), payload.get("gen_model", "")),
        video=ModelConfig(
            payload.get("video_base_url", ""), payload.get("video_api_key", ""), payload.get("video_model", "")),
        embedding=ModelConfig(
            payload.get("embed_base_url", ""), payload.get("embed_api_key", ""),
            payload.get("embed_model", "embedding-3")),
        size=payload.get("size", "1024x1024"),
        image_quality=payload.get("image_quality", "high"),
        output_dir=payload.get("output_dir", ""),
        repo_id=payload.get("repo_id", "") or payload.get("thread_id", "home"),
        message_id=payload.get("message_id", ""),
        proxy_url=payload.get("proxy_url", ""),
        route_model=payload.get("route_model", ""),
        style_template=payload.get("style_template", ""),
        agent_id=payload.get("agent_id", ""),
        user_message_id=payload.get("user_message_id", ""),
        context_max_tokens=payload.get("context_max_tokens", 20_000),
    )
    q = agent_runner.run_multi_stream(context)
    for _ in agent_runner.drain(q):
        pass  # headless：事件丢弃，落盘由 agent_runner worker 内部完成


def cleanup_finished() -> int:
    """清除过期终态任务，并限制近期终态记录总量。"""
    now = _now()
    placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
    with get_connection() as connection:
        cursor = connection.execute(
            f"""
            delete from chat_agent_queue
            where status in ({placeholders}) and (
                updated_at < ? or id not in (
                    select id from chat_agent_queue
                    where status in ({placeholders})
                    order by updated_at desc, created_at desc limit ?
                )
            )
            """,
            (*TERMINAL_STATUSES, now - TASK_RETENTION_MS,
             *TERMINAL_STATUSES, TASK_RETENTION_LIMIT),
        )
        return cursor.rowcount


def enqueue(payload: dict) -> dict:
    task_id = uuid4().hex
    now = _now()
    with get_connection() as connection:
        connection.execute(
            "insert into chat_agent_queue(id,thread_id,need,payload,status,created_at,updated_at)"
            " values(?,?,?,?,?,?,?)",
            (task_id, payload.get("thread_id", "home"), payload.get("message", ""),
             json.dumps(payload, ensure_ascii=False), "queued", now, now),
        )
    start_worker()
    with _WAKE:
        _WAKE.notify_all()
    return get(task_id) or {}


def list_tasks(thread_id: str = "", limit: int = 100) -> list[dict]:
    query = "select * from chat_agent_queue"
    params: list[object] = []
    if thread_id:
        query += " where thread_id=?"
        params.append(thread_id)
    query += " order by case when status in ('queued','running') then 0 else 1 end, created_at asc limit ?"
    params.append(max(1, min(limit, 200)))
    with get_connection() as connection:
        rows = connection.execute(query, params).fetchall()
    return [_public(row) for row in rows]


def get(task_id: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute("select * from chat_agent_queue where id=?", (task_id,)).fetchone()
    return _public(row) if row else None


def cancel(task_id: str) -> dict | None:
    """取消排队消息：只能取消尚未发出的 queued（running 已交给 agent_runner，用其自身 cancel）。"""
    with get_connection() as connection:
        changed = connection.execute(
            """
            update chat_agent_queue
            set status='cancelled', error='已取消', updated_at=?, worker_id='', lease_expires_at=0
            where id=? and status='queued'
            """,
            (_now(), task_id),
        ).rowcount
        if changed == 0 and connection.execute(
            "select 1 from chat_agent_queue where id=?", (task_id,)
        ).fetchone() is None:
            return None
    cleanup_finished()
    with _WAKE:
        _WAKE.notify_all()
    return get(task_id)


def _public(row) -> dict:
    return {
        "id": row["id"], "thread_id": row["thread_id"], "need": row["need"],
        "status": row["status"], "error": row["error"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
