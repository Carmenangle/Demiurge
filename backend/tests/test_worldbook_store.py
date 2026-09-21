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
    # 2026-09-12 起世界书更新对所有条目统一为「原文底座 + 末尾【剧情进展·动态】区」
    # （此前非角色卡条目被整体覆盖，实测丢过机制条目 1,659 字符正文）
    merged = changed["entries"][0]["content"]
    assert merged.startswith("王都仍由旧王统治")
    assert "【剧情进展·动态】" in merged
    assert "王都已由新王统治" in merged
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


def _repo_book() -> dict:
    return {"entries": [
        {"content": "普通条目", "comment": "设定", "keys": ["设定"], "uid": 1},
        {"content": "【角色卡·舞柔·乳母恋娘】【外貌】绝色美母【剧情进展·动态】旧动态",
         "comment": "角色卡·舞柔", "keys": ["舞柔"], "uid": 2},
    ]}


def test_删除类op一律拒绝并上报(tmp_path):
    base = str(tmp_path)
    worldbook_store.ensure_repo_snapshot(base, "repo", [_repo_book()])
    rejections: list = []
    applied = worldbook_store.apply_repo_ops(
        base, "repo",
        [{"op": "worldbook_delete", "index": 1}, {"op": "delete", "index": 0}],
        rejections=rejections,
    )
    assert applied == 0
    assert [item["reason"] for item in rejections] == ["delete_forbidden", "delete_forbidden"]
    book = worldbook_store.read_repo_snapshot(base, "repo")
    assert len(book["entries"]) == 2


def test_角色卡条目只允许改正文动态段(tmp_path):
    from app.services import worldbook_edit
    base = str(tmp_path)
    worldbook_store.ensure_repo_snapshot(base, "repo", [_repo_book()])
    rejections: list = []
    applied = worldbook_store.apply_repo_ops(
        base, "repo",
        [{"op": "worldbook_update", "index": 1, "text": "新动态", "evidence": "正文",
          "title": "角色卡·舞姬恋", "keys": ["舞姬恋"]}],
        rejections=rejections,
    )
    assert applied == 1
    assert rejections == [{
        "op": "worldbook_update", "index": 1,
        "reason": "identity_keys_forbidden",
        "fields": ["comment", "keys"],
    }]
    entry = worldbook_edit.list_entries(worldbook_store.read_repo_snapshot(base, "repo"))[1]
    assert entry["comment"] == "角色卡·舞柔"
    assert entry["keys"] == ["舞柔"]
    assert "新动态" in entry["content"]
    assert "【外貌】绝色美母" in entry["content"]


def test_机制条目被curator改写时原文底座不丢失(tmp_path):
    """回归（2026-09-12）：底座保护原先只对「comment 含『角色卡·』」的条目生效，
    非角色卡条目走 `patch={"content": text}` 整体覆盖——真机实测「全局机制·世界自转与
    事件登门」（constant，1,659 字符，自述「本卡最高级驱动机制」）被 curator 的剧情快照
    顶掉，原文丢失，且因稳定序列按内容 hash 解析而整条塌陷、其后条目左移。
    现所有条目统一为「原文底座 + 末尾【剧情进展·动态】区」。"""
    from app.services import worldbook_edit
    base = str(tmp_path)
    mechanism = "【全局机制·世界自转】（本卡最高级驱动机制）\n■ 核心原则：世界不等人"
    worldbook_store.ensure_repo_snapshot(base, "repo", [{"entries": [
        {"content": mechanism, "comment": "全局机制·世界自转", "constant": True, "id": 7},
    ]}])
    rejections: list = []
    applied = worldbook_store.apply_repo_ops(
        base, "repo",
        [{"op": "worldbook_update", "index": 0, "evidence": "正文",
          "text": "【剧情进展·动态】当前场景为野外山谷，队伍继续前行"}],
        rejections=rejections,
    )
    assert applied == 1 and rejections == []
    entry = worldbook_edit.list_entries(worldbook_store.read_repo_snapshot(base, "repo"))[0]
    assert "■ 核心原则：世界不等人" in entry["content"]          # 底座逐字保留
    assert entry["content"].startswith("【全局机制·世界自转】")   # 底座仍在开头
    assert "当前场景为野外山谷" in entry["content"]              # 新动态进末尾动态区
    assert entry["comment"] == "全局机制·世界自转"


def test_普通条目更新同样保留底座且身份键不可改(tmp_path):
    """2026-09-12：底座保护与身份键保护对所有条目统一生效（此前普通条目会被整体覆盖、
    且允许改 comment/keys）。"""
    from app.services import worldbook_edit
    base = str(tmp_path)
    worldbook_store.ensure_repo_snapshot(base, "repo", [_repo_book()])
    rejections: list = []
    applied = worldbook_store.apply_repo_ops(
        base, "repo",
        [{"op": "worldbook_update", "index": 0, "text": "更新后的设定", "evidence": "正文",
          "title": "设定·新", "keys": ["新键"]}],
        rejections=rejections,
    )
    assert applied == 1
    assert rejections == [{
        "op": "worldbook_update", "index": 0,
        "reason": "identity_keys_forbidden",
        "fields": ["comment", "keys"],
    }]
    entry = worldbook_edit.list_entries(worldbook_store.read_repo_snapshot(base, "repo"))[0]
    assert entry["comment"] == "设定" and entry["keys"] == ["设定"]   # 身份键不被改
    assert "普通条目" in entry["content"]                            # 底座保留
    assert "更新后的设定" in entry["content"]
    assert "【剧情进展·动态】" in entry["content"]


def test_条目正文性别解析优先显式声明():
    """① 显式声明（【性别】锚点 / 「性别：X」）可靠度最高，优先于正文推断。

    属主说明：解析器随**条目正文属主**落在 `worldbook_store`（2026-09-20 从
    `scene_illustration` 下沉——那条反向 import 会踩 `roleplay-agency-stack` 同层违规）。
    """
    assert worldbook_store.gender_from_text("【性别】女\n【外貌】漆黑墨发。") == "女"
    assert worldbook_store.gender_from_text("【主角定位】穿越者。\n性别：男") == "男"
    # 显式声明赢过正文里的相反信号
    assert worldbook_store.gender_from_text("【性别】男\n【身份】某位贵妇的丈夫。") == "男"


def test_条目正文性别解析须有主语_裸扫身份名词不算():
    """②③ 都要求主语（名字或第三人称代词）在场。

    ⚠ 关键防线：全文裸扫身份名词会把男主人公条目里顺带提到的「贵妇」误判成主人公性别，
    真机误判比缺省更糟（会往画面事实里注入错性别）。"""
    assert worldbook_store.gender_from_text("她是流亡的王女，穿越而来。", "甲") == "女"
    assert worldbook_store.gender_from_text("他原是加林贵族家的少年。", "甲") == "男"
    # 身份名词出现在别的句子里、且与本名/代词不相邻 ⇒ 不猜
    assert worldbook_store.gender_from_text("后宫众女与贵妇们围绕着他。", "甲") == ""
    assert worldbook_store.gender_from_text("【身份】加林领地的女贵族兼财政官。", "甲") == ""


def test_条目正文性别解析拿不准就返回空串():
    """宁缺勿猜：无任何信号 ⇒ 空串（调用方据此不注入）。"""
    assert worldbook_store.gender_from_text("") == ""
    assert worldbook_store.gender_from_text("【主角定位】一个来历不明的存在。") == ""
    assert worldbook_store.gender_from_text(None) == ""  # type: ignore[arg-type]


def test_主人公性别从世界书条目正文解析(tmp_path):
    """2026-09-20 用户定案：主人公性别从**绑定世界书条目**取（只服务主人公）。

    解析优先级＝①「角色卡·<名>」条目优先 ②正文/keys 提及该名的条目兜底；
    条目内三级解析＝显式声明 > 系词句 > 与名字/代词相邻的身份名词。取不到 ⇒ 空串（不猜）。
    """
    repo_id = "work"
    book = {"entries": [
        {"comment": "角色卡·甲", "content": "【主角定位】她是流亡的王女，穿越而来。\n【外貌】银发。",
         "keys": ["甲"]},
        {"comment": "角色卡·乙", "content": "【性别】男\n【外貌】高大。", "keys": ["乙"]},
        {"comment": "角色卡·丙", "content": "【身份】加林领地的女贵族兼财政官。", "keys": ["丙"]},
    ]}
    worldbook_store.ensure_repo_snapshot(str(tmp_path), repo_id, [book])

    assert worldbook_store.repo_protagonist_gender(str(tmp_path), repo_id, "甲") == "女"
    assert worldbook_store.repo_protagonist_gender(str(tmp_path), repo_id, "乙") == "男"
    # 裸身份名词无主语 ⇒ 宁缺勿猜
    assert worldbook_store.repo_protagonist_gender(str(tmp_path), repo_id, "丙") == ""
    # 名字不在世界书里 / 名为空 / 无快照 ⇒ 空串
    assert worldbook_store.repo_protagonist_gender(str(tmp_path), repo_id, "丁") == ""
    assert worldbook_store.repo_protagonist_gender(str(tmp_path), repo_id, "") == ""
    assert worldbook_store.repo_protagonist_gender(str(tmp_path), "missing", "甲") == ""


def test_主人公性别_角色卡条目优先于正文提及(tmp_path):
    """同名在多个条目出现时，「角色卡·<名>」条目是身份真源，优先于其他条目的顺带提及。"""
    repo_id = "work"
    book = {"entries": [
        # 机制条目先写到该名，且句中出现女性身份词（若按顺序取会误判成女）
        {"comment": "系统判定机制·主角归一规则",
         "content": "【定位】本作主角为{{user}}，与后宫众女互动。", "keys": ["主角", "甲"]},
        {"comment": "角色卡·甲", "content": "【身份】前骑士团团长，他是被解职的男人。", "keys": ["甲"]},
    ]}
    worldbook_store.ensure_repo_snapshot(str(tmp_path), repo_id, [book])

    assert worldbook_store.repo_protagonist_gender(str(tmp_path), repo_id, "甲") == "男"
