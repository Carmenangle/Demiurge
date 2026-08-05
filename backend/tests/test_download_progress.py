import json

import httpx
import pytest

from app.services import model_downloader, workflow_downloader


@pytest.fixture(autouse=True)
def isolate_progress_persistence(monkeypatch):
    monkeypatch.setattr(model_downloader.task_progress_store, "save", lambda *_args, **_kwargs: None)


def test_transfer_progress_reports_short_window_speed(monkeypatch):
    task = "speed-test"
    model_downloader._TASKS[task] = {}
    try:
        sample = model_downloader._record_transfer(task, 1024, (10.0, 0), now=10.5)
        status = model_downloader.get_status(task)
        assert sample == (10.5, 1024)
        assert status["downloaded"] == 1024
        assert status["speed_bps"] == 2048
    finally:
        model_downloader._TASKS.pop(task, None)


def test_new_download_exposes_visual_progress_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(model_downloader.threading.Thread, "start", lambda self: None)
    task = model_downloader.start_download(
        "https://huggingface.co/a/b/resolve/main/model.safetensors",
        str(tmp_path), "lora", name="示例 LoRA",
    )
    try:
        status = model_downloader.get_status(task)
        assert status["phase"] == "queued"
        assert status["speed_bps"] == 0
        assert status["kind"] == "model"
        assert status["saved_files"] == []
        assert status["started_at"] > 0
    finally:
        model_downloader._TASKS.pop(task, None)


def test_workflow_download_streams_and_records_saved_file(monkeypatch, tmp_path):
    payload = json.dumps({"1": {"class_type": "KSampler"}}, ensure_ascii=False).encode("utf-8")

    class StreamResponse:
        def __enter__(self):
            request = httpx.Request("GET", "https://civitai.com/api/download/models/1")
            self.response = httpx.Response(
                200,
                headers={
                    "content-length": str(len(payload)),
                    "content-disposition": 'attachment; filename="scene-workflow.json"',
                    "content-type": "application/json",
                },
                content=payload,
                request=request,
            )
            return self.response

        def __exit__(self, *_args):
            return False

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return StreamResponse()

    monkeypatch.setattr(workflow_downloader.httpx, "Client", Client)
    task = "workflow-stream-test"
    model_downloader._TASKS[task] = {}
    try:
        workflow_downloader._download(
            task,
            "https://civitai.com/api/download/models/1",
            str(tmp_path),
            "",
            "",
        )
        status = model_downloader.get_status(task)
        saved = tmp_path / "scene-workflow.json"
        assert status["status"] == "done"
        assert status["phase"] == "done"
        assert status["downloaded"] == len(payload)
        assert status["saved_files"] == [str(saved)]
        assert json.loads(saved.read_text(encoding="utf-8"))["1"]["class_type"] == "KSampler"
        assert not list(tmp_path.glob("*.part"))
    finally:
        model_downloader._TASKS.pop(task, None)
