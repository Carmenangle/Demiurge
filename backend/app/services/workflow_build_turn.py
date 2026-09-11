"""AI 搭工作流的一次搭建回合：统一上下文、查询与节点候选准备。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.services import node_candidates


QueryOptimizer = Callable[[str], str]


@dataclass(frozen=True)
class BuildTurn:
    """一次搭建请求所需的稳定上下文快照。"""

    need: str
    current_graph: dict
    history_text: str
    context_query: str
    candidates: node_candidates.NodeCandidates


def _history_text(history: list[dict] | None, max_chars: int = 24000) -> str:
    if not history:
        return ""
    lines: list[str] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = "用户" if item.get("role") == "user" else "助手"
        content = str(item.get("text") or item.get("content") or "").strip()
        if content:
            lines.append(f"{role}：{content}")
    if not lines:
        return ""
    body = "\n".join(lines)
    if len(body) > max_chars:
        head = "\n".join(lines[:2])
        tail_budget = max(0, max_chars - len(head) - 80)
        body = head + "\n…（中间历史因上下文预算折叠）…\n" + body[-tail_budget:]
    return "【搭建对话历史（用户纠正优先于助手旧建议）】\n" + body


def _context_query(need: str, history: list[dict] | None) -> str:
    snippets: list[str] = []
    for item in (history or [])[-6:]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("text") or item.get("content") or "").strip()
        if content:
            snippets.append(content)
    extra = " ".join(snippets)
    return f"{need} {extra}"[:12000] if extra else need


def prepare(
    cfg,
    *,
    need: str,
    comfy_url: str,
    current_graph: dict | None,
    history: list[dict] | None,
    k: int,
    query_optimizer: QueryOptimizer | None = None,
) -> BuildTurn:
    """冻结搭建回合并解析候选；RAG 降级规则仍由节点候选 Module 拥有。"""
    normalized_need = need.strip()
    if not normalized_need:
        raise ValueError("需求为空")
    query = _context_query(normalized_need, history)
    if query_optimizer is not None:
        query = query_optimizer(query)
    return BuildTurn(
        need=normalized_need,
        current_graph=dict(current_graph or {}),
        history_text=_history_text(history),
        context_query=query,
        candidates=node_candidates.resolve(cfg, query, comfy_url, k=k),
    )
