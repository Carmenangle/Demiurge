"""Autopilot 计划执行器（P2）+ 审批/配额闸门（P3）+ 进度/幂等/配方（P4）。

仿 `workflow_build_tasks` 骨架：SQLite 持久化 + 租约式 FIFO worker，一个计划=一个任务，
计划内步骤串行（inputs_from 有依赖）。语义见 docs/ROADMAP-AUTOPILOT.md：

- **失败隔离**：单步失败停在失败步，剩余标 blocked，可指令跳过/重试；执行器不调 LLM。
- **Doom Loop 检测**：同一步骤连续失败 ≥DOOM_LOOP_LIMIT 次 → 步骤 blocked + 任务 blocked，
  禁止自动无限重试。
- **终态判定防 premature completion**：全部步骤显式 done/skipped 才 done；
  有产出但存在 failed/blocked 步骤 → partial（非终态的 blocked 保持任务打开等用户指令）。
- **审批闸门（P3）**：durable/expensive 步骤执行前必须 `capability_sandbox.authorize`
  计划租约（subject=task_id）；无租约/过期 → 步骤 blocked、任务 awaiting_approval；
  批准 = grant 一次性租约（ttl=预算估算×2），安全优先。
- **配额**：expensive 步骤完成数达 budgets.max_gpu_tasks → 后续 expensive 步骤 blocked。
- **幂等（P4）**：规范化计划内容 hash，同 hash 存在未取消任务时拒绝重复提交。
- **配方（P4）**：终态任务可固化为配方（参数槽位化），可实例化重投。
- trace 全程：plan.step_started/done/failed/blocked、plan.terminal。
"""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from uuid import uuid4
from typing import Any

from app.db import get_connection
from app.services import capability_registry, capability_sandbox, plan_validator, task_progress_store
from app.services.structured_contracts import GenerationPlan

logger = logging.getLogger(__name__)

_WAKE = threading.Condition()
_WORKER: threading.Thread | None = None
_CONTROLLERS: dict[str, threading.Event] = {}
_WORKER_ID = f"{os.getpid()}:{uuid4().hex}"

LEASE_MS = 60_000
HEARTBEAT_SECONDS = 10
TASK_RETENTION_MS = 14 * 24 * 60 * 60 * 1000
TASK_RETENTION_LIMIT = 200
DOOM_LOOP_LIMIT = 2
PROGRESS_NAMESPACE = "plan_tasks"

TASK_TERMINAL = ("done", "partial", "error", "cancelled")
TASK_OPEN = ("queued", "running", "awaiting_approval", "blocked")

# P4 幂等：同 hash 的开放/已成功任务视为重复（cancelled/partial/error 允许重投）
_DEDUP_BLOCKING = ("queued", "running", "awaiting_approval", "blocked", "done")


def _now() -> int:
    return int(time.time() * 1000)


def canonical_hash(plan: GenerationPlan) -> str:
    """规范化计划内容 hash：键序固定；只为提交去重而存在（有消费端才建）。"""
    data = json.loads(plan.model_dump_json())
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── 提交与查询 ───────────────────────────────────────────────────────────────

def submit_task(plan: GenerationPlan, *, output_dir: str, repo_id: str = "",
                configured_models: set[str] | frozenset[str] = frozenset()) -> dict:
    """校验 → 幂等去重 → 落库排队。返回 {task_id, deduped, duplicate_of?}。"""
    errors = plan_validator.validate(
        plan, capabilities=capability_registry.all_capabilities(),
        configured_models=configured_models, allowed_prefix=output_dir)
    if errors:
        raise ValueError("计划未通过校验：\n- " + "\n- ".join(errors))
    content_hash = canonical_hash(plan)
    with get_connection() as connection:
        row = connection.execute(
            f"select id from plan_tasks where content_hash=? and status in "
            f"({','.join('?' for _ in _DEDUP_BLOCKING)}) order by created_at desc limit 1",
            (content_hash, *_DEDUP_BLOCKING),
        ).fetchone()
        if row is not None:
            return {"task_id": str(row["id"]), "deduped": True}
        task_id = uuid.uuid4().hex
        now = _now()
        connection.execute(
            "insert into plan_tasks (id, repo_id, output_dir, intent, plan_json, content_hash,"
            " status, created_at, updated_at) values (?,?,?,?,?,?,?,?,?)",
            (task_id, repo_id or plan.repo_id, output_dir, plan.intent,
             plan.model_dump_json(), content_hash, "queued", now, now))
        for seq, step in enumerate(plan.steps):
            connection.execute(
                "insert into plan_task_steps (task_id, seq, step_id, operation, params_json,"
                " inputs_from_json, updated_at) values (?,?,?,?,?,?,?)",
                (task_id, seq, step.id, step.operation,
                 json.dumps(step.params, ensure_ascii=False),
                 json.dumps(step.inputs_from, ensure_ascii=False), now))
    _wake()
    return {"task_id": task_id, "deduped": False}


def get_task(task_id: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute("select * from plan_tasks where id=?", (task_id,)).fetchone()
        if row is None:
            return None
        steps = connection.execute(
            "select * from plan_task_steps where task_id=? order by seq asc", (task_id,)
        ).fetchall()
    return _dump_task(row, steps)


def list_tasks(output_dir: str = "", repo_id: str = "", limit: int = 30) -> list[dict]:
    query = "select * from plan_tasks"
    params: list[Any] = []
    conditions = []
    if output_dir:
        conditions.append("output_dir=?")
        params.append(output_dir)
    if repo_id:
        conditions.append("repo_id=?")
        params.append(repo_id)
    if conditions:
        query += " where " + " and ".join(conditions)
    query += " order by created_at desc limit ?"
    params.append(max(1, min(limit, 100)))
    with get_connection() as connection:
        rows = connection.execute(query, params).fetchall()
        steps = connection.execute(
            f"select * from plan_task_steps where task_id in "
            f"({','.join('?' for _ in rows)}) order by seq asc",
            [str(r["id"]) for r in rows],
        ).fetchall() if rows else []
    grouped: dict[str, list] = {}
    for step in steps:
        grouped.setdefault(str(step["task_id"]), []).append(step)
    return [_dump_task(row, grouped.get(str(row["id"]), [])) for row in rows]


def _dump_task(row, steps) -> dict:
    return {
        "id": str(row["id"]),
        "repo_id": str(row["repo_id"]),
        "output_dir": str(row["output_dir"]),
        "intent": str(row["intent"]),
        "status": str(row["status"]),
        "error": str(row["error"]),
        "lease_id": str(row["lease_id"]),
        "content_hash": str(row["content_hash"]),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
        "steps": [{
            "seq": int(step["seq"]),
            "step_id": str(step["step_id"]),
            "operation": str(step["operation"]),
            "status": str(step["status"]),
            "attempts": int(step["attempts"]),
            "last_error": str(step["last_error"]),
            "params": json.loads(step["params_json"] or "{}"),
            "outputs": json.loads(step["outputs_json"] or "{}"),
        } for step in steps],
    }


# ── 用户指令（P3 审批 / 跳过 / 重试 / 取消；P4 配方）─────────────────────────

def approve_task(task_id: str, *, approved_by: str = "user") -> dict:
    """批准计划 = 对各 durable/expensive 步骤授一次性租约（审批只读：不改 params）。"""
    task = get_task(task_id)
    if task is None:
        raise LookupError("计划任务不存在")
    if task["status"] in TASK_TERMINAL:
        raise ValueError("任务已终态，无需批准")
    plan = GenerationPlan.model_validate(json.loads(_plan_json(task_id)))
    capabilities = [
        {"operation": step.operation, "path": task["output_dir"]}
        for step in plan.steps
        if _step_level(step.operation) in ("durable", "expensive")
    ]
    if not capabilities:
        capabilities = [{"operation": "*", "path": task["output_dir"]}]
    # ttl = 预算估算×2：expensive 步数 × 单步 15 分钟（批量出图可能跑数小时，安全优先留余量）
    gpu_steps = sum(1 for c in capabilities if c["operation"] != "*" and _step_level(
        c["operation"]) == "expensive")
    ttl = max(600, gpu_steps * 900) * 2
    lease = capability_sandbox.grant(
        subject=task_id, capabilities=capabilities, ttl_seconds=ttl, approved_by=approved_by)
    with get_connection() as connection:
        connection.execute(
            "update plan_tasks set lease_id=?, status=case when status='awaiting_approval' "
            "then 'queued' else status end, updated_at=? where id=?",
            (lease["id"], _now(), task_id))
    _wake()
    return {"lease_id": lease["id"], "ttl_seconds": ttl}


def skip_step(task_id: str, step_id: str) -> bool:
    return _set_step_status(task_id, step_id, "skipped")


def retry_step(task_id: str, step_id: str) -> bool:
    # 连败计数不清零：同一步骤同参数的重试累计进 Doom Loop；成功或跳过才归零
    ok = _set_step_status(task_id, step_id, "pending")
    if ok:
        _requeue(task_id)
    return ok


def cancel_task(task_id: str) -> bool:
    cancel = _CONTROLLERS.get(task_id)
    if cancel is not None:
        cancel.set()
    with get_connection() as connection:
        changed = connection.execute(
            "update plan_tasks set status='cancelled', updated_at=? where id=? and status in "
            "('queued','awaiting_approval','blocked')", (_now(), task_id)).rowcount
    _wake()
    return changed == 1 or cancel is not None


def save_recipe(task_id: str, *, name: str = "") -> dict:
    """P4 配方固化：终态且有效的计划参数槽位化存为配方（data/plan_recipes.json）。"""
    task = get_task(task_id)
    if task is None:
        raise LookupError("计划任务不存在")
    if task["status"] not in ("done", "partial"):
        raise ValueError("只有终态且有产出的计划可固化为配方")
    plan = json.loads(_plan_json(task_id))
    recipes = _load_recipes()
    recipe_id = uuid.uuid4().hex[:12]
    recipe = {
        "id": recipe_id, "name": name or plan.get("intent", "")[:40],
        "source_task": task_id, "plan": plan, "created_at": _now(),
    }
    recipes[recipe_id] = recipe
    task_progress_store.save("plan_recipes", recipes)
    return recipe


def list_recipes() -> dict[str, dict]:
    return _load_recipes()


def instantiate_recipe(recipe_id: str, *, output_dir: str, repo_id: str = "",
                       param_overrides: dict[str, dict] | None = None) -> dict:
    """配方实例化：按步骤覆盖 params 后作为新计划重投（走同一校验+幂等闸门）。"""
    recipe = _load_recipes().get(recipe_id)
    if recipe is None:
        raise LookupError("配方不存在")
    plan = GenerationPlan.model_validate(recipe["plan"])
    overrides = param_overrides or {}
    for step in plan.steps:
        patch = overrides.get(step.id)
        if isinstance(patch, dict):
            step.params.update(patch)
    plan.intent = f"[配方:{recipe['name']}] {plan.intent}"[:200]
    return submit_task(plan, output_dir=output_dir, repo_id=repo_id)


def _load_recipes() -> dict[str, dict]:
    return dict(task_progress_store.load("plan_recipes"))


def _plan_json(task_id: str) -> str:
    with get_connection() as connection:
        row = connection.execute("select plan_json from plan_tasks where id=?", (task_id,)).fetchone()
    return str(row["plan_json"]) if row else "{}"


def _step_level(operation: str) -> str:
    cap = capability_registry.get(operation)
    return cap.side_effect_level if cap else "durable"  # 未注册能力按最严处理


def _set_step_status(task_id: str, step_id: str, status: str,
                     reset_attempts: bool = False) -> bool:
    reset_sql = ", attempts=0" if reset_attempts else ""
    with get_connection() as connection:
        changed = connection.execute(
            f"update plan_task_steps set status=?, last_error='', updated_at=?{reset_sql} "
            "where task_id=? and step_id=? and status != 'running'",
            (status, _now(), task_id, step_id)).rowcount
        if changed == 1 and status == "skipped":
            connection.execute(
                "update plan_tasks set status='queued', updated_at=? where id=? and status in "
                "('blocked','awaiting_approval','partial')", (_now(), task_id))
    if changed == 1:
        _wake()
    return changed == 1


def _requeue(task_id: str) -> None:
    with get_connection() as connection:
        connection.execute(
            "update plan_tasks set status='queued', updated_at=? where id=?",
            (_now(), task_id))
    _wake()


# ── Worker（P2 执行器）───────────────────────────────────────────────────────

def start_worker() -> None:
    global _WORKER
    with _WAKE:
        if _WORKER and _WORKER.is_alive():
            _WAKE.notify_all()
            return
        _WORKER = threading.Thread(target=_worker_loop, name="plan-task-worker", daemon=True)
        _WORKER.start()


def _wake() -> None:
    with _WAKE:
        _WAKE.notify_all()


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
        threading.Thread(target=_heartbeat_loop, args=(task_id, heartbeat_stop),
                         name=f"plan-heartbeat-{task_id[:8]}", daemon=True).start()
        try:
            _run_task(task, cancel)
        except Exception as exc:  # noqa: BLE001 - worker 必须持久化失败并继续队列
            logger.exception("plan task %s crashed", task_id)
            _set_task(task_id, "error", error=str(exc))
        finally:
            heartbeat_stop.set()
            _CONTROLLERS.pop(task_id, None)


def _claim_next():
    now = _now()
    with get_connection() as connection:
        connection.execute("begin immediate")
        row = connection.execute(
            """
            select id from plan_tasks
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
            update plan_tasks set status='running', worker_id=?, lease_expires_at=?, updated_at=?
            where id=? and (status='queued' or (status='running' and lease_expires_at<=?))
            """,
            (_WORKER_ID, now + LEASE_MS, now, row["id"], now),
        ).rowcount
        if changed != 1:
            connection.rollback()
            return None
        claimed = connection.execute(
            "select * from plan_tasks where id=?", (row["id"],)).fetchone()
        connection.commit()
        return claimed


def _heartbeat_loop(task_id: str, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            with get_connection() as connection:
                connection.execute(
                    "update plan_tasks set lease_expires_at=? where id=? and status='running' "
                    "and worker_id=?", (_now() + LEASE_MS, task_id, _WORKER_ID))
        except sqlite3.OperationalError:
            continue


def _run_task(task, cancel: threading.Event) -> None:
    task_id = str(task["id"])
    output_dir = str(task["output_dir"])
    lease_id = str(task["lease_id"])
    plan = GenerationPlan.model_validate(json.loads(str(task["plan_json"])))
    with get_connection() as connection:
        steps = connection.execute(
            "select * from plan_task_steps where task_id=? order by seq asc",
            (task_id,)).fetchall()
    outputs: dict[str, dict] = {}
    # 配额计数：done 的 expensive 步 + 失败尝试（失败的 GPU 提交同样消耗配额）
    gpu_done = sum(
        (1 if (_step_level(str(st["operation"])) == "expensive" and str(st["status"]) == "done") else 0)
        + (int(st["attempts"]) if _step_level(str(st["operation"])) == "expensive" else 0)
        for st in steps)
    ctx = {"thread_id": f"plan-{task_id[:8]}", "repo_id": str(task["repo_id"]),
           "output_dir": output_dir, "turn_id": f"plan-{task_id[:8]}"}

    for seq, step in enumerate(steps):
        if cancel.is_set():
            _finalize(task_id, steps, seq, cancelled=True)
            return
        status = str(step["status"])
        if status in ("done", "skipped"):
            outputs[str(step["step_id"])] = json.loads(step["outputs_json"] or "{}")
            if _step_level(str(step["operation"])) == "expensive":
                gpu_done += 1
            continue
        operation = str(step["operation"])
        level = _step_level(operation)

        # ── P3 审批闸门 ──
        if level in ("durable", "expensive"):
            try:
                capability_sandbox.authorize(lease_id, operation, path=output_dir)
            except PermissionError as exc:
                _block_step(task_id, step, str(exc), task_status="awaiting_approval",
                            reason="needs_approval", ctx=ctx)
                _publish_progress(task_id, "awaiting_approval")
                return  # 停在待批步：批准后 wake 继续
        # ── P3 配额闸门 ──
        if level == "expensive" and gpu_done >= plan.budgets.max_gpu_tasks:
            _block_step(task_id, step,
                        f"expensive 步骤已达配额上限 {plan.budgets.max_gpu_tasks}",
                        task_status="blocked", reason="quota_exceeded", ctx=ctx)
            _block_rest(task_id, steps, seq + 1)
            _finalize(task_id, steps, seq + 1)
            return

        # ── 执行 ──
        params = _resolve_params(step, outputs)
        _trace(ctx, "plan.step_started", task_id=task_id, step=step["step_id"], operation=operation)
        try:
            result = _dispatch(operation, params)
        except Exception as exc:  # noqa: BLE001 - 单步失败隔离
            _on_step_failure(task_id, step, str(exc), ctx=ctx, rest=steps, seq=seq)
            return
        outputs[str(step["step_id"])] = result if isinstance(result, dict) else {"result": result}
        if level == "expensive":
            gpu_done += 1
        with get_connection() as connection:
            connection.execute(
                "update plan_task_steps set status='done', outputs_json=?, attempts=0,"
                " last_error='', updated_at=? where task_id=? and seq=?",
                (json.dumps(outputs[str(step["step_id"])], ensure_ascii=False, default=str),
                 _now(), task_id, seq))
        _trace(ctx, "plan.step_done", task_id=task_id, step=step["step_id"], operation=operation)

    _finalize(task_id, steps, len(steps))


def _resolve_params(step, outputs: dict[str, dict]) -> dict:
    params = dict(json.loads(step["params_json"] or "{}"))
    for ref in json.loads(step["inputs_from_json"] or "[]"):
        if ref in outputs:
            value = outputs[ref]
        else:
            head, _, key = ref.partition(".")
            value = outputs.get(head, {}).get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            for name, item in value.items():
                params.setdefault(name, item)
        else:
            key = ref.partition(".")[2] or ref
            params.setdefault(key, value)
    return params


def _dispatch(operation: str, params: dict) -> Any:
    cap = capability_registry.get(operation)
    if cap is None or not cap.handler:
        raise RuntimeError(f"能力 {operation} 未注册 handler（执行面拒绝未知动作）")
    module_name, _, func_name = cap.handler.partition(":")
    func = getattr(importlib.import_module(module_name), func_name)
    accepted = set((cap.params_schema or {}).get("properties") or {})
    kwargs = {k: v for k, v in params.items() if k in accepted} if accepted else params
    return func(**kwargs)


def _on_step_failure(task_id: str, step, error: str, *, ctx: dict, rest, seq: int) -> None:
    attempts = int(step["attempts"]) + 1
    if attempts >= DOOM_LOOP_LIMIT:
        # Doom Loop 检测：同一步骤同参数连续失败达阈值 → blocked，禁止自动重试
        _block_step(task_id, step,
                    f"连续失败 {attempts} 次：{error}",
                    task_status="blocked", reason="doom_loop", ctx=ctx, attempts=attempts)
        _block_rest(task_id, rest, seq + 1)
        _finalize(task_id, rest, seq + 1)
        return
    with get_connection() as connection:
        connection.execute(
            "update plan_task_steps set status='failed', attempts=?, last_error=?, updated_at=? "
            "where task_id=? and seq=?",
            (attempts, error, _now(), task_id, seq))
    _trace(ctx, "plan.step_failed", task_id=task_id, step=step["step_id"],
           operation=step["operation"], error=error, attempts=attempts)
    _block_rest(task_id, rest, seq + 1)
    _finalize(task_id, rest, seq + 1)
    # 失败一次不炸全计划：任务保持 partial 可由用户 retry/skip 指令恢复


def _block_step(task_id: str, step, error: str, *, task_status: str, reason: str,
                ctx: dict, attempts: int | None = None) -> None:
    with get_connection() as connection:
        if attempts is None:
            connection.execute(
                "update plan_task_steps set status='blocked', last_error=?, updated_at=? "
                "where task_id=? and seq=?",
                (f"[{reason}] {error}", _now(), task_id, int(step["seq"])))
        else:
            connection.execute(
                "update plan_task_steps set status='blocked', attempts=?, last_error=?, updated_at=? "
                "where task_id=? and seq=?",
                (attempts, f"[{reason}] {error}", _now(), task_id, int(step["seq"])))
        connection.execute(
            "update plan_tasks set status=?, error=?, updated_at=? where id=?",
            (task_status, f"[{reason}] {error}", _now(), task_id))
    _trace(ctx, "plan.step_blocked", task_id=task_id, step=step["step_id"],
           operation=step["operation"], reason=reason, error=error)


def _block_rest(task_id: str, steps, from_seq: int) -> None:
    with get_connection() as connection:
        for step in steps[from_seq:]:
            if str(step["status"]) == "pending":
                connection.execute(
                    "update plan_task_steps set status='blocked', last_error='前序步骤未完成', "
                    "updated_at=? where task_id=? and seq=?",
                    (_now(), task_id, int(step["seq"])))


def _finalize(task_id: str, steps, upto_seq: int, *, cancelled: bool = False) -> None:
    """终态判定：全部 done/skipped → done；有 failed/blocked → partial；取消 → cancelled。"""
    with get_connection() as connection:
        rows = connection.execute(
            "select status from plan_task_steps where task_id=? order by seq asc",
            (task_id,)).fetchall()
    statuses = [str(r["status"]) for r in rows]
    if cancelled:
        status = "cancelled"
    elif statuses and all(s in ("done", "skipped") for s in statuses):
        status = "done"
    else:
        # 有产出但存在 failed/blocked 步骤：partial（防 premature completion）
        status = "partial"
    with get_connection() as connection:
        current = connection.execute("select status from plan_tasks where id=?", (task_id,)).fetchone()
        # blocked/awaiting_approval 是给用户的明确信号，终态归一不得覆盖它们
        current_status = str(current["status"]) if current else ""
        if current_status not in ("blocked", "awaiting_approval"):
            connection.execute(
                "update plan_tasks set status=?, worker_id='', lease_expires_at=0, updated_at=? "
                "where id=?", (status, _now(), task_id))
        else:
            status = current_status
    _trace({"thread_id": f"plan-{task_id[:8]}", "repo_id": ""}, "plan.terminal",
           task_id=task_id, status=status, steps=statuses)
    _publish_progress(task_id, status)
    _cleanup_finished()


def _set_task(task_id: str, status: str, error: str = "") -> None:
    with get_connection() as connection:
        connection.execute(
            "update plan_tasks set status=?, error=?, worker_id='', lease_expires_at=0, "
            "updated_at=? where id=?", (status, error, _now(), task_id))
    _publish_progress(task_id, status)


def _publish_progress(task_id: str, status: str) -> None:
    """P4：接入 task_progress_store（后台活动面板同源快照）。"""
    try:
        tasks = task_progress_store.load(PROGRESS_NAMESPACE)
        task = get_task(task_id)
        if task is None:
            return
        done = sum(1 for s in task["steps"] if s["status"] in ("done", "skipped"))
        tasks[task_id] = {
            "kind": "plan", "intent": task["intent"], "status": status,
            "progress": f"{done}/{len(task['steps'])} 步",
            "error": task["error"], "updated_at": _now(),
        }
        task_progress_store.save(PROGRESS_NAMESPACE, tasks)
    except Exception:  # noqa: BLE001 - 进度发布失败不影响执行
        pass


def _trace(ctx: dict, event: str, **data: Any) -> None:
    try:
        from app.services import run_trace
        run_trace.emit(ctx, event, **data)
    except Exception:  # noqa: BLE001
        pass


def _cleanup_finished() -> int:
    now = _now()
    placeholders = ",".join("?" for _ in TASK_TERMINAL)
    with get_connection() as connection:
        cursor = connection.execute(
            f"delete from plan_tasks where status in ({placeholders}) and ("
            "updated_at < ? or id not in (select id from plan_tasks where status in "
            f"({placeholders}) order by updated_at desc, created_at desc limit ?))",
            (*TASK_TERMINAL, now - TASK_RETENTION_MS, *TASK_TERMINAL, TASK_RETENTION_LIMIT))
        return cursor.rowcount
