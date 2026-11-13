"""对话删除前归档（软删层）回归：2026-11-05「防误删」审计第 ① 刀。

判绿口径按三层分：属主（chat_archive 的落盘/命名/读取）⇒ 事务（chat_maintenance 的
「归档没成就不删」与恢复往返）⇒ 端点出口（状态码映射）。中间那层是这次要防的缺陷本身，
属主层全绿也挡不住「维护动作压根没调用归档」。
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.routers import ai_chat
from app.services import chat_archive, chat_maintenance


def _idle(monkeypatch):
    monkeypatch.setattr(chat_maintenance.agent_runner, "is_running", lambda thread_id: False)


def _archive_dir(tmp_path):
    return tmp_path / "chat_archives"


def _files(tmp_path, thread="repo"):
    folder = _archive_dir(tmp_path) / thread
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def _name_of(thread, reason):
    return {item["reason"]: item["name"] for item in chat_archive.list_archives(thread)}[reason]


def _read_back(thread, name):
    return chat_archive.read_archive(thread, name)["messages"]


# ── 属主层：落盘 / 命名 / 读取 ──


def test_空会话不归档不建目录(tmp_path):
    assert chat_archive.archive("repo", [], chat_archive.REASON_CLEAR) == ""
    assert not _archive_dir(tmp_path).exists()


def test_归档逐字保留中文并可取回(tmp_path):
    snapshot = [{"id": "u1", "role": "user", "text": "甲的原始剧情「保留引号」"},
                {"id": "a1", "role": "assistant", "text": "乙的回复", "image": "local://图.png"}]
    name = chat_archive.archive("repo", snapshot, chat_archive.REASON_COMPACT)
    raw = (_archive_dir(tmp_path) / "repo" / f"{name}.json").read_text(encoding="utf-8")
    assert "甲的原始剧情" in raw          # ensure_ascii=False：中文不许变成 \uXXXX
    got = chat_archive.read_archive("repo", name)
    assert got["messages"] == snapshot
    assert got["reason"] == chat_archive.REASON_COMPACT


def test_列表只读文件名不读正文且新的在前(tmp_path):
    first = chat_archive.archive("repo", [{"id": "1"}], chat_archive.REASON_CLEAR)
    second = chat_archive.archive("repo", [{"id": str(n)} for n in range(7)],
                                  chat_archive.REASON_CLEAR_CACHE)
    items = chat_archive.list_archives("repo")
    assert [item["name"] for item in items] == [second, first]
    assert [item["message_count"] for item in items] == [7, 1]
    assert [item["reason"] for item in items] == [chat_archive.REASON_CLEAR_CACHE,
                                                  chat_archive.REASON_CLEAR]
    assert all(item["created_at"].count(":") == 2 for item in items)


def test_不符语法的文件不列出来但也不删(tmp_path):
    name = chat_archive.archive("repo", [{"id": "1"}], chat_archive.REASON_CLEAR)
    stray = _archive_dir(tmp_path) / "repo" / "手放的东西.json"
    stray.write_text("{}", encoding="utf-8")
    assert [item["name"] for item in chat_archive.list_archives("repo")] == [name]
    assert stray.is_file()                       # 软删层自己不许删任何东西
    assert chat_archive.read_archive("repo", name)["messages"] == [{"id": "1"}]


@pytest.mark.parametrize("name", [
    "", "../20261105-142233-000-clear-1", "20261105-142233-000-clear-1/../../x",
    "20261105-142233-000-cléar-1", "20261105-142233-000-clear-1.json",
    "..", "20261105-142233-000-clear-1.json.tmp",
])
def test_归档名不合法一律拒绝且不去读目录外(tmp_path, name):
    with pytest.raises(chat_archive.ArchiveNameError):
        chat_archive.read_archive("repo", name)


def test_读不动的归档报错但文件留着(tmp_path):
    name = chat_archive.archive("repo", [{"id": "1"}], chat_archive.REASON_CLEAR)
    target = _archive_dir(tmp_path) / "repo" / f"{name}.json"
    target.write_text("{ broken", encoding="utf-8")
    with pytest.raises(chat_archive.ArchiveNameError, match="读不动"):
        chat_archive.read_archive("repo", name)
    assert target.is_file()


def test_原因名不合法直接拒(tmp_path):
    with pytest.raises(ValueError, match="归档原因名"):
        chat_archive.archive("repo", [{"id": "1"}], "清 缓存/../x")


def test_会话名不许越出归档根(tmp_path):
    chat_archive.archive("..", [{"id": "1"}], chat_archive.REASON_CLEAR)
    assert _archive_dir(tmp_path).is_dir()
    assert not (_archive_dir(tmp_path).parent / "home").exists()


# ── 事务层：归档闸 + 恢复往返 ──


def test_clear_归档失败就不删任何东西(tmp_path, monkeypatch):
    _idle(monkeypatch)
    calls = []
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "load_strict",
                        lambda thread: [{"id": "1", "role": "user", "text": "甲"}])
    monkeypatch.setattr(chat_maintenance.chat_memory, "clear_history",
                        lambda thread: calls.append(thread))
    monkeypatch.setattr(chat_maintenance.attachment_store, "delete_thread",
                        lambda thread: calls.append("attach"))
    monkeypatch.setattr(chat_archive, "archive",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(chat_maintenance.MaintenanceFailed, match="删除前归档对话失败"):
        chat_maintenance.clear("repo")
    assert calls == []
    assert _files(tmp_path) == []


def test_clear_留一份可恢复的归档(tmp_path, monkeypatch):
    _idle(monkeypatch)
    cleared = []
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "load_strict",
                        lambda thread: [{"id": "1", "role": "user", "text": "甲"}])
    monkeypatch.setattr(chat_maintenance.chat_memory, "clear_history",
                        lambda thread: cleared.append(thread))

    assert chat_maintenance.clear("repo") == {"ok": True}
    assert cleared == ["repo"]
    items = chat_archive.list_archives("repo")
    assert [item["reason"] for item in items] == [chat_archive.REASON_CLEAR]
    assert chat_archive.read_archive("repo", items[0]["name"])["messages"] == [
        {"id": "1", "role": "user", "text": "甲"}]


def test_清缓存先归档原快照再抹平(tmp_path, monkeypatch):
    _idle(monkeypatch)
    old = [{"id": "u1", "role": "user", "text": "甲的开场"},
           {"id": "a1", "role": "assistant", "text": "乙的回应"}]
    writes = []
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "load_strict", lambda thread: old)
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "save",
                        lambda thread, value: writes.append(value))
    monkeypatch.setattr(chat_maintenance.chat_memory, "clear_history", lambda thread: None)
    monkeypatch.setattr(chat_maintenance.repo_meta, "repo_folder_path",
                        lambda output, thread: tmp_path / "无此目录")

    result = chat_maintenance.clear_cache("repo", str(tmp_path))

    assert result.removed == 0
    assert writes == [[]]
    items = chat_archive.list_archives("repo")
    assert items[0]["reason"] == chat_archive.REASON_CLEAR_CACHE
    assert chat_archive.read_archive("repo", items[0]["name"])["messages"] == old


def test_压缩把原文与checkpoint历史一起归档(tmp_path, monkeypatch):
    _idle(monkeypatch)
    history = [{"role": "user", "content": "甲说的", "images": []}]
    old_snapshot = [{"id": "u1", "role": "user", "text": "甲说的"}]
    monkeypatch.setattr(chat_maintenance.chat_memory, "get_history", lambda thread: history)
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "load_strict", lambda thread: old_snapshot)
    monkeypatch.setattr(chat_maintenance.rag_store, "list_generations", lambda thread, cfg: [])
    monkeypatch.setattr(chat_maintenance.chat_memory, "summarize_history",
                        lambda hist, llm, gens, **_k: {"ok": True, "summary": "摘要正文", "image_count": 0})
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "save", lambda thread, value: None)
    monkeypatch.setattr(chat_maintenance.chat_memory, "replace_history", lambda thread, value: None)

    chat_maintenance.compact("repo", object(), object())

    items = chat_archive.list_archives("repo")
    assert items[0]["reason"] == chat_archive.REASON_COMPACT
    payload = chat_archive.read_archive("repo", items[0]["name"])
    assert payload["messages"] == old_snapshot
    assert payload["history"] == history


def test_压缩归档失败时摘要一个字都不写(tmp_path, monkeypatch):
    _idle(monkeypatch)
    monkeypatch.setattr(chat_maintenance.chat_memory, "get_history",
                        lambda thread: [{"role": "user", "content": "甲", "images": []}])
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "load_strict",
                        lambda thread: [{"id": "u1", "role": "user", "text": "甲"}])
    monkeypatch.setattr(chat_maintenance.rag_store, "list_generations", lambda thread, cfg: [])
    monkeypatch.setattr(chat_maintenance.chat_memory, "summarize_history",
                        lambda hist, llm, gens, **_k: {"ok": True, "summary": "摘要正文", "image_count": 0})
    writes = []
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "save",
                        lambda thread, value: writes.append(value))
    monkeypatch.setattr(chat_archive, "archive",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("read only")))

    with pytest.raises(chat_maintenance.MaintenanceFailed, match="删除前归档对话失败"):
        chat_maintenance.compact("repo", object(), object())
    assert writes == []


def test_恢复把压缩前的原文整条压回去(tmp_path, monkeypatch):
    _idle(monkeypatch)
    history = [{"role": "user", "content": "甲说的", "images": []}]
    old_snapshot = [{"id": "u1", "role": "user", "text": "甲说的"},
                    {"id": "a1", "role": "assistant", "text": "乙答的"}]
    monkeypatch.setattr(chat_maintenance.chat_memory, "get_history", lambda thread: history)
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "load_strict", lambda thread: old_snapshot)
    monkeypatch.setattr(chat_maintenance.rag_store, "list_generations", lambda thread, cfg: [])
    monkeypatch.setattr(chat_maintenance.chat_memory, "summarize_history",
                        lambda hist, llm, gens, **_k: {"ok": True, "summary": "摘要正文", "image_count": 0})
    snapshot_writes = []
    memory_writes = []
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "save",
                        lambda thread, value: snapshot_writes.append(value))
    monkeypatch.setattr(chat_maintenance.chat_memory, "replace_history",
                        lambda thread, value: memory_writes.append(value))
    chat_maintenance.compact("repo", object(), object())
    archived = _name_of("repo", chat_archive.REASON_COMPACT)
    snapshot_writes.clear()
    memory_writes.clear()
    summary_only = [{"id": "s1", "role": "assistant", "text": "【历史摘要】摘要正文"}]
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "load_strict",
                        lambda thread: summary_only)

    result = chat_maintenance.restore("repo", archived)

    assert result == {"ok": True, "messages": 2, "history": 2}
    assert snapshot_writes[0] == old_snapshot            # 原文整条写回真源
    assert memory_writes[0] == [{"role": "user", "content": "甲说的", "images": []},
                                {"role": "assistant", "content": "乙答的", "images": []}]
    # 覆盖前先把「当前状态」归档了一份 ⇒ 压错还能再压回去
    assert _read_back("repo", _name_of("repo", chat_archive.REASON_RESTORE)) == summary_only


def test_恢复不认识的归档名报参数错(tmp_path, monkeypatch):
    _idle(monkeypatch)
    with pytest.raises(ValueError, match="归档名不合法"):
        chat_maintenance.restore("repo", "../escape")


def test_后台在跑时归档闸还没开就拒(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_maintenance.agent_runner, "is_running", lambda thread_id: True)
    monkeypatch.setattr(chat_archive, "archive",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不许归档")))
    with pytest.raises(chat_maintenance.MaintenanceConflict):
        chat_maintenance.clear("repo")


# ── 端点出口：状态码映射（service 全绿也挡不住端点漏接线） ──


def test_归档清单端点返回条目(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_archive, "archive",
                        lambda *a, **k: "20261105-142233-000-clear-1")
    monkeypatch.setattr(chat_archive, "list_archives",
                        lambda thread: [{"name": "20261105-142233-000-clear-1",
                                         "reason": "clear", "message_count": 1}])
    got = ai_chat.chat_archives(thread_id="repo")
    assert got["items"][0]["name"] == "20261105-142233-000-clear-1"


def test_恢复端点把三类异常映射成三种状态码(monkeypatch):
    def _raise(kind):
        def restore(thread_id, name):
            if kind == "value":
                raise ValueError("归档不存在或已被删除")
            if kind == "conflict":
                raise chat_maintenance.MaintenanceConflict("后台在跑")
            raise chat_maintenance.MaintenanceFailed("写入恢复快照失败")
        return restore

    req = ai_chat.RestoreRequest(thread_id="repo", name="20261105-142233-000-clear-1")
    for kind, code in (("value", 400), ("conflict", 409), ("failed", 500)):
        monkeypatch.setattr(chat_maintenance, "restore", _raise(kind))
        with pytest.raises(HTTPException) as exc:
            ai_chat.chat_archives_restore(req)
        assert exc.value.status_code == code


def test_恢复端点成功时透传计数(monkeypatch):
    monkeypatch.setattr(chat_maintenance, "restore",
                        lambda thread_id, name: {"ok": True, "messages": 2, "history": 2})
    got = ai_chat.chat_archives_restore(
        ai_chat.RestoreRequest(thread_id="repo", name="20261105-142233-000-clear-1"))
    assert got["messages"] == 2


def test_归档正文落盘形状稳定(tmp_path):
    """payload 的键集是恢复端的读契约，改这里要同步 read_archive。"""
    name = chat_archive.archive("repo", [{"id": "1"}], chat_archive.REASON_CLEAR,
                                [{"role": "user", "content": "甲", "images": []}])
    data = json.loads((_archive_dir(tmp_path) / "repo" / f"{name}.json").read_text(encoding="utf-8"))
    assert set(data) == {"thread_id", "reason", "created_at", "message_count",
                         "messages", "history"}
    assert data["history"] == [{"role": "user", "content": "甲", "images": []}]
