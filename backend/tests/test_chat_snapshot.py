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
