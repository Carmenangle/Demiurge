"""连续性编译：把多种真源与检索证据压成单一、定额的剧情上下文。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.services.agent_context import estimate_tokens


@dataclass(frozen=True)
class ContextSource:
    kind: str
    content: str
    authoritative: bool
    priority: int


@dataclass(frozen=True)
class CompiledContext:
    text: str
    tokens: int
    included: tuple[str, ...]


def _clip(value: str, budget: int) -> str:
    text = value.strip()
    if not text or budget <= 0:
        return ""
    if estimate_tokens(text) <= budget:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


def compile_context(sources: Iterable[ContextSource], *, token_budget: int = 900) -> CompiledContext:
    """按真源优先级去重并裁剪；RAG 证据永远不能覆盖结构化真源。"""
    remaining = max(0, token_budget)
    blocks: list[str] = []
    included: list[str] = []
    seen: set[str] = set()
    for source in sorted(sources, key=lambda item: (-item.priority, item.kind)):
        content = "\n".join(
            line for line in source.content.strip().splitlines()
            if line.strip() and line.strip().casefold() not in seen
        )
        for line in content.splitlines():
            seen.add(line.strip().casefold())
        if not content or remaining <= 0:
            continue
        label = "authoritative" if source.authoritative else "evidence-only"
        header = f"[{source.kind} · {label}]"
        header_cost = estimate_tokens(header) + 1
        clipped = _clip(content, max(0, remaining - header_cost))
        if not clipped:
            continue
        block = f"{header}\n{clipped}"
        blocks.append(block)
        included.append(source.kind)
        remaining -= estimate_tokens(block)
    if not blocks:
        return CompiledContext("", 0, ())
    preface = (
        "【连续性上下文】结构化真源优先；evidence-only 仅供回忆，若与当前状态或有效事实冲突必须忽略。"
    )
    text = preface + "\n" + "\n\n".join(blocks)
    return CompiledContext(text, estimate_tokens(text), tuple(included))


def temporal_fact_text(facts: Iterable[dict]) -> str:
    return "\n".join(
        f"- {fact.get('subject', '')}｜{fact.get('predicate', '')}｜{fact.get('object', '')}"
        f"（自第{fact.get('valid_from_turn', 0)}回合；证据：{fact.get('evidence', '')}）"
        for fact in facts
        if fact.get("subject") and fact.get("predicate") and fact.get("object")
    )
