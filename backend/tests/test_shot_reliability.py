"""分镜模拟可靠性：重试 + 截断 JSON 续写（离线、零 API 调用）。"""

from __future__ import annotations

import json

import pytest

from app.services import shot_reliability as sr


def test_call_with_retry_transient_then_success():
    calls = {"n": 0, "retries": 0}

    def fn() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("502 upstream timeout")
        return "ok"

    result = sr.call_with_retry(
        fn, attempts=3, backoff=(0.0, 0.0),
        on_retry=lambda n, exc: calls.__setitem__("retries", calls["retries"] + 1),
    )

    assert result == "ok"
    assert calls["n"] == 2
    assert calls["retries"] == 1


def test_call_with_retry_does_not_retry_deterministic_error():
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        raise ValueError("400 invalid parameter")

    with pytest.raises(RuntimeError):
        sr.call_with_retry(fn, attempts=3, backoff=(0.0, 0.0))
    assert calls["n"] == 1


def test_complete_json_continues_truncated_payload():
    payload = json.dumps({"anchors": [{"anchor": "甲"}, {"anchor": "乙"}]}, ensure_ascii=False)
    cut = payload[:len(payload) // 2]
    calls = {"n": 0}

    def continue_fn(partial: str) -> str:
        calls["n"] += 1
        return payload[len(partial):]

    merged = sr.complete_json(cut, continue_fn, max_attempts=3)

    assert calls["n"] == 1
    assert json.loads(merged)["anchors"] == [{"anchor": "甲"}, {"anchor": "乙"}]


def test_complete_json_stops_without_progress():
    calls = {"n": 0}

    def continue_fn(partial: str) -> str:
        calls["n"] += 1
        return ""

    merged = sr.complete_json('{"anchors": [', continue_fn, max_attempts=3)

    assert calls["n"] == 1
    assert not sr.json_complete(merged)


def test_json_complete_distinguishes_truncated():
    assert sr.json_complete('{"anchors": []}')
    assert not sr.json_complete('{"anchors": [')
    assert not sr.json_complete('{"anchors": ["未闭合')
