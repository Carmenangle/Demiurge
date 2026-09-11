"""作品仓库卡快照：新建作品时把源库卡+世界书+正则快照进作品文件夹，运行时快照优先读。

要点：快照隔离（改源卡不回灌已建作品）、幂等不覆盖、无卡回退源库。
"""
from __future__ import annotations

import json

from app.services import character_store as cs


def _seed_source_card(base, name, *, book=True, regex=True):
    """在源库落一张标准卡（含世界书/正则），返回卡文件夹。"""
    folder = cs.card_dir(base, name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / cs.CARD_FILE).write_text(
        json.dumps({"name": name, "description": f"{name}的设定"}, ensure_ascii=False), encoding="utf-8")
    if book:
        (folder / cs.WORLDBOOK_FILE).write_text(
            json.dumps({"entries": [{"keys": ["城"], "content": "王都"}]}, ensure_ascii=False), encoding="utf-8")
    if regex:
        (folder / cs.REGEX_FILE).write_text(
            json.dumps([{"scriptName": "清洗", "findRegex": "/x/g", "replaceString": ""}], ensure_ascii=False),
            encoding="utf-8")
    return folder


def test_快照拷卡世界书正则(tmp_path):
    src = str(tmp_path / "源库")
    out = str(tmp_path / "作品库")
    _seed_source_card(src, "露娜")
    work_folder = str(cs.card_dir(out, "露娜"))  # 父作品文件夹=卡名

    assert cs.snapshot_to_work(src, "露娜", work_folder) is True
    snap = cs.card_dir(cs.card_dir(out, "露娜") / cs.WORK_CARD_SUBDIR, "露娜")
    assert (snap / cs.CARD_FILE).is_file()
    assert (snap / cs.WORLDBOOK_FILE).is_file()
    assert (snap / cs.REGEX_FILE).is_file()


def test_头像表情可定制且随作品快照(tmp_path):
    src = str(tmp_path / "源库")
    out = str(tmp_path / "作品库")
    _seed_source_card(src, "露娜")
    cs.write_avatar(src, "露娜", b"avatar")
    cs.write_expression(src, "露娜", "愤怒.png", b"angry")

    assert cs.list_expressions(src, "露娜") == [{"name": "愤怒", "file": "愤怒.png"}]
    assert cs.snapshot_to_work(src, "露娜", str(cs.card_dir(out, "露娜"))) is True
    snap = cs.card_dir(cs.card_dir(out, "露娜") / cs.WORK_CARD_SUBDIR, "露娜")
    assert (snap / cs.AVATAR_FILE).read_bytes() == b"avatar"
    assert (snap / cs.EXPRESSIONS_DIR / "愤怒.png").read_bytes() == b"angry"


def test_多张绑定卡快照到当前小仓库(tmp_path, monkeypatch):
    from app.services import repo_meta

    src = str(tmp_path / "源库")
    repo_folder = tmp_path / "作品" / "SAVE01"
    _seed_source_card(src, "露娜")
    _seed_source_card(src, "米拉")
    monkeypatch.setattr(repo_meta, "repo_folder", lambda _output, _repo: repo_folder)

    result = cs.snapshot_cards_to_repo(src, ["露娜", "米拉"], str(tmp_path / "输出"), "save-1")

    assert result == {"created": ["露娜", "米拉"], "existing": [], "missing": []}
    assert cs.read_card(str(repo_folder / cs.WORK_CARD_SUBDIR), "露娜")["description"] == "露娜的设定"
    assert cs.read_card(str(repo_folder / cs.WORK_CARD_SUBDIR), "米拉")["description"] == "米拉的设定"


def test_当前小仓库角色卡优先于父作品快照(tmp_path, monkeypatch):
    from app.services import repo_meta

    folder = tmp_path / "作品" / "SAVE01"
    parent_base = folder.parent / cs.WORK_CARD_SUBDIR
    current_base = folder / cs.WORK_CARD_SUBDIR
    _seed_source_card(str(parent_base), "露娜")
    _seed_source_card(str(current_base), "露娜")
    (cs.card_dir(current_base, "露娜") / cs.CARD_FILE).write_text(
        json.dumps({"name": "露娜", "description": "当前小仓库版本"}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(repo_meta, "repo_folder_path", lambda _output, _repo: folder)
    monkeypatch.setattr(repo_meta, "parent_folder_seg", lambda _repo: "作品")

    base = cs.repo_card_base(str(tmp_path), "save-1", "露娜")

    assert base == str(current_base)
    assert cs.read_card(base, "露娜")["description"] == "当前小仓库版本"


def test_角色卡正文可编辑且空描述可保存(tmp_path):
    base = str(tmp_path / "角色卡")
    folder = _seed_source_card(base, "露娜")
    card_path = folder / cs.CARD_FILE
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card.update({"first_mes": "旧开场", "creator_notes": "旧注释", "extensions": {"keep": True}})
    card_path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")

    updated = cs.update_card_fields(base, "露娜", {
        "description": "", "first_mes": "新开场", "creator_notes": "新注释", "name": "不可改名",
    })

    assert updated["name"] == "露娜"
    assert updated["description"] == ""
    assert updated["first_mes"] == "新开场"
    assert updated["creator_notes"] == "新注释"
    assert updated["extensions"] == {"keep": True}


def test_幂等不覆盖保隔离(tmp_path):
    src = str(tmp_path / "源库")
    out = str(tmp_path / "作品库")
    _seed_source_card(src, "凯", book=False, regex=False)
    work_folder = str(cs.card_dir(out, "凯"))
    assert cs.snapshot_to_work(src, "凯", work_folder) is True

    # 改源卡后再快照 → 不覆盖（隔离），快照仍是旧内容
    (cs.card_dir(src, "凯") / cs.CARD_FILE).write_text(
        json.dumps({"name": "凯", "description": "改过的"}, ensure_ascii=False), encoding="utf-8")
    assert cs.snapshot_to_work(src, "凯", work_folder) is False
    snap = cs.card_dir(cs.card_dir(out, "凯") / cs.WORK_CARD_SUBDIR, "凯")
    got = json.loads((snap / cs.CARD_FILE).read_text(encoding="utf-8"))
    assert got["description"] == "凯的设定"  # 旧快照未被改源回灌


def test_源无卡返回False(tmp_path):
    out = str(tmp_path / "作品库")
    assert cs.snapshot_to_work(str(tmp_path / "空源库"), "无", str(cs.card_dir(out, "无"))) is False


def test_work_card_base命中与回退(tmp_path):
    src = str(tmp_path / "源库")
    out = str(tmp_path / "作品库")
    _seed_source_card(src, "米拉")
    # 未快照 → None
    assert cs.work_card_base(out, "米拉") is None
    # 快照后 → 命中，且路径下可被 read_card 读到
    cs.snapshot_to_work(src, "米拉", str(cs.card_dir(out, "米拉")))
    base = cs.work_card_base(out, "米拉")
    assert base is not None
    assert cs.read_card(base, "米拉")["name"] == "米拉"


def test_缺参空值安全(tmp_path):
    assert cs.snapshot_to_work("", "露娜", str(tmp_path)) is False
    assert cs.work_card_base("", "露娜") is None
    assert cs.work_card_base(str(tmp_path), "") is None


def test_人设快照写入与读回(tmp_path):
    out = str(tmp_path / "作品库")
    assert cs.snapshot_persona_to_work(out, "露娜", "旅人", "我是一名旅行者") is True
    got = cs.read_work_persona(out, "露娜")
    assert got == {"name": "旅人", "content": "我是一名旅行者"}


def test_人设快照幂等不覆盖(tmp_path):
    out = str(tmp_path / "作品库")
    assert cs.snapshot_persona_to_work(out, "凯", "旅人", "初版") is True
    # 改设置里的人设后再快照 → 不覆盖（隔离），快照仍是旧内容
    assert cs.snapshot_persona_to_work(out, "凯", "别的名", "改过的") is False
    assert cs.read_work_persona(out, "凯")["content"] == "初版"


def test_人设快照空人设不写(tmp_path):
    out = str(tmp_path / "作品库")
    assert cs.snapshot_persona_to_work(out, "米拉", "", "") is False
    assert cs.read_work_persona(out, "米拉") is None
