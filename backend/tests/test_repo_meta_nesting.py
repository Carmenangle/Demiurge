"""子仓库嵌到父作品文件夹下，修复同名子仓库（SAVE01）互相覆盖对话/图片的 bug。

背景：卡作品的对话子仓库都叫 "SAVE01"，旧逻辑按仓库名建文件夹 → 九天神女传/SAVE01
与 神权大陆/SAVE01 撞进同一 outputDir/SAVE01/，后写的覆盖先写的。改为嵌套后
outputDir/<父名>/<子名>/，父名=卡名唯一，不再撞。
"""
from __future__ import annotations

from app.services import repo_meta


def _state(*repos: dict) -> dict:
    return {"repos": list(repos)}


def test_同名子仓库嵌套后文件夹段不再冲突(monkeypatch):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九天神女传"},
        {"id": "c1", "name": "SAVE01", "parentId": "p1"},
        {"id": "p2", "name": "神权大陆"},
        {"id": "c2", "name": "SAVE01", "parentId": "p2"},
    ))
    seg1 = repo_meta.folder_name("c1")
    seg2 = repo_meta.folder_name("c2")
    assert seg1 == "九天神女传/SAVE01"
    assert seg2 == "神权大陆/SAVE01"
    assert seg1 != seg2  # 核心：不再撞


def test_父仓库与无父仓库保持单段(monkeypatch):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九天神女传"},
        {"id": "solo", "name": "独立作品"},
    ))
    assert repo_meta.folder_name("p1") == "九天神女传"
    assert repo_meta.folder_name("solo") == "独立作品"


def test_repo_folder_path_嵌套建到父下(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九天神女传"},
        {"id": "c1", "name": "SAVE01", "parentId": "p1"},
    ))
    p = repo_meta.repo_folder_path(str(tmp_path), "c1")
    assert p == tmp_path / "九天神女传" / "SAVE01"


def test_惰性迁移_UUID旧文件夹搬到嵌套位置(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九天神女传"},
        {"id": "ef9e4bad", "name": "SAVE01", "parentId": "p1"},
    ))
    out = tmp_path
    old = out / "ef9e4bad"          # 旧 UUID 文件夹
    old.mkdir()
    (old / "chat.json").write_text('[{"id":"九天"}]', encoding="utf-8")
    dst = repo_meta.migrate_legacy_folder(str(out), "ef9e4bad")
    assert dst == out / "九天神女传" / "SAVE01"
    assert (dst / "chat.json").read_text(encoding="utf-8") == '[{"id":"九天"}]'
    assert not old.exists()          # 旧位置已搬走


def test_惰性迁移_扁平文件夹带本repo标记才搬(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九天神女传"},
        {"id": "c1", "name": "SAVE01", "parentId": "p1"},
    ))
    out = tmp_path
    flat = out / "SAVE01"
    flat.mkdir()
    (flat / "chat.json").write_text('[{"id":"x"}]', encoding="utf-8")
    (flat / "_repo.json").write_text('{"id":"c1","name":"SAVE01"}', encoding="utf-8")
    dst = repo_meta.migrate_legacy_folder(str(out), "c1")
    assert dst == out / "九天神女传" / "SAVE01"
    assert (dst / "chat.json").is_file()
    assert not flat.exists()


def test_惰性迁移_无标记扁平文件夹不搬_防歧义(monkeypatch, tmp_path):
    # 扁平 SAVE01 无 _repo.json（可能属另一张卡），保守不搬，避免误认
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九天神女传"},
        {"id": "c1", "name": "SAVE01", "parentId": "p1"},
    ))
    out = tmp_path
    flat = out / "SAVE01"
    flat.mkdir()
    (flat / "chat.json").write_text('[{"id":"歧义"}]', encoding="utf-8")
    dst = repo_meta.migrate_legacy_folder(str(out), "c1")
    assert dst == out / "九天神女传" / "SAVE01"
    assert not dst.exists()          # 未创建（无内容可搬）
    assert flat.exists()             # 原扁平文件夹留原地


def test_惰性迁移_已在新位置则不动(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九天神女传"},
        {"id": "c1", "name": "SAVE01", "parentId": "p1"},
    ))
    out = tmp_path
    nested = out / "九天神女传" / "SAVE01"
    nested.mkdir(parents=True)
    (nested / "chat.json").write_text('[{"id":"已就位"}]', encoding="utf-8")
    dst = repo_meta.migrate_legacy_folder(str(out), "c1")
    assert dst == nested
    assert (dst / "chat.json").read_text(encoding="utf-8") == '[{"id":"已就位"}]'


def test_delete_folder_优先删嵌套位置(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九天神女传"},
        {"id": "c1", "name": "SAVE01", "parentId": "p1"},
    ))
    out = tmp_path
    nested = out / "九天神女传" / "SAVE01"
    nested.mkdir(parents=True)
    (nested / "chat.json").write_text("[]", encoding="utf-8")
    res = repo_meta.delete_folder(str(out), "c1", "SAVE01")
    assert res["deleted"] is True
    assert not nested.exists()


def test_rename_子仓库_嵌套内改末段(monkeypatch, tmp_path):
    # 改子仓库名 SAVE01→存档甲，父段（九天神女传）不变
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九天神女传"},
        {"id": "c1", "name": "存档甲", "parentId": "p1"},  # 状态已是新名（前端先改）
    ))
    out = tmp_path
    src = out / "九天神女传" / "SAVE01"
    src.mkdir(parents=True)
    (src / "chat.json").write_text("[]", encoding="utf-8")
    res = repo_meta.rename_folder(str(out), "c1", "SAVE01", "存档甲")
    assert res["folder"] == "renamed"
    assert (out / "九天神女传" / "存档甲" / "chat.json").is_file()
    assert not src.exists()
