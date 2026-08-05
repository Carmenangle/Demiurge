"""⑤ 世界书条目级增删改（纯逻辑，dict/list 两种容器）。"""
from __future__ import annotations

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
