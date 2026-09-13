"""canvas_store 回归测试：deleted_ids 黑名单随布局往返持久化。

合同：画布删除的投影节点（img-/video-/audio-<generationId>）必须落盘进 canvas.json，
refresh/重新挂载时投影过滤，不得复活（同会话快照删除语义）。
"""
from __future__ import annotations

from app.services import canvas_store


def _layout(**over):
    base = {
        "nodes": {"img-abc123": {"x": 10, "y": 20, "w": 240, "h": 300}},
        "edges": [{"source": "img-abc123", "target": "img-def456"}],
        "viewport": {"x": 0, "y": 0, "scale": 1},
        "inspiration_cards": [],
        "deleted_ids": ["img-def456"],
    }
    base.update(over)
    return base


def test_save_then_load_preserves_deleted_ids(tmp_path):
    canvas_store.save_layout(str(tmp_path), "repo-x", _layout())
    out = canvas_store.load_layout(str(tmp_path), "repo-x")
    assert out["deleted_ids"] == ["img-def456"]
    assert out["nodes"]["img-abc123"]["x"] == 10
    assert out["edges"] == [{"source": "img-abc123", "target": "img-def456"}]


def test_load_missing_file_returns_empty_deleted_ids(tmp_path):
    out = canvas_store.load_layout(str(tmp_path), "repo-none")
    assert out["deleted_ids"] == []


def test_load_legacy_file_without_deleted_ids_backfills_empty(tmp_path):
    """旧 canvas.json 没有 deleted_ids 字段：加载回填空列表（向后兼容）。"""
    canvas_store.save_layout(str(tmp_path), "repo-legacy", _layout())
    path = tmp_path / "repo-legacy" / "canvas.json"
    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("deleted_ids", None)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = canvas_store.load_layout(str(tmp_path), "repo-legacy")
    assert out["deleted_ids"] == []
    assert out["nodes"]["img-abc123"]["x"] == 10


def test_load_corrupted_file_returns_empty(tmp_path):
    folder = tmp_path / "repo-bad"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "canvas.json").write_text("{ not json", encoding="utf-8")
    out = canvas_store.load_layout(str(tmp_path), "repo-bad")
    assert out["deleted_ids"] == []
    assert out["nodes"] == {}
