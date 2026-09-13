"""跨调用复用的结构化输出领域合同。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SupervisorDecision(BaseModel):
    route: str
    confidence: str = "high"
    alternatives: list[str] = Field(default_factory=list)
    scene: str = ""


class TemporalFactCandidate(BaseModel):
    subject: str
    predicate: str
    object: str
    evidence: str
    # P3② 指认式替代（2026-09-06 用户裁决）：本次剧情使某条既有事实过期时，抽取模型
    # 引用【现存事实】清单里的 id 前缀（≥8 位）——旧事实收口、时间线保留。禁止 DELETE；
    # 空/解析失败/校验失败一律降级为普通 ADD（矛盾交 conflicts 诊断，绝不静默破坏）。
    supersedes_id: str = ""


class RichChronicle(BaseModel):
    overview: str = ""
    chronicle: str = ""
    summary: str = ""
    dialogue: str = ""
    characters: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    facts: list[TemporalFactCandidate] = Field(default_factory=list)


class ManualFillResult(BaseModel):
    ops: list[dict[str, Any]] = Field(default_factory=list)
    chronicles: list[RichChronicle] = Field(default_factory=list)


class PlanStep(BaseModel):
    id: str
    operation: str                      # 能力清单里的「动词.宾语」，全局唯一
    params: dict[str, Any] = Field(default_factory=dict)
    inputs_from: list[str] = Field(default_factory=list)   # 引用上一步 outputs 的键
    outputs: list[str] = Field(default_factory=list)


class PlanBudgets(BaseModel):
    max_steps: int = 24
    max_gpu_tasks: int = 32
    max_llm_calls: int = 8


class GenerationPlan(BaseModel):
    """Autopilot P1 计划文档合同（docs/ROADMAP-AUTOPILOT.md）。执行真源，落 <作品>/plans/。"""
    intent: str
    repo_id: str = ""
    budgets: PlanBudgets = Field(default_factory=PlanBudgets)
    steps: list[PlanStep] = Field(default_factory=list)
    approval_required: list[str] = Field(default_factory=list)  # 需审批的 operation 汇总
