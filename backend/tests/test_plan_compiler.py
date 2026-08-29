"""Autopilot P1 单测：委派意图识别 / plan_validator 各分支 / 编译落盘闭环。"""
from __future__ import annotations

import json
from pathlib import Path

from app.services import plan_compiler, plan_validator
from app.services.capability_registry import all_capabilities
from app.services.structured_contracts import (
    GenerationPlan, PlanBudgets, PlanStep,
)

WORKS = r"D:\works\我的作品"


def _cap(operation: str):
    return next(c for c in all_capabilities() if c["operation"] == operation)


def _plan(**over) -> GenerationPlan:
    base = dict(
        intent="批量出 3 张变体图", repo_id="work",
        budgets=PlanBudgets(max_steps=4, max_gpu_tasks=4, max_llm_calls=2),
        steps=[PlanStep(id="s1", operation="workflow.list_templates")],
    )
    base.update(over)
    return GenerationPlan(**base)


# ── 委派意图识别（路由界限·零 LLM 层）────────────────────────────────────────

def test_高置信委派命中():
    assert plan_compiler.is_delegation_intent("帮我批量出 20 张变体图")
    assert plan_compiler.is_delegation_intent("整理全部世界书条目")
    assert plan_compiler.is_delegation_intent("把这三张卡都导入并建仓")
    assert plan_compiler.is_delegation_intent("帮我做个计划自动完成出图")


def test_单次创作与疑问不误判():
    assert not plan_compiler.is_delegation_intent("画一张图")
    assert not plan_compiler.is_delegation_intent("生成一张图")
    assert not plan_compiler.is_delegation_intent("为什么批量出图失败了？")
    assert not plan_compiler.is_delegation_intent("她提笔画了一幅像")
    assert not plan_compiler.is_delegation_intent("")


# ── plan_validator ───────────────────────────────────────────────────────────

def test_合法计划零错误():
    assert plan_validator.validate(
        _plan(), capabilities=all_capabilities(),
        configured_models={"chat", "image"}, allowed_prefix=WORKS) == []


def test_未知能力被拦():
    plan = _plan(steps=[PlanStep(id="s1", operation="ghost.action")])
    errors = plan_validator.validate(plan, capabilities=all_capabilities())
    assert any("ghost.action" in e for e in errors)


def test_缺必填参数与多余参数被拦():
    plan = _plan(steps=[PlanStep(id="s1", operation="workflow.read_exposed_fields")])
    errors = plan_validator.validate(plan, capabilities=all_capabilities())
    assert any("template_id" in e for e in errors)
    plan2 = _plan(steps=[PlanStep(id="s1", operation="workflow.list_templates",
                                  params={"junk": 1})])
    errors2 = plan_validator.validate(plan2, capabilities=all_capabilities())
    assert any("junk" in e for e in errors2)


def test_模型缺口被拦():
    plan = _plan(steps=[PlanStep(id="s1", operation="workflow.submit_batch",
                                 params={"template_id": "t", "variants": [{}],
                                         "prompt": "p", "url": "http://127.0.0.1:8188"})])
    errors = plan_validator.validate(plan, capabilities=all_capabilities(),
                                     configured_models={"chat"})
    assert any("image" in e and "未配置" in e for e in errors)


def test_无预算与巨型计划被拦():
    plan = _plan(budgets=PlanBudgets(max_steps=0, max_gpu_tasks=1, max_llm_calls=1))
    assert any("budgets" in e for e in plan_validator.validate(plan, capabilities=all_capabilities()))
    plan2 = _plan(budgets=PlanBudgets(max_steps=99, max_gpu_tasks=1, max_llm_calls=1))
    assert any("拆成多个小计划" in e for e in plan_validator.validate(plan2, capabilities=all_capabilities()))


def test_inputs_from环被拦():
    plan = _plan(steps=[
        PlanStep(id="a", operation="workflow.list_templates", inputs_from=["b"]),
        PlanStep(id="b", operation="workflow.list_templates", inputs_from=["a"]),
    ])
    assert any("成环" in e for e in plan_validator.validate(plan, capabilities=all_capabilities()))


def test_审批汇总不一致被拦():
    plan = _plan(approval_required=["workflow.submit_template"])
    errors = plan_validator.validate(plan, capabilities=all_capabilities(),
                                     configured_models={"chat", "image"})
    assert any("approval_required" in e for e in errors)


def test_路径越出作品域被拦():
    plan = _plan(steps=[PlanStep(id="s1", operation="workflow.submit_template",
                                 params={"template_id": "t", "values": {},
                                         "prompt": r"D:\other\evil.png",
                                         "url": "http://127.0.0.1:8188"})])
    errors = plan_validator.validate(plan, capabilities=all_capabilities(),
                                     configured_models={"chat", "image"},
                                     allowed_prefix=WORKS)
    assert any("越出作品域" in e for e in errors)


# ── 编译闭环（structured_output 假件）────────────────────────────────────────

class _FakeStructured:
    def __init__(self, payload_fn):
        self.payload_fn = payload_fn
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        schema = kwargs["schema"]
        return schema.model_validate(self.payload_fn(self.calls))


def test_编译一次成功并落盘(tmp_path):
    payload = {
        "intent": "批量出 3 张变体图", "repo_id": "work",
        "budgets": {"max_steps": 4, "max_gpu_tasks": 4, "max_llm_calls": 2},
        "steps": [
            {"id": "s1", "operation": "workflow.list_templates"},
            {"id": "s2", "operation": "workflow.submit_batch",
             "params": {"template_id": "t", "variants": [{"steps": 20}],
                        "prompt": "p", "url": "http://127.0.0.1:8188"},
             "inputs_from": ["s1"]},
        ],
        "approval_required": ["workflow.submit_batch"],
    }
    fake = _FakeStructured(lambda _c: payload)
    outcome = plan_compiler.compile_plan(
        intent="批量出 3 张变体图", repo_id="work", output_dir=str(tmp_path),
        configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="", chat_fn=lambda *a, **k: "", structured_chat_fn=fake)
    assert outcome.plan is not None, outcome.errors
    assert outcome.plan.steps[1].inputs_from == ["s1"]

    json_path = plan_compiler.save_plan(str(tmp_path), "work", outcome.plan)
    assert Path(json_path).is_file()
    assert Path(json_path).suffix == ".json"
    saved = json.loads(Path(json_path).read_text(encoding="utf-8"))
    assert saved["intent"] == "批量出 3 张变体图"
    md = Path(json_path.replace(".plan.json", ".plan.md"))
    assert md.is_file() and "需审批" in md.read_text(encoding="utf-8")

    card = plan_compiler.render_plan_card(outcome.plan, json_path)
    assert "workflow.submit_batch" in card and "已投递执行队列" in card


def test_编译两次仍非法如实返回错误(tmp_path):
    bad = {"intent": "x", "steps": [{"id": "s1", "operation": "ghost.action"}]}
    fake = _FakeStructured(lambda _c: bad)
    outcome = plan_compiler.compile_plan(
        intent="x", output_dir=str(tmp_path), configured_models={"chat"},
        chat_base="", chat_key="", chat_model="", chat_fn=lambda *a, **k: "",
        structured_chat_fn=fake)
    assert outcome.plan is None
    assert outcome.errors and any("ghost.action" in e for e in outcome.errors)
    assert fake.calls == 2  # 带校验错误重试一次
