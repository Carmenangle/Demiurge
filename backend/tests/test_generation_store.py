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


def test_workflow_batch_persists_audio_messages(monkeypatch):
    """音频产物：与图/视频同结构，消息以 audio 字段承载（不索引、可快照）。"""
    generation_store._MEMORY_DONE.clear()
    monkeypatch.setattr(generation_store.image_store, "save_local", lambda *a, **k: "C:/out/voice.wav")
    monkeypatch.setattr(generation_store, "_index_with_retry", lambda *a, **k: True)
    saved = []
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert", lambda thread, msg: saved.append(msg))
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a, **k: None)

    result = generation_store.finalize_workflow_batch(**_args(
        images=[],
        audios=[{"filename": "voice.wav", "subfolder": "", "type": "output"}],
    ))

    assert result["messages"][0]["audio"].startswith("http://127.0.0.1:8010/api/comfyui/local-view?path=")
    assert saved[0]["audio"].startswith("http://127.0.0.1:8010/api/comfyui/local-view?path=")
    assert saved[0]["text"] == "text"  # 首个产物承载提示词
    assert result["images"][0]["persisted"] is True
    assert result["images"][0]["snapshotted"] is True


def test_workflow_batch_audio_empty_raises(monkeypatch):
    """全空批次（含 audios 空）才报错，纯音频批次不再误报。"""
    import pytest
    with pytest.raises(ValueError):
        generation_store.finalize_workflow_batch(**_args(
            images=[], videos=[], audios=[], prompt="",
        ))


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


def test_自动插画只回填目标slot且不新增对话轮(monkeypatch):
    generation_store._MEMORY_DONE.clear()
    patched = []
    monkeypatch.setattr(generation_store.image_store, "save_local", lambda *a, **k: "C:/out/a.png")
    monkeypatch.setattr(generation_store, "_index_with_retry", lambda *a, **k: True)
    monkeypatch.setattr(
        generation_store.chat_snapshot, "resolve_media_slot",
        lambda *args, **kwargs: patched.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        generation_store.chat_snapshot, "upsert",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不得新增图片气泡")),
    )
    monkeypatch.setattr(
        generation_store.chat_memory, "append_message",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("自动插画不得回灌图像历史")),
    )

    result = generation_store.finalize_workflow_batch(**_args(
        target_message_id="bot-1", target_slot_id="slot-1",
    ))

    assert result["messages"] == []
    assert result["target"] == {
        "message_id": "bot-1", "slot_id": "slot-1",
        "media_type": "image", "url": result["images"][0]["display_url"],
    }
    assert patched[0][0][:3] == ("thread", "bot-1", "slot-1")


def test_自动插画失败写trace并删除快照slot(monkeypatch):
    removed = []
    traced = []
    monkeypatch.setattr(
        generation_store.chat_snapshot, "remove_media_slot",
        lambda *args: removed.append(args) or True,
    )
    monkeypatch.setattr(
        generation_store.run_trace, "emit",
        lambda ctx, event, **data: traced.append((ctx, event, data)),
    )

    removed_ok = generation_store.persist_illustration_failure(
        thread_id="thread-1", repo_id="repo-1", message_id="bot-1",
        slot_id="slot-1", stage="submit", error="ComfyUI 未启动",
        prompt_id="prompt-1",
    )

    assert removed_ok is True
    assert removed == [("thread-1", "bot-1", "slot-1")]
    assert traced == [({"thread_id": "thread-1", "repo_id": "repo-1"},
                       "illustration.failed", {
                           "message_id": "bot-1", "slot_id": "slot-1",
                           "stage": "submit", "error": "ComfyUI 未启动",
                           "prompt_id": "prompt-1", "slot_removed": True,
                       })]
