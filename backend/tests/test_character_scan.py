"""散装卡扫描入库：手动丢进角色卡文件夹的 .json/.png → 解析入库 + 拆世界书/正则 + 删源。"""
from __future__ import annotations

import json

from app.services import character_store as cs


def _loose_card(name: str, *, with_book: bool = False, with_regex: bool = False) -> bytes:
    """构造一张 V2 JSON 卡（可带内嵌世界书/正则），返回字节。"""
    data: dict = {"name": name, "description": f"{name} 的设定"}
    if with_book:
        data["character_book"] = {"entries": [{"keys": ["城"], "content": "王都设定"}]}
    if with_regex:
        data["extensions"] = {"regex_scripts": [{"scriptName": "清洗", "findRegex": "/x/g", "replaceString": ""}]}
    payload = {"spec": "chara_card_v2", "spec_version": "2.0", "data": data}
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def test_散装json入库并拆世界书正则删源(tmp_path):
    base = str(tmp_path)
    loose = tmp_path / "露娜.json"
    loose.write_bytes(_loose_card("露娜", with_book=True, with_regex=True))

    res = cs.scan_loose_cards(base)

    assert res["imported"] == ["露娜"]
    assert not loose.exists()                                  # 源文件已删
    folder = cs.card_dir(base, "露娜")
    assert (folder / cs.CARD_FILE).is_file()
    assert (folder / cs.WORLDBOOK_FILE).is_file()              # 世界书一并导入
    assert (folder / cs.REGEX_FILE).is_file()                  # 正则一并导入
    # 扫描后列表可见（问题1：刷新即出现）
    assert any(c.name == "露娜" for c in cs.list_cards(base))


def test_已存在同名卡跳过不覆盖不删源(tmp_path):
    base = str(tmp_path)
    # 先正常入库一张
    (tmp_path / "凯.json").write_bytes(_loose_card("凯"))
    cs.scan_loose_cards(base)
    # 再丢一张同名散装卡
    dup = tmp_path / "凯-copy.json"
    dup.write_bytes(_loose_card("凯"))

    res = cs.scan_loose_cards(base)

    assert res["imported"] == [] and res["skipped"] == ["凯-copy.json"]
    assert dup.exists()  # 同名跳过 → 保留源文件供用户处置


def test_非卡文件不动不算导入(tmp_path):
    base = str(tmp_path)
    junk = tmp_path / "readme.json"
    junk.write_text("{ not a card", encoding="utf-8")

    res = cs.scan_loose_cards(base)

    assert res["imported"] == [] and junk.exists()
    assert "readme.json" in res["failed"]


def test_子目录卡文件夹不被当散装处理(tmp_path):
    base = str(tmp_path)
    # 已有标准卡文件夹（含 card.json）不应被扫描逻辑重复处理
    (tmp_path / "米拉.json").write_bytes(_loose_card("米拉"))
    cs.scan_loose_cards(base)
    folder = cs.card_dir(base, "米拉")
    assert (folder / cs.CARD_FILE).is_file()

    res = cs.scan_loose_cards(base)  # 再扫一次
    assert res == {"imported": [], "skipped": [], "failed": []}  # 无散装文件，什么都不做


def test_无目录返回空(tmp_path):
    res = cs.scan_loose_cards(str(tmp_path / "不存在"))
    assert res == {"imported": [], "skipped": [], "failed": []}
