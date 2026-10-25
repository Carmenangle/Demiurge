"""sync-markers 根校验回归（2026-10-24 二轮审计补齐）。

合同：sync_markers 会向 output_dir 的子目录**写入** _repo.json 标记——写类端点
必须与 artifacts 等同口径过 `repo_meta.works_root_violation`（未配置拒绝 + 必须等于
配置真源），不能例外于「防客户端指定任意目录」。
"""
import json

import pytest

from app.routers.user_state import SyncMarkersRequest, sync_markers
from app.services import repo_meta


def _configure_root(tmp_path, root) -> None:
    (tmp_path / "user_state.json").write_text(
        json.dumps({"repos": [], "settings": {"outputDir": str(root)}}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_根与配置真源不符拒绝(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path)
    root = tmp_path / "works"
    root.mkdir()
    _configure_root(tmp_path, root)
    other = tmp_path / "elsewhere"
    other.mkdir()

    with pytest.raises(Exception) as exc:
        sync_markers(SyncMarkersRequest(output_dir=str(other)))

    assert "仓库文件夹" in str(exc.value)


def test_未配置根拒绝(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path)
    target = tmp_path / "somewhere"
    target.mkdir()

    with pytest.raises(Exception) as exc:
        sync_markers(SyncMarkersRequest(output_dir=str(target)))

    assert "仓库文件夹" in str(exc.value)


def test_根匹配时正常扫描_非仓库目录跳过(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path)
    root = tmp_path / "works"
    root.mkdir()
    (root / "not-a-repo-folder").mkdir()
    _configure_root(tmp_path, root)

    assert sync_markers(SyncMarkersRequest(output_dir=str(root))) == {"written": 0}
