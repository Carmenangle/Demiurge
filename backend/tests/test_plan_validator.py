"""plan_validator 占位符规则（P1 纯函数）：内容宏豁免 + 真引用仍被拒。

回归背景（2026-09-10）：`{{user}}`/`{{char}}` 是 ST 惯例的**内容**宏——本项目按用户定案
把主角名替换成 `{{user}}` 写进世界书/角色卡（`capability_handlers`），固化04 合并产物
必然带它。此前「value 里同时出现 `{{` 与 `}}`」一律判未解析占位符，导致**配方从未产出过**
（`plan_recipes.json` 至今不存在）。现先剥登记宏、再看残留里是否仍有大括号。

名单单一属主 = `preset_store.CONTENT_MACRO_NAMES`，本文件只断言行为、不复制名单。
"""
from __future__ import annotations

import pytest

from app.services import plan_validator, preset_store
from app.services.capability_registry import all_capabilities
from app.services.structured_contracts import GenerationPlan, PlanBudgets, PlanStep


def _plan(content: str) -> GenerationPlan:
    return GenerationPlan(
        intent="占位符规则用例", repo_id="",
        budgets=PlanBudgets(max_steps=1, max_gpu_tasks=0, max_llm_calls=0),
        steps=[PlanStep(id="s1", operation="doc.create_repo",
                        params={"rel_path": "a.md", "content": content})],
        approval_required=["doc.create_repo"])


def _errors(content: str) -> list[str]:
    return plan_validator.validate(_plan(content), capabilities=all_capabilities())


# ── 内容宏：放行 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("content", [
    "# 设定总集\n\n{{user}} 扮演的主角。",
    "# 设定总集\n\n{{char}} 的搭档。",
    "# 设定总集\n\n{{ lastUserMessage }}",              # 带空格
    "# 设定总集\n\n{{lastCharMessage}} 与 {{USER}}",     # 大小写不敏感
    "# 设定总集\n\n正文里没有宏。",
])
def test_内容宏不被判为占位符(content):
    assert _errors(content) == []


def test_内容宏与非大括号占位符并存时仍拦后者():
    errs = _errors("# 设定总集\n\n{{user}} 待解析")
    assert errs and "占位符" in errs[0]


# ── 真引用 / 其它占位符：仍必须被拒 ──────────────────────────────────────────

@pytest.mark.parametrize("content", [
    "{{step1.output}}",                    # 引用前序步骤
    "{{step2.result}}",
    "{{ user }} 与 {{step1.out}}",          # 有内容宏也要拦真引用
    "TO_BE_RESOLVED",
    "内容 placeholder 占位",
    "待解析",
    "待定",
])
def test_真占位符仍被拒(content):
    errs = _errors(content)
    assert errs and "未解析占位符" in errs[0]


def test_占位符检查递归进嵌套结构():
    """params 里嵌 dict/list 时也要扫到（模型常把内容塞在数组/对象里）。"""
    plan = GenerationPlan(
        intent="嵌套用例", repo_id="",
        budgets=PlanBudgets(max_steps=1, max_gpu_tasks=0, max_llm_calls=0),
        steps=[PlanStep(id="s1", operation="doc.create_repo",
                        params={"rel_path": "a.md",
                                "content": {"blocks": ["{{user}}", "{{step1.out}}"]}})],)
    errs = plan_validator.validate(plan, capabilities=all_capabilities())
    # content 声明为 string，嵌套结构本身也会触发 schema 错误；这里只钉占位符那条
    assert any("未解析占位符" in e and "blocks[1]" in e for e in errs)


# ── 生词表剥宏工具本身 ──────────────────────────────────────────────────────

def test_strip_content_macros_只剥登记宏() -> None:
    assert preset_store.strip_content_macros("{{user}} 与 {{step1.out}}") == " 与 {{step1.out}}"
    assert preset_store.strip_content_macros("{{ lastUserMessage }}") == ""
    assert preset_store.strip_content_macros("") == ""
    assert preset_store.strip_content_macros("无宏正文") == "无宏正文"


# ── 生成意图守卫：「生图提示词」复合词剥离（2026-09-11 实锤）────────────────

def _readonly_plan(intent: str) -> GenerationPlan:
    return GenerationPlan(
        intent=intent, repo_id="",
        budgets=PlanBudgets(max_steps=2, max_gpu_tasks=0, max_llm_calls=0),
        steps=[PlanStep(id="s1", operation="file.read_text",
                        params={"path": "文档.md"})],
        approval_required=[])


def test_讨论生图提示词不触发submit守卫():
    """intent 提「生图提示词」（讨论对象）但计划只读 → 不再报「意图要求出图/提交」。"""
    errs = plan_validator.validate(
        _readonly_plan("参考服装文档，讨论各情况下的生图提示词"),
        capabilities=all_capabilities())
    assert not any("没有任何 submit/collect" in e for e in errs)


def test_真生成意图仍触发submit守卫():
    errs = plan_validator.validate(
        _readonly_plan("批量出图：10 张角色立绘"),
        capabilities=all_capabilities())
    assert any("没有任何 submit/collect" in e for e in errs)


def test_strip_prompt_compounds():
    assert plan_validator.strip_prompt_compounds("讨论生图提示词") == "讨论"
    assert plan_validator.strip_prompt_compounds("出图思路与要点") == "与要点"
    assert plan_validator.strip_prompt_compounds("批量生图") == "批量生图"  # 非复合词不动
