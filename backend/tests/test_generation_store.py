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


def test_workflow_batch_pure_audio_skips_tag_extraction(monkeypatch):
    """纯音频批次不调 _extract_tags：标签只服务图片/文字入库，避免 LLM 慢调用阻塞 finalize。"""
    generation_store._MEMORY_DONE.clear()
    monkeypatch.setattr(generation_store.image_store, "save_local", lambda *a, **k: "C:/out/voice.wav")
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert", lambda *a, **k: None)
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(generation_store, "_extract_tags", lambda *a, **k: calls.append(1) or "tag")
    monkeypatch.setattr(generation_store, "_index_with_retry", lambda *a, **k: True)

    generation_store.finalize_workflow_batch(**_args(
        images=[],
        audios=[{"filename": "voice.wav", "subfolder": "", "type": "output"}],
    ))

    assert calls == []


def test_workflow_batch_images_still_extract_tags(monkeypatch):
    """图片批次保留标签提取（资产库检索需要 tags）。"""
    generation_store._MEMORY_DONE.clear()
    monkeypatch.setattr(generation_store.image_store, "save_local", lambda *a, **k: "C:/out/a.png")
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert", lambda *a, **k: None)
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(generation_store, "_extract_tags", lambda *a, **k: calls.append(1) or "tag")
    monkeypatch.setattr(generation_store, "_index_with_retry", lambda *a, **k: True)

    generation_store.finalize_workflow_batch(**_args())

    assert calls == [1]


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

def test_audio_slot_meta_读取槽位角色与序号(monkeypatch, tmp_path):
    """配音分条命名元数据：从快照槽位读 speaker/seq。"""
    monkeypatch.setattr(generation_store.chat_snapshot, "SNAP_DIR", tmp_path)
    generation_store.chat_snapshot.save("thread", [{
        "id": "bot1", "role": "assistant", "text": "t", "parts": [
            {"type": "media-slot", "slotId": "audio-bot1-0", "kind": "audio",
             "speaker": "虞妙玥", "seq": 2, "total": 3},
        ],
    }])
    meta = generation_store._audio_slot_meta("thread", "bot1", "audio-bot1-0")
    assert meta["speaker"] == "虞妙玥"
    assert meta["seq"] == 2
    # 槽位不存在 → 空元数据
    assert generation_store._audio_slot_meta("thread", "bot1", "nope") == {"speaker": "", "seq": 0}


def test_audio_turn_按含配音消息计数轮次(monkeypatch, tmp_path):
    """第几次对话 = 快照中该消息之前（含）含配音槽的 assistant 消息数。"""
    monkeypatch.setattr(generation_store.chat_snapshot, "SNAP_DIR", tmp_path)
    generation_store.chat_snapshot.save("thread", [
        {"id": "m1", "role": "assistant", "text": "a",
         "parts": [{"type": "audio", "slotId": "x", "url": "u"}]},
        {"id": "m2", "role": "assistant", "text": "b"},          # 无配音 → 不计数
        {"id": "m3", "role": "assistant", "text": "c",
         "parts": [{"type": "media-slot", "slotId": "y", "kind": "audio"}]},
    ])
    assert generation_store._audio_turn("thread", "m3") == 2
    assert generation_store._audio_turn("thread", "m2") == 1
    # 消息不存在：遍历到底，返回总含配音消息数（调用方总传真实 message_id）
    assert generation_store._audio_turn("thread", "unknown") == 2


def test_save_audio_local_存voices并自定义命名(monkeypatch, tmp_path):
    """配音分条落 <repo>/voices/，文件名用 dest_stem（保留中文角色名），幂等。"""
    from pathlib import Path
    monkeypatch.setattr(generation_store.image_store.comfyui_client, "fetch_view",
                        lambda *a, **k: (b"audio-bytes", "audio/flac"))
    out = tmp_path / "out"
    p1 = generation_store.image_store.save_audio_local(
        str(out), "repo",
        filename="workflow_abc.flac", subfolder="", type="output",
        url="http://comfy", dest_stem="虞妙玥_3_2",
    )
    assert Path(p1).name == "虞妙玥_3_2.flac"
    assert str(Path(p1).parent).endswith("voices")
    # 幂等：同名已存在直接返回旧路径，不重复取字节
    p2 = generation_store.image_store.save_audio_local(
        str(out), "repo",
        filename="workflow_abc.flac", subfolder="", type="output",
        url="http://comfy", dest_stem="虞妙玥_3_2",
    )
    assert p2 == p1


def test_audio_finalize_按角色轮次句号命名落voices(monkeypatch, tmp_path):
    """inline 配音槽：finalize 时走 save_audio_local，dest_stem=角色_轮次_句号。"""
    monkeypatch.setattr(generation_store.chat_snapshot, "SNAP_DIR", tmp_path)
    generation_store.chat_snapshot.save("thread", [
        {"id": "m1", "role": "assistant", "text": "a",
         "parts": [{"type": "audio", "slotId": "x", "url": "u"}]},
        {"id": "bot", "role": "assistant", "text": "b",
         "parts": [{"type": "media-slot", "slotId": "audio-bot-0", "kind": "audio",
                    "speaker": "冷倾雪", "seq": 2, "total": 3}]},
    ])
    calls = []
    monkeypatch.setattr(generation_store.image_store, "save_audio_local",
                        lambda *a, **k: calls.append(k) or "C:/out/voices/冷倾雪_2_2.flac")
    monkeypatch.setattr(generation_store.chat_snapshot, "resolve_media_slot", lambda *a, **k: True)
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a, **k: None)
    generation_store._MEMORY_DONE.clear()

    generation_store.finalize_workflow_batch(**_args(
        images=[], audios=[{"filename": "workflow_x.flac", "subfolder": "", "type": "output"}],
        target_message_id="bot", target_slot_id="audio-bot-0",
    ))

    assert calls, "应走 save_audio_local"
    # 第 2 轮（m1 含配音 + bot 自身）里的第 2 句
    assert calls[0]["dest_stem"] == "冷倾雪_2_2"
    assert calls[0]["subfolder"] == ""

def test_image_turn_按含图片消息计数轮次(monkeypatch, tmp_path):
    """第几次带插画的对话 = 快照中该消息之前（含）含图片/插画槽的 assistant 消息数。"""
    monkeypatch.setattr(generation_store.chat_snapshot, "SNAP_DIR", tmp_path)
    generation_store.chat_snapshot.save("thread", [
        {"id": "m1", "role": "assistant", "text": "a",
         "parts": [{"type": "image", "slotId": "x", "url": "u"}]},
        {"id": "m2", "role": "assistant", "text": "b"},          # 无图 → 不计数
        {"id": "m3", "role": "assistant", "text": "c",
         "parts": [{"type": "media-slot", "slotId": "y", "kind": "image"}]},
    ])
    assert generation_store._image_turn("thread", "m3") == 2
    assert generation_store._image_turn("thread", "m2") == 1
    assert generation_store._image_turn("thread", "unknown") == 2


def test_save_image_named_角色lora命名并覆盖(monkeypatch, tmp_path):
    """角色 LoRA 生图：<repo>/ 根目录 + 角色_轮次_序号.png + 同名覆盖写。"""
    from pathlib import Path
    calls = {"n": 0}
    def fake_fetch(*a, **k):
        calls["n"] += 1
        return (f"bytes-{calls['n']}".encode(), "image/png")
    monkeypatch.setattr(generation_store.image_store.comfyui_client, "fetch_view", fake_fetch)
    out = tmp_path / "out"
    p1 = generation_store.image_store.save_image_named(
        str(out), "repo", filename="workflow_a.png", url="http://comfy",
        dest_stem="虞妙玥_2_1",
    )
    assert Path(p1).name == "虞妙玥_2_1.png"
    assert str(Path(p1).parent).endswith("repo")  # 根目录，非 voices
    # 同名覆盖：内容更新，路径不变
    p2 = generation_store.image_store.save_image_named(
        str(out), "repo", filename="workflow_b.png", url="http://comfy",
        dest_stem="虞妙玥_2_1",
    )
    assert p2 == p1
    assert Path(p1).read_bytes() == b"bytes-2"


def test_image_finalize_角色lora命名_非角色lora时间戳(monkeypatch, tmp_path):
    """regeneration.characterLoraActor 非空 → save_image_named；空 → save_local 时间戳。"""
    monkeypatch.setattr(generation_store.chat_snapshot, "SNAP_DIR", tmp_path)
    generation_store.chat_snapshot.save("thread", [
        {"id": "m1", "role": "assistant", "text": "a",
         "parts": [{"type": "image", "slotId": "x", "url": "u"}]},
        {"id": "bot", "role": "assistant", "text": "b",
         "parts": [{"type": "media-slot", "slotId": "img-bot-0", "kind": "image"}]},
    ])
    named = []
    monkeypatch.setattr(generation_store.image_store, "save_image_named",
                        lambda *a, **k: named.append(k) or "C:/out/虞妙玥_2_1.png")
    monkeypatch.setattr(generation_store, "_index_with_retry", lambda *a, **k: True)
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a, **k: None)
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert", lambda *a, **k: None)

    generation_store.finalize_workflow_batch(**_args(
        images=[{"filename": "a.png", "subfolder": "", "type": "output"}],
        regeneration={"kind": "template", "templateId": "t", "values": {},
                      "comfyuiUrl": "http://comfy", "outputNodeIds": [], "prompt": "",
                      "characterLoraActor": "虞妙玥"},
        target_message_id="bot", target_slot_id="img-bot-0",
    ))
    assert named, "角色 LoRA 生图应走 save_image_named"
    assert named[0]["dest_stem"] == "虞妙玥_2_1"  # 第 2 次带插画对话第 1 张

    # 非角色 lora（兜底）：回退 save_local
    called = []
    monkeypatch.setattr(generation_store.image_store, "save_local",
                        lambda *a, **k: called.append(True) or "C:/out/x.png")
    named.clear()
    generation_store.finalize_workflow_batch(**_args(
        images=[{"filename": "a.png", "subfolder": "", "type": "output"}],
        regeneration={"kind": "template", "templateId": "t", "values": {},
                      "comfyuiUrl": "http://comfy", "outputNodeIds": [], "prompt": ""},
        target_message_id="bot", target_slot_id="img-bot-0",
    ))
    assert not named and called, "非角色 LoRA 应走 save_local 时间戳命名"
