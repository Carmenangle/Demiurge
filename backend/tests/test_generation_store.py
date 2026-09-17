import base64
import re
import types

import pytest

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


def test_persist_text_interrupted_appends_marker(monkeypatch):
    """2026-09-04 收口：中断半成品落快照必须拼「（已打断）」后缀——它是跨轮续写
    （agent_graph._resume_interrupted_messages）的唯一识别信号（to_prompt_history 只带
    role/content，interrupted 字段到不了模型上下文）。不带则刷新后下轮永远匹配不到。"""
    from app.services import chat_snapshot
    saved: list[dict] = []
    monkeypatch.setattr(
        generation_store.chat_snapshot, "upsert", lambda thread, msg: saved.append(msg),
    )
    generation_store.persist_text("t1", "m1", "她转身，衣袂翻飞。", interrupted=True)
    assert saved and saved[-1]["text"] == "她转身，衣袂翻飞。" + chat_snapshot.INTERRUPT_MARKER
    assert saved[-1].get("interrupted") is True

    # 幂等：文本已带后缀不再重复拼（worker 收尾/前端补标竞态安全）
    generation_store.persist_text("t1", "m1", "她转身。" + chat_snapshot.INTERRUPT_MARKER, interrupted=True)
    assert saved[-1]["text"] == "她转身。" + chat_snapshot.INTERRUPT_MARKER

    # 非中断路径不改文本（仍走 append_text 增量）
    append_calls: list[tuple] = []
    monkeypatch.setattr(
        generation_store.chat_snapshot, "append_text", lambda *a: append_calls.append(a),
    )
    generation_store.persist_text("t1", "m2", "正常正文", interrupted=False)
    assert append_calls and append_calls[-1][2] == "正常正文"


def test_persist_inspiration_快照携带slim图片(monkeypatch):
    """灵感卡快照必须存 images（URL 元信息，≤8 条）——2026-09-11 合同变更。

    事故：队列 headless 完成后前端从快照重载消息，当轮 SSE 里的 images 只活在
    事件里 → 重载后卡片图全灭（真机实锤：SSE 带 8 图、重载后 0 图）。
    """
    saved: list[dict] = []
    monkeypatch.setattr(
        generation_store.chat_snapshot, "upsert", lambda thread, msg: saved.append(msg),
    )
    images = [
        {"thumb_url": f"https://cdn.test/t{i}.jpg", "full_url": f"https://cdn.test/f{i}.jpg",
         "source_url": "https://page.test", "title": f"图{i}"}
        for i in range(10)  # 故意超 8，验证封顶
    ]
    card = generation_store.persist_inspiration("t1", "标题", "正文", [{"title": "s", "url": "https://s.test"}], images)
    assert len(card["images"]) == 10                      # 返回给 wire 的 card 保留全量
    insp = saved[-1]["inspiration"]
    assert len(insp["images"]) == 8                       # 快照封顶 8 条
    assert insp["images"][0]["full_url"] == "https://cdn.test/f0.jpg"
    assert set(insp["images"][0].keys()) == {"thumb_url", "full_url", "source_url", "title"}
    assert insp["selected"] == []


def test_persist_inspiration_无图与脏图不炸(monkeypatch):
    saved: list[dict] = []
    monkeypatch.setattr(
        generation_store.chat_snapshot, "upsert", lambda thread, msg: saved.append(msg),
    )
    generation_store.persist_inspiration("t1", "标题", "正文", [], None)
    assert saved[-1]["inspiration"]["images"] == []
    dirty = [{"full_url": ""}, "not-a-dict", {"full_url": "https://cdn.test/ok.jpg"}]
    generation_store.persist_inspiration("t1", "标题", "正文", [], dirty)
    insp = saved[-1]["inspiration"]
    assert len(insp["images"]) == 1 and insp["images"][0]["full_url"] == "https://cdn.test/ok.jpg"


def test_设计讨论注入提示词规范(monkeypatch):
    """_fabric_knowledge_history(task_type="design_discussion") 追加规范全文。

    2026-09-11 用户四条质量反馈固化：锚点第一句/渐变恒定/三段式混排/长度下限
    ——讨论产出的草案必须逐条遵守，规范随设计讨论分支注入自由循环历史。
    """
    from app.services import agent_graph, chat_snapshot

    monkeypatch.setattr(chat_snapshot, "load", lambda tid: [])
    ctx = {"thread_id": "Anima/原创", "message": "讨论场景1的生图提示词"}
    out = agent_graph._fabric_knowledge_history(ctx, "讨论场景1的生图提示词",
                                                task_type="design_discussion")
    assert "设计讨论提示词规范" in out
    assert "渐变" in out and "参考图" in out            # 规范正文关键约束在场
    # 普通自由循环（不含灵感/讨论）不注入规范
    out_plain = agent_graph._fabric_knowledge_history(ctx, "整理一下资料")
    assert "设计讨论提示词规范" not in out_plain


def test_讨论接续轮附件跨轮注入(monkeypatch):
    """_recent_attachment_lines：从快照 parts 捞历史附件并解析真实路径。

    2026-09-11 实锤：文档在上一轮上传、接续轮 ctx 无 attachments →
    AI 回头找用户要「你文档中的服装设计内容」。
    """
    from app.services import agent_graph, attachment_store, chat_snapshot

    items = [
        {"id": "m1", "role": "user", "text": "讨论服装", "parts": [
            {"type": "text", "text": "讨论服装"},
            {"type": "file", "fileId": "f1", "name": "服装搭配参考分析.md"},
        ]},
        {"id": "m2", "role": "assistant", "text": "好", "parts": [
            {"type": "file", "fileId": "fX", "name": "不该被捞到的助手附件"},
        ]},
    ]
    monkeypatch.setattr(chat_snapshot, "load", lambda tid: items)
    monkeypatch.setattr(attachment_store, "resolve", lambda fid: f"D:/store/{fid}.md")
    ctx = {"thread_id": "Anima/原创"}
    out = agent_graph._recent_attachment_lines(ctx)
    assert "服装搭配参考分析.md" in out and "D:/store/f1.md" in out
    assert "不该被捞到的助手附件" not in out            # 只捞用户消息
    assert "禁止向用户索要" in out
    # 空线程不炸
    monkeypatch.setattr(chat_snapshot, "load", lambda tid: [])
    assert agent_graph._recent_attachment_lines(ctx) == ""


def test_任务会话接续判定(monkeypatch):
    """_task_session_continuation：先查任务登记（显式状态），词表只兜底。

    上下文合同 P1（2026-09-11）：登记里有活跃 design_discussion + 本轮带
    设计话题词 + 非委派 → 接续；登记缺失/TTL 过期 → False（fail-open，
    由 supervisor 落到词表粘性兜底）。
    """
    from app.services import agent_graph, task_session_store

    cur = "不用ComfyUI的提示词,我需要的是适配gpt,banana这种ai模型的文字描述内容"
    session = {"task_type": "design_discussion",
               "created_intent": "跟我讨论各服装的生图提示词",
               "thread_id": "t1"}
    monkeypatch.setattr(task_session_store, "active",
                        lambda output_dir="", thread_id="": session)
    assert agent_graph._task_session_continuation({"thread_id": "t1"}, cur)
    # 明确委派优先，不被任务会话劫持
    assert not agent_graph._task_session_continuation(
        {"thread_id": "t1"}, "按这套提示词批量出 10 张图")
    # 纯剧情叙述（无设计话题词）不接续
    assert not agent_graph._task_session_continuation(
        {"thread_id": "t1"}, "她提笔画了一幅像，转身离去")
    # 登记缺失 → False（fail-open）
    monkeypatch.setattr(task_session_store, "active",
                        lambda output_dir="", thread_id="": None)
    assert not agent_graph._task_session_continuation({"thread_id": "t1"}, cur)


def test_自由循环历史注入任务产物指针(monkeypatch):
    """上下文合同⑤：活跃任务的 product_paths 注入「已产出文件」块——接续轮改文件不重生成。"""
    from app.services import agent_graph, chat_snapshot, task_session_store

    monkeypatch.setattr(chat_snapshot, "load", lambda tid: [])
    session = {"task_type": "design_discussion",
               "product_paths": ["D:/out/场景1服装.md", ""],
               "thread_id": "t1"}
    monkeypatch.setattr(task_session_store, "active",
                        lambda output_dir="", thread_id="": session)
    ctx = {"thread_id": "t1", "message": "把场景1的配色改一下"}
    out = agent_graph._fabric_knowledge_history(ctx, "把场景1的配色改一下")
    assert "已产出文件" in out
    assert "D:/out/场景1服装.md" in out
    # 无活跃会话不注入
    monkeypatch.setattr(task_session_store, "active",
                        lambda output_dir="", thread_id="": None)
    out_plain = agent_graph._fabric_knowledge_history(ctx, "把场景1的配色改一下")
    assert "已产出文件" not in out_plain


def test_最近灵感卡注入讨论上下文(monkeypatch):
    """_recent_inspiration_blocks：取最近 ≤2 张卡的标题+正文节选+已勾选图 URL。

    2026-09-11 实锤：to_prompt_history 跳过灵感卡 → 讨论型请求的 AI 看不到
    角色特征，反问用户要发色瞳色（观感「图丢了/特征没理解」）。
    """
    from app.services import agent_graph, chat_snapshot

    cards = [
        {"title": "更旧卡", "content": "最早的内容", "selected": []},
        {"title": "旧卡", "content": "上一轮的资料", "selected": ["https://a.test/old.jpg"]},
        {"title": "月见八千代外貌", "content": "银色至粉色渐变双马尾，环形辫内层挑染，上粉下青渐变瞳",
         "selected": ["https://cdn.test/p1.jpg", "https://cdn.test/p2.jpg"]},
    ]
    items = [{"id": f"m{i}", "role": "assistant", "text": "", "inspiration": c}
             for i, c in enumerate(cards)]
    monkeypatch.setattr(chat_snapshot, "load", lambda tid: items)

    ctx = {"thread_id": "Anima/原创"}
    block = agent_graph._recent_inspiration_blocks(ctx)
    assert "月见八千代外貌" in block and "环形辫内层挑染" in block
    assert "更旧卡" not in block                                   # 只取最近 2 张，最旧淘汰
    assert "https://cdn.test/p1.jpg" in block                      # 勾选图 URL 随卡注入
    # 空线程不炸
    monkeypatch.setattr(chat_snapshot, "load", lambda tid: [])
    assert agent_graph._recent_inspiration_blocks(ctx) == ""


# ---------------------------------------------------------------------------
# T4.3 低覆盖补测（2026-09-17）：视频批次 / 纯文本批次 / 各 persist_* 失败留痕 /
# 下载上限 / 小属主。缺口来自 P5 覆盖率复测（scorecard 附录 A：55%）——
# 本文件是持久化属主、也是 P4 审计里纯吞最集中处，故按「真实失败路径」补：
# 视频分支此前**零覆盖**，纯文本分支与各 persist_* 的留痕分支此前均未执行。
# ---------------------------------------------------------------------------


def _capture_index(monkeypatch):
    """把 _index_with_retry 换成可观测桩；返回 state，可用 state["ok"] 翻转成败。"""
    state = {"ok": True, "calls": []}

    def _fake(repo_id, cfg, prompt, tags="", image_url="", **kw):
        state["calls"].append({"repo_id": repo_id, "prompt": prompt, "tags": tags,
                               "image_url": image_url, **kw})
        return state["ok"]

    monkeypatch.setattr(generation_store, "_index_with_retry", _fake)
    return state


def test_video_batch_持久化并承载提示词(monkeypatch):
    """视频批次：落盘 + video 字段承载 + 首个产物（无图）带提示词 + media_type=video 入库。"""
    generation_store._MEMORY_DONE.clear()
    monkeypatch.setattr(generation_store.image_store, "save_local", lambda *a, **k: "C:/out/v.mp4")
    idx = _capture_index(monkeypatch)
    saved: list[dict] = []
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert", lambda t, m: saved.append(m))
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a: None)
    monkeypatch.setattr(generation_store, "_extract_tags", lambda *a, **k: "t")

    result = generation_store.finalize_workflow_batch(**_args(
        images=[], videos=[{"filename": "v.mp4", "subfolder": "", "type": "output"}],
    ))

    msg = result["messages"][0]
    assert msg["video"].startswith("http://127.0.0.1:8010/api/comfyui/local-view?path=")
    assert msg["text"] == "text"
    row = result["images"][0]
    assert (row["persisted"], row["indexed"], row["snapshotted"], row["errors"]) == (True, True, True, [])
    assert idx["calls"][0]["media_type"] == "video"
    assert idx["calls"][0]["derived_from"] is None
    assert saved and saved[0]["video"] == msg["video"]
    assert result["complete"] is True


def test_video_batch_底图槽弱引用随入库记录(monkeypatch):
    """base_slot_ref → derived_from 记「视频首帧底图」弱引用（来源删除不级联）。"""
    generation_store._MEMORY_DONE.clear()
    monkeypatch.setattr(generation_store.image_store, "save_local", lambda *a, **k: "C:/out/v.mp4")
    idx = _capture_index(monkeypatch)
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert", lambda *a, **k: None)
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a: None)

    generation_store.finalize_workflow_batch(**_args(
        images=[], videos=[{"filename": "v.mp4", "subfolder": "", "type": "output"}],
        base_slot_ref={"message_id": "bot-1", "slot_id": "img-bot-0"},
    ))

    assert idx["calls"][0]["derived_from"] == [{
        "media_slot_ref": {"message_id": "bot-1", "slot_id": "img-bot-0"},
        "kind": "video_base_image",
    }]


def test_video_batch_回填目标槽不新增对话轮(monkeypatch):
    """inline 视频槽：resolve_media_slot(media_type=video)，不新增气泡、target 带 url。"""
    generation_store._MEMORY_DONE.clear()
    monkeypatch.setattr(generation_store.image_store, "save_local", lambda *a, **k: "C:/out/v.mp4")
    _capture_index(monkeypatch)
    patched: list[tuple] = []
    monkeypatch.setattr(generation_store.chat_snapshot, "resolve_media_slot",
                        lambda *a, **k: patched.append((a, k)) or True)
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不得新增视频气泡")))

    result = generation_store.finalize_workflow_batch(**_args(
        images=[], videos=[{"filename": "v.mp4", "subfolder": "", "type": "output"}],
        target_message_id="bot-1", target_slot_id="v-bot-0",
    ))

    assert result["messages"] == []
    assert result["target"]["media_type"] == "video"
    assert result["target"]["slot_id"] == "v-bot-0"
    assert result["target"]["url"] == result["images"][0]["display_url"]
    assert patched[0][0][:3] == ("thread", "bot-1", "v-bot-0")
    assert patched[0][1]["media_type"] == "video"


def test_video_batch_三阶段全失败仍回落在线地址(monkeypatch):
    """persist/index/snapshot 各自失败互不阻断：errors 齐、complete=False、仍给可播在线地址。"""
    generation_store._MEMORY_DONE.clear()
    monkeypatch.setattr(generation_store.image_store, "save_local",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("磁盘满")))
    idx = _capture_index(monkeypatch)
    idx["ok"] = False
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("锁冲突")))
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a: None)

    result = generation_store.finalize_workflow_batch(**_args(
        images=[], videos=[{"filename": "v.mp4", "subfolder": "sub", "type": "output"}],
    ))

    row = result["images"][0]
    assert row["errors"] == ["persist", "index", "snapshot"]
    assert row["persisted"] is False and row["indexed"] is False and row["snapshotted"] is False
    assert row["display_url"].startswith("http://127.0.0.1:8010/api/comfyui/view?")
    assert result["messages"][0]["video"] == row["display_url"]
    assert result["complete"] is False


def test_video_batch_home无持久化副作用(monkeypatch):
    generation_store._MEMORY_DONE.clear()
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError())  # noqa: E731
    monkeypatch.setattr(generation_store.image_store, "save_local", boom)
    monkeypatch.setattr(generation_store, "_index_with_retry", boom)
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert", boom)
    monkeypatch.setattr(generation_store.chat_memory, "append_message", boom)

    result = generation_store.finalize_workflow_batch(**_args(
        repo_id="home", images=[],
        videos=[{"filename": "v.mp4", "subfolder": "", "type": "output"}],
    ))

    assert result["durable"] is False and result["complete"] is False
    assert result["images"][0]["errors"] == []
    assert result["messages"][0]["video"].startswith("http://127.0.0.1:8010/api/comfyui/view?")


def test_纯文本批次落快照入记忆(monkeypatch):
    """无任何产物但带提示词：仍落一条纯文字助手消息（不产出 media 行、display_url 为空）。"""
    generation_store._MEMORY_DONE.clear()
    monkeypatch.setattr(generation_store, "_extract_tags", lambda *a, **k: "标签")
    idx = _capture_index(monkeypatch)
    saved: list[dict] = []
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert", lambda t, m: saved.append(m))
    mem: list[tuple] = []
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a: mem.append(a))

    result = generation_store.finalize_workflow_batch(**_args(images=[]))

    assert [m["text"] for m in result["messages"]] == ["text"]
    row = result["images"][0]
    assert row["display_url"] == "" and row["indexed"] is True and row["snapshotted"] is True
    assert saved and saved[0]["text"] == "text"
    assert idx["calls"][0]["tags"] == "标签"
    assert mem == [("thread", "assistant", "text", None)]
    assert result["complete"] is True


def test_纯文本批次索引与快照失败记错误(monkeypatch):
    generation_store._MEMORY_DONE.clear()
    monkeypatch.setattr(generation_store, "_extract_tags", lambda *a, **k: "")
    idx = _capture_index(monkeypatch)
    idx["ok"] = False
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("锁冲突")))
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a: None)

    result = generation_store.finalize_workflow_batch(**_args(images=[]))

    assert result["images"][0]["errors"] == ["index", "snapshot"]
    assert result["complete"] is False


def test_记忆写入失败留痕且不占去重键(monkeypatch):
    """chat_memory 写失败必须留痕、且不得进 _MEMORY_DONE —— 否则同一工作流永久不再入记忆。"""
    generation_store._MEMORY_DONE.clear()
    monkeypatch.setattr(generation_store, "_extract_tags", lambda *a, **k: "")
    _capture_index(monkeypatch)
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert", lambda *a, **k: None)
    calls: list[tuple] = []

    def _boom(*a):
        calls.append(a)
        raise RuntimeError("记忆库锁死")

    monkeypatch.setattr(generation_store.chat_memory, "append_message", _boom)

    result = generation_store.finalize_workflow_batch(**_args(images=[]))
    generation_store.finalize_workflow_batch(**_args(images=[]))

    assert result["complete"] is True          # 记忆失败不阻断收尾
    assert len(calls) == 2                     # 未被记「已完成」→ 下次照常重试


def test_图片批次索引失败记错误(monkeypatch):
    generation_store._MEMORY_DONE.clear()
    monkeypatch.setattr(generation_store.image_store, "save_local", lambda *a, **k: "C:/out/a.png")
    idx = _capture_index(monkeypatch)
    idx["ok"] = False
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert", lambda *a, **k: None)
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a: None)
    monkeypatch.setattr(generation_store, "_extract_tags", lambda *a, **k: "")

    result = generation_store.finalize_workflow_batch(**_args())

    assert result["images"][0]["errors"] == ["index"]
    assert result["complete"] is False


def test_图片批次槽回填与快照失败记错误(monkeypatch):
    """inline：resolve_media_slot 返回 False 记 snapshot；非 inline：upsert 抛错同样记。"""
    generation_store._MEMORY_DONE.clear()
    monkeypatch.setattr(generation_store.image_store, "save_local", lambda *a, **k: "C:/out/a.png")
    _capture_index(monkeypatch)
    monkeypatch.setattr(generation_store, "_extract_tags", lambda *a, **k: "")
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a: None)
    monkeypatch.setattr(generation_store.chat_snapshot, "resolve_media_slot", lambda *a, **k: False)

    inline = generation_store.finalize_workflow_batch(**_args(
        target_message_id="bot-1", target_slot_id="img-1"))
    assert inline["images"][0]["errors"] == ["snapshot"]

    monkeypatch.setattr(generation_store.chat_snapshot, "upsert",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("锁冲突")))
    plain = generation_store.finalize_workflow_batch(**_args())
    assert plain["images"][0]["errors"] == ["snapshot"]


def test_音频批次落盘失败回落在线地址(monkeypatch):
    generation_store._MEMORY_DONE.clear()
    monkeypatch.setattr(generation_store.image_store, "save_local",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("voices 目录不可写")))
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a: None)

    result = generation_store.finalize_workflow_batch(**_args(
        images=[], audios=[{"filename": "a.flac", "subfolder": "", "type": "output"}],
    ))

    row = result["images"][0]
    assert row["errors"] == ["persist"]
    assert row["persisted"] is False
    assert row["display_url"].startswith("http://127.0.0.1:8010/api/comfyui/view?")


def test_音频批次槽回填失败记错误不抛(monkeypatch):
    """① 槽不存在（resolve False）② 回填抛错 ③ 非 inline upsert 抛错 —— 都只记错误。"""
    generation_store._MEMORY_DONE.clear()
    monkeypatch.setattr(generation_store.image_store, "save_audio_local",
                        lambda *a, **k: "C:/out/voices/v.flac")
    monkeypatch.setattr(generation_store.image_store, "save_local", lambda *a, **k: "C:/out/v.flac")
    monkeypatch.setattr(generation_store, "_audio_slot_meta",
                        lambda *a, **k: {"speaker": "冷倾雪", "seq": 1})
    monkeypatch.setattr(generation_store, "_audio_turn", lambda *a, **k: 2)
    monkeypatch.setattr(generation_store.chat_memory, "append_message", lambda *a: None)
    audios = [{"filename": "a.flac", "subfolder": "", "type": "output"}]

    monkeypatch.setattr(generation_store.chat_snapshot, "resolve_media_slot", lambda *a, **k: False)
    inline = generation_store.finalize_workflow_batch(
        **_args(images=[], audios=audios, target_message_id="bot", target_slot_id="audio-bot-0"))
    assert inline["images"][0]["errors"] == ["snapshot"]

    monkeypatch.setattr(generation_store.chat_snapshot, "resolve_media_slot",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("快照锁")))
    inline2 = generation_store.finalize_workflow_batch(
        **_args(images=[], audios=audios, target_message_id="bot", target_slot_id="audio-bot-0"))
    assert inline2["images"][0]["errors"] == ["snapshot"]

    monkeypatch.setattr(generation_store.chat_snapshot, "upsert",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("锁冲突")))
    plain = generation_store.finalize_workflow_batch(**_args(images=[], audios=audios))
    assert plain["images"][0]["errors"] == ["snapshot"]


def test_finalize_缺关键标识抛错():
    with pytest.raises(ValueError, match="不能为空"):
        generation_store.finalize_workflow_batch(**_args(thread_id=""))
    with pytest.raises(ValueError, match="不能为空"):
        generation_store.finalize_workflow_batch(**_args(prompt_id=""))


class _FakeStream:
    def __init__(self, chunks, status_error=None):
        self._chunks = list(chunks)
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def iter_bytes(self):
        yield from self._chunks


def _patch_http_client(monkeypatch, stream, seen):
    """把 httpx.Client 换成喂固定分块的桩，记录构造参数与请求。"""
    class _Client:
        def __init__(self, **kwargs):
            seen.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def stream(self, method, url):
            seen.append((method, url))

            class _Ctx:
                def __enter__(self_inner):
                    return stream

                def __exit__(self_inner, *exc):
                    return False

            return _Ctx()

    monkeypatch.setattr(generation_store.httpx, "Client", _Client)


def test_下载上限_拼接分块(monkeypatch):
    seen: list = []
    _patch_http_client(monkeypatch, _FakeStream([b"ab", b"cd", b"ef"]), seen)
    assert generation_store._download_capped("http://x/f", timeout=5, max_bytes=10) == b"abcdef"
    assert seen[0] == {"trust_env": False, "timeout": 5}
    assert seen[1] == ("GET", "http://x/f")


def test_下载上限_超限中止并抛(monkeypatch):
    seen: list = []
    _patch_http_client(monkeypatch, _FakeStream([b"a" * 6, b"b" * 6]), seen)
    with pytest.raises(ValueError, match="上限"):
        generation_store._download_capped("http://x/big", timeout=5, max_bytes=10)


def test_下载上限_http错误直接抛(monkeypatch):
    seen: list = []
    _patch_http_client(monkeypatch, _FakeStream([], status_error=RuntimeError("500")), seen)
    with pytest.raises(RuntimeError):
        generation_store._download_capped("http://x/err", timeout=5, max_bytes=10)


def test_远程图片_空参直接回原址():
    assert generation_store._save_remote_image("", "out") == ""
    assert generation_store._save_remote_image("http://x/a.png", "") == "http://x/a.png"


def test_远程图片_data_url按扩展名落盘(tmp_path, monkeypatch):
    raw = b"\x89PNG-bytes"
    url = "data:image/webp;base64," + base64.b64encode(raw).decode()
    monkeypatch.setattr(generation_store.repo_meta, "repo_folder", lambda *a, **k: tmp_path)
    monkeypatch.setattr(generation_store.image_store, "_next_seq_name",
                        lambda base, ext: f"seq.{ext}")

    out = generation_store._save_remote_image(url, "out", "repo")

    assert (tmp_path / "seq.webp").read_bytes() == raw
    assert out == generation_store.view_urls.local_view(str(tmp_path / "seq.webp"))


def test_远程图片_下载失败回退原址(monkeypatch):
    monkeypatch.setattr(generation_store, "_download_capped",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("网络断")))
    url = "http://x/a.png"
    assert generation_store._save_remote_image(url, "out", "repo") == url


def test_远程视频_data_url落盘(tmp_path, monkeypatch):
    raw = b"mp4-bytes"
    url = "data:video/mp4;base64," + base64.b64encode(raw).decode()
    monkeypatch.setattr(generation_store.repo_meta, "repo_folder", lambda *a, **k: tmp_path)

    out = generation_store._save_remote_video(url, "out", "repo")

    files = list(tmp_path.iterdir())
    assert len(files) == 1 and files[0].suffix == ".mp4"
    assert files[0].read_bytes() == raw
    assert out == generation_store.view_urls.local_view(str(files[0]))


def test_远程视频_直链按原名落盘(tmp_path, monkeypatch):
    monkeypatch.setattr(generation_store.repo_meta, "repo_folder", lambda *a, **k: tmp_path)
    monkeypatch.setattr(generation_store, "_download_capped", lambda *a, **k: b"bytes")

    out = generation_store._save_remote_video("http://x/movie.mp4?sig=1", "out", "repo")

    assert (tmp_path / "movie.mp4").read_bytes() == b"bytes"
    assert out == generation_store.view_urls.local_view(str(tmp_path / "movie.mp4"))


def test_远程视频_空参与下载失败回退原址(monkeypatch):
    assert generation_store._save_remote_video("", "out") == ""
    assert generation_store._save_remote_video("http://x/v.mp4", "") == "http://x/v.mp4"
    monkeypatch.setattr(generation_store, "_download_capped",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("网络断")))
    url = "http://x/v.mp4"
    assert generation_store._save_remote_video(url, "out", "repo") == url


def test_persist_video_落快照(monkeypatch, tmp_path):
    """真快照端到端：Agent 云视频留存后，会话里多出一条带 video 字段的消息。"""
    monkeypatch.setattr(generation_store.repo_meta, "repo_folder", lambda *a, **k: tmp_path)
    monkeypatch.setattr(generation_store, "_download_capped", lambda *a, **k: b"v")

    out = generation_store.persist_video("t-video", "repo", "提示", "http://x/v.mp4", "out")

    msgs = generation_store.chat_snapshot.load("t-video")
    assert [m["video"] for m in msgs] == [out["url"]]
    assert out["id"]


def test_persist_video_写快照失败留痕不阻断(monkeypatch):
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("锁冲突")))
    out = generation_store.persist_video("t1", "repo", "提示", "http://x/v.mp4", "")
    assert out["url"] == "http://x/v.mp4" and out["id"]


def test_persist_image_写快照失败留痕不阻断(monkeypatch):
    monkeypatch.setattr(generation_store, "_index_with_retry", lambda *a, **k: True)
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("锁冲突")))
    out = generation_store.persist_image("t1", "repo", "p", "http://x/a.png", "", "", "", "m")
    assert out["url"] == "http://x/a.png"


def test_索引重试_三次失败后返回False(monkeypatch):
    attempts: list = []
    sleeps: list = []

    def _boom(*a, **k):
        attempts.append(a)
        raise RuntimeError("rag 不可用")

    monkeypatch.setattr(generation_store.rag_store, "index_generation", _boom)
    monkeypatch.setattr(generation_store, "time", types.SimpleNamespace(sleep=sleeps.append))
    cfg = generation_store.EmbedConfig("http://e", "k", "m")

    assert generation_store._index_with_retry("repo", cfg, "p") is False
    assert len(attempts) == 3
    assert [round(s, 2) for s in sleeps] == [0.8, 1.6]     # 退避递增，最后一次不睡


def test_索引重试_第二次成功(monkeypatch):
    attempts: list = []
    sleeps: list = []

    def _flaky(*a, **k):
        attempts.append(a)
        if len(attempts) == 1:
            raise RuntimeError("首次抖动")

    monkeypatch.setattr(generation_store.rag_store, "index_generation", _flaky)
    monkeypatch.setattr(generation_store, "time", types.SimpleNamespace(sleep=sleeps.append))
    cfg = generation_store.EmbedConfig("http://e", "k", "m")

    assert generation_store._index_with_retry("repo", cfg, "p") is True
    assert len(attempts) == 2 and len(sleeps) == 1


def test_标签提取_切分与回退(monkeypatch):
    from app.services import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "光效, 少女， 场景\n光影")
    assert generation_store._extract_tags("画个少女", "http://e", "k", "m") == "光效,少女,场景,光影"

    called: list = []
    monkeypatch.setattr(llm, "chat", lambda *a, **k: called.append(1) or "x")
    assert generation_store._extract_tags("", "http://e", "k", "m") == ""   # 空提示词
    assert generation_store._extract_tags("p", "", "k", "m") == ""          # 缺配置
    assert called == []                                                     # 都不碰 LLM

    monkeypatch.setattr(llm, "chat", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("超时")))
    assert generation_store._extract_tags("p", "http://e", "k", "m") == ""  # 失败兜底空串


def test_有界去重集_超限FIFO淘汰与LRU刷新():
    s = generation_store._BoundedKeySet(cap=2)
    s.add("a")
    s.add("b")
    assert "a" in s and "b" in s
    s.add("c")                                   # 淘汰最旧 a
    assert "a" not in s and {"b", "c"} <= set(s._d)
    s.add("b")                                   # 已有键刷新到最新位次
    s.add("d")                                   # 此时最旧是 c
    assert "c" not in s and {"b", "d"} <= set(s._d)
    s.clear()
    assert "b" not in s and "d" not in s


def test_persist_text_空文本早退(monkeypatch):
    calls: list = []
    monkeypatch.setattr(generation_store.chat_snapshot, "append_text", lambda *a: calls.append(a))
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert", lambda *a: calls.append(a))
    generation_store.persist_text("t1", "m1", "   ")
    generation_store.persist_text("t1", "", "正文")
    assert calls == []


def test_persist_text_落盘失败留痕不抛(monkeypatch):
    boom = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("锁冲突"))  # noqa: E731
    monkeypatch.setattr(generation_store.chat_snapshot, "upsert", boom)
    monkeypatch.setattr(generation_store.chat_snapshot, "append_text", boom)
    generation_store.persist_text("t1", "m1", "正文", interrupted=True)   # 中断路径
    generation_store.persist_text("t1", "m1", "正文")                     # 增量路径


def test_persist_user_message_失败留痕不抛(monkeypatch):
    monkeypatch.setattr(generation_store.chat_snapshot, "ensure_user_message",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("锁冲突")))
    generation_store.persist_user_message("t1", "m1", "正文")


def test_persist_media_slot_落盘与失败留痕(monkeypatch):
    got: list = []
    monkeypatch.setattr(generation_store.chat_snapshot, "ensure_media_slot",
                        lambda *a, **k: got.append((a, k)))
    generation_store.persist_media_slot("t1", "m1", "slot", offset=3)
    assert got == [(("t1", "m1", "slot"), {"offset": 3})]
    monkeypatch.setattr(generation_store.chat_snapshot, "ensure_media_slot",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("锁冲突")))
    generation_store.persist_media_slot("t1", "m1", "slot")


def test_persist_audio_slot_落盘与失败留痕(monkeypatch):
    got: list = []
    monkeypatch.setattr(generation_store.chat_snapshot, "append_media_slot",
                        lambda *a, **k: got.append((a, k)))
    generation_store.persist_audio_slot("t1", "m1", "audio-1", speaker="冷倾雪", seq=2, total=3)
    assert got == [(("t1", "m1", "audio-1"),
                    {"kind": "audio", "speaker": "冷倾雪", "seq": 2, "total": 3})]
    monkeypatch.setattr(generation_store.chat_snapshot, "append_media_slot",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("锁冲突")))
    generation_store.persist_audio_slot("t1", "m1", "audio-1")


def test_persist_illustration_submission_失败返回False(monkeypatch):
    got: list = []
    monkeypatch.setattr(generation_store.chat_snapshot, "bind_media_slot_prompt",
                        lambda *a: got.append(a) or True)
    assert generation_store.persist_illustration_submission(
        thread_id="t1", message_id="m1", slot_id="s1", prompt_id="p1") is True
    assert got == [("t1", "m1", "s1", "p1")]
    monkeypatch.setattr(generation_store.chat_snapshot, "bind_media_slot_prompt",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("锁冲突")))
    assert generation_store.persist_illustration_submission(
        thread_id="t1", message_id="m1", slot_id="s1", prompt_id="p1") is False


def test_claim_illustration_submission_记录trace与失败降级(monkeypatch):
    traced: list = []
    monkeypatch.setattr(generation_store.run_trace, "emit",
                        lambda ctx, event, **data: traced.append((event, data)))
    monkeypatch.setattr(generation_store.chat_snapshot, "claim_media_slot_submission",
                        lambda *a: True)
    assert generation_store.claim_illustration_submission(
        thread_id="t1", message_id="m1", slot_id="s1") is True
    assert traced == [("illustration.claim",
                       {"message_id": "m1", "slot_id": "s1", "claimed": True})]

    traced.clear()
    monkeypatch.setattr(generation_store.chat_snapshot, "claim_media_slot_submission",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("快照缺失")))
    assert generation_store.claim_illustration_submission(
        thread_id="t1", message_id="m1", slot_id="s1") is False
    assert traced[0][1]["claimed"] is False and traced[0][1]["error"] == "快照缺失"


def test_persist_illustration_failure_槽清理失败仍记trace(monkeypatch):
    traced: list = []
    monkeypatch.setattr(generation_store.run_trace, "emit",
                        lambda ctx, event, **data: traced.append((event, data)))
    monkeypatch.setattr(generation_store.chat_snapshot, "remove_media_slot",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("锁冲突")))
    ok = generation_store.persist_illustration_failure(
        thread_id="t1", repo_id="r1", message_id="m1", slot_id="s1",
        stage="submit", error="未启动", prompt_id="p1")
    assert ok is False
    assert traced[0][0] == "illustration.failed" and traced[0][1]["slot_removed"] is False


def test_审批与路由选择_空id早退与失败留痕(monkeypatch):
    calls: list = []
    monkeypatch.setattr(generation_store.chat_snapshot, "merge_fields",
                        lambda *a, **k: calls.append((a, k)))
    generation_store.persist_prompt_approval("t1", {})      # 无 messageId → 早退
    generation_store.persist_route_choice("t1", {})
    assert calls == []

    approval = {"messageId": "m1", "status": "approved"}
    generation_store.persist_prompt_approval("t1", approval)
    assert calls == [(("t1", "m1"), {"promptApproval": approval})]
    calls.clear()
    choice = {"messageId": "m2", "route": "answer"}
    generation_store.persist_route_choice("t1", choice)
    assert calls == [(("t1", "m2"), {"routeChoice": choice})]

    calls.clear()
    monkeypatch.setattr(generation_store.chat_snapshot, "merge_fields",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("锁冲突")))
    generation_store.persist_prompt_approval("t1", approval)   # 失败留痕不抛
    generation_store.persist_route_choice("t1", choice)


def test_槽位元数据与轮次_快照读失败回退(monkeypatch):
    """快照不可读时降级：元数据空、轮次 0 —— finalize 不因快照损坏而中断。"""
    monkeypatch.setattr(generation_store.chat_snapshot, "load",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("快照损坏")))
    assert generation_store._audio_slot_meta("t", "m", "s") == {"speaker": "", "seq": 0}
    assert generation_store._audio_turn("t", "m") == 0
    assert generation_store._image_turn("t", "m") == 0


def test_轮次计数跳过非字典项与无parts项(monkeypatch):
    monkeypatch.setattr(generation_store.chat_snapshot, "load",
                        lambda *a, **k: ["not-a-dict", {"id": "m1"},
                                         {"id": "m2", "parts": [{"kind": "audio"}]}])
    assert generation_store._audio_turn("t", "m2") == 1
    assert generation_store._image_turn("t", "m2") == 0
