"""Autopilot P1 计划编译：委派意图识别（零 LLM）→ 意图编译成计划文档 → 校验 → 落盘。

- 委派强命令层只收高置信确定性模式（规模词+资产动作 / 显式计划语言），模糊表达
  交给剧情默认或 supervisor（误判方向见 docs/ROADMAP-AUTOPILOT.md「路由界限」）。
- 编译用 structured_output 统一接缝；校验失败带错误重试一次，仍败如实返回错误，
  不编造计划。执行器（P2）与审批（P3）另立，本模块只产出计划文档。
- 落盘 <作品>/plans/<ts>-<slug>.plan.json（执行真源）+ 姊妹稿 .plan.md（单向渲染）。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.services import capability_registry, plan_validator, structured_output
from app.services.structured_contracts import GenerationPlan

# 显式计划语言：命中即委派
_EXPLICIT = (
    "做个计划", "做一个计划", "制定计划", "制定一个计划", "编排计划",
    "做个执行计划", "自动完成", "帮我安排", "批处理",
)
# 规模词：批量/全部/... 或 ≥2 的数量词（一张/一个不算——那可能只是单次生成）
_SCALE_RE = re.compile(r"批量|全部|所有|每个|每张|每条|每款|一批|[2-9\d二三四五六七八九十百千]+\s*(张|个|条|款)")
# 资产/生成动作：与规模词共现才算委派（剧情内单次创作不抢）
_ACTION_RE = re.compile(
    r"出\s*[0-9一二两三四五六七八九十百千]*\s*张|出图|生图|生成图片|生成视频|提交|导入|整理|建仓|"
    r"创建|更新|下载|编排|标注|重命名|删除")
# 疑问信号：带疑问的句子按剧情/对话处理，不做委派分派
_QUESTION_RE = re.compile(r"为什么|失败|问题|检查|审查|分析|？|\?")


def is_delegation_intent(text: str) -> bool:
    """零 LLM 委派强命令判定：高置信才 True（误判方向：模糊不改剧情默认）。"""
    source = (text or "").strip()
    if not source or _QUESTION_RE.search(source):
        return False
    if any(mark in source for mark in _EXPLICIT):
        return True
    return bool(_SCALE_RE.search(source) and _ACTION_RE.search(source))


@dataclass
class CompileOutcome:
    plan: GenerationPlan | None = None
    errors: list[str] = field(default_factory=list)
    raw: str = ""
    strategy: str = ""


def _manifest_lines(capabilities: list[dict]) -> str:
    lines = []
    for item in capabilities:
        avail = "" if item.get("available", True) else "（当前不可用：模型未配置）"
        level = item.get("side_effect_level")
        schema = item.get("params_schema") or {}
        required = set(schema.get("required") or [])
        props = schema.get("properties", {})
        params = "、".join(
            f"{name}*" if name in required else f"{name}(可选)"
            for name in props)
        lines.append(
            f"- {item['operation']}[{level}]{avail}：{item['description']} 参数：{params or '无'}"
            f"（带 * 为必填，标注「可选」的参数可省略）")
    return "\n".join(lines)


_COMPILE_SYSTEM = (
    "你是 Demiurge 的计划编译器。把用户意图编译成可机械执行的计划文档 JSON。\n"
    "只能使用下方能力清单里的 operation，不得编造能力；每个计划必须带预算 budgets；"
    "步骤数尽量少，超预算请拆多计划。durable/expensive 能力会要求用户审批，不要回避。\n"
    "意图含糊或缺少关键信息时，steps 留空并在 intent 里写清缺什么，禁止猜测硬编。\n"
    "【输出 JSON 合同（字段名必须逐字一致）】\n"
    "{\n"
    '  "intent": "一句话意图",\n'
    '  "repo_id": "作品ID或空串",\n'
    '  "budgets": {"max_steps": 24, "max_gpu_tasks": 32, "max_llm_calls": 8},\n'
    '  "steps": [{"id": "s1", "operation": "清单里的动词.宾语",\n'
    '             "params": {"参数名": "值"}, "inputs_from": [], "outputs": []}],\n'
    '  "approval_required": ["需要审批的 operation"]\n'
    "}\n"
    "steps[].id 是步骤标识（s1/s2…），inputs_from 引用此前步骤的 outputs 键。\n"
    "【能力清单（manifest）】\n{manifest}"
)

_COMPILE_USER = "{history}【用户意图】\n{intent}\n\n请输出计划 JSON。"


def compile_plan(*, intent: str, history: str = "", repo_id: str = "",
                 output_dir: str = "", configured_models: set[str] | frozenset[str] = frozenset(),
                 chat_base: str = "", chat_key: str = "", chat_model: str = "",
                 chat_fn: Callable | None = None, structured_chat_fn: Callable | None = None,
                 temperature: float = 0.2, proxy_kwargs: dict | None = None,
                 trace: Callable | None = None) -> CompileOutcome:
    """意图 → 校验通过的计划；两次编译都失败时返回带 errors 的 outcome。"""
    capabilities = capability_registry.with_availability(configured_models)
    system = _COMPILE_SYSTEM.replace(
        "{manifest}", _manifest_lines(capabilities))
    user = _COMPILE_USER.format(history=history, intent=intent)
    call_args = (chat_base, chat_key, chat_model, system, user)
    call_kwargs: dict[str, Any] = {"temperature": temperature, **(proxy_kwargs or {})}
    outcome = CompileOutcome()
    validator_errors: list[str] = []

    for attempt in (1, 2):  # 编译失败带校验错误重试一次（structured_output 现成模式）
        attempt_user = user if attempt == 1 else (
            user + "\n\n上一次编译未通过校验，请修正：\n" + "\n".join(validator_errors))
        try:
            result = structured_output.invoke(
                GenerationPlan,
                native=(lambda u=attempt_user: structured_chat_fn(
                    *call_args[:4], u, schema=GenerationPlan, **call_kwargs))
                if callable(structured_chat_fn) else None,
                legacy=lambda u=attempt_user: chat_fn(*call_args[:4], u, **call_kwargs),
                trace=trace,
            )
        except Exception as exc:  # noqa: BLE001 - 统一结构化错误
            outcome.errors = [f"计划编译失败：{exc}"]
            return outcome
        plan = result.value
        outcome.raw = result.raw or plan.model_dump_json()
        outcome.strategy = result.strategy
        # 计划声明的 repo/output 缺失时按调用方上下文补齐（编译器不知道运行环境）
        plan = plan.model_copy(update={
            "repo_id": plan.repo_id or repo_id,
        })
        validator_errors = plan_validator.validate(
            plan, capabilities=capabilities,
            configured_models=configured_models, allowed_prefix=output_dir,
        )
        if not validator_errors:
            outcome.plan = plan
            if trace is not None:
                trace("plan.compiled", status="ok", steps=len(plan.steps),
                      strategy=result.strategy)
            return outcome
        if trace is not None:
            trace("plan.compiled", status="invalid", attempt=attempt,
                  errors=validator_errors)
    if not outcome.errors:
        outcome.errors = validator_errors
    if plan.intent.strip() and any("steps 为空" in e for e in outcome.errors):
        # 模型判定意图含糊时，把它的「缺什么」说明如实带给用户（P1 clarify 语义）
        outcome.errors.append(f"模型认为意图缺少以下信息：{plan.intent.strip()}")
    return outcome


def save_plan(output_dir: str, repo_id: str, plan: GenerationPlan) -> str:
    """落盘执行真源 JSON + 人审视图 md；返回 JSON 路径。md 改动不回灌。"""
    from pathlib import Path

    base = Path(output_dir)
    plans = base / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", plan.intent)[:24].strip("-") or "plan"
    ts = time.strftime("%Y%m%d-%H%M%S")
    json_path = plans / f"{ts}-{slug}.plan.json"
    json_path.write_text(
        plan.model_dump_json(indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (plans / f"{ts}-{slug}.plan.md").write_text(render_plan_md(plan), encoding="utf-8")
    return str(json_path)


def render_plan_md(plan: GenerationPlan) -> str:
    """单向 json→md 人审视图；手改 md 不回灌（要改就改 json 或重新对话）。"""
    lines = [f"# 计划：{plan.intent}", "",
             f"- 作品：{plan.repo_id or '（未指定）'}",
             f"- 预算：步数≤{plan.budgets.max_steps}，GPU 任务≤{plan.budgets.max_gpu_tasks}，"
             f"LLM 调用≤{plan.budgets.max_llm_calls}", ""]
    if plan.approval_required:
        lines.append(f"需审批能力：{', '.join(plan.approval_required)}")
        lines.append("")
    read_paths = _declared_read_paths(plan)
    if read_paths:
        lines.append("将读取文件（批准计划即授权访问以下路径）：")
        lines.extend(f"- {path}" for path in read_paths)
        lines.append("")
    for index, step in enumerate(plan.steps, 1):
        lines.append(f"{index}. **{step.operation}**（id={step.id}）")
        if step.params:
            lines.append(f"   - params：`{step.params}`")
        if step.inputs_from:
            lines.append(f"   - inputs_from：{', '.join(step.inputs_from)}")
    lines.append("")
    return "\n".join(lines)


_PATH_LIKE_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|[/\\])")


def _declared_read_paths(plan: GenerationPlan) -> list[str]:
    """readonly 步骤 params 里声明的绝对路径（审批卡明示，批准即授权）。"""
    from app.services.capability_registry import get as _get
    out: list[str] = []
    for step in plan.steps:
        cap = _get(step.operation)
        if cap is None or cap.side_effect_level != "readonly":
            continue
        for value in step.params.values():
            if isinstance(value, str) and _PATH_LIKE_RE.match(value) and value not in out:
                out.append(value)
    return out


def render_plan_card(plan: GenerationPlan, json_path: str) -> str:
    """对话内计划卡：列步骤/预算/审批要求/将读取的文件。"""
    steps = "\n".join(
        f"  {i}. {step.operation}" for i, step in enumerate(plan.steps, 1))
    approval = f"\n- 需审批：{', '.join(plan.approval_required)}" if plan.approval_required else ""
    reads = _declared_read_paths(plan)
    read_note = ("；将读取文件（批准即授权）：" + "；".join(reads)) if reads else ""
    return (
        f"📋 已编译计划：\n"
        f"- 意图：{plan.intent}\n"
        f"- 步骤（{len(plan.steps)}）：\n{steps}\n"
        f"- 预算：步数≤{plan.budgets.max_steps} / GPU≤{plan.budgets.max_gpu_tasks} / "
        f"LLM≤{plan.budgets.max_llm_calls}{approval}{read_note}\n"
        f"- 文档：{json_path}\n"
        f"已投递执行队列：只读步骤直跑，写/烧卡步骤与越域读取等你批准后执行。"
    )
