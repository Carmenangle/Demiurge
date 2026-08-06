import json

from app.services import run_trace


def test_只读取当前作品最近Trace并可按turn过滤(monkeypatch, tmp_path):
    trace_file = tmp_path / "agent-trace.jsonl"
    records = [
        {"repo_id": "作品一", "turn_id": "t1", "event": "a", "data": {}},
        {"repo_id": "作品二", "turn_id": "t2", "event": "b", "data": {}},
        {"repo_id": "作品一", "turn_id": "t3", "event": "c", "data": {}},
    ]
    trace_file.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(run_trace, "TRACE_FILE", trace_file)

    assert [item["event"] for item in run_trace.read_recent("作品一")] == ["a", "c"]
    assert [item["event"] for item in run_trace.read_recent("作品一", turn_id="t3")] == ["c"]
