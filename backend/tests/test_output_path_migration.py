import json
from pathlib import Path

import pytest

from app.services import output_path_migration as migration


def _ref(source: Path, destination: Path, doc_id: str = "asset-1") -> migration.AssetRef:
    old_url = f"http://127.0.0.1:8010/api/comfyui/local-view?path={source}"
    return migration.AssetRef(
        collection="repo_test",
        doc_id=doc_id,
        metadata={"kind": "generation", "image_url": old_url},
        source=source,
        destination=destination,
        old_url=old_url,
        new_url=migration._with_local_path(old_url, destination),
    )


def test_audit_counts_asset_records_and_unique_files(tmp_path, monkeypatch):
    old_root, new_root = tmp_path / "old", tmp_path / "new"
    source = old_root / "repo" / "a.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image")
    refs = [_ref(source, new_root / "repo" / "a.png", "a"),
            _ref(source, new_root / "repo" / "a.png", "b")]
    monkeypatch.setattr(migration, "_scan_asset_refs", lambda *_: refs)

    result = migration.audit(str(old_root), str(new_root))

    assert result["asset_count"] == 2
    assert result["file_count"] == 1
    assert result["total_bytes"] == 5
    assert result["missing_count"] == 0
    assert result["conflict_count"] == 0


def test_migrate_copies_updates_references_then_removes_source(tmp_path, monkeypatch):
    old_root, new_root = tmp_path / "old", tmp_path / "new"
    source = old_root / "repo" / "a.png"
    destination = new_root / "repo" / "a.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image")
    refs = [_ref(source, destination)]
    monkeypatch.setattr(migration, "_scan_asset_refs", lambda *_: refs)
    monkeypatch.setattr(migration, "_update_index", lambda items: [])
    monkeypatch.setattr(migration, "_rewrite_json_references", lambda files: ([], 3))

    result = migration.migrate(str(old_root), str(new_root))

    assert not source.exists()
    assert destination.read_bytes() == b"image"
    assert result == {
        "migrated_files": 1,
        "updated_assets": 1,
        "updated_references": 3,
        "delete_failures": 0,
        "skipped_missing": 0,
    }


def test_migrate_skips_already_missing_asset(tmp_path, monkeypatch):
    old_root, new_root = tmp_path / "old", tmp_path / "new"
    missing = old_root / "repo" / "missing.png"
    existing = old_root / "repo" / "existing.png"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"image")
    refs = [
        _ref(missing, new_root / "repo" / "missing.png", "missing"),
        _ref(existing, new_root / "repo" / "existing.png", "existing"),
    ]
    updated = []
    monkeypatch.setattr(migration, "_scan_asset_refs", lambda *_: refs)
    monkeypatch.setattr(migration, "_update_index", lambda items: updated.extend(items) or [])
    monkeypatch.setattr(migration, "_rewrite_json_references", lambda files: ([], 1))

    result = migration.migrate(str(old_root), str(new_root))

    assert [item.doc_id for item in updated] == ["existing"]
    assert result["skipped_missing"] == 1
    assert (new_root / "repo" / "existing.png").is_file()


def test_migrate_refuses_different_target_file(tmp_path, monkeypatch):
    old_root, new_root = tmp_path / "old", tmp_path / "new"
    source = old_root / "repo" / "a.png"
    destination = new_root / "repo" / "a.png"
    source.parent.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    source.write_bytes(b"old")
    destination.write_bytes(b"different")
    monkeypatch.setattr(migration, "_scan_asset_refs", lambda *_: [_ref(source, destination)])

    with pytest.raises(migration.MigrationError, match="同名但内容不同"):
        migration.migrate(str(old_root), str(new_root))

    assert source.read_bytes() == b"old"
    assert destination.read_bytes() == b"different"


def test_rewrite_json_references_updates_snapshot_and_cover(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    snapshots = data_dir / "chat_snapshots"
    snapshots.mkdir(parents=True)
    monkeypatch.setattr(migration, "DATA_DIR", data_dir)
    source = tmp_path / "old" / "repo" / "a.png"
    destination = tmp_path / "new" / "repo" / "a.png"
    old_url = migration._with_local_path(
        "http://127.0.0.1:8010/api/comfyui/local-view?path=x", source,
    )
    (data_dir / "user_state.json").write_text(
        json.dumps({"repos": [{"cover": old_url}]}), encoding="utf-8",
    )
    (snapshots / "repo.json").write_text(
        json.dumps([{"image": old_url}]), encoding="utf-8",
    )

    _, count = migration._rewrite_json_references([(source, destination)])

    assert count == 2
    state = json.loads((data_dir / "user_state.json").read_text(encoding="utf-8"))
    snapshot = json.loads((snapshots / "repo.json").read_text(encoding="utf-8"))
    assert migration._local_path(state["repos"][0]["cover"]) == migration._absolute(str(destination))
    assert migration._local_path(snapshot[0]["image"]) == migration._absolute(str(destination))
