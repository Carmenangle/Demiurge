import re

from app.services import generation_store


def _args(**overrides):
    values = dict(
        thread_id="thread", repo_id="repo", prompt_id="prompt", prompt="text",
        images=[{"filename": "a.png", "subfolder": "", "type": "output"}],
        output_dir="out", comfyui_url="http://comfy", embed_base="", embed_key="",
        embed_model="embed",
    )
    values.update(overrides)
    return values


def test_workflow_batch_uses_stable_identity(monkeypatch):
    generation_store._MEMORY_DONE.clear()
    monkeypatch.setattr(generation_store.image_store, "save_local", lambda *a, **k: "C:/out/a.png")
    monkeypatch.setattr(generation_store, "_index_with_retry", lambda *a, **k: True)
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a, **k: None)
    saved = []
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert", lambda thread, msg: saved.append(msg))

    first = generation_store.finalize_workflow_batch(**_args())
    second = generation_store.finalize_workflow_batch(**_args())

    assert first["messages"] == second["messages"]
    assert first["images"][0]["message_id"] == second["images"][0]["message_id"]
    assert saved[0]["id"] == saved[1]["id"]


def test_workflow_batch_home_has_no_durable_side_effects(monkeypatch):
    monkeypatch.setattr(generation_store.image_store, "save_local", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(generation_store, "_index_with_retry", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))

    result = generation_store.finalize_workflow_batch(**_args(repo_id="home"))

    assert result["durable"] is False
    assert result["messages"][0]["image"].startswith("http://127.0.0.1:8010/api/comfyui/view?")


def test_workflow_batch_keeps_online_image_when_save_fails(monkeypatch):
    generation_store._MEMORY_DONE.clear()
    monkeypatch.setattr(generation_store.image_store, "save_local", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(generation_store, "_index_with_retry", lambda *a, **k: True)
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert", lambda *a, **k: None)
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a, **k: None)

    result = generation_store.finalize_workflow_batch(**_args())

    assert result["images"][0]["errors"] == ["persist"]
    assert result["messages"][0]["image"].startswith("http://127.0.0.1:8010/api/comfyui/view?")


def test_agent_remote_image_uses_standard_time_name(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(generation_store.repo_meta, "repo_folder", lambda *args: repo)
    monkeypatch.setattr(generation_store, "_download_capped", lambda *args, **kwargs: b"png")

    generation_store._save_remote_image(
        "https://example.com/660510cd-04d9-4a95-90e6-609cd21cd133.png",
        str(tmp_path),
        "repo",
    )

    names = [path.name for path in repo.iterdir()]
    assert len(names) == 1
    assert re.fullmatch(r"\d{8}_\d{6}_\d{6}_[0-9a-f]{8}\.png", names[0])
    assert (repo / names[0]).read_bytes() == b"png"


def test_supervisor_route_choice_is_persisted_on_its_message(monkeypatch):
    saved = []
    monkeypatch.setattr(
        generation_store.chat_snapshot,
        "merge_fields",
        lambda thread_id, message_id, **fields: saved.append((thread_id, message_id, fields)),
    )
    choice = {
        "id": "route-1",
        "messageId": "message-1",
        "userMessageId": "user-1",
        "status": "pending",
        "options": [{"route": "answer", "label": "继续对话"}],
    }

    generation_store.persist_route_choice("thread-1", choice)

    assert saved == [("thread-1", "message-1", {"routeChoice": choice})]


def test_agent_image_persists_exact_regeneration_snapshot(monkeypatch):
    saved = []
    snapshot = {
        "kind": "ai-image",
        "prompt": "原始提示词",
        "images": ["data:image/png;base64,AAA", "http://local/reference.png"],
        "size": "1536x1024",
        "quality": "medium",
        "model": {"baseUrl": "https://images.example", "modelName": "image-v2"},
    }
    monkeypatch.setattr(generation_store, "_save_remote_image", lambda *args: "saved.png")
    monkeypatch.setattr(generation_store, "_index_with_retry", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        generation_store.chat_snapshot,
        "upsert",
        lambda thread_id, message: saved.append((thread_id, message)),
    )

    result = generation_store.persist_image(
        "thread-1", "repo-1", "原始提示词", "remote.png", "out",
        "", "", "embed", snapshot,
    )

    assert result["regeneration"] == snapshot
    assert saved[0][1]["regeneration"] == snapshot


def test_workflow_batch_attaches_exact_snapshot_to_every_image(monkeypatch):
    generation_store._MEMORY_DONE.clear()
    snapshot = {
        "kind": "workflow",
        "graph": {"1": {"class_type": "KSampler", "inputs": {"seed": 42}}},
        "comfyuiUrl": "http://127.0.0.1:8188",
        "outputNodeIds": ["9"],
        "prompt": "",
    }
    monkeypatch.setattr(generation_store.image_store, "save_local", lambda *args, **kwargs: "C:/out/a.png")
    monkeypatch.setattr(generation_store, "_index_with_retry", lambda *args, **kwargs: True)
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *args, **kwargs: None)

    result = generation_store.finalize_workflow_batch(**_args(
        images=[
            {"filename": "a.png", "subfolder": "", "type": "output"},
            {"filename": "b.png", "subfolder": "", "type": "output"},
        ],
        regeneration=snapshot,
    ))

    assert [message["regeneration"] for message in result["messages"]] == [snapshot, snapshot]
