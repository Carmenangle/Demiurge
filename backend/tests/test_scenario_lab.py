import json
import sqlite3
import sys
import time
from types import SimpleNamespace

import pytest

from app.services import repo_meta, scenario_lab


class _Embeddings:
    def __bool__(self):
        raise ValueError("ambiguous")

    def tolist(self):
        return [[0.1, 0.2]]


def _configure(monkeypatch, tmp_path):
    output = tmp_path / "output"
    monkeypatch.setattr(scenario_lab, "SNAPSHOT_ROOT", tmp_path / "snapshots")
    monkeypatch.setattr(scenario_lab, "EXPERIMENT_ROOT", tmp_path / "experiments")
    monkeypatch.setattr(scenario_lab, "_export_collections", lambda *_: [])
    monkeypatch.setattr(scenario_lab, "_restore_collections", lambda *_: 0)
    monkeypatch.setattr(repo_meta, "repo_name", lambda rid: rid)
    monkeypatch.setattr(repo_meta, "_repo_record", lambda rid: None)
    return output


def test_full_snapshot_and_isolated_fork(monkeypatch, tmp_path):
    output = _configure(monkeypatch, tmp_path)
    source = repo_meta.repo_folder(str(output), "source")
    (source / "chat.json").write_text('[{"text":"source"}]', encoding="utf-8")
    (source / "state.json").write_text('{"位置":"城外"}', encoding="utf-8")
    with sqlite3.connect(source / "chronicle.db") as conn:
        conn.execute("create table facts(value text)")
        conn.execute("insert into facts values('source')")

    snap = scenario_lab.create_snapshot(str(output), "source", turn=42, label="分歧点")
    assert snap["turn"] == 42
    result = scenario_lab.fork_snapshot(str(output), "source", snap["snapshot_id"], "branch")
    target = repo_meta.repo_folder(str(output), "branch")

    assert result["files"] == 3
    assert json.loads((target / "chat.json").read_text(encoding="utf-8"))[0]["text"] == "source"
    with sqlite3.connect(target / "chronicle.db") as conn:
        assert conn.execute("select value from facts").fetchone()[0] == "source"
    (target / "state.json").write_text('{"位置":"王宫"}', encoding="utf-8")
    assert json.loads((source / "state.json").read_text(encoding="utf-8"))["位置"] == "城外"


def test_fork_copies_chat_threads_before_branch_point(monkeypatch, tmp_path):
    output = _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(scenario_lab, "DATA_DIR", tmp_path / "data")
    source = repo_meta.repo_folder(str(output), "source")
    (source / "chat.json").write_text('[{"text":"剧情"}]', encoding="utf-8")
    (source / "tables.json").write_text('{"表":[{"行":1}]}', encoding="utf-8")
    now = time.time()
    chat = output / "source__chat__alice_abc123"
    chat.mkdir()
    (chat / "chat.json").write_text(json.dumps([
        {"id": "past", "text": "分支前", "ts": now - 60},
        {"id": "future", "text": "分支后", "ts": now + 3600},
        {"id": "undated", "text": "旧消息没有时间戳"},
        {"id": "ms", "text": "毫秒时间戳", "ts": int(now * 1000) - 60_000},
    ], ensure_ascii=False), encoding="utf-8")
    group = output / "source__group"
    group.mkdir()
    (group / "chat.json").write_text(json.dumps([
        {"id": "g-past", "text": "群聊过去", "ts": now - 120},
        {"id": "g-future", "text": "群聊未来", "ts": now + 7200},
    ], ensure_ascii=False), encoding="utf-8")
    legacy = tmp_path / "data" / "chat_snapshots"
    legacy.mkdir(parents=True)
    (legacy / "source__chat__bob_def456.json").write_text(
        json.dumps([{"id": "legacy", "text": "旧落点线程"}], ensure_ascii=False),
        encoding="utf-8",
    )

    snapshot = scenario_lab.create_snapshot(str(output), "source")
    result = scenario_lab.fork_snapshot(
        str(output), "source", snapshot["snapshot_id"], "branch",
    )

    branch_chat = output / "branch__chat__alice_abc123"
    assert [m["id"] for m in json.loads((branch_chat / "chat.json").read_text(encoding="utf-8"))] == [
        "past", "undated", "ms",
    ]
    assert [m["id"] for m in json.loads(
        (output / "branch__group" / "chat.json").read_text(encoding="utf-8")
    )] == ["g-past"]
    assert (output / "branch__chat__bob_def456" / "chat.json").is_file()
    assert result["chat_threads"] == 3
    assert result["chat_messages"] == 5
    target = repo_meta.repo_folder(str(output), "branch")
    assert json.loads((target / "tables.json").read_text(encoding="utf-8"))["表"][0]["行"] == 1


def test_fork_refuses_non_empty_target(monkeypatch, tmp_path):
    output = _configure(monkeypatch, tmp_path)
    source = repo_meta.repo_folder(str(output), "source")
    (source / "chat.json").write_text("[]", encoding="utf-8")
    snap = scenario_lab.create_snapshot(str(output), "source")
    target = repo_meta.repo_folder(str(output), "target")
    (target / "state.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError):
        scenario_lab.fork_snapshot(str(output), "source", snap["snapshot_id"], "target")


def test_experiment_requires_snapshot_then_creates_isolated_branches(monkeypatch, tmp_path):
    output = _configure(monkeypatch, tmp_path)
    source = repo_meta.repo_folder(str(output), "source")
    (source / "chat.json").write_text("[]", encoding="utf-8")
    snap = scenario_lab.create_snapshot(str(output), "source")
    experiment = scenario_lab.create_experiment(
        str(output), "source", snap["snapshot_id"],
        [{"choice": "进入密道", "target_repo_id": "branch-a"},
         {"choice": "留在原地", "target_repo_id": "branch-b"}], rounds=3,
    )
    assert experiment["rounds"] == 3
    assert {item["target_repo_id"] for item in experiment["branches"]} == {"branch-a", "branch-b"}
    selected = scenario_lab.select_branch(experiment["experiment_id"], "branch-b")
    assert selected["selected_repo_id"] == "branch-b"


def test_chroma_snapshot_preserves_embeddings_and_rewrites_repo_metadata(monkeypatch, tmp_path):
    exported = {"ids": ["doc"], "documents": ["text"],
                "metadatas": [{"repo_id": "source", "image_url": "C:/source/a.png"}],
                "embeddings": _Embeddings()}
    upserts = []

    class Client:
        def get_collection(self, name):
            if name != "repo_source":
                raise KeyError(name)
            return SimpleNamespace(get=lambda **_kwargs: exported)

        def get_or_create_collection(self, name):
            return SimpleNamespace(upsert=lambda **kwargs: upserts.append((name, kwargs)))

    monkeypatch.setitem(sys.modules, "chromadb", SimpleNamespace(PersistentClient=lambda **_kwargs: Client()))
    root = tmp_path / "snapshot"
    collections = scenario_lab._export_collections("source", root / "collections")
    assert collections[0]["count"] == 1
    manifest = {"source_repo_id": "source", "collections": collections}
    restored = scenario_lab._restore_collections(root, manifest, "branch", "C:/source", "D:/branch")
    assert restored == 1
    assert upserts[0][0] == "repo_branch"
    assert upserts[0][1]["metadatas"][0]["repo_id"] == "branch"
    assert upserts[0][1]["metadatas"][0]["image_url"] == "D:/branch/a.png"


def test_repeated_snapshot_reuses_hash_for_unchanged_asset(monkeypatch, tmp_path):
    output = _configure(monkeypatch, tmp_path)
    source = repo_meta.repo_folder(str(output), "source")
    (source / "image.png").write_bytes(b"stable image")
    calls: list[str] = []
    original = scenario_lab._sha256
    monkeypatch.setattr(
        scenario_lab,
        "_sha256",
        lambda path: calls.append(path.name) or original(path),
    )

    scenario_lab.create_snapshot(str(output), "source", turn=1)
    first_count = calls.count("image.png")
    scenario_lab.create_snapshot(str(output), "source", turn=2)

    assert first_count == 1
    assert calls.count("image.png") == 1


def test_snapshot_failure_removes_staging_directory(monkeypatch, tmp_path):
    output = _configure(monkeypatch, tmp_path)
    source = repo_meta.repo_folder(str(output), "source")
    (source / "chat.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(
        scenario_lab,
        "_export_collections",
        lambda *_: (_ for _ in ()).throw(RuntimeError("export failed")),
    )

    with pytest.raises(RuntimeError, match="export failed"):
        scenario_lab.create_snapshot(str(output), "source")

    base = scenario_lab.SNAPSHOT_ROOT / "source"
    assert not base.exists() or list(base.iterdir()) == []


def test_experiment_rolls_back_completed_branches_when_later_fork_fails(monkeypatch, tmp_path):
    output = _configure(monkeypatch, tmp_path)
    source = repo_meta.repo_folder(str(output), "source")
    (source / "chat.json").write_text("[]", encoding="utf-8")
    thread = output / "source__chat__alice_abc123"
    thread.mkdir()
    (thread / "chat.json").write_text(
        json.dumps([{"id": "1", "text": "hi", "ts": time.time() - 10}]), encoding="utf-8",
    )
    snap = scenario_lab.create_snapshot(str(output), "source")
    occupied = repo_meta.repo_folder(str(output), "branch-b")
    (occupied / "user.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError):
        scenario_lab.create_experiment(
            str(output), "source", snap["snapshot_id"],
            [{"choice": "A", "target_repo_id": "branch-a"},
             {"choice": "B", "target_repo_id": "branch-b"}],
        )

    branch_a = repo_meta.repo_folder_path(str(output), "branch-a")
    assert not (branch_a / "chat.json").exists()
    assert not (output / "branch-a__chat__alice_abc123").exists()
    assert (occupied / "user.txt").read_text(encoding="utf-8") == "keep"
