"""Autopilot 计划执行器（P2）+ 审批/配额闸门（P3）+ 进度/幂等/配方（P4）。

仿 `workflow_build_tasks` 骨架：SQLite 持久化 + 租约式 FIFO worker，一个计划=一个任务，
计划内步骤串行（inputs_from 有依赖）。语义见 docs/ROADMAP-AUTOPILOT.md：

- **失败隔离**：单步失败停在失败步，剩余标 blocked，可指令跳过/重试；执行器不调 LLM。
- **Doom Loop 检测**：同一步骤连续失败 ≥DOOM_LOOP_LIMIT 次 → 步骤 blocked + 任务 blocked，
  禁止自动无限重试。
- **终态判定防 premature completion**：全部步骤显式 done/skipped 才 done；
  有产出但存在 failed/blocked 步骤 → partial（非终态的 blocked 保持任务打开等用户指令）。
- **两档访问标准（P1）**：approval（默认）走审批闸门——durable/expensive 步骤执行前必须
  `capability_sandbox.authorize` 计划租约（subject=task_id）；无租约/过期 → 步骤 blocked、
  任务 awaiting_approval；批准 = grant 一次性租约（ttl=预算估算×2），安全优先。
  full（用户显式开启）在 submit_task 时自动签通配租约（approved_by=full_mode），
  免逐项审批；配额/路径域/Doom Loop/终态判定等硬闸门照旧。
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

from app.config import DATA_DIR
from app.db import get_connection
from app.services import capability_registry, capability_sandbox, plan_validator, task_progress_store
from app.services.structured_contracts import GenerationPlan, PlanBudgets, PlanStep

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
WRITE_LOOP_LIMIT = 3  # 同一任务内对同一文件最多写 3 次（LoopDetection 中间件）
PROGRESS_NAMESPACE = "plan_tasks"

TASK_TERMINAL = ("done", "partial", "error", "cancelled")
TASK_OPEN = ("queued", "running", "awaiting_approval", "blocked")

# P4 幂等：同 hash 的开放/已成功任务视为重复（cancelled/partial/error 允许重投）
_DEDUP_BLOCKING = ("queued", "running", "awaiting_approval", "blocked", "done")


def _now() -> int:
    return int(time.time() * 1000)


# 智能编造 Agent 两档访问标准（2026-09-02 定案，ARCHITECTURE.md 同步）：
# approval = 默认，durable/expensive 与越域读走审批租约（现有 P3 闸门）；
# full     = 用户显式开启的完全访问，提交时自动签通配租约（approved_by=full_mode），
#            免逐项审批但配额/路径域等硬闸门照旧。设置真源：DATA_DIR/user_state.json
#            的 settings.agentAccessMode（前端 Settings 面板写入）。
def _agent_access_mode() -> str:
    """读访问模式设置（真源 DATA_DIR/user_state.json，缺失/损坏一律回退 approval）。"""
    try:
        data = json.loads((DATA_DIR / "user_state.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 设置文件缺失/损坏不阻断提交，安全默认 approval
        return capability_sandbox.ACCESS_APPROVAL
    if not isinstance(data, dict):
        return capability_sandbox.ACCESS_APPROVAL
    settings = data.get("settings")
    if not isinstance(settings, dict):
        return capability_sandbox.ACCESS_APPROVAL
    mode = str(settings.get("agentAccessMode") or "").strip()
    return mode if mode in capability_sandbox.ACCESS_LEVELS else capability_sandbox.ACCESS_APPROVAL


# full 租约不按 GPU 步数缩 TTL：完全访问的承诺是全程免中断，租约只作为可撤销的
# 熔断器存在（内存态、随进程消失）；grant 内部 clamp 86400s。
FULL_LEASE_TTL_SECONDS = 86_400


def _full_lease_ttl(plan: GenerationPlan) -> int:
    del plan  # 保留形参兼容将来按计划收紧；当前固定取熔断上限
    return FULL_LEASE_TTL_SECONDS


def canonical_hash(plan: GenerationPlan) -> str:
    """规范化计划内容 hash：键序固定；只为提交去重而存在（有消费端才建）。"""
    data = json.loads(plan.model_dump_json())
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── 提交与查询 ───────────────────────────────────────────────────────────────

def submit_task(plan: GenerationPlan, *, output_dir: str, repo_id: str = "",
                configured_models: set[str] | frozenset[str] = frozenset()) -> dict:
    """校验 → 幂等去重 → 落库排队。返回 {task_id, deduped, duplicate_of?}。"""
    # 运行环境归一与编译器同款：collect 落盘目标由环境决定（堵 reversible 绕过路径域）
    from app.services.capability_handlers import _resolve_template_id
    submit_ids = []
    for st in plan.steps:
        if st.operation.startswith("workflow.submit"):
            tid_param = str(st.params.get("template_id") or "")
            if tid_param:
                st.params["template_id"] = _resolve_template_id(tid_param)
            submit_ids.append(st.id)
    for step in plan.steps:
        # 通用创作能力的落盘 base 一律环境归一为作品目录（模型不得决定写入外部）。
        if step.operation in ("worldbook.upsert_repo", "character.upsert_repo",
                              "doc.create_repo"):
            step.params["base"] = output_dir
        if step.operation == "worldbook.upsert_repo":
            step.params["repo_id"] = repo_id or plan.repo_id
        if step.operation == "novel.scan_anonymity":
            # 收尾闸门（固化02 §3.6）：base/repo_id 由环境注入，模型只需给主角名名单；
            # 执行期 handler 机械读作品世界书快照取条目，approval 计划里编得进这一步。
            step.params["base"] = output_dir
            step.params["repo_id"] = repo_id or plan.repo_id
        if step.operation == "media.collect_comfy_outputs":
            step.params["output_dir"] = output_dir
            step.params["repo_id"] = repo_id or plan.repo_id
        if step.operation == "plan.instantiate_recipe":
            # 配方重放的落盘域同样由环境决定（handler 内还会对配置真源做等值校验）
            step.params["output_dir"] = output_dir
            if step.inputs_from:
                # 编译器可能把 collect 写成链接到 submit 步骤本身（如 ["s3"]）；
                # 执行器语义里 collect 需要 submit_result/prompt_ids，这里归一为虚拟键
                # sX.submit_result（_resolve_params 取不到该键时会回退为整个 submit 产出）。
                normalized: list[str] = []
                for ref in step.inputs_from:
                    normalized.append(f"{ref}.submit_result" if ref in submit_ids else ref)
                step.inputs_from = normalized
            if not step.inputs_from and submit_ids:
                # 采集自动链接 submit 产出（模型经常漏写 inputs_from）
                step.inputs_from = [f"{sid}.submit_result" for sid in submit_ids]
    errors = plan_validator.validate(
        plan, capabilities=capability_registry.all_capabilities(),
        configured_models=configured_models, allowed_prefix=output_dir)
    if errors:
        raise ValueError("计划未通过校验：\n- " + "\n- ".join(errors))
    content_hash = canonical_hash(plan)
    with get_connection() as connection:
        connection.execute("begin immediate")
        row = connection.execute(
            f"select id from plan_tasks where content_hash=? and status in "
            f"({','.join('?' for _ in _DEDUP_BLOCKING)}) order by created_at desc limit 1",
            (content_hash, *_DEDUP_BLOCKING),
        ).fetchone()
        if row is not None:
            return {"task_id": str(row["id"]), "deduped": True}
        task_id = uuid.uuid4().hex
        now = _now()
        # 一个用户任务 = 一个后台活动：新计划提交时，自动取消同一作品旧的
        # queued/awaiting_approval 任务（重试/重新生成的旧版本让位，不堆积）。
        connection.execute(
            "update plan_tasks set status='cancelled', updated_at=? "
            "where output_dir=? and repo_id=? and status in ('queued','awaiting_approval')",
            (now, output_dir, repo_id or plan.repo_id))
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
        connection.commit()
    if _agent_access_mode() == capability_sandbox.ACCESS_FULL:
        lease = capability_sandbox.grant(
            subject=task_id, capabilities=[{"operation": "*", "path": ""}],
            ttl_seconds=_full_lease_ttl(plan), approved_by="full_mode",
            mode=capability_sandbox.ACCESS_FULL)
        with get_connection() as connection:
            connection.execute("update plan_tasks set lease_id=?, updated_at=? where id=?",
                               (lease["id"], _now(), task_id))
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
    dumped = [_dump_task(row, grouped.get(str(row["id"]), [])) for row in rows]
    # 后台活动聚合：未终态任务全部显示；终态任务按 intent 合并为一条活动（同
    # 意图的历史重复计划不再刷屏），组内保留最近更新者并携带 merged_count。
    open_tasks = [t for t in dumped if t["status"] in TASK_OPEN]
    terminal_groups: dict[str, list[dict]] = {}
    import re as _re
    for t in dumped:
        if t["status"] in TASK_TERMINAL:
            key = _re.sub(r"\[\d+\]\s*$", "", t["intent"].strip()).strip()
            terminal_groups.setdefault(key, []).append(t)
    terminal = []
    for intent, group in terminal_groups.items():
        latest = max(group, key=lambda t: t["updated_at"])
        latest["merged_count"] = len(group)
        latest["intent"] = intent or latest["intent"]
        terminal.append(latest)
    terminal.sort(key=lambda t: t["updated_at"], reverse=True)
    # 后台活动只显示未终态任务（待审批/执行中/受阻/排队）；已终态不占活动面板，
    # 结果在对话计划卡上查看。terminal 聚合留给「计划历史」视图（后续需要时用）。
    return open_tasks


def _dump_task(row, steps) -> dict:
    step_dicts = [{
        "seq": int(step["seq"]),
        "step_id": str(step["step_id"]),
        "operation": str(step["operation"]),
        "status": str(step["status"]),
        "attempts": int(step["attempts"]),
        "last_error": str(step["last_error"]),
        "params": json.loads(step["params_json"] or "{}"),
        "outputs": json.loads(step["outputs_json"] or "{}"),
    } for step in steps]
    done_steps = sum(1 for s in step_dicts if s["status"] in ("done", "skipped"))
    images_total = 0
    for s in step_dicts:
        if s["operation"] == "workflow.submit_batch":
            variants = s["params"].get("variants")
            if isinstance(variants, list):
                images_total += len(variants)
        elif s["operation"] == "workflow.submit_template":
            images_total += 1
    images_done = 0
    for s in step_dicts:
        if s["operation"] == "media.collect_comfy_outputs" and s["status"] == "done":
            collected = s["outputs"].get("collected")
            if isinstance(collected, int) and collected > 0:
                images_done += collected
    progress = f"{done_steps}/{len(step_dicts)} 步"
    if images_total > 0:
        progress += f" · 图 {min(images_done, images_total)}/{images_total}"
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
        "progress": progress,
        "images_total": images_total,
        "images_done": images_done,
        "merged_count": 1,
        "steps": step_dicts,
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
    seen: set[tuple[str, str]] = set()
    capabilities: list[dict[str, str]] = []
    for step in plan.steps:
        level = _step_level(step.operation)
        if level in ("durable", "expensive"):
            entry = (step.operation, task["output_dir"])
        elif _out_domain_paths(step, task["output_dir"]):
            # readonly 越域读取：把声明的读取路径写进租约（审批卡已明示）
            for read_path in _out_domain_paths(step, task["output_dir"]):
                entry = (step.operation, read_path)
                if entry not in seen:
                    seen.add(entry)
                    capabilities.append({"operation": step.operation, "path": read_path})
            continue
        else:
            continue
        if entry not in seen:
            seen.add(entry)
            capabilities.append({"operation": entry[0], "path": entry[1]})
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
        "intent": plan.get("intent", ""), "description": "",
        "status": "saved", "origin": "plan",
        "source_task": task_id, "plan": plan, "created_at": _now(),
    }
    recipes[recipe_id] = recipe
    task_progress_store.save("plan_recipes", recipes)
    return recipe


# 自由循环轨迹里的环境属主参数：重放时由 submit_task 重新归一注入，不得固化进配方
_ENV_PARAM_KEYS = ("base", "repo_id", "output_dir", "client_id")


def solidify_steps(*, intent: str, steps: list[dict], output_dir: str,
                   name: str = "", description: str = "") -> dict:
    """自由循环轨迹固化（2026-09-03）：成功步骤转为草稿配方，用户确认后可重放。

    与 save_recipe（终态计划→配方）同源同存储，只是入口不同：这里没有落库任务，
    直接把 fabric_loop 的 {tool, params} 轨迹转成 GenerationPlan。环境属主参数
    （base/repo_id/output_dir/client_id）剥离，重放时由 submit_task 重新注入；
    无 durable/expensive 步骤的纯探索轨迹不固化（无重放价值）。
    """
    solid: list[PlanStep] = []
    for raw in steps:
        if not raw.get("ok"):
            continue
        operation = str(raw.get("tool") or "")
        if capability_registry.get(operation) is None:
            continue
        params = {k: v for k, v in (raw.get("params") or {}).items()
                  if k not in _ENV_PARAM_KEYS}
        solid.append(PlanStep(id=f"s{len(solid) + 1}", operation=operation, params=params))
    levels = {_step_level(s.operation) for s in solid}
    if not levels & {"durable", "expensive"}:
        raise ValueError("没有 durable/expensive 步骤，纯探索轨迹不固化")
    plan = GenerationPlan(
        intent=(intent or "自由循环固化")[:200], repo_id="",
        budgets=PlanBudgets(
            max_steps=max(1, len(solid)),
            max_gpu_tasks=sum(1 for s in solid if _step_level(s.operation) == "expensive"),
            max_llm_calls=0),
        steps=solid,
        approval_required=sorted({s.operation for s in solid
                                  if _step_level(s.operation) in ("durable", "expensive")}))
    errors = plan_validator.validate(
        plan, capabilities=capability_registry.all_capabilities(), allowed_prefix=output_dir)
    if errors:
        raise ValueError("固化计划未通过校验：\n- " + "\n- ".join(errors))
    recipes = _load_recipes()
    recipe_id = uuid.uuid4().hex[:12]
    recipe = {
        "id": recipe_id, "name": name or intent[:40], "intent": intent[:200],
        "description": description, "status": "draft", "origin": "fabric",
        "plan": plan.model_dump(), "created_at": _now(),
    }
    recipes[recipe_id] = recipe
    task_progress_store.save("plan_recipes", recipes)
    return recipe


def keep_recipe(recipe_id: str) -> dict:
    """用户确认草稿配方：保留后进入固化流程清单（可被重放与复用匹配）。"""
    recipes = _load_recipes()
    recipe = recipes.get(recipe_id)
    if recipe is None:
        raise LookupError("配方不存在")
    recipe["status"] = "saved"
    recipes[recipe_id] = recipe
    task_progress_store.save("plan_recipes", recipes)
    return recipe


def delete_recipe(recipe_id: str) -> dict:
    recipes = _load_recipes()
    if recipe_id not in recipes:
        raise LookupError("配方不存在")
    recipes.pop(recipe_id)
    task_progress_store.save("plan_recipes", recipes)
    return {"ok": True}


def list_recipes() -> dict[str, dict]:
    return _load_recipes()


def instantiate_recipe(recipe_id: str, *, output_dir: str, repo_id: str = "",
                       param_overrides: dict[str, dict] | None = None) -> dict:
    """配方实例化：按步骤覆盖 params 后作为新计划重投（走同一校验+幂等闸门）。"""
    recipe = _load_recipes().get(recipe_id)
    if recipe is None:
        raise LookupError("配方不存在")
    if str(recipe.get("status") or "saved") != "saved":
        raise ValueError("草稿配方需先保留（keep）才能重放")
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




def _step_paths(step) -> list[str]:
    """步骤 params 里像绝对路径的值（复用 plan_validator 的路径识别，单一属主）。

    兼容两种步骤形态：DB Row（params_json）与 Pydantic PlanStep（params）。
    """
    from app.services.plan_validator import _PATH_LIKE_RE
    try:  # DB Row（键索引）
        params = json.loads(step["params_json"] or "{}")
    except (TypeError, IndexError, KeyError):  # Pydantic PlanStep
        params = dict(getattr(step, "params", {}) or {})
    return [v for v in params.values() if isinstance(v, str) and _PATH_LIKE_RE.match(v)]


def _out_domain_paths(step, output_dir: str) -> list[str]:
    """越出作品域的读取路径（readonly 越域读取需要审批授权）。"""
    if not output_dir:
        return _step_paths(step)
    return [p for p in _step_paths(step) if not capability_sandbox._path_allowed(p, output_dir)]


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
        changed = connection.execute(
            "update plan_tasks set status='queued', updated_at=? where id=? "
            "and status != 'running'",
            (_now(), task_id)).rowcount
    if changed:
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
    # LoopDetection（P5）：同一任务内对同一文件的写次数超限即阻断，防「反复写同一文件」循环
    write_counts: dict[str, int] = {}
    # 配额计数：done 的 expensive 步 + 失败尝试（失败的 GPU 提交同样消耗配额）
    gpu_done = 0
    for st in steps:
        if _step_level(str(st["operation"])) == "expensive":
            gpu_done += 1 if str(st["status"]) == "done" else int(st["attempts"])
    ctx = {"thread_id": f"plan-{task_id[:8]}", "repo_id": str(task["repo_id"]),
           "output_dir": output_dir, "turn_id": f"plan-{task_id[:8]}"}

    for seq, step in enumerate(steps):
        if cancel.is_set():
            _finalize(task_id, steps, seq, cancelled=True)
            return
        status = str(step["status"])
        if status in ("done", "skipped"):
            outputs[str(step["step_id"])] = json.loads(step["outputs_json"] or "{}")
            continue  # 配额已在任务级初始汇总计入，循环不重复加
        operation = str(step["operation"])
        level = _step_level(operation)

        # ── P3 审批闸门：写类步骤 + 越域 readonly 读取 ──
        out_domain_reads = (_out_domain_paths(step, output_dir)
                            if level == "readonly" else [])
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

        for read_path in out_domain_reads:
            # 越域读取：租约必须精确覆盖该路径（审批卡明示后授权）
            try:
                capability_sandbox.authorize(lease_id, operation, path=read_path)
            except PermissionError as exc:
                _block_step(task_id, step, str(exc), task_status="awaiting_approval",
                            reason="needs_approval", ctx=ctx)
                _publish_progress(task_id, "awaiting_approval")
                return
        # ── 执行 ──
        params = _resolve_params(step, outputs)
        if operation == "file.write_text":
            write_path = str(params.get("path") or "")
            write_counts[write_path] = write_counts.get(write_path, 0) + 1
            if write_counts[write_path] > WRITE_LOOP_LIMIT:
                _block_step(task_id, step,
                            f"同一文件 {write_path} 在本任务内已写入 "
                            f"{WRITE_LOOP_LIMIT} 次，疑似循环编辑，已阻断",
                            task_status="blocked", reason="write_loop", ctx=ctx)
                _block_rest(task_id, steps, seq + 1)
                _finalize(task_id, steps, seq + 1)
                return
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

    def _assign(key: str, value: Any) -> None:
        # 同键多来源（如两个 submit 步骤都产出 submit_result）自动累积为列表
        if key not in params:
            params[key] = value
        elif isinstance(params[key], list) and not isinstance(value, dict):
            params[key].append(value)
        else:
            params[key] = [params[key], value]

    for ref in json.loads(step["inputs_from_json"] or "[]"):
        if ref in outputs:
            value = outputs[ref]
        else:
            head, _, key = ref.partition(".")
            source = outputs.get(head) or {}
            value = source.get(key)
            if value is None and head in outputs:
                # 产出里没有同名键：回退为整包产出（handler 自行取所需字段）
                value = source
        if value is None:
            continue
        if isinstance(value, dict) and ref not in outputs:
            # 点引用命中整包回退时按「引用键=整包」处理，不摊开字段
            _assign(key, value)
            continue
        if isinstance(value, dict):
            for name, item in value.items():
                params.setdefault(name, item)
        else:
            _assign(ref.partition(".")[2] or ref, value)
    return params


def _dispatch(operation: str, params: dict) -> Any:
    cap = capability_registry.get(operation)
    if cap is None or not cap.handler:
        raise RuntimeError(f"能力 {operation} 未注册 handler（执行面拒绝未知动作）")
    module_name, _, func_name = cap.handler.partition(":")
    func = getattr(importlib.import_module(module_name), func_name)
    accepted = set((cap.params_schema or {}).get("properties") or {})
    kwargs = {k: v for k, v in params.items() if k in accepted} if accepted else params
    if "client_id" in accepted and not kwargs.get("client_id"):
        # ComfyUI /prompt 要求合法 UUID 的 client_id；聊天链路由前端提供，执行器自行生成
        kwargs["client_id"] = uuid.uuid4().hex
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
        tasks[task_id] = {
            "kind": "plan", "intent": task["intent"], "status": status,
            "progress": task.get("progress") or f"0/{len(task['steps'])} 步",
            "images_total": task.get("images_total", 0),
            "images_done": task.get("images_done", 0),
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
