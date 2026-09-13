from app.services import model_downloader
from pathlib import Path


def test_model_artifact_risk_prefers_safetensors_and_flags_pickle_formats():
    safe = model_downloader.artifact_trust("hero.safetensors")
    risky = model_downloader.artifact_trust("legacy.ckpt")

    assert safe == {"format": "safetensors", "risk": "low", "preferred": True}
    assert risky["risk"] == "high"
    assert risky["preferred"] is False


def test_public_source_url_removes_tokens_but_keeps_version_selector():
    value = model_downloader.public_source_url(
        "https://civitai.com/api/download/models/9?modelVersionId=7&token=secret",
    )

    assert value == "https://civitai.com/api/download/models/9?modelVersionId=7"
    assert "secret" not in value


def test_download_part_path_is_unique_and_existing_destination_is_not_overwritten(
    monkeypatch, tmp_path,
):
    payload = b"new"

    class Response:
        status_code = 200
        headers = {
            "content-length": str(len(payload)),
            "content-disposition": 'attachment; filename="hero.safetensors"',
        }
        url = "https://huggingface.co/a/b/resolve/main/hero.safetensors"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_bytes(self, _size):
            yield payload

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(model_downloader.httpx, "Client", Client)
    monkeypatch.setattr(model_downloader.task_progress_store, "save", lambda *_a, **_k: None)
    target = Path(tmp_path) / "loras"
    target.mkdir()
    destination = target / "hero.safetensors"
    destination.write_bytes(b"existing")
    model_downloader._TASKS["task-a"] = {}
    try:
        model_downloader._download(
            "task-a",
            "https://huggingface.co/a/b/resolve/main/hero.safetensors",
            str(tmp_path), "lora", "", "",
        )
        status = model_downloader.get_status("task-a")
        assert status["status"] == "error"
        assert "拒绝覆盖" in status["error"]
        assert destination.read_bytes() == b"existing"
        assert not list(target.glob("*.part"))
    finally:
        model_downloader._TASKS.pop("task-a", None)
