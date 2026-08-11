"""Trace Replay：离线重放已录模型输出与事件不变量，不执行任何外部副作用。"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.services import run_trace, structured_output
from app.services.structured_contracts import SupervisorDecision


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
    return {
        "version": 1,
        "summary": {"cases": len(cases), "passed": passed, "failed": len(cases) - passed},
        "cases": cases,
    }


def replay_recent(repo_id: str, *, turn_id: str = "", limit: int = 200) -> dict[str, Any]:
    return evaluate_records(run_trace.read_recent(repo_id, turn_id=turn_id, limit=limit))
