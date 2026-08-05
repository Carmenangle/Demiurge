"""独立世界书落盘 + 导入端点测试。"""
from __future__ import annotations

import json

import pytest

from app.services import worldbook_store


def _book(n: int = 2) -> dict:
    return {"entries": {str(i): {"content": f"设定{i}", "uid": i} for i in range(n)}, "originalData": {}}


def test_保存与读取世界书往返(tmp_path):
    base = str(tmp_path)
    summary = worldbook_store.save(base, "测试书", _book(3))
    assert summary.name == "测试书"
    assert summary.entries == 3
    got = worldbook_store.read_book(base, "测试书")
    assert got is not None and len(got["entries"]) == 3


def test_同名不覆盖抛错(tmp_path):
    base = str(tmp_path)
    worldbook_store.save(base, "书", _book())
    with pytest.raises(FileExistsError):
        worldbook_store.save(base, "书", _book())
    # overwrite=True 允许
    summary = worldbook_store.save(base, "书", _book(5), overwrite=True)
    assert summary.entries == 5


def test_列表与删除(tmp_path):
    base = str(tmp_path)
    worldbook_store.save(base, "甲", _book())
    worldbook_store.save(base, "乙", _book())
    names = {s.name for s in worldbook_store.list_books(base)}
    assert names == {"甲", "乙"}
    assert worldbook_store.delete_book(base, "甲") is True
    assert worldbook_store.exists(base, "甲") is False
    assert {s.name for s in worldbook_store.list_books(base)} == {"乙"}


def test_数组格式entries计数(tmp_path):
    base = str(tmp_path)
    summary = worldbook_store.save(base, "数组书", {"entries": [{"content": "a"}, {"content": "b"}]})
    assert summary.entries == 2


def test_读不存在返回None(tmp_path):
    assert worldbook_store.read_book(str(tmp_path), "无") is None
    assert worldbook_store.delete_book(str(tmp_path), "无") is False


def test_小仓库世界书快照隔离且只受控增改(tmp_path):
    source = {"entries": [{"keys": ["王都"], "content": "王都仍由旧王统治", "constant": True}]}
    snap_a = worldbook_store.ensure_repo_snapshot(str(tmp_path), "repo-a", [source])
    snap_b = worldbook_store.ensure_repo_snapshot(str(tmp_path), "repo-b", [source])

    assert worldbook_store.apply_repo_ops(str(tmp_path), "repo-a", [
        {"op": "worldbook_update", "index": 0, "text": "王都已由新王统治", "evidence": "加冕完成"},
        {"op": "worldbook_add", "title": "北门", "text": "北门在宵禁后关闭", "keys": ["北门"]},
        {"op": "worldbook_delete", "index": 0},
    ]) == 2

    changed = worldbook_store.read_repo_snapshot(str(tmp_path), "repo-a")
    untouched = worldbook_store.read_repo_snapshot(str(tmp_path), "repo-b")
    assert changed["entries"][0]["content"] == "王都已由新王统治"
    assert changed["entries"][0]["keys"] == ["王都"]
    assert changed["entries"][0]["constant"] is True
    assert changed["entries"][1]["content"] == "北门在宵禁后关闭"
    assert untouched == snap_b
    assert snap_a == snap_b == source


def test_世界书更新必须有依据且索引有效(tmp_path):
    worldbook_store.ensure_repo_snapshot(str(tmp_path), "repo", [{"entries": [{"content": "旧设定"}]}])
    assert worldbook_store.apply_repo_ops(str(tmp_path), "repo", [
        {"op": "worldbook_update", "index": 0, "text": "无依据更新"},
        {"op": "worldbook_update", "index": 8, "text": "越界", "evidence": "剧情"},
    ]) == 0
    assert worldbook_store.read_repo_snapshot(str(tmp_path), "repo")["entries"][0]["content"] == "旧设定"


def test_空世界书快照仍允许当前小仓库新增(tmp_path):
    assert worldbook_store.ensure_repo_snapshot(str(tmp_path), "repo", [{"entries": []}]) == {"entries": []}
    assert worldbook_store.apply_repo_ops(str(tmp_path), "repo", [
        {"op": "worldbook_add", "title": "新规则", "text": "月蚀时城门关闭"},
    ]) == 1
    assert worldbook_store.read_repo_snapshot(str(tmp_path), "repo")["entries"][0]["content"] == "月蚀时城门关闭"


def test_curator上下文优先包含本轮命中的角色条目(tmp_path):
    repo_id = "work"
    book = {"entries": [
        {"comment": "超长机制", "content": "无关机制" * 500, "keys": ["机制"]},
        {"comment": "角色卡·冷倾雪", "content": "【角色卡·冷倾雪】\n【外貌】漆黑墨发与紫玉金髻",
         "keys": ["冷倾雪", "紫冷玄女"]},
    ]}
    worldbook_store.ensure_repo_snapshot(str(tmp_path), repo_id, [book])

    context = worldbook_store.repo_snapshot_context(
        str(tmp_path), repo_id, query="冷倾雪在第四日清晨醒来", max_chars=300,
    )

    assert "角色卡·冷倾雪" in context
    assert '"index":1' in context


def test_curator上下文只包含主剧情实际注入的条目(tmp_path):
    repo_id = "work"
    worldbook_store.ensure_repo_snapshot(str(tmp_path), repo_id, [{"entries": [
        {"comment": "角色卡·冷倾雪", "content": "冷倾雪本轮出场"},
        {"comment": "角色卡·旁人", "content": "旁人未出场"},
        {"comment": "机制", "content": "未参与本轮的机制"},
    ]}])

    context = worldbook_store.repo_snapshot_context(
        str(tmp_path), repo_id, allowed_indices={0},
    )

    assert '"index":0' in context
    assert '"index":1' not in context
    assert '"index":2' not in context
    assert "旁人未出场" not in context


def test_curator写回拒绝未注入索引但仍允许新增(tmp_path):
    repo_id = "work"
    worldbook_store.ensure_repo_snapshot(str(tmp_path), repo_id, [{"entries": [
        {"content": "本轮注入"},
        {"content": "本轮未注入"},
    ]}])

    applied = worldbook_store.apply_repo_ops(str(tmp_path), repo_id, [
        {"op": "worldbook_update", "index": 1, "text": "越权修改", "evidence": "本轮正文"},
        {"op": "worldbook_add", "title": "新事实", "text": "本轮新产生的事实"},
    ], allowed_update_indices={0})

    book = worldbook_store.read_repo_snapshot(str(tmp_path), repo_id)
    assert applied == 1
    assert book["entries"][1]["content"] == "本轮未注入"
    assert book["entries"][2]["content"] == "本轮新产生的事实"


def test_角色条目动态更新保留基础设定并替换旧动态(tmp_path):
    repo_id = "work"
    original = (
        "【角色卡·冷倾雪】\n【外貌】漆黑墨发扎成发团、插紫玉金髻\n"
        "【性格】清冷孤高\n\n【剧情进展·动态】\n第三日仍在昏睡"
    )
    worldbook_store.ensure_repo_snapshot(str(tmp_path), repo_id, [{"entries": [{
        "comment": "角色卡·冷倾雪", "content": original, "keys": ["冷倾雪"],
    }]}])

    applied = worldbook_store.apply_repo_ops(str(tmp_path), repo_id, [{
        "op": "worldbook_update", "index": 0,
        "text": "第四日清晨醒来，理智回笼但身体状态仍延续。",
        "evidence": "本轮正文明确发生",
    }])

    content = worldbook_store.read_repo_snapshot(str(tmp_path), repo_id)["entries"][0]["content"]
    assert applied == 1
    assert "【外貌】漆黑墨发扎成发团、插紫玉金髻" in content
    assert "【性格】清冷孤高" in content
    assert "第三日仍在昏睡" not in content
    assert content.endswith("【剧情进展·动态】\n第四日清晨醒来，理智回笼但身体状态仍延续。")


def test_导入路由解析非法json报400(tmp_path):
    from app.routers.worldbook import _parse_book
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        _parse_book(b"not json")
    assert ei.value.status_code == 400


def test_导入路由缺entries报400(tmp_path):
    from app.routers.worldbook import _parse_book
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        _parse_book(json.dumps({"foo": 1}).encode("utf-8"))
    assert ei.value.status_code == 400
