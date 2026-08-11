from app.services import trace_replay
from app.routers import ai_agent


def _record(event: str, data: dict, turn: str = "t1") -> dict:
    return {
        "timestamp": "2026-08-10T12:00:00+08:00",
        "turn_id": turn,
        "repo_id": "repo",
        "event": event,
        "data": data,
    }


def test_replay_validates_recorded_supervisor_and_turn_invariants():
    report = trace_replay.evaluate_records([
        _record("turn.started", {}),
        _record("model.response", {
            "agent": "supervisor",
            "content": '{"route":"answer","confidence":"high","alternatives":[],"scene":"dialogue"}',
        }),
        _record("agent.completed", {"agent": "supervisor", "route": "answer"}),
        _record("illustration.request", {"status": "emitted", "reason": "main_plan"}),
        _record("turn.completed", {"interrupted": False}),
    ])

    assert report["summary"] == {"cases": 1, "passed": 1, "failed": 0}
    case = report["cases"][0]
    assert case["checks"]["turn_completed"] is True
    assert case["checks"]["supervisor_schema"] is True
    assert case["checks"]["route_consistent"] is True
    assert case["checks"]["illustration_terminal"] is True


def test_replay_reports_broken_recorded_json_without_executing_side_effects():
    report = trace_replay.evaluate_records([
        _record("turn.started", {}),
        _record("model.response", {"agent": "supervisor", "content": "{broken"}),
        _record("turn.completed", {}),
    ])

    assert report["summary"]["failed"] == 1
    assert report["cases"][0]["checks"]["supervisor_schema"] is False


def test_trace_replay_router_is_a_thin_offline_adapter(monkeypatch):
    monkeypatch.setattr(
        trace_replay, "replay_recent",
        lambda repo_id, *, turn_id, limit: {
            "repo_id": repo_id, "turn_id": turn_id, "limit": limit,
        },
    )

    result = ai_agent.replay_trace(ai_agent.TraceReplayRequest(
        repo_id="  work  ", turn_id=" t1 ", limit=20,
    ))

    assert result == {"repo_id": "work", "turn_id": "t1", "limit": 20}
