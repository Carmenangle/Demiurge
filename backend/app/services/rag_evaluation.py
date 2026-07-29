"""RAG 检索评估指标与 RAGAS 数据行构造。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RetrievalMetrics:
    cases: int = 0
    hits: int = 0
    reciprocal_rank_sum: float = 0.0
    found: int = 0
    expected: int = 0
    durations: list[float] = field(default_factory=list)

    def add(self, ranks: list[int], found: int, expected: int, duration: float) -> None:
        self.cases += 1
        self.hits += int(bool(ranks))
        self.reciprocal_rank_sum += 1.0 / min(ranks) if ranks else 0.0
        self.found += found
        self.expected += expected
        self.durations.append(duration)

    def summary(self) -> dict[str, float | int]:
        count = self.cases or 1
        return {
            "cases": self.cases,
            "hit_rate": self.hits / count,
            "mrr": self.reciprocal_rank_sum / count,
            "recall": self.found / self.expected if self.expected else 0.0,
            "avg_latency_sec": sum(self.durations) / count,
            "max_latency_sec": max(self.durations, default=0.0),
        }


def assess_node_hits(hits: list[dict], expected_nodes: list[str],
                     expected_any: list[list[str]] | None = None) -> dict[str, object]:
    """按必需节点和功能平替组评估一次节点召回。"""
    required = set(expected_nodes)
    alternative_groups = [list(dict.fromkeys(group)) for group in (expected_any or [])]
    found_nodes: set[str] = set()
    found_any: list[str | None] = [None] * len(alternative_groups)
    ranks: list[int] = []
    for rank, hit in enumerate(hits, 1):
        names = set(hit.get("node_names", []) or [])
        before = len(found_nodes) + sum(value is not None for value in found_any)
        found_nodes.update(required.intersection(names))
        for index, group in enumerate(alternative_groups):
            if found_any[index] is None:
                found_any[index] = next((name for name in group if name in names), None)
        after = len(found_nodes) + sum(value is not None for value in found_any)
        if after > before:
            ranks.append(rank)
    matched_alternatives = [value for value in found_any if value is not None]
    return {
        "ranks": ranks,
        "found_nodes": sorted(found_nodes),
        "found_any": matched_alternatives,
        "found_count": len(found_nodes) + len(matched_alternatives),
        "expected_count": len(required) + len(alternative_groups),
    }


def assess_context_hits(hits: list[dict], expected_contexts: list[str],
                        required_source: str | None = None) -> dict[str, object]:
    """评估知识片段，并可要求命中来自指定知识库来源。"""
    expected = list(dict.fromkeys(expected_contexts))
    found: list[str] = []
    ranks: list[int] = []
    for rank, hit in enumerate(hits, 1):
        if required_source is not None and hit.get("source") != required_source:
            continue
        matched = [
            marker for marker in expected
            if marker not in found and marker in str(hit.get("content", ""))
        ]
        if matched:
            found.extend(matched)
            ranks.append(rank)
    return {"ranks": ranks, "found": found}


def ragas_row(user_input: str, retrieved_contexts: list[str],
              response: str = "", reference: str = "") -> dict:
    """生成新版 RAGAS 强制要求的四字段记录。"""
    return {
        "user_input": user_input,
        "retrieved_contexts": retrieved_contexts,
        "response": response,
        "reference": reference,
    }
