"""`/delete-folder` 的作品根校验 + 「删不净必须可见」（2026-11-05「防误删」审计第 ② 刀）。

改前两处缺口都在同一条链上：
  1. 端点把客户端报上来的 `output_dir` 直接喂给 `rmtree`，没过 `works_root_violation`
     ——同面的 `sync-markers` 早在 2026-10-24 补齐，这条是漏网；
  2. `shutil.rmtree(..., ignore_errors=True)` 把「文件被占用」吞掉，函数照样回
     `deleted=True`，用户以为作品清干净了，库里其实剩半套。
"""
from __future__ import annotations

import pytest

from app.routers.user_state import (
    DeleteFolderRequest,
    RenameRequest,
    delete_folder,
    rename_folder,
)
from app.services import repo_meta


def _wire(monkeypatch, tmp_path, root, *, configured=True):
    """钉住读侧接缝：`_load_state` 是 output_dir 与父子关系的单一真源。"""
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(repo_meta, "_load_state", lambda: {
        "repos": [{"id": "p1", "name": "作品甲"},
                  {"id": "c1", "name": "存档乙", "parentId": "p1"}],
        "settings": {"outputDir": str(root) if configured else ""},
    })


def _nested_repo_folder(root):
    folder = root / "作品甲" / "存档乙"
    folder.mkdir(parents=True)
    (folder / "chat.json").write_text('[{"id":"m1"}]', encoding="utf-8")
    (folder / "_repo.json").write_text('{"id":"c1"}', encoding="utf-8")
    return folder


def test_根与配置真源不符拒绝且一个文件都没删(tmp_path, monkeypatch):
    root = tmp_path / "works"
    root.mkdir()
    _wire(monkeypatch, tmp_path, root)
    elsewhere = tmp_path / "elsewhere"
    victim = elsewhere / "重要目录"
    victim.mkdir(parents=True)
    (victim / "keep.txt").write_text("别删", encoding="utf-8")

    with pytest.raises(Exception) as exc:
        delete_folder(DeleteFolderRequest(repo_id="r", name="重要目录", output_dir=str(elsewhere)))

    assert "仓库文件夹" in str(exc.value)
    assert (victim / "keep.txt").read_text(encoding="utf-8") == "别删"


def test_未配置仓库文件夹拒绝(tmp_path, monkeypatch):
    root = tmp_path / "works"
    root.mkdir()
    _wire(monkeypatch, tmp_path, root, configured=False)
    folder = _nested_repo_folder(root)

    with pytest.raises(Exception) as exc:
        delete_folder(DeleteFolderRequest(repo_id="c1", name="存档乙", output_dir=str(root)))

    assert "仓库文件夹" in str(exc.value)
    assert folder.is_dir()


def test_根匹配时删掉作品自有文件夹(tmp_path, monkeypatch):
    root = tmp_path / "works"
    _wire(monkeypatch, tmp_path, root)
    folder = _nested_repo_folder(root)

    got = delete_folder(DeleteFolderRequest(repo_id="c1", name="存档乙", output_dir=str(root)))

    assert got == {"deleted": True, "folder": "存档乙", "error": ""}
    assert not folder.exists()
    assert (root / "作品甲").is_dir()          # 只删子作品，父目录留着


def test_漏传根时按配置根照删而不是静默跳过(tmp_path, monkeypatch):
    root = tmp_path / "works"
    _wire(monkeypatch, tmp_path, root)
    folder = _nested_repo_folder(root)

    got = delete_folder(DeleteFolderRequest(repo_id="c1", name="存档乙"))

    assert got["deleted"] is True
    assert not folder.exists()


def test_删不净时把失败回给用户而不是报成功(tmp_path, monkeypatch):
    root = tmp_path / "works"
    _wire(monkeypatch, tmp_path, root)
    folder = _nested_repo_folder(root)

    def _locked(*args, **kwargs):
        raise PermissionError(32, "另一个程序正在使用此文件")

    monkeypatch.setattr("shutil.rmtree", _locked)
    got = repo_meta.delete_folder(str(root), "c1", "存档乙")

    assert got["deleted"] is False
    assert "被占用" in got["error"]
    assert folder.is_dir()                      # 删失败 ⇒ 目录还在，用户能重试


def test_没有作品文件夹时不算失败(tmp_path, monkeypatch):
    root = tmp_path / "works"
    root.mkdir()
    _wire(monkeypatch, tmp_path, root)

    got = repo_meta.delete_folder(str(root), "c1", "存档乙")

    assert got == {"deleted": False, "folder": "missing", "error": ""}


def test_名称清洗碰撞只删除对应UUID目录(tmp_path, monkeypatch):
    root = tmp_path / "works"
    monkeypatch.setattr(repo_meta, "_load_state", lambda: {
        "repos": [{"id": "first", "name": "A:B"}, {"id": "second", "name": "A?B"}],
        "settings": {"outputDir": str(root)},
    })
    first = repo_meta.repo_folder(str(root), "first")
    second = repo_meta.repo_folder(str(root), "second")
    (first / "chat.json").write_text("保留第一份", encoding="utf-8")
    assert first != second

    got = delete_folder(DeleteFolderRequest(repo_id="second", name="A?B", output_dir=str(root)))

    assert got["deleted"] is True
    assert (first / "chat.json").read_text(encoding="utf-8") == "保留第一份"
    assert not second.exists()


def test_请求名称不能顶替UUID归属(tmp_path, monkeypatch):
    root = tmp_path / "works"
    _wire(monkeypatch, tmp_path, root)
    owned = _nested_repo_folder(root)
    other = root / "作品甲" / "别人的目录"
    other.mkdir()
    (other / "_repo.json").write_text('{"id":"other"}', encoding="utf-8")

    got = repo_meta.delete_folder(str(root), "c1", "别人的目录")

    assert got["deleted"] is True
    assert not owned.exists()
    assert other.is_dir()


def test_索引已移除仍按marker找回隔离目录(tmp_path, monkeypatch):
    root = tmp_path / "works"
    owned = root / "父作品" / "旧名 [c1]"
    owned.mkdir(parents=True)
    (owned / "_repo.json").write_text('{"id":"c1"}', encoding="utf-8")
    monkeypatch.setattr(repo_meta, "_load_state", lambda: {"repos": []})

    got = repo_meta.delete_folder(str(root), "c1", "新名")

    assert got["deleted"] is True
    assert not owned.exists()


@pytest.mark.parametrize("marker", ['', '{"id":"other"}', '{broken'])
def test_无归属或他仓扁平目录不得回退删除(tmp_path, monkeypatch, marker):
    root = tmp_path / "works"
    _wire(monkeypatch, tmp_path, root)
    flat = root / "存档乙"
    flat.mkdir(parents=True)
    (flat / "chat.json").write_text("必须保留", encoding="utf-8")
    if marker:
        (flat / "_repo.json").write_text(marker, encoding="utf-8")

    got = repo_meta.delete_folder(str(root), "c1", "存档乙")

    assert got["deleted"] is False
    assert got["error"]
    assert (flat / "chat.json").read_text(encoding="utf-8") == "必须保留"


def test_无标记嵌套目录拒绝删除(tmp_path, monkeypatch):
    root = tmp_path / "works"
    _wire(monkeypatch, tmp_path, root)
    owned = _nested_repo_folder(root)
    (owned / "_repo.json").unlink()

    got = repo_meta.delete_folder(str(root), "c1", "存档乙")

    assert got["deleted"] is False
    assert got["error"]
    assert owned.is_dir()


def test_作品根自身即使带marker也不能删除(tmp_path, monkeypatch):
    root = tmp_path / "works"
    root.mkdir()
    (root / "_repo.json").write_text('{"id":"c1"}', encoding="utf-8")
    monkeypatch.setattr(repo_meta, "_load_state", lambda: {"repos": []})

    got = repo_meta.delete_folder(str(root), "c1", "旧名")

    assert got["deleted"] is False
    assert got["error"]
    assert root.is_dir()


# ── 第 ⑦ 刀（2026-11-05）：`/rename-folder` 与 `/delete-folder` 同一张面 ──────────


def test_第7刀_改名指错根拒绝且服务层一次没跑(tmp_path, monkeypatch):
    """改前 `output_dir` 直接进 `rename_folder`——除重命名外它还会**重写快照/RAG 里的图片路径段**，
    指错根的破坏面比删除更宽。守卫必须挡在调用服务层**之前**。
    """
    root = tmp_path / "works"
    (root / "作品甲").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    victim = elsewhere / "旧名"
    victim.mkdir(parents=True)
    called: list = []
    monkeypatch.setattr(repo_meta, "rename_folder",
                        lambda *a: called.append(a) or {"folder": "renamed"})
    _wire(monkeypatch, tmp_path, root)

    with pytest.raises(Exception) as exc:
        rename_folder(RenameRequest(repo_id="p1", old_name="旧名", new_name="新名",
                                    output_dir=str(elsewhere)))

    assert "仓库文件夹" in str(exc.value)
    assert called == []
    assert victim.is_dir() and not (elsewhere / "新名").exists()


def test_第7刀_改名同根放行到服务层(tmp_path, monkeypatch):
    """零回归：真实界面传的就是设置里那个「仓库文件夹」根 ⇒ 守卫不得挡掉正常改名。"""
    root = tmp_path / "works"
    root.mkdir()
    called = []
    monkeypatch.setattr(repo_meta, "rename_folder",
                        lambda *a: called.append(a) or {"folder": "renamed"})
    _wire(monkeypatch, tmp_path, root)

    got = rename_folder(RenameRequest(repo_id="p1", old_name="作品甲", new_name="作品乙",
                                      output_dir=str(root)))

    assert got == {"folder": "renamed"}
    assert len(called) == 1


def test_第7刀_改名未配置根时拒绝(tmp_path, monkeypatch):
    root = tmp_path / "works"
    root.mkdir()
    called = []
    monkeypatch.setattr(repo_meta, "rename_folder",
                        lambda *a: called.append(a) or {"folder": "renamed"})
    _wire(monkeypatch, tmp_path, root, configured=False)

    with pytest.raises(Exception) as exc:
        rename_folder(RenameRequest(repo_id="p1", old_name="作品甲", new_name="作品乙",
                                    output_dir=str(root)))

    assert "仓库文件夹" in str(exc.value)
    assert called == []
