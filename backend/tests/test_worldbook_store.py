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


def test_新增重复条目被拒且不影响同批其它op(tmp_path):
    """L1 写入闸（2026-10-22）：worldbook_add 撞既有条目 ⇒ rejections 记 duplicate_entry，
    同批其它 op 照常 apply（与机制层/constant 拒绝同口径）。"""
    worldbook_store.ensure_repo_snapshot(str(tmp_path), "repo", [{"entries": [
        {"content": "王都仍由旧王统治", "comment": "王都", "keys": ["王都"]},
    ]}])
    rejections: list[dict] = []
    applied = worldbook_store.apply_repo_ops(
        str(tmp_path), "repo", [
            {"op": "worldbook_add", "title": "王都（重复）", "text": "王都仍由旧王统治", "keys": ["王都"]},
            {"op": "worldbook_add", "title": "北门", "text": "北门在宵禁后关闭", "keys": ["北门"]},
        ], rejections=rejections)
    assert applied == 1
    assert rejections == [{
        "op": "worldbook_add", "title": "王都（重复）", "reason": "duplicate_entry",
        "existing": "王都", "existing_index": 0, "detail": "same_content",
    }]
    snap = worldbook_store.read_repo_snapshot(str(tmp_path), "repo")
    assert [e.get("comment") for e in snap["entries"]] == ["王都", "北门"]


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
        {"comment": "角色卡·顾凝霜", "content": "【角色卡·顾凝霜】\n【外貌】漆黑墨发与紫玉金髻",
         "keys": ["顾凝霜", "紫冷玄女"]},
    ]}
    worldbook_store.ensure_repo_snapshot(str(tmp_path), repo_id, [book])

    context = worldbook_store.repo_snapshot_context(
        str(tmp_path), repo_id, query="顾凝霜在第四日清晨醒来", max_chars=300,
    )

    assert "角色卡·顾凝霜" in context
    assert '"index":1' in context


def test_curator上下文只包含主剧情实际注入的条目(tmp_path):
    repo_id = "work"
    worldbook_store.ensure_repo_snapshot(str(tmp_path), repo_id, [{"entries": [
        {"comment": "角色卡·顾凝霜", "content": "顾凝霜本轮出场"},
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
        "【角色卡·顾凝霜】\n【外貌】漆黑墨发扎成发团、插紫玉金髻\n"
        "【性格】清冷孤高\n\n【剧情进展·动态】\n第三日仍在昏睡"
    )
    worldbook_store.ensure_repo_snapshot(str(tmp_path), repo_id, [{"entries": [{
        "comment": "角色卡·顾凝霜", "content": original, "keys": ["顾凝霜"],
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
        {"content": "【角色卡·绮柔·乳母恋娘】【外貌】绝色美母【剧情进展·动态】旧动态",
         "comment": "角色卡·绮柔", "keys": ["绮柔"], "uid": 2},
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
          "title": "角色卡·星织恋", "keys": ["星织恋"]}],
        rejections=rejections,
    )
    assert applied == 1
    assert rejections == [{
        "op": "worldbook_update", "index": 1,
        "reason": "identity_keys_forbidden",
        "fields": ["comment", "keys"],
    }]
    entry = worldbook_edit.list_entries(worldbook_store.read_repo_snapshot(base, "repo"))[1]
    assert entry["comment"] == "角色卡·绮柔"
    assert entry["keys"] == ["绮柔"]
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


# ── 第 51 刀（2026-09-22）：主人公姓名三段读取（修复 D4/D5）──

def _write_protagonist_table(base: str, repo_id: str, name: str) -> None:
    """写一张最小「主角信息表」（只含姓名列）供测试读取。"""
    from app.services import table_store
    tables = [{
        "name": table_store.PROTAGONIST_TABLE,
        "columns": ["姓名", "性别", "外貌特征"],
        "rows": [[name, "女", "示例"]],
        "mode": "full", "rowPolicy": "singleton", "alwaysFill": False,
        "deletePolicy": "retain", "keyCol": "姓名",
    }]
    table_store.save(base, repo_id, tables)


def test_主角姓名三段读取_user_name优先(tmp_path):
    """① user_name 非空 ⇒ 直接返回，source='user_name'（②/③ 即使有数据也不抢）。"""
    repo_id = "work"
    _write_protagonist_table(str(tmp_path), repo_id, "乙")
    book = {"entries": [
        {"comment": "角色卡·丙", "content": "【性别】女", "keys": ["丙", "主角"]},
    ]}
    worldbook_store.ensure_repo_snapshot(str(tmp_path), repo_id, [book])

    assert worldbook_store.repo_protagonist_name(str(tmp_path), repo_id, "甲") == ("甲", "user_name")


def test_主角姓名三段读取_主角信息表兜底(tmp_path):
    """② user_name 为空 ⇒ 取通用表「主角信息表」姓名列，source='protagonist_table'。"""
    repo_id = "work"
    _write_protagonist_table(str(tmp_path), repo_id, "乙")

    assert worldbook_store.repo_protagonist_name(str(tmp_path), repo_id, "") == ("乙", "protagonist_table")


def test_主角姓名三段读取_世界书兜底(tmp_path):
    """③ 主角信息表无姓名 ⇒ 世界书「角色卡·<主角>」（keys 含『主角』）兜底，
    source='worldbook'；普通角色卡（无主角标记）不得被误当主人公。"""
    repo_id = "work"
    book = {"entries": [
        {"comment": "角色卡·乙", "content": "【性别】女", "keys": ["乙"]},  # 无主角标记 ⇒ 跳过
        {"comment": "角色卡·丙", "content": "【性别】女", "keys": ["丙", "主角"]},  # 命中
    ]}
    worldbook_store.ensure_repo_snapshot(str(tmp_path), repo_id, [book])

    assert worldbook_store.repo_protagonist_name(str(tmp_path), repo_id, "") == ("丙", "worldbook")


def test_主角姓名三段读取_全空返回空串(tmp_path):
    """三段都取不到 ⇒ ('', '')（宁缺勿猜）。"""
    repo_id = "work"
    assert worldbook_store.repo_protagonist_name(str(tmp_path), repo_id, "") == ("", "")
    # 读盘失败不得抛（user_name 必须留空，否则①段短路、走不到读盘路径）
    assert worldbook_store.repo_protagonist_name(str(tmp_path), "missing", "") == ("", "")
    # ①段优先与仓库是否存在无关：缺仓库但 user_name 非空，仍返回 user_name
    assert worldbook_store.repo_protagonist_name(str(tmp_path), "missing", "甲") == ("甲", "user_name")


# ── 2026-09-23（C 约束）：运行时新增条目禁建机制层、一律 constant=False ──

def test_新增条目拒绝机制层标题前缀并上报(tmp_path):
    """C 约束之一：`全局机制·` / `系统判定机制·` / `局部机制·` 是**建卡时由用户设计**的层，
    运行时 curator 不得自造同名层条目。

    真机实锤（2026-09-23）：curator 把谢若冰的身体状态写进了「全局机制·{{user}}(少宗主)」
    （175 字符）、把「星织恋…往返抚弄其经脉」写进「全局机制·龙阳决与双修」（仅 39 字符），
    而这几条常年 `constant=True` ⇒ 全程恒开、全文注入、永不截断 ⇒ 「经脉」每轮无条件注入。
    处理策略＝**拒收而非改名**：改名等价于替用户决定分层，宁可漏记也不污染设定。
    """
    base = str(tmp_path)
    worldbook_store.ensure_repo_snapshot(base, "repo", [{"entries": [{"content": "底座"}]}])
    rejections: list = []
    applied = worldbook_store.apply_repo_ops(base, "repo", [
        {"op": "worldbook_add", "title": "全局机制·新机制", "text": "自造机制"},
        {"op": "worldbook_add", "title": "系统判定机制·X", "text": "自造判定"},
        {"op": "worldbook_add", "title": "局部机制·Y", "text": "自造局部"},
    ], rejections=rejections)

    assert applied == 0
    assert [r["reason"] for r in rejections] == ["mechanism_layer_forbidden"] * 3
    assert [r["title"] for r in rejections] == ["全局机制·新机制", "系统判定机制·X", "局部机制·Y"]
    assert len(worldbook_store.read_repo_snapshot(base, "repo")["entries"]) == 1


def test_新增条目强制关闭constant并上报(tmp_path):
    """C 约束之二：`constant` ⇒ 全程恒开、全文注入、永不截断，代价极高，只能由用户在卡里
    显式设置；运行时新增一律降为 `constant=False`（条目照常落盘，只是不再恒开）。"""
    base = str(tmp_path)
    worldbook_store.ensure_repo_snapshot(base, "repo", [{"entries": [{"content": "底座"}]}])
    rejections: list = []
    applied = worldbook_store.apply_repo_ops(base, "repo", [
        {"op": "worldbook_add", "title": "新设定", "text": "月蚀时城门关闭", "constant": True},
    ], rejections=rejections)

    assert applied == 1
    assert rejections == [
        {"op": "worldbook_add", "title": "新设定", "reason": "constant_forced_off"},
    ]
    entry = worldbook_store.read_repo_snapshot(base, "repo")["entries"][1]
    assert entry["content"] == "月蚀时城门关闭"
    assert not entry.get("constant")   # 不得恒开


def test_新增普通设定条目不受新约束影响(tmp_path):
    """等价矩阵锁：合法的运行时新增（非机制层、未请求 constant）逐字不变、零 rejections。"""
    base = str(tmp_path)
    worldbook_store.ensure_repo_snapshot(base, "repo", [{"entries": []}])
    rejections: list = []
    assert worldbook_store.apply_repo_ops(base, "repo", [
        {"op": "worldbook_add", "title": "北门", "text": "北门在宵禁后关闭", "keys": ["北门"]},
    ], rejections=rejections) == 1
    assert rejections == []
    entry = worldbook_store.read_repo_snapshot(base, "repo")["entries"][0]
    assert entry["comment"] == "北门"
    assert entry["content"] == "北门在宵禁后关闭"
    assert entry["keys"] == ["北门"]


# ── 2026-10-23：角色外貌提取不再依赖「角色卡·」＋固定行首锚点 ──────────────

def _visual_profiles(tmp_path, entries, query, **kwargs):
    worldbook_store.ensure_repo_snapshot(
        str(tmp_path), "work", [{"entries": entries}],
    )
    return worldbook_store.repo_visual_profiles(str(tmp_path), "work", query, **kwargs)


def test_外貌提取保留简介瞳色并兼容任意标题(tmp_path):
    """真机实锤：旧实现只取【外貌】整行 ⇒ 简介里的「暗黄色的眼睛」整段丢失。
    新实现保留结构化块（含【外观】），并把块外的外貌线索句一并带上。"""
    entries = [{
        "comment": "腓特烈大帝",
        "keys": ["腓特烈大帝"],
        "content": (
            "她有一双暗黄色的眼睛。\n"
            "【外观】身高近两米，比 {{user}} 高出一个头。暗黄色长发。\n"
            "【身材】巨乳、蜂腰、肥臀。\n"
            "【穿着】黑色连衣裙、黑色连裤袜、黑色高跟鞋。\n"
            "【剧情进展·动态】三夜未合眼。"
        ),
    }]

    profiles = _visual_profiles(tmp_path, entries, "腓特烈大帝")

    assert profiles.startswith("腓特烈大帝：")
    for fact in (
        "暗黄色的眼睛", "身高近两米", "暗黄色长发",
        "巨乳", "黑色连衣裙", "黑色连裤袜", "黑色高跟鞋",
    ):
        assert fact in profiles
    # 非外貌的动态段不得因「整条兜底」被当进外貌
    assert "三夜未合眼" not in profiles


def test_外貌提取支持纯散文与行标签(tmp_path):
    """任意导入格式：纯散文（无角色卡前缀/无标题）与 `外貌：` 行标签都要能提到。"""
    prose = _visual_profiles(
        tmp_path / "prose",
        [{
            "comment": "艾莉丝",
            "keys": ["艾莉丝"],
            "content": "艾莉丝是王国公主。她有一头银发和一双金眼，穿着黑色长裙。",
        }],
        "艾莉丝",
    )
    assert "银发" in prose and "金眼" in prose and "黑色长裙" in prose
    assert "王国公主" not in prose

    labels = _visual_profiles(
        tmp_path / "labels",
        [{
            "comment": "米拉",
            "keys": ["米拉"],
            "content": "外貌：黑色长发，金色眼睛。\n穿着：红色长裙。",
        }],
        "米拉",
    )
    assert "黑色长发" in labels and "金色眼睛" in labels and "红色长裙" in labels

    english = _visual_profiles(
        tmp_path / "english",
        [{
            "comment": "Elise",
            "keys": ["Elise"],
            "content": "Appearance: long silver hair and golden eyes, wearing a black dress.",
        }],
        "Elise",
    )
    assert "silver hair" in english and "black dress" in english


def test_外貌提取长条目按预算裁剪而不是整条丢弃(tmp_path):
    """旧实现单条超过 max_chars 就直接 break ⇒ 明明有角色却返回空串。
    新实现必须裁剪到预算内并保留可用外貌，而不是丢弃。"""
    entries = [{
        "comment": "长外貌",
        "keys": ["长外貌"],
        "content": "【外貌】" + "银发金眼，黑色长裙。" * 600,
    }]

    profiles = _visual_profiles(tmp_path, entries, "长外貌", max_chars=120)

    assert profiles
    assert len(profiles) <= 120
    assert "银发" in profiles


def test_非角色条目不会误当外貌画像(tmp_path):
    """地点/道具/状态条目即使命中 query，也不得被塞进「角色外貌」。"""
    entries = [
        {"comment": "港区账房", "keys": ["账房"], "content": "【外观】一间昏暗的账房，长桌与油灯。"},
        {"comment": "魔戒", "keys": ["魔戒"], "content": "【外观】金色戒指，镶嵌红宝石，戒面刻有纹章。"},
        {"comment": "状态", "keys": ["状态"], "content": "【状态】胸口涨得发沉，三夜未合眼。"},
    ]
    worldbook_store.ensure_repo_snapshot(
        str(tmp_path), "work", [{"entries": entries}],
    )

    assert worldbook_store.repo_visual_profiles(str(tmp_path), "work", "账房") == ""
    assert worldbook_store.repo_visual_profiles(str(tmp_path), "work", "魔戒") == ""
    assert worldbook_store.repo_visual_profiles(str(tmp_path), "work", "状态") == ""


def test_外貌提取跳过动态段及其中性子标题(tmp_path):
    """真机实锤：`【命定危机·详版】` 下的 `【第一次是怎么被迫的】` 不含任何动态关键词，
    若按标题各自判定就会把正文里的「红发／蕾丝眼罩」当成稳定外貌收进画像。"""
    entries = [{
        "comment": "角色卡·白见怜",
        "keys": ["白见怜"],
        "content": (
            "【外貌】高挑妖娆的倾城御姐，金色眼眸，涂红色唇釉。\n"
            "【命定危机·详版】\n"
            "【第一次是怎么被迫的】成年红发异域妖姬落入拍卖行后，被戴上蕾丝眼罩与口球。\n"
            "【触发时间与地点】无阙城地下拍卖行，压轴拍品。\n"
            "【性格与反差】表面妩媚、内里冷酷。\n"
        ),
    }]

    profiles = _visual_profiles(tmp_path, entries, "白见怜")

    assert "金色眼眸" in profiles and "倾城御姐" in profiles
    for leak in ("红发", "蕾丝眼罩", "拍卖行", "冷酷"):
        assert leak not in profiles


def test_动态段之后的外貌标题仍要重新放行(tmp_path):
    """sticky 拒绝态只沿中性标题向下传；遇到 `【外貌】` 这类画像标题必须恢复收录。"""
    entries = [{
        "comment": "角色卡·白见怜",
        "keys": ["白见怜"],
        "content": (
            "【命定危机】她被红发妖姬戴上蕾丝眼罩。\n"
            "【外貌】银发金眼，黑色长裙。\n"
        ),
    }]

    profiles = _visual_profiles(tmp_path, entries, "白见怜")

    assert "银发" in profiles and "黑色长裙" in profiles
    assert "蕾丝眼罩" not in profiles


def test_事件系统势力条目不会因顺带提外貌混入(tmp_path):
    """非角色条目的正文常顺带提到某人发色/「角色条目」，标题级门必须先于正文启发式生效。"""
    entries = [
        {
            "comment": "15. 大事件·天魔教白见怜的暗局",
            "keys": ["白见怜"],
            "content": "天魔教红发妖姬白见怜潜伏商会，她以千金令换一个要求。",
        },
        {
            "comment": "系统判定机制·AI叙事核心指令",
            "keys": ["系统"],
            "content": (
                "【角色条目规则】不得捏造世界书里不存在的人物。\n"
                "金发女战士曜姬（碧眼、白丝长腿）。"
            ),
        },
        {
            "comment": "1. 势力与种族·中土宗门",
            "keys": ["势力"],
            "content": "掌门之妹天狐剑仙沈映澜（银狐、白丝长腿、佩眼罩）。",
        },
    ]
    worldbook_store.ensure_repo_snapshot(
        str(tmp_path), "work", [{"entries": entries}],
    )

    for query in ("白见怜", "系统", "势力"):
        assert worldbook_store.repo_visual_profiles(str(tmp_path), "work", query) == ""


def test_外貌提取多角色共享受预算约束(tmp_path):
    entries = [
        {"comment": "甲", "keys": ["甲"], "content": "外貌：银发金眼。"},
        {"comment": "乙", "keys": ["乙"], "content": "外貌：黑发红眼。"},
    ]

    profiles = _visual_profiles(tmp_path, entries, "甲与乙", max_chars=200)

    assert "甲：" in profiles and "乙：" in profiles
    assert len(profiles) <= 200
