"""子仓库嵌到父作品文件夹下，修复同名子仓库（SAVE01）互相覆盖对话/图片的 bug。

背景：卡作品的对话子仓库都叫 "SAVE01"，旧逻辑按仓库名建文件夹 → 九霄仙纪/SAVE01
与 神恩大陆/SAVE01 撞进同一 outputDir/SAVE01/，后写的覆盖先写的。改为嵌套后
outputDir/<父名>/<子名>/，父名=卡名唯一，不再撞。
"""
from __future__ import annotations

from urllib.parse import quote

from app.services import repo_meta


def _state(*repos: dict) -> dict:
    return {"repos": list(repos)}


def _write_chat(folder, image_url: str = "") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    body = f'[{{"image_url":"{image_url}"}}]' if image_url else '[{"text":"对话"}]'
    (folder / "chat.json").write_text(body, encoding="utf-8")


def _write_marker(folder, repo_id: str, name: str = "") -> None:
    (folder / "_repo.json").write_text(
        f'{{"id":"{repo_id}","name":"{name}"}}', encoding="utf-8")


def test_同名子仓库嵌套后文件夹段不再冲突(monkeypatch):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九霄仙纪"},
        {"id": "c1", "name": "SAVE01", "parentId": "p1"},
        {"id": "p2", "name": "神恩大陆"},
        {"id": "c2", "name": "SAVE01", "parentId": "p2"},
    ))
    seg1 = repo_meta.folder_name("c1")
    seg2 = repo_meta.folder_name("c2")
    assert seg1 == "九霄仙纪/SAVE01"
    assert seg2 == "神恩大陆/SAVE01"
    assert seg1 != seg2  # 核心：不再撞


def test_父仓库与无父仓库保持单段(monkeypatch):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九霄仙纪"},
        {"id": "solo", "name": "独立作品"},
    ))
    assert repo_meta.folder_name("p1") == "九霄仙纪"
    assert repo_meta.folder_name("solo") == "独立作品"


def test_repo_folder_path_嵌套建到父下(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九霄仙纪"},
        {"id": "c1", "name": "SAVE01", "parentId": "p1"},
    ))
    p = repo_meta.repo_folder_path(str(tmp_path), "c1")
    assert p == tmp_path / "九霄仙纪" / "SAVE01"


def test_新同名仓库不复用旧仓库的会话目录(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "Anima"},
        {"id": "old-repo-id", "name": "傅知宁", "parentId": "p1"},
        {"id": "new-repo-id", "name": "简月晞", "parentId": "p1"},
    ))
    stale = tmp_path / "Anima" / "简月晞"
    stale.mkdir(parents=True)
    (stale / "_repo.json").write_text(
        '{"id":"old-repo-id","name":"简月晞"}', encoding="utf-8",
    )
    (stale / "chat.json").write_text('[{"text":"傅知宁的旧对话"}]', encoding="utf-8")

    resolved = repo_meta.migrate_legacy_folder(str(tmp_path), "new-repo-id")

    assert resolved != stale
    assert not (resolved / "chat.json").exists()


def test_改名后按marker找回旧目录并迁移(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "Anima"},
        {"id": "old-repo-id", "name": "傅知宁", "parentId": "p1"},
    ))
    stale = tmp_path / "Anima" / "简月晞"
    stale.mkdir(parents=True)
    (stale / "_repo.json").write_text(
        '{"id":"old-repo-id","name":"简月晞"}', encoding="utf-8",
    )
    (stale / "chat.json").write_text('[{"text":"保留的旧对话"}]', encoding="utf-8")

    resolved = repo_meta.migrate_legacy_folder(str(tmp_path), "old-repo-id")

    assert resolved == tmp_path / "Anima" / "傅知宁"
    assert (resolved / "chat.json").is_file()
    assert not stale.exists()


def test_仓库暂时不在状态列表时仍按marker归档到原目录(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state())
    owned = tmp_path / "白露谷" / "SAVE01"
    owned.mkdir(parents=True)
    (owned / "_repo.json").write_text(
        '{"id":"old-repo-id","name":"SAVE01"}', encoding="utf-8",
    )

    resolved = repo_meta.repo_folder_path(str(tmp_path), "old-repo-id")

    assert resolved == owned


def test_惰性迁移_UUID旧文件夹搬到嵌套位置(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九霄仙纪"},
        {"id": "ef9e4bad", "name": "SAVE01", "parentId": "p1"},
    ))
    out = tmp_path
    old = out / "ef9e4bad"          # 旧 UUID 文件夹
    old.mkdir()
    (old / "chat.json").write_text('[{"id":"九天"}]', encoding="utf-8")
    dst = repo_meta.migrate_legacy_folder(str(out), "ef9e4bad")
    assert dst == out / "九霄仙纪" / "SAVE01"
    assert (dst / "chat.json").read_text(encoding="utf-8") == '[{"id":"九天"}]'
    assert not old.exists()          # 旧位置已搬走


def test_惰性迁移_扁平文件夹带本repo标记才搬(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九霄仙纪"},
        {"id": "c1", "name": "SAVE01", "parentId": "p1"},
    ))
    out = tmp_path
    flat = out / "SAVE01"
    flat.mkdir()
    (flat / "chat.json").write_text('[{"id":"x"}]', encoding="utf-8")
    (flat / "_repo.json").write_text('{"id":"c1","name":"SAVE01"}', encoding="utf-8")
    dst = repo_meta.migrate_legacy_folder(str(out), "c1")
    assert dst == out / "九霄仙纪" / "SAVE01"
    assert (dst / "chat.json").is_file()
    assert not flat.exists()


def test_惰性迁移_无标记扁平文件夹不搬_防歧义(monkeypatch, tmp_path):
    # 扁平 SAVE01 无 _repo.json（可能属另一张卡），保守不搬，避免误认
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九霄仙纪"},
        {"id": "c1", "name": "SAVE01", "parentId": "p1"},
    ))
    out = tmp_path
    flat = out / "SAVE01"
    flat.mkdir()
    (flat / "chat.json").write_text('[{"id":"歧义"}]', encoding="utf-8")
    dst = repo_meta.migrate_legacy_folder(str(out), "c1")
    assert dst == out / "九霄仙纪" / "SAVE01"
    assert not dst.exists()          # 未创建（无内容可搬）
    assert flat.exists()             # 原扁平文件夹留原地


def test_惰性迁移_已在新位置则不动(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九霄仙纪"},
        {"id": "c1", "name": "SAVE01", "parentId": "p1"},
    ))
    out = tmp_path
    nested = out / "九霄仙纪" / "SAVE01"
    nested.mkdir(parents=True)
    (nested / "chat.json").write_text('[{"id":"已就位"}]', encoding="utf-8")
    dst = repo_meta.migrate_legacy_folder(str(out), "c1")
    assert dst == nested
    assert (dst / "chat.json").read_text(encoding="utf-8") == '[{"id":"已就位"}]'


def test_delete_folder_优先删嵌套位置(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九霄仙纪"},
        {"id": "c1", "name": "SAVE01", "parentId": "p1"},
    ))
    out = tmp_path
    nested = out / "九霄仙纪" / "SAVE01"
    nested.mkdir(parents=True)
    (nested / "chat.json").write_text("[]", encoding="utf-8")
    (nested / "_repo.json").write_text('{"id":"c1"}', encoding="utf-8")
    res = repo_meta.delete_folder(str(out), "c1", "SAVE01")
    assert res["deleted"] is True
    assert not nested.exists()


def test_rename_子仓库_嵌套内改末段(monkeypatch, tmp_path):
    # 改子仓库名 SAVE01→存档甲，父段（九霄仙纪）不变
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九霄仙纪"},
        {"id": "c1", "name": "存档甲", "parentId": "p1"},  # 状态已是新名（前端先改）
    ))
    out = tmp_path
    src = out / "九霄仙纪" / "SAVE01"
    src.mkdir(parents=True)
    (src / "chat.json").write_text("[]", encoding="utf-8")
    res = repo_meta.rename_folder(str(out), "c1", "SAVE01", "存档甲")
    assert res["folder"] == "renamed"
    assert (out / "九霄仙纪" / "存档甲" / "chat.json").is_file()
    assert not src.exists()


# ── 改名身份解析：UUID 标记是唯一身份，名字只是候选提示 ──────────────────────


def test_rename_清洗碰撞的旧名指向他仓目录时拒绝(monkeypatch, tmp_path):
    # A:B 与 A?B 清洗后同为 A_B。旧实现按名拼路径，会把 b1 的文件夹整体挪走。
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "a1", "name": "新名"},
        {"id": "b1", "name": "A?B"},
    ))
    out = tmp_path
    foreign = out / "A_B"
    _write_chat(foreign)
    _write_marker(foreign, "b1", "A?B")

    res = repo_meta.rename_folder(str(out), "a1", "A:B", "新名")

    assert res["folder"] != "renamed"
    assert res["error"]
    assert foreign.is_dir()
    assert repo_meta._marker_repo_id(foreign) == "b1"
    assert (foreign / "chat.json").read_text(encoding="utf-8") == '[{"text":"对话"}]'
    assert not (out / "新名").exists()


def test_rename_无标记旧目录唯一时仍可改并补标记(monkeypatch, tmp_path):
    # 历史遗留目录没有 _repo.json：仅一个候选时才允许按名迁移（改名非破坏性）。
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九霄仙纪"},
        {"id": "c1", "name": "存档甲", "parentId": "p1"},
    ))
    out = tmp_path
    src = out / "九霄仙纪" / "SAVE01"
    _write_chat(src)

    res = repo_meta.rename_folder(str(out), "c1", "SAVE01", "存档甲")

    dst = out / "九霄仙纪" / "存档甲"
    assert res["folder"] == "renamed"
    assert res["error"] == ""
    assert (dst / "chat.json").is_file()
    assert repo_meta._marker_repo_id(dst) == "c1"
    assert not src.exists()


def test_rename_多个无标记候选时拒绝且不重写路径(monkeypatch, tmp_path):
    # 嵌套位与扁平位各有一个无标记同名目录 ⇒ 无法判定归属，宁可拒绝也不猜。
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九霄仙纪"},
        {"id": "c1", "name": "存档甲", "parentId": "p1"},
    ))
    out = tmp_path
    nested = out / "九霄仙纪" / "旧名"
    flat = out / "旧名"
    _write_chat(nested)
    _write_chat(flat)

    res = repo_meta.rename_folder(str(out), "c1", "旧名", "存档甲")

    assert res["folder"] == "ambiguous"
    assert res["error"]
    assert nested.is_dir() and flat.is_dir()
    assert not (out / "九霄仙纪" / "存档甲").exists()
    assert (nested / "chat.json").read_text(encoding="utf-8") == '[{"text":"对话"}]'
    assert (flat / "chat.json").read_text(encoding="utf-8") == '[{"text":"对话"}]'


def test_rename_源缺失不覆盖他仓目标标记(monkeypatch, tmp_path):
    # 旧实现 src 不存在时会走 elif dst.is_dir() 分支，把他仓 _repo.json 改成自己。
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九霄仙纪"},
        {"id": "c1", "name": "新名", "parentId": "p1"},
        {"id": "b1", "name": "别家", "parentId": "p1"},
    ))
    out = tmp_path
    foreign = out / "九霄仙纪" / "新名"
    _write_chat(foreign)
    _write_marker(foreign, "b1", "别家")

    res = repo_meta.rename_folder(str(out), "c1", "旧名", "新名")

    assert res["folder"] != "renamed"
    assert res["error"]
    assert repo_meta._marker_repo_id(foreign) == "b1"
    assert (foreign / "chat.json").read_text(encoding="utf-8") == '[{"text":"对话"}]'


def test_rename_目标已存在时不重写快照路径(monkeypatch, tmp_path):
    # 目标已存在 ⇒ 没有发生搬移，绝不能拿 dst 当「新快照位置」去改别人的 chat.json。
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九霄仙纪"},
        {"id": "c1", "name": "新名", "parentId": "p1"},
    ))
    out = tmp_path
    old_seg = quote(f"{tmp_path}\\九霄仙纪\\旧名\\")
    src = out / "九霄仙纪" / "旧名"
    _write_chat(src, image_url=f"{old_seg}img.png")
    _write_marker(src, "c1", "旧名")
    dst = out / "九霄仙纪" / "新名"
    _write_chat(dst, image_url=f"{old_seg}img.png")
    before = (dst / "chat.json").read_text(encoding="utf-8")

    res = repo_meta.rename_folder(str(out), "c1", "旧名", "新名")

    assert res["folder"] == "target_exists"
    assert res["error"]
    assert res["snapshot"] == 0
    assert (dst / "chat.json").read_text(encoding="utf-8") == before
    assert src.is_dir()


def test_rename_成功后重写快照与marker(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九霄仙纪"},
        {"id": "c1", "name": "存档甲", "parentId": "p1"},
    ))
    out = tmp_path
    old_seg = quote(f"{tmp_path}\\九霄仙纪\\旧名\\")
    new_seg = quote(f"{tmp_path}\\九霄仙纪\\存档甲\\")
    src = out / "九霄仙纪" / "旧名"
    _write_chat(src, image_url=f"{old_seg}img.png")
    _write_marker(src, "c1", "旧名")

    res = repo_meta.rename_folder(str(out), "c1", "旧名", "存档甲")

    dst = out / "九霄仙纪" / "存档甲"
    body = (dst / "chat.json").read_text(encoding="utf-8")
    assert res["folder"] == "renamed"
    assert res["error"] == ""
    assert res["snapshot"] >= 1
    assert new_seg in body and old_seg not in body
    assert repo_meta._marker_repo_id(dst) == "c1"
    assert not src.exists()


def test_rename_仓库根自身带标记时拒绝搬根(monkeypatch, tmp_path):
    # _find_marked_folder 若把 output_dir 根当成「本仓目录」，搬根会整个挪走作品库。
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "r1", "name": "新名"},
    ))
    out = tmp_path
    _write_chat(out)
    _write_marker(out, "r1", "旧名")

    res = repo_meta.rename_folder(str(out), "r1", "旧名", "新名")

    assert res["folder"] != "renamed"
    assert res["error"]
    assert out.is_dir()
    assert (out / "chat.json").is_file()
    assert not (out / "新名").exists()


def test_rename_目标被他仓UUID占用时改到隔离目录(monkeypatch, tmp_path):
    # A:B 与 A?B 同父同清洗名：b1 已占 A_B，c1 改名必须落到带 UUID 后缀的隔离目录。
    monkeypatch.setattr(repo_meta, "_load_state", lambda: _state(
        {"id": "p1", "name": "九霄仙纪"},
        {"id": "c1", "name": "A?B", "parentId": "p1"},
        {"id": "b1", "name": "A:B", "parentId": "p1"},
    ))
    out = tmp_path
    foreign = out / "九霄仙纪" / "A_B"
    _write_chat(foreign)
    _write_marker(foreign, "b1", "A:B")
    src = out / "九霄仙纪" / "旧名"
    _write_chat(src)
    _write_marker(src, "c1", "旧名")

    res = repo_meta.rename_folder(str(out), "c1", "旧名", "A?B")

    isolated = out / "九霄仙纪" / "A_B [c1]"
    assert res["folder"] == "renamed"
    assert isolated.is_dir()
    assert repo_meta._marker_repo_id(isolated) == "c1"
    assert repo_meta._marker_repo_id(foreign) == "b1"
    assert not src.exists()
