"""编辑落库端点：/ai/chat/message/edit 原子替换既有消息，不制造幽灵消息（2026-09-13）。"""
from __future__ import annotations

import pytest

from app.routers import ai_chat
from app.services import chat_snapshot, repo_meta


@pytest.fixture()
def isolated_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")


def test_编辑落库按id原子替换既有消息(isolated_snapshot):
    chat_snapshot.save("t1", [{"id": "m1", "role": "assistant", "text": "旧文本（正文到此）"}])
    req = ai_chat.MessageEditRequest(
        thread_id="t1", message={"id": "m1", "role": "assistant", "text": "新文本"})
    assert ai_chat.chat_message_edit(req) == {"ok": True, "updated": True}
    assert chat_snapshot.load("t1") == [{"id": "m1", "role": "assistant", "text": "新文本"}]


def test_编辑未知消息不追加幽灵(isolated_snapshot):
    chat_snapshot.save("t1", [{"id": "m1", "role": "assistant", "text": "正文"}])
    req = ai_chat.MessageEditRequest(
        thread_id="t1", message={"id": "ghost", "role": "assistant", "text": "x"})
    assert ai_chat.chat_message_edit(req) == {"ok": True, "updated": False}
    assert chat_snapshot.load("t1") == [{"id": "m1", "role": "assistant", "text": "正文"}]


def test_编辑缺id返回400(isolated_snapshot):
    req = ai_chat.MessageEditRequest(thread_id="t1", message={"role": "assistant", "text": "x"})
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        ai_chat.chat_message_edit(req)
    assert exc.value.status_code == 400


def test_编辑保留消息其他字段(isolated_snapshot):
    chat_snapshot.save("t1", [{"id": "m2", "role": "assistant", "text": "旧",
                               "parts": [{"type": "text", "text": "旧"}]}])
    req = ai_chat.MessageEditRequest(
        thread_id="t1", message={"id": "m2", "role": "assistant", "text": "新",
                                 "parts": [{"type": "media-slot", "slotId": "s1", "status": "pending"},
                                           {"type": "text", "text": "新"}]})
    ai_chat.chat_message_edit(req)
    got = chat_snapshot.load("t1")[0]
    assert got["text"] == "新"
    assert [p["type"] for p in got["parts"]] == ["media-slot", "text"]
