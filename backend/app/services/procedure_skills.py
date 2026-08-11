"""从 Trace 提炼、审核并在能力沙箱内执行的流程技能。"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable

from app.config import DATA_DIR
from app.services import capability_sandbox, run_trace, scenario_lab


STORE = DATA_DIR / "procedure_skills.json"
_EVENT_ACTIONS = {
    "illustration.request": "illustration.generate",
    "workflow.submitted": "workflow.submit",
    "rag.write": "rag.index",
    "scenario.snapshot": "scenario.snapshot",
}


def _load() -> list[dict[str, Any]]:
    if not STORE.is_file():
        return []
    try:
        value = json.loads(STORE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(items: list[dict[str, Any]]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def propose(repo_id: str, turn_id: str, name: str) -> dict[str, Any]:
    records = run_trace.read_recent(repo_id, turn_id=turn_id, limit=200)
    steps: list[dict[str, Any]] = []
    for record in records:
        action = _EVENT_ACTIONS.get(str(record.get("event") or ""))
        if not action:
            continue
        raw_data = record.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        params = {key: value for key, value in data.items()
                  if key in {"repo_id", "output_dir", "workflow_id", "prompt", "query"}}
        steps.append({"action": action, "params": params, "approval_required": True})
    deduped: list[dict[str, Any]] = []
    for step in steps:
        if not deduped or deduped[-1]["action"] != step["action"]:
            deduped.append(step)
    item = {
        "id": uuid.uuid4().hex, "name": name.strip() or "Trace 流程",
        "source": {"repo_id": repo_id, "turn_id": turn_id},
        "status": "draft", "steps": deduped, "created_at": int(time.time() * 1000),
    }
    items = _load()
    items.append(item)
    _save(items)
    return item


def review(skill_id: str, steps: list[dict[str, Any]], *, approved: bool) -> dict[str, Any]:
    items = _load()
    for index, item in enumerate(items):
        if item.get("id") != skill_id:
            continue
        normalized = []
        for step in steps:
            action = str(step.get("action") or "").strip()
            if not action:
                raise ValueError("流程步骤缺少 action")
            normalized.append({"action": action, "params": dict(step.get("params") or {}),
                               "approval_required": bool(step.get("approval_required", True))})
        item = {**item, "steps": normalized, "status": "approved" if approved else "draft",
                "reviewed_at": int(time.time() * 1000)}
        items[index] = item
        _save(items)
        return item
    raise FileNotFoundError("流程技能不存在")


def dry_run(skill_id: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    item = next((value for value in _load() if value.get("id") == skill_id), None)
    if not item:
        raise FileNotFoundError("流程技能不存在")
    parameters = parameters or {}
    plan = []
    for step in item.get("steps") or []:
        params = {**(step.get("params") or {}), **parameters}
        action = str(step.get("action") or "")
        plan.append({"action": action, "params": params,
                     "supported": action in _ADAPTERS,
                     "approval_required": bool(step.get("approval_required", True))})
    return {"skill_id": skill_id, "status": item.get("status"), "plan": plan,
            "executable": item.get("status") == "approved" and bool(plan)
            and all(step["supported"] for step in plan)}


def _snapshot(params: dict[str, Any]) -> dict[str, Any]:
    return scenario_lab.create_snapshot(
        str(params.get("output_dir") or ""), str(params.get("repo_id") or ""),
        turn=int(params.get("turn") or 0), label=str(params.get("label") or "流程技能"),
    )


_ADAPTERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "scenario.snapshot": _snapshot,
}


def execute(skill_id: str, lease_id: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = dry_run(skill_id, parameters)
    if not plan["executable"]:
        raise ValueError("流程未审核、为空或含未实现的执行 Adapter")
    results = []
    for step in plan["plan"]:
        params = step["params"]
        capability_sandbox.authorize(
            lease_id, step["action"], path=str(params.get("output_dir") or ""),
        )
        results.append({"action": step["action"], "result": _ADAPTERS[step["action"]](params)})
    return {"ok": True, "skill_id": skill_id, "results": results}
