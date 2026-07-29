import base64
from pathlib import Path

from app.services import image_store
from app.services.message_images import extract_image_url


def test_message_image_formats():
    assert extract_image_url({"type": "image_url", "image_url": {"url": " https://x/a.png "}}) == "https://x/a.png"
    assert extract_image_url({"type": "image", "source": {"type": "url", "url": "https://x/b.png"}}) == "https://x/b.png"
    encoded = base64.b64encode(b"\x89PNG").decode()
    assert extract_image_url({"type": "image", "source_type": "base64", "data": encoded, "mime_type": "image/png"}) == f"data:image/png;base64,{encoded}"
    assert extract_image_url({"type": "text", "text": "x"}) is None


def test_idempotent_image_store_reuses_path(tmp_path, monkeypatch):
    base = tmp_path / "repo"
    base.mkdir()
    monkeypatch.setattr("app.services.repo_meta.repo_folder", lambda output, repo: base)
    calls = []
    monkeypatch.setattr(image_store.comfyui_client, "fetch_view", lambda *a, **k: (calls.append(1) or b"png", "image/png"))

    first = image_store.save_local(str(tmp_path), "repo", filename="a.png", idempotency_key="same")
    second = image_store.save_local(str(tmp_path), "repo", filename="a.png", idempotency_key="same")

    assert first == second
    assert Path(first).read_bytes() == b"png"
    assert len(calls) == 1
