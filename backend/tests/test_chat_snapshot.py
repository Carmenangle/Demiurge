import threading
import time

from app.services import chat_snapshot


def test_assistant_message_preserves_shape_and_key_order():
    message = chat_snapshot.assistant_message(
        "mid", "text", image="image.png", interrupted=True,
    )

    assert message == {
        "id": "mid", "role": "assistant", "text": "text",
        "image": "image.png", "interrupted": True,
    }
    assert list(message) == ["id", "role", "text", "image", "interrupted"]


def test_private_alias_points_to_public_message():
    # generation_store 已改用公共 assistant_message；别名仍在，保内部旧调用不破
    assert chat_snapshot._assistant_message is chat_snapshot.assistant_message


def test_用户消息在助手生成前落盘且重复确保不覆盖前端富内容(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")

    assert chat_snapshot.ensure_user_message(
        "thread", "user-1", "继续剧情", ["reference.png"],
    )
    assert chat_snapshot.load("thread") == [{
        "id": "user-1", "role": "user", "text": "继续剧情",
        "parts": [
            {"type": "text", "text": "继续剧情"},
            {"type": "image", "url": "reference.png"},
        ],
    }]

    rich = [{
        "id": "user-1", "role": "user", "text": "继续剧情",
        "parts": [{"type": "masked-image", "url": "preview.png"}],
    }]
    chat_snapshot.save("thread", rich)
    assert chat_snapshot.ensure_user_message("thread", "user-1", "继续剧情")
    assert chat_snapshot.load("thread") == rich


def test_可从Trace把遗失用户消息恢复到对应助手消息之前(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [
        {"id": "before", "role": "assistant", "text": "前一轮"},
        {"id": "answer", "role": "assistant", "text": "本轮回答"},
    ])

    assert chat_snapshot.ensure_user_message(
        "thread", "recovered-user", "从Trace恢复的输入", before_id="answer",
    )
    assert [item["id"] for item in chat_snapshot.load("thread")] == [
        "before", "recovered-user", "answer",
    ]


def test_prompt_history只转换快照中仍存在的对话文本():
    snapshot = [
        {"id": "u1", "role": "user", "text": "保留的用户消息"},
        {"id": "card", "role": "assistant", "text": "", "workflow": {"templateName": "x"}},
        {"id": "a1", "role": "assistant", "text": "", "parts": [
            {"type": "text", "text": "保留的助手消息"},
            {"type": "image", "url": "x.png"},
            {"type": "media-slot", "slotId": "slot-1", "status": "ready"},
        ]},
        {"id": "a2", "role": "assistant", "text": "", "image": "top-level.png"},
        {"id": "u2", "role": "user", "text": "仅保留文字", "images": ["upload.png"]},
    ]

    assert chat_snapshot.to_prompt_history(snapshot) == [
        {"role": "user", "content": "保留的用户消息"},
        {"role": "assistant", "content": "保留的助手消息"},
        {"role": "user", "content": "仅保留文字"},
    ]


def test_prompt_history与前端标签过滤对齐():
    snapshot = [
        # 状态/Toast 不进上下文
        {"id": "toast", "role": "assistant", "text": "已提交到 ComfyUI 生成（prompt_id: x，20 个节点）…", "system": True},
        # 顶层媒体气泡（工作流产物带提示词）不进上下文
        {"id": "gen", "role": "assistant", "text": "1girl, portrait", "image": "/local-view?path=a.png"},
        # 非剧情路由（生图/视频专家）不进上下文
        {"id": "vid", "role": "assistant", "text": "girl dancing", "route": "video"},
        {"id": "gen2", "role": "assistant", "text": "提示词", "route": "generate"},
        # 剧情专家产出进上下文
        {"id": "story", "role": "assistant", "text": "剧情正文", "route": "roleplay"},
        {"id": "ans", "role": "assistant", "text": "对话正文", "route": "answer"},
        {"id": "u1", "role": "user", "text": "用户消息"},
    ]

    assert chat_snapshot.to_prompt_history(snapshot) == [
        {"role": "assistant", "content": "剧情正文"},
        {"role": "assistant", "content": "对话正文"},
        {"role": "user", "content": "用户消息"},
    ]


def test_backfill_story_tags回填剧情与状态标签():
    snapshot = [
        {"id": "s1", "role": "assistant", "text": "夜风穿过巷口，她停下脚步。"},
        {"id": "toast", "role": "assistant", "text": "已提交到 ComfyUI 生成（prompt_id: abc，20 个节点），正在运转工作流…"},
        {"id": "gen", "role": "assistant", "text": "1girl, portrait", "image": "/local-view?path=a.png"},
        {"id": "prompt", "role": "assistant", "text": "QRQ, masterpiece, very aesthetic, best quality, score_9, nsfw, 1girl, solo, ultra detailed, absurdres, 8k, high resolution"},
        {"id": "tagged", "role": "assistant", "text": "已有标签", "route": "generate"},
        {"id": "u1", "role": "user", "text": "继续"},
        {"id": "empty", "role": "assistant", "text": ""},
        {"id": "card", "role": "assistant", "text": "模板卡", "workflow": {"templateName": "A"}},
    ]

    out, stats = chat_snapshot.backfill_story_tags(snapshot)
    by_id = {item["id"]: item for item in out}
    assert by_id["s1"]["route"] == "roleplay"
    assert by_id["toast"]["system"] is True and "route" not in by_id["toast"]
    assert by_id["prompt"]["route"] == "generate"
    assert "route" not in by_id["gen"] and "system" not in by_id["gen"]
    assert by_id["tagged"]["route"] == "generate"
    assert "route" not in by_id["u1"]
    assert "route" not in by_id["empty"]
    assert "route" not in by_id["card"]
    assert stats == {"changed": 3, "story": 1, "status": 1, "generate": 1}


def test_backfill_story_tags幂等已标签消息不动():
    snapshot = [{"id": "x", "role": "assistant", "text": "正文", "route": "roleplay"}]
    out, stats = chat_snapshot.backfill_story_tags(snapshot)
    assert out == snapshot
    assert stats["changed"] == 0


def test_较旧的异步快照不得覆盖删除后的新快照(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")

    assert chat_snapshot.save_if_newer("thread", [{"id": "kept"}], revision=20)
    assert not chat_snapshot.save_if_newer(
        "thread", [{"id": "kept"}, {"id": "deleted"}], revision=19,
    )
    assert chat_snapshot.load("thread") == [{"id": "kept"}]


def test_生成图片按slot原位替换而不追加消息(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "前文后文",
        "parts": [
            {"type": "text", "text": "前文"},
            {"type": "media-slot", "slotId": "slot-1", "status": "pending"},
            {"type": "text", "text": "后文"},
        ],
    }])

    assert chat_snapshot.resolve_media_slot(
        "thread", "bot", "slot-1", "local://image", media_type="image",
    )
    items = chat_snapshot.load("thread")
    assert len(items) == 1
    assert items[0]["parts"] == [
        {"type": "text", "text": "前文"},
        {"type": "image", "url": "local://image", "slotId": "slot-1", "status": "ready"},
        {"type": "text", "text": "后文"},
    ]


def test_后台正文先落盘并创建稳定媒体槽(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")

    chat_snapshot.append_text("thread", "bot", "前文后文")
    assert chat_snapshot.ensure_media_slot("thread", "bot", "slot-1", offset=2)
    chat_snapshot.append_text("thread", "bot", "前文后文")

    assert chat_snapshot.load("thread") == [{
        "id": "bot", "role": "assistant", "text": "前文后文",
        "parts": [
            {"type": "text", "text": "前文"},
            {"type": "media-slot", "slotId": "slot-1", "status": "pending"},
            {"type": "text", "text": "后文"},
        ],
    }]


def test_Comfy提交后把prompt_id写回持久化媒体槽(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "正文",
        "parts": [{"type": "media-slot", "slotId": "slot-1", "status": "pending"}],
    }])

    assert chat_snapshot.bind_media_slot_prompt("thread", "bot", "slot-1", "prompt-1")
    assert chat_snapshot.load("thread")[0]["parts"] == [{
        "type": "media-slot", "slotId": "slot-1", "status": "pending",
        "promptId": "prompt-1",
    }]


def test_自动插画提交前只能原子认领一次pending槽(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "正文",
        "parts": [{"type": "media-slot", "slotId": "slot-1", "status": "pending"}],
    }])

    assert chat_snapshot.claim_media_slot_submission("thread", "bot", "slot-1")
    assert not chat_snapshot.claim_media_slot_submission("thread", "bot", "slot-1")
    assert chat_snapshot.bind_media_slot_prompt("thread", "bot", "slot-1", "prompt-1")
    assert not chat_snapshot.claim_media_slot_submission("thread", "bot", "slot-1")


def test_前端旧快照不能清掉服务端插画认领或完成结果(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot._REVISIONS.clear()
    pending = [{
        "id": "bot", "role": "assistant", "text": "正文",
        "parts": [{"type": "media-slot", "slotId": "slot-1", "status": "pending"}],
    }]
    chat_snapshot.save("thread", pending)
    assert chat_snapshot.claim_media_slot_submission("thread", "bot", "slot-1")

    assert chat_snapshot.save_if_newer("thread", pending, revision=1)
    assert chat_snapshot.load("thread")[0]["parts"][0]["submissionClaim"] is True
    assert not chat_snapshot.claim_media_slot_submission("thread", "bot", "slot-1")

    assert chat_snapshot.bind_media_slot_prompt("thread", "bot", "slot-1", "prompt-1")
    assert chat_snapshot.resolve_media_slot(
        "thread", "bot", "slot-1", "ready.png", regeneration={"kind": "template"},
    )
    assert chat_snapshot.save_if_newer("thread", pending, revision=2)
    assert chat_snapshot.load("thread")[0]["parts"][0]["url"] == "ready.png"

    deleted = [{"id": "bot", "role": "assistant", "text": "正文", "parts": []}]
    assert chat_snapshot.save_if_newer("thread", deleted, revision=3)
    assert chat_snapshot.load("thread")[0]["parts"] == []


def test_重新生图按相同slot替换已有图片而不追加消息(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "前文后文",
        "parts": [
            {"type": "text", "text": "前文"},
            {"type": "image", "url": "local://old", "slotId": "slot-1", "status": "ready"},
            {"type": "text", "text": "后文"},
        ],
    }])

    regeneration = {"kind": "template", "templateId": "tpl", "values": {"39.text": "p"}}
    assert chat_snapshot.resolve_media_slot(
        "thread", "bot", "slot-1", "local://new", media_type="image",
        regeneration=regeneration,
    )
    items = chat_snapshot.load("thread")
    assert len(items) == 1
    assert items[0]["parts"][1] == {
        "type": "image", "url": "local://new", "slotId": "slot-1", "status": "ready",
        "regeneration": regeneration,
    }


def test_自动插画失败删除slot并保留正文(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "前文后文",
        "parts": [
            {"type": "text", "text": "前文"},
            {"type": "media-slot", "slotId": "slot-1", "status": "pending"},
            {"type": "text", "text": "后文"},
        ],
    }])

    assert chat_snapshot.remove_media_slot("thread", "bot", "slot-1")
    assert chat_snapshot.load("thread") == [{
        "id": "bot", "role": "assistant", "text": "前文后文",
        "parts": [{"type": "text", "text": "前文后文"}],
    }]
    assert not chat_snapshot.remove_media_slot("thread", "bot", "slot-1")


def test_append_helpers_preserve_existing_behavior(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")

    chat_snapshot.append_image("thread", "image", "image.png", None)
    chat_snapshot.append_text("thread", "blank", "   ")
    chat_snapshot.append_text("thread", "text", "hello")

    assert chat_snapshot.load("thread") == [
        {"id": "image", "role": "assistant", "text": "", "image": "image.png"},
        {"id": "text", "role": "assistant", "text": "hello"},
    ]


def test_save_and_upsert_share_thread_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    original_save = chat_snapshot._save_unlocked
    save_started = threading.Event()
    release_save = threading.Event()

    def slow_save(thread_id, messages):
        if messages == [{"id": "frontend"}]:
            save_started.set()
            assert release_save.wait(timeout=2)
        original_save(thread_id, messages)

    monkeypatch.setattr(chat_snapshot, "_save_unlocked", slow_save)
    full_write = threading.Thread(
        target=chat_snapshot.save,
        args=("thread", [{"id": "frontend"}]),
    )
    incremental_write = threading.Thread(
        target=chat_snapshot.upsert,
        args=("thread", {"id": "backend"}),
    )

    full_write.start()
    assert save_started.wait(timeout=2)
    incremental_write.start()
    time.sleep(0.05)
    assert incremental_write.is_alive()

    release_save.set()
    full_write.join(timeout=2)
    incremental_write.join(timeout=2)

    assert chat_snapshot.load("thread") == [
        {"id": "frontend"},
        {"id": "backend"},
    ]


def test_未配仓库文件夹回退旧位置(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    assert chat_snapshot._path("r1") == tmp_path / "r1.json"


def test_配了仓库文件夹落作品子文件夹(monkeypatch, tmp_path):
    from app.services import repo_meta
    out = tmp_path / "repos"
    out.mkdir()
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path / "legacy")
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: str(out))
    monkeypatch.setattr(repo_meta, "folder_name", lambda rid: "爱丽丝的故事")
    chat_snapshot.save("r1", [{"id": "a"}])
    assert (out / "爱丽丝的故事" / "chat.json").is_file()
    assert chat_snapshot.load("r1") == [{"id": "a"}]


def test_存量会话读时惰性迁移到仓库文件夹(monkeypatch, tmp_path):
    from app.services import repo_meta
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    out = tmp_path / "repos"
    out.mkdir()
    # 旧位置先有存量会话
    (legacy / "r1.json").write_text('[{"id":"old"}]', encoding="utf-8")
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", legacy)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: str(out))
    monkeypatch.setattr(repo_meta, "folder_name", lambda rid: "作品A")
    # 首次解析即搬迁：新位置出现、旧位置消失
    resolved = chat_snapshot._path("r1")
    assert resolved == out / "作品A" / "chat.json"
    assert resolved.is_file()
    assert not (legacy / "r1.json").exists()
    assert chat_snapshot.load("r1") == [{"id": "old"}]


def test_视频槽原位回填与目标删除后不追加(monkeypatch, tmp_path):
    """V1.3：视频 media_type 原位替换 media-slot，且目标消息删除后 resolve 不追加新消息。"""
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread-v", [{
        "id": "bot", "role": "assistant", "text": "正文",
        "parts": [{"type": "media-slot", "slotId": "slot-1", "status": "pending"}],
    }])

    # 视频原位回填：type 变 video + status ready
    assert chat_snapshot.resolve_media_slot(
        "thread-v", "bot", "slot-1", "local://movie.mp4", media_type="video",
    )
    part = chat_snapshot.load("thread-v")[0]["parts"][0]
    assert part["type"] == "video" and part["status"] == "ready"
    assert part["url"] == "local://movie.mp4"

    # 目标消息已删除（无 parts 槽）→ resolve 返回 False，不追加新消息
    chat_snapshot.save("thread-v", [{"id": "bot", "role": "assistant", "text": "正文", "parts": []}])
    assert chat_snapshot.resolve_media_slot(
        "thread-v", "bot", "slot-1", "local://orphan.mp4", media_type="video",
    ) is False
    assert len(chat_snapshot.load("thread-v")) == 1


def test_resolve_media_slot_音频槽保留角色名分条元数据(monkeypatch, tmp_path):
    """A1.6：音频槽回填后保留 kind/speaker/seq/total，刷新恢复仍能按角色分条展示。"""
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread-a", [{
        "id": "bot", "role": "assistant", "text": "正文",
        "parts": [{
            "type": "media-slot", "slotId": "audio-1", "status": "pending",
            "kind": "audio", "speaker": "阿尼玛", "seq": 2, "total": 3,
        }],
    }])

    assert chat_snapshot.resolve_media_slot(
        "thread-a", "bot", "audio-1", "local://a.wav", media_type="audio",
    )
    part = chat_snapshot.load("thread-a")[0]["parts"][0]
    assert part["type"] == "audio" and part["status"] == "ready"
    assert part["url"] == "local://a.wav"
    assert part["speaker"] == "阿尼玛"
    assert part["seq"] == 2 and part["total"] == 3 and part["kind"] == "audio"

    # 非音频槽（图片）不注入音频元数据
    chat_snapshot.save("thread-a", [{
        "id": "bot", "role": "assistant", "text": "正文",
        "parts": [{"type": "media-slot", "slotId": "slot-1", "status": "pending"}],
    }])
    assert chat_snapshot.resolve_media_slot(
        "thread-a", "bot", "slot-1", "local://img.png", media_type="image",
    )
    part = chat_snapshot.load("thread-a")[0]["parts"][0]
    assert part["type"] == "image" and part["status"] == "ready"
    assert "speaker" not in part and "kind" not in part


def test_merge_fields_对未知message_id静默返回不追加幽灵消息(monkeypatch, tmp_path):
    """N1 修复：未知 message_id 的 merge_fields 不应追加幽灵消息。"""
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [
        {"id": "existing", "role": "assistant", "text": "已知消息"},
    ])

    # merge_fields 对未知 id 静默返回，不追加
    chat_snapshot.merge_fields("thread", "unknown-id", inspiration={"selected": ["url1"]})
    assert len(chat_snapshot.load("thread")) == 1
    assert chat_snapshot.load("thread")[0]["id"] == "existing"

    # merge_fields 对已知 id 正常更新
    chat_snapshot.merge_fields("thread", "existing", inspiration={"selected": ["url2"]})
    assert len(chat_snapshot.load("thread")) == 1
    assert chat_snapshot.load("thread")[0]["inspiration"] == {"selected": ["url2"]}


def test_select_inspiration_正常选中并更新(monkeypatch, tmp_path):
    """N3 测试：正常选中灵感卡图片并持久化到快照。"""
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [
        {"id": "msg-1", "role": "assistant", "text": "搜索结果",
         "inspiration": {"query": "猫", "results": [{"url": "http://img.com/cat.png"}]}},
    ])

    result = chat_snapshot.select_inspiration("thread", "msg-1",
                                               ["http://img.com/cat.png", "http://img.com/dog.png"])
    assert result == {"ok": True, "selected": ["http://img.com/cat.png", "http://img.com/dog.png"]}
    loaded = chat_snapshot.load("thread")
    assert loaded[0]["inspiration"]["selected"] == ["http://img.com/cat.png", "http://img.com/dog.png"]


def test_select_inspiration_未知message_id不追加幽灵消息(monkeypatch, tmp_path):
    """N3 测试：未知 message_id 应静默返回，不追加幽灵消息。"""
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [
        {"id": "existing", "role": "assistant", "text": "已知消息"},
    ])

    result = chat_snapshot.select_inspiration("thread", "unknown-id",
                                               ["http://img.com/test.png"])
    assert result == {"ok": True, "selected": ["http://img.com/test.png"]}
    # 不应追加幽灵消息
    assert len(chat_snapshot.load("thread")) == 1
    assert chat_snapshot.load("thread")[0]["id"] == "existing"


def test_select_inspiration_过滤非http协议(monkeypatch, tmp_path):
    """N3 测试：仅接受 http(s) URL，过滤 file:// 等非法协议。"""
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [
        {"id": "msg-1", "role": "assistant", "text": "搜索结果",
         "inspiration": {"query": "test", "results": []}},
    ])

    result = chat_snapshot.select_inspiration("thread", "msg-1",
                                               ["http://a.com/1.png", "file:///etc/passwd", "   "])
    assert result == {"ok": True, "selected": ["http://a.com/1.png"]}
    loaded = chat_snapshot.load("thread")
    assert loaded[0]["inspiration"]["selected"] == ["http://a.com/1.png"]


def test_音频槽追加保留已有图片槽并写入分条元数据(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    # 同轮先出图：消息已有图片槽
    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "正文",
        "parts": [
            {"type": "image", "url": "local://img", "slotId": "img-1", "status": "ready"},
        ],
    }])

    assert chat_snapshot.append_media_slot(
        "thread", "bot", "audio-0", speaker="虞妙玥", seq=1, total=2,
    )
    parts = chat_snapshot.load("thread")[0]["parts"]
    # 图片槽保留，音频槽追加在末尾且带分条元数据
    assert parts == [
        {"type": "image", "url": "local://img", "slotId": "img-1", "status": "ready"},
        {"type": "media-slot", "slotId": "audio-0", "status": "pending",
         "kind": "audio", "speaker": "虞妙玥", "seq": 1, "total": 2},
    ]


def test_音频槽纯文本消息补正文且幂等(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "她低声道：「我认输。」",
    }])

    assert chat_snapshot.append_media_slot("thread", "bot", "audio-0")
    # 幂等：重复追加不产生第二个槽
    assert chat_snapshot.append_media_slot("thread", "bot", "audio-0")
    parts = chat_snapshot.load("thread")[0]["parts"]
    assert parts == [
        {"type": "text", "text": "她低声道：「我认输。」"},
        {"type": "media-slot", "slotId": "audio-0", "status": "pending", "kind": "audio"},
    ]

