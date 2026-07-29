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


def test_append_helpers_preserve_existing_behavior(monkeypatch):
    saved = []
    monkeypatch.setattr(chat_snapshot, "upsert", lambda thread, msg: saved.append((thread, msg)))

    chat_snapshot.append_image("thread", "image", "image.png", None)
    chat_snapshot.append_text("thread", "blank", "   ")
    chat_snapshot.append_text("thread", "text", "hello")

    assert saved == [
        ("thread", {"id": "image", "role": "assistant", "text": "", "image": "image.png"}),
        ("thread", {"id": "text", "role": "assistant", "text": "hello"}),
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
