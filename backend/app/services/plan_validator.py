"""计划校验（Autopilot P1 纯函数）：能力存在 / params 合 schema / inputs_from 无环 /
模型缺口 / 副作用分级汇总 / 配额 / 路径域。

校验不调 LLM、不做 I/O；错误逐条中文可读，返回 list[str]（空=通过）。
capability_sandbox path 精确租约是执行期闸门，本模块是计划期前置闸：params 中的
绝对路径必须落在计划声明的作品域（allowed_prefix）内，防越权写。
"""
from __future__ import annotations

import re
from pathlib import PureWindowsPath

from app.services.structured_contracts import GenerationPlan

DEFAULT_MAX_STEPS = 24

# JSON schema 基本类型的轻量校验（能力清单只用这几种；避免引入 jsonschema 依赖）
_TYPE_CHECKS: dict[str, tuple] = {
    "string": (str,), "number": (int, float), "integer": (int,),
    "boolean": (bool,), "object": (dict,), "array": (list,),
}

# 像绝对路径的参数值（Windows 盘符/反斜杠 或 POSIX 根开始）
_PATH_LIKE_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|[/\\])")


def validate(plan: GenerationPlan, *,
             capabilities: list[dict],
             configured_models: set[str] | frozenset[str] = frozenset(),
             allowed_prefix: str = "") -> list[str]:
    """返回逐条中文错误；空列表=合法计划。capabilities 接受 manifest dict 或 Capability。"""
    from app.services.capability_registry import Capability

    errors: list[str] = []
    norm = [item.to_manifest() if isinstance(item, Capability) else item
            for item in capabilities]
    by_op = {str(item.get("operation")): item for item in norm}

    # ── budgets：无预算计划校验不通过（红线）────────────────────────────────
    budgets = plan.budgets
    if budgets.max_steps <= 0 or budgets.max_gpu_tasks < 0 or budgets.max_llm_calls < 0:
        errors.append("budgets 非法：max_steps≥1，max_gpu_tasks/max_llm_calls≥0"
                      "（预算是上限，允许声明本计划不调用 GPU 或 LLM）。")
    if budgets.max_steps > DEFAULT_MAX_STEPS:
        errors.append(
            f"budgets.max_steps={budgets.max_steps} 超过默认上限 {DEFAULT_MAX_STEPS}，"
            "请把意图拆成多个小计划，禁止一步到位巨型计划。")

    if not plan.steps:
        errors.append("计划没有任何步骤（steps 为空）。")

    # ── 逐步校验 ────────────────────────────────────────────────────────────
    step_ids: set[str] = set()
    expensive_or_durable: list[str] = []
    gpu_tasks = 0
    for index, step in enumerate(plan.steps):
        label = f"步骤 {index + 1}（{step.operation}）"
        if not step.id or step.id in step_ids:
            errors.append(f"{label}: 步骤 id 缺失或重复（{step.id!r}）。")
        step_ids.add(step.id)
        cap = by_op.get(step.operation)
        if cap is None:
            errors.append(f"{label}: 能力清单里没有「{step.operation}」——"
                          "manifest 有什么，agent 才能编排什么。")
            continue
        # params 合 schema
        errors.extend(_check_params(label, step.params, cap.get("params_schema") or {}))
        # inputs_from 引用存在：支持「步骤id」与「步骤id.产出键」两种点引用
        for ref in step.inputs_from:
            if not _ref_resolvable(ref, step_ids - {step.id}):
                errors.append(f"{label}: inputs_from 引用了尚不存在的步骤输出「{ref}」。")
        # 模型缺口
        needs = cap.get("needs_model")
        if needs and needs not in configured_models:
            errors.append(f"{label}: 需要模型「{needs}」但当前未配置，agent 计划阶段即见缺口。")
        # 分级汇总 + GPU 计数
        level = str(cap.get("side_effect_level") or "readonly")
        if level in ("durable", "expensive"):
            expensive_or_durable.append(step.operation)
        if level == "expensive":
            gpu_tasks += 1

    if gpu_tasks > budgets.max_gpu_tasks:
        errors.append(f"expensive 步骤共 {gpu_tasks} 个，超过 budgets.max_gpu_tasks={budgets.max_gpu_tasks}。")

    # ── inputs_from 环检测（引用图上不能回到自身）──────────────────────────
    errors.extend(_check_cycles(plan))

    if plan.approval_required != sorted(set(expensive_or_durable)):
        errors.append(
            "approval_required 与 durable/expensive 步骤汇总不一致，应为 "
            f"{sorted(set(expensive_or_durable))}。")

    # ── 路径域：写类（durable/expensive）步骤的路径必须落在作品域内；
    # readonly 的读取路径豁免（越域读取在审批卡明示并由 capability_sandbox 租约授权）──
    if allowed_prefix:
        errors.extend(_check_paths(plan, allowed_prefix, by_op))
    return errors


def _ref_resolvable(ref: str, earlier_steps: set[str]) -> bool:
    """inputs_from 引用可解析：「s1」或「s1.产出键」都指向此前已存在的步骤。"""
    if ref in earlier_steps:
        return True
    head = ref.split(".", 1)[0]
    return head in earlier_steps


def _check_params(label: str, params: dict, schema: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(params, dict):
        return [f"{label}: params 必须是对象。"]
    required = schema.get("required") or []
    for key in required:
        if key not in params:
            errors.append(f"{label}: 缺少必填参数「{key}」。")
    properties = schema.get("properties") or {}
    for key, value in params.items():
        spec = properties.get(key)
        if spec is None:
            if schema.get("additionalProperties") is False:
                errors.append(f"{label}: 参数「{key}」不在能力参数定义里。")
            continue
        expected = spec.get("type")
        checks = _TYPE_CHECKS.get(str(expected))
        if checks and not isinstance(value, checks):
            errors.append(f"{label}: 参数「{key}」应为 {expected}。")
        if expected == "array" and isinstance(value, list):
            min_items = spec.get("minItems")
            if min_items and len(value) < int(min_items):
                errors.append(f"{label}: 参数「{key}」至少需要 {min_items} 项。")
    return errors


def _check_cycles(plan: GenerationPlan) -> list[str]:
    errors: list[str] = []
    edges: dict[str, set[str]] = {}
    for step in plan.steps:
        for ref in step.inputs_from:
            edges.setdefault(ref, set()).add(step.id)
    for start in edges:
        seen: set[str] = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node == start and node in seen:
                errors.append(f"步骤「{start}」的 inputs_from 依赖成环。")
                break
            if node in seen:
                continue
            seen.add(node)
            stack.extend(edges.get(node, ()))
    return errors


def _check_paths(plan: GenerationPlan, allowed_prefix: str,
                 by_op: dict[str, dict]) -> list[str]:
    errors: list[str] = []
    prefix = PureWindowsPath(allowed_prefix)
    for step in plan.steps:
        if str((by_op.get(step.operation) or {}).get("side_effect_level")) == "readonly":
            continue
        for key, value in step.params.items():
            if not isinstance(value, str) or not _PATH_LIKE_RE.match(value):
                continue
            try:
                candidate = PureWindowsPath(value)
                candidate.relative_to(prefix)
            except ValueError:
                errors.append(
                    f"步骤 {index_label(plan, step.id)}: 参数「{key}」的路径 {value} "
                    f"越出作品域 {allowed_prefix}（防越权写）。")
    return errors


def index_label(plan: GenerationPlan, step_id: str) -> str:
    for index, step in enumerate(plan.steps):
        if step.id == step_id:
            return f"{index + 1}（{step.operation}）"
    return f"（{step_id}）"
