"""⑤ 世界书条目级增删改（纯逻辑，dict/list 两种容器）。"""
from __future__ import annotations

import pytest

from app.services import worldbook_edit as we


def _list_book():
    return {"entries": [
        {"content": "常驻设定", "constant": True, "comment": "世界观", "keys": ["世界"]},
        {"content": "魔法体系", "comment": "魔法", "keys": ["魔法", "咒语"]},
    ]}


def _dict_book():
    return {"entries": {
        "0": {"uid": 0, "content": "常驻设定", "constant": True, "comment": "世界观"},
        "1": {"uid": 1, "content": "魔法体系", "comment": "魔法"},
    }}


def test_列出条目_list容器():
    items = we.list_entries(_list_book())
    assert len(items) == 2
    assert items[0]["index"] == 0 and items[0]["constant"] is True
    assert items[1]["keys"] == ["魔法", "咒语"]


def test_列出条目_enabled语义():
    book = {"entries": [
        {"content": "a", "enabled": False},
        {"content": "b", "disable": True},
        {"content": "c"},
    ]}
    items = we.list_entries(book)
    assert [e["enabled"] for e in items] == [False, False, True]


def test_新增条目_list追加():
    book = _list_book()
    idx = we.add_entry(book, {"content": "新条目", "keys": ["x"], "constant": False})
    assert idx == 2
    assert book["entries"][2]["content"] == "新条目"


def test_新增条目_dict新键():
    book = _dict_book()
    idx = we.add_entry(book, {"content": "新条目"})
    assert idx == 2
    # dict 容器保留：新键 uid=2
    assert "2" in book["entries"] and book["entries"]["2"]["content"] == "新条目"


def test_更新条目_改字段():
    book = _list_book()
    assert we.update_entry(book, 1, {"content": "改后的魔法", "enabled": False})
    assert book["entries"][1]["content"] == "改后的魔法"
    assert book["entries"][1]["enabled"] is False and book["entries"][1]["disable"] is True


def test_更新条目_越界返回False():
    assert we.update_entry(_list_book(), 9, {"content": "x"}) is False


def test_删除条目_list():
    book = _list_book()
    assert we.delete_entry(book, 0)
    assert len(book["entries"]) == 1 and book["entries"][0]["content"] == "魔法体系"


def test_删除条目_dict删对应键():
    book = _dict_book()
    assert we.delete_entry(book, 0)   # 删第一个（键 "0"）
    assert "0" not in book["entries"] and "1" in book["entries"]


def test_删除条目_越界返回False():
    assert we.delete_entry(_list_book(), 9) is False


# ── L1 写入闸：新增条目的重复检测（2026-10-22）────────────────────────────────
# 判据经真实世界书校准（堕落魔戒 69 条）：
#   ① 内容精确相同（归一化后）—— 逐字重复
#   ② 归一化共同前缀 ≥ 14 字 —— 同开头改写（韦尔纳真案例：16 字；合法对 0~6 字，全库仅 2 对 ≥12，
#      其中 12 字那对是边界假阳性 ⇒ 阈值取 14）
#   ③ comment 归一化后同名 —— 同标题重加
# ⛔ 不用字符相似度：真重复对 difflib=0.239 < 合法机制对 0.272（反序，校准否决）
# ⛔ 不用 keys[0] 单独作判据：角色卡与其事件条目共享身份 key 是设计常态

def _weierner_pair():
    a = ("铁血总部督军府后勤支系的巡察使，名字叫韦尔纳。此人掌着物资调拨的实权，为人精明，"
         "胃口极大，风格是「公干之外顺手揩油」。")
    b = ("铁血总部督军府后勤支系的巡察使，专管港区物资与账目稽查。身材矮，常穿驼色大衣、"
         "麂皮手套，配夹鼻眼镜；举止不急不躁，先看环境再开口。")
    return a, b


def test_新增_同开头改写被拒_韦尔纳真案例():
    a, b = _weierner_pair()
    book = {"entries": [{"content": a, "comment": "韦尔纳（铁血督军府巡察使）", "keys": ["韦尔纳"]}]}
    with pytest.raises(we.DuplicateEntryError) as exc:
        we.add_entry(book, {"content": b, "comment": "人物·韦尔纳（督军府后勤支系巡察使）", "keys": []})
    assert exc.value.reason == "same_opening"
    assert exc.value.prefix_chars >= we.DUP_PREFIX_CHARS
    assert exc.value.existing_comment == "韦尔纳（铁血督军府巡察使）"


def test_新增_共同前缀12字不拦_阈值边界():
    """母牛化 vs 母性溺爱 的共同前缀 12 字（全库唯一 ≥12 的合法对）⇒ 不拦。"""
    book = {"entries": [{"content": "局部机制·NSFW·母牛化与榨乳哺乳：" + "甲" * 30, "comment": "局部机制·NSFW·母牛化与榨乳哺乳", "keys": ["母牛"]}]}
    we.add_entry(book, {"content": "局部机制·NSFW·母性溺爱与母子话术：" + "乙" * 30, "comment": "局部机制·NSFW·母性溺爱与母子话术", "keys": ["母性"]})
    assert len(book["entries"]) == 2


def test_新增_内容精确重复被拒():
    book = _list_book()
    with pytest.raises(we.DuplicateEntryError) as exc:
        we.add_entry(book, {"content": "  魔法体系 ", "comment": "另一条", "keys": ["新"]})
    assert exc.value.reason == "same_content"
    assert exc.value.existing_comment == "魔法"


def test_新增_同名comment被拒_含标点漂移():
    book = _list_book()
    with pytest.raises(we.DuplicateEntryError) as exc:
        we.add_entry(book, {"content": "完全不同的内容文本，与既有条目毫无重叠。", "comment": "魔法。"})
    assert exc.value.reason == "same_title"


def test_新增_同角色的事件条目不受影响():
    """角色卡与 E 事件共享 keys[0] 是设计常态 ⇒ keys 不单独作判据。"""
    book = {"entries": [{"content": "角色卡·俾斯麦正文：金发军装……", "comment": "角色卡·俾斯麦", "keys": ["俾斯麦"]}]}
    idx = we.add_entry(book, {"content": "E4 事件正文：腓特烈以量产魔戒为饵……", "comment": "E4 俾斯麦的野望", "keys": ["俾斯麦", "魔戒量产"]})
    assert idx == 1 and len(book["entries"]) == 2


def test_新增_空内容不参与判重():
    """表单初建的空内容条目（content="")不得触发判重。"""
    book = _list_book()
    idx = we.add_entry(book, {"content": "", "comment": "全新条目"})
    assert idx == 2


def test_新增_dict容器同样拦重复():
    book = _dict_book()
    with pytest.raises(we.DuplicateEntryError):
        we.add_entry(book, {"content": "魔法体系", "comment": "X"})


def test_新增_同字重复填充不判重_退化前缀():
    """退化输入：`"密"*850` 与 `"密"*2300` 共同前缀 850 字但只有 1 个不同字符 ⇒ 不判重
    （真实夹具与部分模板化条目会出现这种同字填充）。"""
    book = {"entries": [{"content": "密" * 2300, "comment": "甲", "keys": ["甲"]}]}
    idx = we.add_entry(book, {"content": "密" * 850, "comment": "乙", "keys": ["乙"]})
    assert idx == 1
