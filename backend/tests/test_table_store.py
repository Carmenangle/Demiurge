"""通用多表：模板解析、行级 op 应用、落盘幂等、导入。

要点：跳过好感度/纪要引擎表、去 row_id 列、insert/update/delete 边界、按表名去重导入。
"""
from __future__ import annotations

import json

from app.services import table_store as ts
from app.services import table_update as tu


def _tpl() -> dict:
    return {
        "mate": {"type": "chatSheets"},  # 非 sheet_ 跳过
        "sheet_a": {
            "name": "背包物品表",
            "sourceData": {"note": "记录物品"},
            "content": [["row_id", "物品名称", "数量", "类别"]],
        },
        "sheet_b": {
            "name": "好感度表",  # 引擎表，跳过
            "content": [["row_id", "角色名", "好感度"]],
        },
    }


def test_parse_template_跳过引擎表去rowid():
    tables = ts.parse_template(_tpl())
    assert len(tables) == 1
    t = tables[0]
    assert t["name"] == "背包物品表"
    assert t["columns"] == ["物品名称", "数量", "类别"]  # row_id 去掉
    assert t["note"] == "记录物品"
    assert t["rows"] == []


def test_apply_ops_insert_update_delete():
    tables = ts.parse_template(_tpl())
    n = ts.apply_ops(tables, [
        {"op": "insert", "table": "背包物品表", "values": {"物品名称": "药水", "数量": "3", "类别": "消耗品"}},
        {"op": "insert", "table": "背包物品表", "values": {"物品名称": "铁剑"}},  # 缺列补空
    ])
    assert n == 2
    assert tables[0]["rows"][0] == ["药水", "3", "消耗品"]
    assert tables[0]["rows"][1] == ["铁剑", "", ""]
    # update 第 0 行数量
    ts.apply_ops(tables, [{"op": "update", "table": "背包物品表", "row": 0, "values": {"数量": "5"}}])
    assert tables[0]["rows"][0] == ["药水", "5", "消耗品"]
    # delete 第 1 行
    ts.apply_ops(tables, [{"op": "delete", "table": "背包物品表", "row": 1}])
    assert len(tables[0]["rows"]) == 1


def test_apply_ops_非法项跳过():
    tables = ts.parse_template(_tpl())
    n = ts.apply_ops(tables, [
        {"op": "insert", "table": "不存在的表", "values": {}},   # 未知表
        {"op": "update", "table": "背包物品表", "row": 99, "values": {}},  # 越界
        {"op": "delete", "table": "背包物品表", "row": -1},        # 越界
        "非dict",
    ])
    assert n == 0


def test_load_save_导入幂等(tmp_path):
    base = str(tmp_path)
    rid = "repo-1"
    # replace 导入
    assert ts.import_template(base, rid, _tpl(), replace=True) == 1
    assert (tmp_path / rid / ts.TABLES_FILE).is_file()
    loaded = ts.load(base, rid)
    assert loaded[0]["name"] == "背包物品表"
    # 非 replace 再导同模板 → 按表名去重，不重复加
    assert ts.import_template(base, rid, _tpl(), replace=False) == 0
    assert len(ts.load(base, rid)) == 1


def test_load_无库空列表(tmp_path):
    tables = ts.load(str(tmp_path), "none")
    assert [t["name"] for t in tables] == [
        "全局数据表", "主角信息表", "重要角色表", "主角技能表",
        "背包物品表", "任务与事件表", "选项表",
    ]
    assert tables[2]["keyCol"] == "姓名"
    assert tables[2]["mode"] == ts.MODE_RETRIEVAL
    assert tables[0]["columns"] == ["时间", "地点", "世界状态", "世界规则"]
    assert tables[0]["rowPolicy"] == "singleton" and tables[0]["alwaysFill"] is True
    assert tables[1]["rowPolicy"] == "singleton"
    assert tables[3]["columns"][-1] == "状态"
    assert tables[6]["columns"] == ["后续动作选项", "推导依据"]
    assert tables[6]["rowPolicy"] == "singleton"
    assert ts.load("", "x") == []


def test_全局与选项始终只有一张卡并由新值替换():
    tables = ts.default_tables()
    ops = [
        {"op": "insert", "table": "全局数据表", "values": {
            "时间": "第一日", "地点": "王城", "世界状态": "戒严", "世界规则": "宵禁",
        }},
        {"op": "insert", "table": "全局数据表", "values": {
            "时间": "第二日", "地点": "荒野", "世界状态": "追捕中", "世界规则": "禁空",
        }},
        {"op": "insert", "table": "选项表", "values": {
            "后续动作选项": "1. 潜行\n2. 交涉", "推导依据": "当前处于追捕中",
        }},
    ]
    assert ts.apply_ops(tables, ops) == 3
    global_table = next(table for table in tables if table["name"] == "全局数据表")
    options = next(table for table in tables if table["name"] == "选项表")
    assert global_table["rows"] == [["第二日", "荒野", "追捕中", "禁空"]]
    assert options["rows"] == [["1. 潜行\n2. 交涉", "当前处于追捕中"]]


def test_人物按姓名更新_技能废除与任务完成保留_背包允许删除():
    tables = ts.default_tables()
    ts.apply_ops(tables, [
        {"op": "insert", "table": "重要角色表", "values": {"姓名": "甲", "当前目标": "调查"}},
        {"op": "insert", "table": "重要角色表", "values": {"姓名": "乙", "当前目标": "逃离"}},
        {"op": "insert", "table": "重要角色表", "values": {"姓名": "甲", "当前目标": "复仇"}},
        {"op": "insert", "table": "主角技能表", "values": {"技能名称": "剑术", "状态": "可用"}},
        {"op": "insert", "table": "任务与事件表", "values": {"名称": "救援", "状态": "已完成"}},
        {"op": "insert", "table": "背包物品表", "values": {"物品名称": "药水", "数量": "1"}},
    ])
    ts.apply_ops(tables, [
        {"op": "delete", "table": "主角技能表", "key": "剑术"},
        {"op": "delete", "table": "任务与事件表", "key": "救援"},
        {"op": "delete", "table": "重要角色表", "key": "乙"},
        {"op": "delete", "table": "背包物品表", "key": "药水"},
    ])
    chars = next(table for table in tables if table["name"] == "重要角色表")
    skills = next(table for table in tables if table["name"] == "主角技能表")
    quests = next(table for table in tables if table["name"] == "任务与事件表")
    inventory = next(table for table in tables if table["name"] == "背包物品表")
    assert len(chars["rows"]) == 2 and chars["rows"][0][-1] == "复仇"
    assert skills["rows"][0][-1] == "不可用"
    assert len(quests["rows"]) == 1 and quests["rows"][0][2] == "已完成"
    assert inventory["rows"] == []


def test_读取与维护频率彻底分离():
    tables = ts.default_tables()
    assert ts.tables_for_read(tables) == tables
    assert [table["name"] for table in ts.tables_for_maintenance(tables, False)] == ["全局数据表"]
    assert ts.tables_for_maintenance(tables, True) == tables


def test_检索表身份列精确优先且无嵌入也可召回():
    tables = ts.default_tables()
    chars = next(table for table in tables if table["name"] == "重要角色表")
    chars["rows"] = [
        ["冷倾雪", "女", "成年", "剑客", "黑发", "白衣", "山门", "在场", "同伴", "", "脱困"],
        ["虞妙玥", "女", "成年", "医师", "银发", "青衣", "药房", "离场", "盟友", "", "配药"],
    ]

    rows = ts.recall_retrieval_rows(tables, "冷倾雪回到山门", k=1)

    assert len(rows) == 1
    assert "冷倾雪" in rows[0]


def test_存量全局与选项结构迁移为单卡(tmp_path):
    base, rid = str(tmp_path), "r"
    path = tmp_path / rid / ts.TABLES_FILE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"tables": [
        {"uid": "sheet_default_global", "name": "全局数据表",
         "columns": ["字段", "值", "说明"],
         "rows": [["时间", "第三日", ""], ["地点", "雪山", ""], ["战况", "停战", ""]]},
        {"uid": "sheet_default_options", "name": "选项表",
         "columns": ["选项", "条件", "可能影响", "状态"],
         "rows": [["追击", "敌人负伤", "进入山谷", "可用"], ["休整", "安全", "恢复", "可用"]]},
    ]}, ensure_ascii=False), encoding="utf-8")

    global_table, options = ts.load(base, rid)
    assert global_table["rows"] == [["第三日", "雪山", "战况：停战", ""]]
    assert options["rows"][0][0].splitlines() == [
        "追击（敌人负伤；进入山谷；可用）", "休整（安全；恢复；可用）",
    ]


def test_render_tables_block():
    tables = ts.parse_template(_tpl())
    ts.apply_ops(tables, [{"op": "insert", "table": "背包物品表", "values": {"物品名称": "药水"}}])
    block = ts.render_tables_block(tables)
    assert "背包物品表" in block
    assert "[0]" in block and "药水" in block


def test_parse_table_block_剥块():
    reply = '正文内容。\n<表格更新>[{"op":"insert","table":"背包物品表","values":{"物品名称":"药水"}}]</表格更新>'
    clean, ops = tu.parse_table_block(reply)
    assert clean == "正文内容。"
    assert ops == [{"op": "insert", "table": "背包物品表", "values": {"物品名称": "药水"}}]


def test_parse_table_block_坏json去块返空():
    reply = "正文。<表格更新>不是json</表格更新>"
    clean, ops = tu.parse_table_block(reply)
    assert clean == "正文。"
    assert ops == []


def test_parse_table_block_输出截断时仍隐藏未闭合尾块():
    reply = '正文。\n<表格更新>[{"op":"update","table":"全局数据表","values":{"时间":"次日"'
    clean, ops = tu.parse_table_block(reply)
    assert clean == "正文。"
    assert ops == []
    assert tu.has_table_block(reply) is True


def test_parse_table_block_无块原样():
    clean, ops = tu.parse_table_block("纯正文没有块")
    assert clean == "纯正文没有块"
    assert ops == []


def test_table_context_无表空串():
    assert tu.table_context([]) == ""


def test_table_context_有表只读且无维护协议():
    tables = ts.parse_template(_tpl())
    ins = tu.table_context(tables)
    assert "背包物品表" in ins
    assert "只读剧情上下文" in ins
    assert "<表格更新>" not in ins and '"op"' not in ins


def test_独立表格维护提示词只要求JSON不要求拼接剧情正文():
    tables = ts.parse_template(_tpl())
    ins = tu.maintenance_instruction(tables)
    assert "只输出 JSON 数组" in ins
    assert "<表格更新>" not in ins
    assert "正文之后" not in ins


def test_create_table_去重去空建表():
    tables: list = []
    t = ts.create_table(tables, "符箓表", ["名称", "名称", " ", "品阶", "数量"],
                        note="记录符箓", rule="获得时增行", col_types={"数量": "数字"}, key_col="名称")
    assert t is not None
    assert t["columns"] == ["名称", "品阶", "数量"]  # 去重去空
    assert t["keyCol"] == "名称"
    assert t["colTypes"]["数量"] == "数字"
    assert t["note"] == "记录符箓" and t["rule"] == "获得时增行"
    assert len(tables) == 1
    # 重名不建
    assert ts.create_table(tables, "符箓表", ["x"]) is None
    # 无有效列不建
    assert ts.create_table(tables, "空表", ["", "  "]) is None
    assert len(tables) == 1


def test_create_table_keyCol不在列则置空():
    tables: list = []
    t = ts.create_table(tables, "T", ["a", "b"], key_col="不存在")
    assert t is not None and t["keyCol"] == ""


def test_drop_table():
    tables: list = []
    ts.create_table(tables, "T1", ["a"])
    ts.create_table(tables, "T2", ["b"])
    assert ts.drop_table(tables, "T1") is True
    assert [t["name"] for t in tables] == ["T2"]
    assert ts.drop_table(tables, "不存在") is False


def test_set_meta():
    tables: list = []
    ts.create_table(tables, "T", ["a", "b"])
    assert ts.set_meta(tables, "T", note="新说明", rule="新规则", key_col="a") is True
    assert tables[0]["note"] == "新说明" and tables[0]["rule"] == "新规则"
    assert tables[0]["keyCol"] == "a"
    # key_col 非现有列置空
    ts.set_meta(tables, "T", key_col="z")
    assert tables[0]["keyCol"] == ""
    assert ts.set_meta(tables, "无", note="x") is False


def test_apply_ops_按身份列定位():
    tables: list = []
    ts.create_table(tables, "好感", ["角色名", "好感度"], key_col="角色名")
    ts.apply_ops(tables, [
        {"op": "insert", "table": "好感", "values": {"角色名": "塞西莉亚", "好感度": "15"}},
        {"op": "insert", "table": "好感", "values": {"角色名": "艾莉丝", "好感度": "5"}},
    ])
    # 按 key 更新（不靠行号）
    n = ts.apply_ops(tables, [{"op": "update", "table": "好感", "key": "塞西莉亚", "values": {"好感度": "25"}}])
    assert n == 1
    assert tables[0]["rows"][0] == ["塞西莉亚", "25"]
    # 按 values[keyCol] 也能定位删除
    ts.apply_ops(tables, [{"op": "delete", "table": "好感", "values": {"角色名": "艾莉丝"}}])
    assert len(tables[0]["rows"]) == 1
    # 指名身份列值但不存在 → 不误伤
    n = ts.apply_ops(tables, [{"op": "update", "table": "好感", "key": "查无此人", "values": {"好感度": "0"}}])
    assert n == 0
    assert tables[0]["rows"][0] == ["塞西莉亚", "25"]


def test_parse_template_从ddl_unique取身份列():
    tpl = {
        "sheet_x": {
            "name": "关系表",
            "sourceData": {
                "note": "n",
                "ddl": ("CREATE TABLE rel ( -- 关系表\n"
                        "  row_id INTEGER PRIMARY KEY, -- 行号\n"
                        "  char_name TEXT NOT NULL UNIQUE, -- 角色名\n"
                        "  rel TEXT -- 关系\n);"),
            },
            "content": [["row_id", "角色名", "关系"]],
        },
    }
    tables = ts.parse_template(tpl)
    assert tables[0]["keyCol"] == "角色名"


def test_render_block_含说明与规则():
    tables: list = []
    ts.create_table(tables, "背包", ["物品", "数量"], note="记录物品", rule="获得时增行", key_col="物品")
    block = ts.render_tables_block(tables)
    assert "说明：记录物品" in block
    assert "更新规则：获得时增行" in block
    assert "身份列：物品" in block


def test_mode_默认full_可切retrieval():
    tables: list = []
    t = ts.create_table(tables, "名册", ["角色", "关系"], key_col="角色")
    assert t is not None and t["mode"] == ts.MODE_FULL
    assert ts.set_meta(tables, "名册", mode="retrieval")
    assert tables[0]["mode"] == ts.MODE_RETRIEVAL
    assert ts.set_meta(tables, "名册", mode="乱填")  # 非法值归 full
    assert tables[0]["mode"] == ts.MODE_FULL


def test_render_block_检索表只出结构不倾倒行():
    tables: list = []
    ts.create_table(tables, "名册", ["角色", "关系"], key_col="角色")
    ts.apply_ops(tables, [{"op": "insert", "table": "名册", "values": {"角色": "奥萝拉", "关系": "敌"}}])
    ts.set_meta(tables, "名册", mode="retrieval")
    block = ts.render_tables_block(tables)
    assert "检索表" in block and "奥萝拉" in block  # 身份列值列出供定位
    assert "关系=敌" not in block  # 但完整行不倾倒
    assert ts.retrieval_tables(tables)[0]["name"] == "名册"


def test_row_text_可嵌入():
    tables: list = []
    ts.create_table(tables, "名册", ["角色", "关系"])
    txt = ts.row_text(tables[0], ["奥萝拉", "敌"])
    assert "名册" in txt and "角色=奥萝拉" in txt and "关系=敌" in txt


def test_config_load_save_回退默认(tmp_path):
    base, rid = str(tmp_path), "r"
    assert ts.load_config(base, rid) == ts.DEFAULT_CONFIG  # 缺文件回退
    assert ts.DEFAULT_CONFIG["fillEvery"] == 1
    assert ts.DEFAULT_CONFIG["skipLatest"] == 0
    saved = ts.save_config(base, rid, {"fillEvery": 5, "minReplyLen": -3, "unknown": 9})
    assert saved["fillEvery"] == 5 and saved["minReplyLen"] == 0  # 负数归 0，未知键忽略
    assert ts.load_config(base, rid)["fillEvery"] == 5


def test_round_trip_ai搭车(tmp_path):
    """模拟 AI 尾附 <表格更新>，解析→应用→落盘。"""
    base, rid = str(tmp_path), "r"
    ts.import_template(base, rid, _tpl(), replace=True)
    reply = ('剧情正文。\n<表格更新>[{"op":"insert","table":"背包物品表",'
             '"values":{"物品名称":"符纸","数量":"10"}}]</表格更新>')
    clean, ops = tu.parse_table_block(reply)
    tables = ts.load(base, rid)
    assert ts.apply_ops(tables, ops) == 1
    ts.save(base, rid, tables)
    reloaded = json.loads((tmp_path / rid / ts.TABLES_FILE).read_text(encoding="utf-8"))
    assert reloaded["tables"][0]["rows"][0][0] == "符纸"


def test_角色状态表singleton插入不抹掉未提供列():
    """2026-09-01 用户定案：角色状态表所有参数永久存在，agent 部分字段插入不得把
    其余列抹成空串。"""
    tables = ts.default_tables()
    ts.apply_ops(tables, [
        {"op": "insert", "table": "主角信息表", "values": {
            "姓名": "凌渊", "性别": "男", "当前状态": "修炼中",
        }},
    ])
    protagonist = next(table for table in tables if table["name"] == "主角信息表")
    row = protagonist["rows"][0]
    assert row[0] == "凌渊" and row[1] == "男" and row[7] == "修炼中"  # 当前状态在第 8 列
    # 未提供的列保留旧值（默认空串）
    assert row[2] == "" and row[3] == ""


def test_自建角色类表也禁止agent删行():
    """2026-09-01 用户定案：名字含「角色」的表一律视为角色状态表，agent 删行被拒。"""
    tables = ts.default_tables()
    tables.append({
        "uid": "custom_roles", "name": "角色状态表", "columns": ["角色", "状态"],
        "rows": [["阿尼玛", "在场"]], "rowPolicy": "keyed", "keyCol": "角色",
        "deletePolicy": "delete",
    })
    assert ts.apply_ops(tables, [{"op": "delete", "table": "角色状态表", "key": "阿尼玛"}]) == 0
    custom = next(table for table in tables if table["name"] == "角色状态表")
    assert custom["rows"] == [["阿尼玛", "在场"]]
