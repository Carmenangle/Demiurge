import json

from app.services import task_progress_store


def test_progress_store_round_trip_and_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(task_progress_store, "STORE_DIR", tmp_path)
    tasks = {
        "old": {"updated_at": 1, "name": "旧任务"},
        "new": {"updated_at": 2, "name": "新任务"},
    }
    task_progress_store.save("downloads", tasks, limit=1)
    assert task_progress_store.load("downloads") == {
        "new": {"updated_at": 2, "name": "新任务"},
    }
    assert json.loads((tmp_path / "downloads.json").read_text(encoding="utf-8"))["new"]["name"] == "新任务"


def test_running_task_becomes_interrupted_after_restart():
    tasks = {"task": {"status": "downloading", "running": True, "speed_bps": 10}}
    assert task_progress_store.mark_interrupted(
        tasks, running_statuses={"pending", "downloading"},
    ) is True
    assert tasks["task"]["status"] == "error"
    assert tasks["task"]["phase"] == "interrupted"
    assert tasks["task"]["running"] is False
    assert "重启" in tasks["task"]["error"]


def test_terminal_task_is_preserved():
    tasks = {"task": {"status": "done", "phase": "done"}}
    assert task_progress_store.mark_interrupted(tasks, running_statuses={"pending"}) is False
    assert tasks["task"] == {"status": "done", "phase": "done"}
