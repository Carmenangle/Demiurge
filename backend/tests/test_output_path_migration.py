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


def _make_repo_folder(root: Path, name: str, files: dict[str, bytes]) -> Path:
    """在 root 下建带 _repo.json 标记的作品文件夹并写入文件（值即内容）。"""
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "_repo.json").write_text(json.dumps({"id": name, "name": name}), encoding="utf-8")
    for rel, data in files.items():
        p = folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return folder


def test_audit_counts_repo_folder_files(tmp_path, monkeypatch):
    old_root, new_root = tmp_path / "old", tmp_path / "new"
    folder = _make_repo_folder(old_root, "repo", {"a.png": b"image", "chat.json": b"[]"})
    source = folder / "a.png"
    monkeypatch.setattr(migration, "_scan_asset_refs",
                        lambda *_: [_ref(source, new_root / "repo" / "a.png", "a")])

    result = migration.audit(str(old_root), str(new_root))

    assert result["changed"] is True
    assert result["asset_count"] == 1              # Chroma 索引到的图片
    assert result["file_count"] == 3               # a.png + chat.json + _repo.json 全搬
    assert result["total_bytes"] == 5 + 2 + len(folder.joinpath("_repo.json").read_bytes())
    assert result["missing_count"] == 0
    assert result["conflict_count"] == 0


def test_migrate_moves_whole_repo_folder_and_rewrites(tmp_path, monkeypatch):
    old_root, new_root = tmp_path / "old", tmp_path / "new"
    folder = _make_repo_folder(old_root, "repo", {
        "a.png": b"image", "chat.json": b"[]", "state.json": b"{}",
    })
    source = folder / "a.png"
    destination = new_root / "repo" / "a.png"
    seen = {}
    monkeypatch.setattr(migration, "_scan_asset_refs", lambda *_: [_ref(source, destination)])
    monkeypatch.setattr(migration, "_update_index", lambda items: [])
    monkeypatch.setattr(migration, "_rewrite_json_references",
                        lambda files, extra_candidates=None: seen.update(extra=extra_candidates) or ([], 3))

    result = migration.migrate(str(old_root), str(new_root))

    assert not folder.exists()                                  # 旧作品文件夹整体删除
    assert destination.read_bytes() == b"image"
    assert (new_root / "repo" / "chat.json").read_bytes() == b"[]"   # 会话随文件夹迁移
    assert (new_root / "repo" / "state.json").read_bytes() == b"{}"  # 状态也随迁
    assert (new_root / "repo" / "chat.json") in seen["extra"]        # 新位置 chat.json 参与重写
    assert result["migrated_files"] == 4                        # a.png+chat.json+state.json+_repo.json
    assert result["updated_assets"] == 1
    assert result["updated_references"] == 3
    assert result["delete_failures"] == 0


def test_migrate_ignores_unmarked_folders(tmp_path, monkeypatch):
    """没有 _repo.json 标记的目录(ComfyUI 自有输出)不迁移，避免误搬。"""
    old_root, new_root = tmp_path / "old", tmp_path / "new"
    loose = old_root / "comfy_out"
    loose.mkdir(parents=True)
    (loose / "x.png").write_bytes(b"comfy")
    monkeypatch.setattr(migration, "_scan_asset_refs", lambda *_: [])
    monkeypatch.setattr(migration, "_update_index", lambda items: [])
    monkeypatch.setattr(migration, "_rewrite_json_references",
                        lambda files, extra_candidates=None: ([], 0))

    result = migration.migrate(str(old_root), str(new_root))

    assert loose.joinpath("x.png").is_file()      # 原地不动
    assert not (new_root / "comfy_out").exists()
    assert result["migrated_files"] == 0


def test_migrate_refuses_different_target_file(tmp_path, monkeypatch):
    old_root, new_root = tmp_path / "old", tmp_path / "new"
    folder = _make_repo_folder(old_root, "repo", {"a.png": b"old"})
    source = folder / "a.png"
    destination = new_root / "repo" / "a.png"
    destination.parent.mkdir(parents=True)
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
