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
