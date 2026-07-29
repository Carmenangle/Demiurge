import pytest

from app.services import chat_maintenance


class FakeLLM:
    def invoke(self, messages):
        return type("Response", (), {"content": "摘要正文"})()


def _idle(monkeypatch):
    monkeypatch.setattr(chat_maintenance.agent_runner, "is_running", lambda thread_id: False)


def test_clear_only_clears_checkpoint(monkeypatch):
    _idle(monkeypatch)
    calls = []
    monkeypatch.setattr(chat_maintenance.chat_memory, "clear_history", lambda thread: calls.append(thread))
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "save", lambda *a: (_ for _ in ()).throw(AssertionError()))

    assert chat_maintenance.clear("repo") == {"ok": True}
    assert calls == ["repo"]


def test_clear_cache_only_deletes_reference(tmp_path, monkeypatch):
    _idle(monkeypatch)
    repo = tmp_path / "repo"
    reference = repo / "reference"
    reference.mkdir(parents=True)
    (reference / "a.png").write_bytes(b"ref")
    generated = repo / "generated.png"
    generated.write_bytes(b"asset")
    marker = repo / "_repo.json"
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(chat_maintenance.repo_meta, "repo_folder_path", lambda output, thread: repo)
    snapshots = [[{"id": "old"}]]
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "load_strict", lambda thread: snapshots[-1])
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "save", lambda thread, value: snapshots.append(value))
    monkeypatch.setattr(chat_maintenance.chat_memory, "clear_history", lambda thread: None)

    result = chat_maintenance.clear_cache("repo", str(tmp_path))

    assert result.removed == 1
    assert not reference.exists()
    assert generated.read_bytes() == b"asset"
    assert marker.exists()
    assert snapshots[-1] == []


def test_clear_cache_missing_repo_does_not_create(tmp_path, monkeypatch):
    _idle(monkeypatch)
    target = tmp_path / "missing"
    monkeypatch.setattr(chat_maintenance.repo_meta, "repo_folder_path", lambda output, thread: target)
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "load_strict", lambda thread: [])
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "save", lambda thread, value: None)
    monkeypatch.setattr(chat_maintenance.chat_memory, "clear_history", lambda thread: None)

    result = chat_maintenance.clear_cache("repo", str(tmp_path))

    assert result.removed == 0
    assert not target.exists()


def test_clear_cache_restores_snapshot_when_checkpoint_fails(monkeypatch):
    _idle(monkeypatch)
    writes = []
    old = [{"id": "old"}]
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "load_strict", lambda thread: old)
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "save", lambda thread, value: writes.append(value))
    monkeypatch.setattr(chat_maintenance.chat_memory, "clear_history", lambda thread: (_ for _ in ()).throw(RuntimeError("db")))

    with pytest.raises(chat_maintenance.MaintenanceFailed):
        chat_maintenance.clear_cache("repo", "")
    assert writes == [[], old]


def test_compact_commits_same_summary_to_memory_and_snapshot(monkeypatch):
    _idle(monkeypatch)
    history = [{"role": "user", "content": "hello", "images": []}]
    monkeypatch.setattr(chat_maintenance.chat_memory, "get_history", lambda thread: history)
    old_snapshot = [
        {"id": "u1", "role": "user", "text": "最开始确定金发绿瞳"},
        {"id": "a1", "role": "assistant", "text": "最终方案完成", "image": "local://final.png"},
    ]
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "load_strict", lambda thread: old_snapshot)
    monkeypatch.setattr(chat_maintenance.rag_store, "list_generations", lambda thread, cfg: [{"prompt": "p"}])
    summarized = []
    def summarize(hist, llm, gens):
        summarized.extend(hist)
        return {"ok": True, "summary": "摘要正文", "image_count": 1}
    monkeypatch.setattr(chat_maintenance.chat_memory, "summarize_history", summarize)
    snapshot_writes = []
    memory_writes = []
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "save", lambda thread, value: snapshot_writes.append(value))
    monkeypatch.setattr(chat_maintenance.chat_memory, "replace_history", lambda thread, value: memory_writes.append(value))

    result = chat_maintenance.compact("repo", FakeLLM(), object())

    assert result["message"] == snapshot_writes[-1][0]
    assert memory_writes[-1][0]["content"] == result["message"]["text"]
    assert result["message"]["image"] == "local://final.png"
    assert memory_writes[-1][0]["images"] == ["local://final.png"]
    assert [item["content"] for item in summarized] == ["最开始确定金发绿瞳", "最终方案完成"]
    assert result["image_count"] == 1


def test_compact_falls_back_to_latest_generation_image(monkeypatch):
    _idle(monkeypatch)
    history = [{"role": "user", "content": "hello", "images": []}]
    monkeypatch.setattr(chat_maintenance.chat_memory, "get_history", lambda thread: history)
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "load_strict", lambda thread: [])
    monkeypatch.setattr(chat_maintenance.rag_store, "list_generations", lambda thread, cfg: [
        {"prompt": "old", "image_url": "local://old.png", "created_at": 1},
        {"prompt": "new", "image_url": "local://new.png", "created_at": 2},
    ])
    monkeypatch.setattr(chat_maintenance.chat_memory, "summarize_history", lambda hist, llm, gens: {
        "ok": True, "summary": "摘要正文", "image_count": 2,
    })
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "save", lambda thread, value: None)
    monkeypatch.setattr(chat_maintenance.chat_memory, "replace_history", lambda thread, value: None)

    result = chat_maintenance.compact("repo", FakeLLM(), object())

    assert result["message"]["image"] == "local://new.png"


def test_compact_failure_before_commit_preserves_state(monkeypatch):
    _idle(monkeypatch)
    history = [{"role": "user", "content": "hello", "images": []}]
    monkeypatch.setattr(chat_maintenance.chat_memory, "get_history", lambda thread: history)
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "load_strict", lambda thread: [])
    monkeypatch.setattr(chat_maintenance.rag_store, "list_generations", lambda thread, cfg: (_ for _ in ()).throw(RuntimeError("rag")))
    monkeypatch.setattr(chat_maintenance.chat_snapshot, "save", lambda *a: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(chat_maintenance.chat_memory, "replace_history", lambda *a: (_ for _ in ()).throw(AssertionError()))

    with pytest.raises(chat_maintenance.MaintenanceFailed):
        chat_maintenance.compact("repo", FakeLLM(), object())


def test_active_agent_blocks_maintenance(monkeypatch):
    monkeypatch.setattr(chat_maintenance.agent_runner, "is_running", lambda thread_id: True)
    monkeypatch.setattr(chat_maintenance.chat_memory, "clear_history", lambda *a: (_ for _ in ()).throw(AssertionError()))
    with pytest.raises(chat_maintenance.MaintenanceConflict):
        chat_maintenance.clear("repo")
