"""Trace Replay：离线重放已录模型输出与事件不变量，不执行任何外部副作用。"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.services import prompt_compliance, run_trace, structured_output
from app.services.structured_contracts import SupervisorDecision


def _model_quality(records: list[dict[str, Any]]) -> dict[str, Any]:
    """模型调用质量统计：usage 缓存命中率、失败重试率、请求-响应配对完整性。

    依据 model.request / model.response / model.usage / agent.error 事件。
    """
    requests = [r for r in records if r.get("event") == "model.request"]
    responses = [r for r in records if r.get("event") == "model.response"]
    usage_events = [r for r in records if r.get("event") == "model.usage"]
    errors = [r for r in records if r.get("event") == "agent.error"]

    stats: dict[str, Any] = {
        "requests": len(requests),
        "responses": len(responses),
        "usage_events": len(usage_events),
        "errors": len(errors),
        "request_response_ratio": round(len(responses) / len(requests), 4) if requests else 1.0,
        "cached_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cache_hit_ratio": 0.0,
        "by_agent": {},
    }

    for rec in usage_events:
        data = rec.get("data")
        data = data if isinstance(data, dict) else {}
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        stats["cached_tokens"] += int(usage.get("cached_tokens") or 0)
        stats["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        stats["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        stats["total_tokens"] += int(usage.get("total_tokens") or 0)
        agent = str(data.get("agent") or "?")
        bucket = stats["by_agent"].setdefault(agent, {
            "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0,
        })
        bucket["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        bucket["cached_tokens"] += int(usage.get("cached_tokens") or 0)
        bucket["completion_tokens"] += int(usage.get("completion_tokens") or 0)

    if stats["prompt_tokens"]:
        stats["cache_hit_ratio"] = round(stats["cached_tokens"] / stats["prompt_tokens"], 4)

    # 错误率（含 model.error 与 agent.error 中的模型失败）
    model_errors = sum(1 for e in errors if "model" in str(e.get("data") or {}).lower())
    stats["error_rate"] = round(model_errors / len(requests), 4) if requests else 0.0
    return stats


def _case(turn_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    events = [str(record.get("event") or "") for record in records]
    supervisor_raw = ""
    completed_route = ""
    for record in records:
        raw_data = record.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        if record.get("event") == "model.response" and data.get("agent") == "supervisor":
            supervisor_raw = str(data.get("content") or "")
        if record.get("event") == "agent.completed" and data.get("agent") == "supervisor":
            completed_route = str(data.get("route") or "")

    decision: SupervisorDecision | None = None
    supervisor_schema = True
    if supervisor_raw:
        try:
            decision = structured_output.parse_model(supervisor_raw, SupervisorDecision)
        except structured_output.StructuredOutputError:
            supervisor_schema = False
    route_consistent = (
        decision is None or not completed_route or decision.route == completed_route
        or completed_route == "clarify"
    )
    requests = [
        record for record in records if record.get("event") == "illustration.request"
    ]
    illustration_terminal = all(
        str((record.get("data") or {}).get("status") or "") in {"emitted", "skipped"}
        for record in requests
    )
    ci_records = [record for record in records if record.get("event") == "narrative.ci"]
    narrative_ci_terminal = all(
        str((record.get("data") or {}).get("status") or "") in {"evaluated", "unavailable"}
        for record in ci_records
    )
    checks = {
        "turn_started": "turn.started" in events,
        "turn_completed": "turn.completed" in events,
        "supervisor_schema": supervisor_schema,
        "route_consistent": route_consistent,
        "illustration_terminal": illustration_terminal,
        "narrative_ci_terminal": narrative_ci_terminal,
    }
    return {
        "turn_id": turn_id,
        "repo_id": str(records[0].get("repo_id") or "") if records else "",
        "passed": all(checks.values()),
        "checks": checks,
        "compliance": prompt_compliance.evaluate_turn(records),
        "model_quality": _model_quality(records),
        "event_count": len(records),
    }


def evaluate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        turn_id = str(record.get("turn_id") or "").strip()
        if turn_id:
            grouped[turn_id].append(record)
    cases = [_case(turn_id, items) for turn_id, items in grouped.items()]
    passed = sum(1 for case in cases if case["passed"])
    outcomes: dict[str, int] = {}
    for case in cases:
        outcome = str(case["compliance"]["outcome"])
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    # 全局模型调用质量聚合（跨回合）
    global_quality = _model_quality(records)
    return {
        "version": 2,
        "summary": {
            "cases": len(cases), "passed": passed, "failed": len(cases) - passed,
            "outcomes": outcomes,
        },
        "model_quality": global_quality,
        "cases": cases,
    }


def replay_recent(repo_id: str, *, turn_id: str = "", limit: int = 200) -> dict[str, Any]:
    return evaluate_records(run_trace.read_recent(repo_id, turn_id=turn_id, limit=limit))
