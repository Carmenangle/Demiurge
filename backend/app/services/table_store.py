"""通用多表落盘 + 纯逻辑：补 SillyTavern chatSheets 的通用多表能力（背包/技能/任务/角色/选项…）。

按 repo_id 物理隔离：`<base>/<safe repo_id>/tables.json`（与 state.json / chronicle.db 同目录）。
好感度=状态引擎(character_state)、纪要=RAG(narrative_store) 各有单一属主，**不在此重复**；
本模块只管「其余通用表」：模板定 schema（列头 + note），行数据随剧情增删改。

不引 sqlite：AI 每轮吐行级 op（insert/update/delete），apply_ops 应用。纯逻辑
（parse_template/apply_ops/render_tables_block）无 I/O 可单测；load/save/import 是唯一 I/O。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.services.pathnames import safe_seg

TABLES_FILE = "tables.json"
CONFIG_FILE = "table_config.json"
# 填表 6 参数默认值（用户在「数据表」左下角设置面板可调；默认每轮参与）
DEFAULT_CONFIG = {
    "contextTurns": 3,   # 填表回看：本轮触发时提示 AI 结合最近 N 轮事件补记（避免跳轮遗漏）
    "fillEvery": 1,      # 自动填表频率：默认每轮注入填表指令并写回；可调大以省 token
    "chronicleEvery": 3, # 纪要频率：默认每 3 个 assistant 回合生成一条丰富纪要
    "batchTurns": 3,     # 批处理层数：一次填表覆盖的回合跨度（并入回看提示）
    "skipLatest": 0,     # 默认从首轮开始；可调大以跳过开局稀薄内容
    "minReplyLen": 200,  # AI 回复最小长度：短于此的回复不写回表（碎回复无信息量）
    "maxRetry": 3,       # 填表最大重试：解析失败时的容错次数（搭车范式内预留）
}
_ROW_ID_COL = "row_id"  # 模板首列，行号占位——本模块行号隐式给（序号），存储不带此列
# 好感度/纪要由专门引擎接管，模板导入时跳过这些表名（避免与虚拟表重复）
_ENGINE_TABLE_NAMES = {"好感度表", "状态表（好感度/态度）", "角色状态表（好感度/状态）", "纪要表", "纪要表（往事）"}


COL_TEXT = "文本"
COL_NUM = "数字"


MODE_FULL = "full"          # 全量注入：整表现值每轮进提示词（状态表：好感度/背包/任务）
MODE_RETRIEVAL = "retrieval"  # 检索注入：行索引进 RAG，只召回与本轮相关的行（大表：名册/设定/日志）

GLOBAL_TABLE = "全局数据表"
PROTAGONIST_TABLE = "主角信息表"
CHARACTERS_TABLE = "重要角色表"
SKILLS_TABLE = "主角技能表"
INVENTORY_TABLE = "背包物品表"
QUESTS_TABLE = "任务与事件表"
OPTIONS_TABLE = "选项表"
_SINGLETON_TABLES = {GLOBAL_TABLE, PROTAGONIST_TABLE, OPTIONS_TABLE}
_ALWAYS_FILL_TABLES = {GLOBAL_TABLE}
_RETAIN_ON_DELETE_TABLES = {
    GLOBAL_TABLE, PROTAGONIST_TABLE, CHARACTERS_TABLE, SKILLS_TABLE, QUESTS_TABLE, OPTIONS_TABLE,
}


def default_tables() -> list[dict[str, Any]]:
    """新作品默认的剧情资料分类；与状态/纪要两个专用引擎互补，不复制它们的数据。"""
    specs = [
        ("sheet_default_global", GLOBAL_TABLE, ["时间", "地点", "世界状态", "世界规则"],
         "始终只有一张卡，反映上一轮结束后全局有效的时间、地点、世界状态与规则。",
         "每个剧情回合完整替换这张卡；未变化的有效事实也要保留。", "", MODE_FULL),
        ("sheet_default_protagonist", "主角信息表",
         ["姓名", "性别", "年龄", "一句话介绍", "外貌特征", "穿着打扮", "所在地点", "当前状态", "人际关系", "过往经历"],
         "始终只有一张主角卡，记录主角相对稳定的人设与当前处境。", "按自动填表频率更新唯一主角卡，不得新增第二张。", "姓名", MODE_FULL),
        ("sheet_default_characters", "重要角色表",
         ["姓名", "性别", "年龄", "一句话介绍", "外貌特征", "穿着打扮", "所在地点", "在场状态", "人际关系", "过往经历", "当前目标"],
         "每名重要角色一张卡，记录身份、外观、关系、经历与当前目标。", "按自动填表频率处理；新角色新增，同名角色更新，离场或失效也保留。", "姓名", MODE_RETRIEVAL),
        ("sheet_default_skills", SKILLS_TABLE, ["技能名称", "类型", "等级", "效果", "限制", "状态"],
         "记录主角拥有或曾拥有的技能、功法与能力。", "获得时新增，变化时更新；废掉时把状态改为不可用，禁止删除。", "技能名称", MODE_FULL),
        ("sheet_default_inventory", "背包物品表", ["物品名称", "数量", "类别", "效果", "备注"],
         "记录主角持有的物品、装备与消耗品。", "按自动填表频率处理；获得时新增，数量或状态变化时更新，用尽或丢失时删除。", "物品名称", MODE_FULL),
        ("sheet_default_quests", QUESTS_TABLE, ["名称", "类型", "状态", "目标", "进展", "相关人物", "地点", "备注"],
         "记录待办任务、事件线及其最终结果。", "建立时新增、推进时更新；完成或失效后保留并更新状态，禁止删除。", "名称", MODE_FULL),
        ("sheet_default_options", OPTIONS_TABLE, ["后续动作选项", "推导依据"],
         "始终只有一张卡，集中记录 AI 根据当前剧情推导的用户后续动作选项。",
         "按自动填表频率完整替换；所有当前有效选项写在同一张卡内。", "", MODE_FULL),
    ]
    return [
        _table_dict(uid, name, columns, [], note, order, rule=rule,
                    key_col=key_col, mode=mode)
        for order, (uid, name, columns, note, rule, key_col, mode) in enumerate(specs)
    ]


def _table_dict(uid: str, name: str, columns: list[str], rows: list[list[str]],
                note: str = "", order: int = 0, *,
                rule: str = "", col_types: dict[str, str] | None = None,
                key_col: str = "", mode: str = MODE_FULL) -> dict[str, Any]:
    """一张通用表。列 meta：col_types 每列文本/数字（缺省文本）；key_col 身份列名
    （AI/人工 update/delete 可按它定位同一条，替代行号，空=只按行号）。rule=何时增/改/删。
    mode=full 全量注入 / retrieval 行进 RAG 按 query 召回（大表省 token，见 [[card-source-work-decouple]]）。"""
    return {"uid": uid, "name": name, "columns": columns, "rows": rows,
            "note": note, "order": order,
            "rule": rule, "colTypes": col_types or {}, "keyCol": key_col,
            "mode": mode if mode in (MODE_FULL, MODE_RETRIEVAL) else MODE_FULL,
            "rowPolicy": "singleton" if name in _SINGLETON_TABLES else "keyed",
            "alwaysFill": name in _ALWAYS_FILL_TABLES,
            "deletePolicy": "retain" if name in _RETAIN_ON_DELETE_TABLES else "delete"}


def parse_template(data: Any) -> list[dict[str, Any]]:
    """解析 TavernDB chatSheets 模板（JSON）→ 通用表列表（纯逻辑，跳过好感度/纪要引擎表）。

    每个 sheet：name 作表名；content[0] 去掉首列 row_id 作列头；sourceData.note 作说明。
    行数据不导（模板通常只有列头）。非 sheet_ 键（如 mate）跳过。
    """
    out: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        return out
    order = 0
    for uid, sheet in data.items():
        if not (isinstance(uid, str) and uid.startswith("sheet_") and isinstance(sheet, dict)):
            continue
        name = str(sheet.get("name") or "").strip()
        if not name or name in _ENGINE_TABLE_NAMES:
            continue
        content = sheet.get("content")
        header = content[0] if isinstance(content, list) and content and isinstance(content[0], list) else []
        columns = [str(c) for c in header if str(c) != _ROW_ID_COL]
        if not columns:
            continue
        note, key_col = "", ""
        sd = sheet.get("sourceData")
        if isinstance(sd, dict):
            note = str(sd.get("note") or "").strip()
            key_col = _key_from_ddl(str(sd.get("ddl") or ""), columns)
        out.append(_table_dict(uid, name, columns, [], note, order, key_col=key_col))
        order += 1
    return out


def _key_from_ddl(ddl: str, columns: list[str]) -> str:
    """从 DDL 里挑身份列：某列声明含 UNIQUE 且该列(按注释中文名)在 columns 里 → 作 keyCol。

    DDL 形如 `char_name TEXT NOT NULL UNIQUE, -- 角色名`；我们的列头是中文名（注释），
    故取带 UNIQUE 那行的行尾 `-- 中文名` 与 columns 对齐。找不到 → 空串（只按行号）。
    """
    for line in ddl.splitlines():
        if "UNIQUE" not in line.upper():
            continue
        m = re.search(r"--\s*(.+?)\s*$", line)
        if m and m.group(1).strip() in columns:
            return m.group(1).strip()
    return ""


def _find(tables: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for t in tables:
        if t.get("name") == name:
            return t
    return None


def apply_ops(tables: list[dict[str, Any]], ops: Any) -> int:
    """按序应用行级 op（insert/update/delete），原地改 tables，返回成功条数（纯逻辑）。

    op 形状：
    - insert: {"op":"insert","table":"背包物品表","values":{列名:值,...}}
    - update: {"op":"update","table":"背包物品表","row":<0基行号>,"values":{列名:值,...}}
    - delete: {"op":"delete","table":"背包物品表","row":<0基行号>}
    未知表/越界行/非法 op 跳过（不抛）。insert 缺列补空串，多列忽略。
    """
    if not isinstance(ops, list):
        return 0
    done = 0
    for item in ops:
        if not isinstance(item, dict):
            continue
        op = str(item.get("op") or "").strip().lower()
        tbl = _find(tables, str(item.get("table") or "").strip())
        if tbl is None:
            continue
        cols: list[str] = tbl["columns"]
        rows: list[list[str]] = tbl["rows"]
        if op == "insert":
            vals = item.get("values")
            if not isinstance(vals, dict):
                continue
            if tbl.get("rowPolicy") == "singleton":
                rows[:] = [[str(vals.get(c, "")) for c in cols]]
            else:
                existing = _locate(tbl, {"key": vals.get(tbl.get("keyCol") or ""), "values": vals})
                if existing is not None and tbl.get("keyCol"):
                    for i, c in enumerate(cols):
                        if c in vals:
                            rows[existing][i] = str(vals[c])
                else:
                    rows.append([str(vals.get(c, "")) for c in cols])
            done += 1
        elif op == "update":
            vals = item.get("values")
            if not isinstance(vals, dict):
                continue
            idx = 0 if tbl.get("rowPolicy") == "singleton" and rows else _locate(tbl, item)
            if idx is None and tbl.get("rowPolicy") == "singleton":
                rows.append([str(vals.get(c, "")) for c in cols])
                done += 1
                continue
            if idx is None:
                continue
            for i, c in enumerate(cols):
                if c in vals:
                    rows[idx][i] = str(vals[c])
            done += 1
        elif op == "delete":
            idx = _locate(tbl, item)
            if idx is None:
                continue
            if tbl.get("name") == SKILLS_TABLE and "状态" in cols:
                rows[idx][cols.index("状态")] = "不可用"
                done += 1
                continue
            if tbl.get("deletePolicy") == "retain":
                continue
            rows.pop(idx)
            done += 1
    return done


def tables_for_maintenance(tables: list[dict[str, Any]], scheduled: bool) -> list[dict[str, Any]]:
    """写侧门控：每轮维护全局表；到填表轮次再维护其余表。"""
    return list(tables) if scheduled else [table for table in tables if table.get("alwaysFill")]


def tables_for_read(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """读侧不受填表频率影响；full 表读现值，retrieval 表由渲染器只读 schema。"""
    return list(tables)


def tables_for_turn(tables: list[dict[str, Any]], scheduled: bool) -> list[dict[str, Any]]:
    """兼容旧调用；新代码应显式选择 read 或 maintenance。"""
    return tables_for_maintenance(tables, scheduled)


def _locate(tbl: dict[str, Any], item: dict[str, Any]) -> int | None:
    """定位 update/delete 目标行：优先按身份列 key 匹配，回退 0 基行号。都不中 → None。

    身份列存在且 item 带 key（或 values[keyCol]）→ 找该值所在行（首个匹配）；否则用 row 序号。
    """
    rows: list[list[str]] = tbl["rows"]
    key_col = tbl.get("keyCol") or ""
    if key_col and key_col in tbl["columns"]:
        ci = tbl["columns"].index(key_col)
        vals = item.get("values")
        key_val = item.get("key")
        if key_val is None and isinstance(vals, dict):
            key_val = vals.get(key_col)
        if key_val is not None:
            target = str(key_val)
            for i, r in enumerate(rows):
                if (r[ci] if ci < len(r) else "") == target:
                    return i
            return None  # 指名了身份列值却没找到 → 不误伤别的行
    return _row_index(item.get("row"), len(rows))


def _row_index(raw: Any, n: int) -> int | None:
    try:
        idx = int(raw)
    except (TypeError, ValueError):
        return None
    return idx if 0 <= idx < n else None


# ── 建表 / 删表 / 改结构（人工在「数据表」弹窗用；纯逻辑，原地改 tables）──

_UID_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _gen_uid(existing: set[str]) -> str:
    import secrets
    while True:
        uid = "sheet_" + "".join(secrets.choice(_UID_ALPHABET) for _ in range(8))
        if uid not in existing:
            return uid


def create_table(tables: list[dict[str, Any]], name: str, columns: list[str], *,
                 note: str = "", rule: str = "", col_types: dict[str, str] | None = None,
                 key_col: str = "") -> dict[str, Any] | None:
    """新建一张空表并追加到 tables（原地）。返回新表；表名重复或无列 → None（不改）。

    列去重去空；key_col 不在列里则置空；col_types 只留合法列的映射。
    """
    name = (name or "").strip()
    seen: set[str] = set()
    cols: list[str] = []
    for c in columns:
        c = (c or "").strip()
        if c and c not in seen:
            seen.add(c)
            cols.append(c)
    if not name or not cols or _find(tables, name) is not None:
        return None
    ct = {c: (col_types.get(c) or COL_TEXT) for c in cols} if col_types else {}
    kc = key_col if key_col in cols else ""
    uid = _gen_uid({t.get("uid", "") for t in tables})
    order = max((int(t.get("order") or 0) for t in tables), default=-1) + 1
    tbl = _table_dict(uid, name, cols, [], note.strip(), order,
                      rule=rule.strip(), col_types=ct, key_col=kc)
    tables.append(tbl)
    return tbl


def drop_table(tables: list[dict[str, Any]], name: str) -> bool:
    """按表名删整表（原地）。删到返回 True。"""
    t = _find(tables, name)
    if t is None:
        return False
    tables.remove(t)
    return True


def set_meta(tables: list[dict[str, Any]], name: str, *,
             note: str | None = None, rule: str | None = None,
             key_col: str | None = None, mode: str | None = None) -> bool:
    """改某表的说明/规则/身份列/注入模式（原地）。key_col 须是现有列。改到返回 True。"""
    t = _find(tables, name)
    if t is None:
        return False
    if note is not None:
        t["note"] = note.strip()
    if rule is not None:
        t["rule"] = rule.strip()
    if key_col is not None:
        t["keyCol"] = key_col if key_col in t["columns"] else ""
    if mode is not None:
        t["mode"] = mode if mode in (MODE_FULL, MODE_RETRIEVAL) else MODE_FULL
    return True


def render_tables_block(tables: list[dict[str, Any]]) -> str:
    """把各表 schema+说明+规则+现状组装成注入块（供 AI 读懂每表用途再增量更新）。空 → 空串。

    每表带：列头、身份列（有则标）、说明 note、更新规则 rule、当前行（带 0 基行号）。
    note/rule 是 AI 知道"这表记什么、何时增删改"的唯一依据（自建表尤其依赖）。
    检索表（mode=retrieval）只出 schema+说明+规则，不倾倒全部行（行由 RAG 按 query 召回，省 token）；
    仍列身份列已用值清单，供 AI update/delete 定位已存在的行。
    """
    parts: list[str] = []
    for t in tables:
        cols = " | ".join(t["columns"])
        head = f"◆ {t['name']}（列：{cols}）"
        if t.get("rowPolicy") == "singleton":
            head += "（单卡：始终只能有一行）"
        key_col = t.get("keyCol") or ""
        if key_col:
            head += f"（身份列：{key_col}，update/delete 可按它定位）"
        lines = [head]
        if (t.get("note") or "").strip():
            lines.append(f"  说明：{t['note'].strip()}")
        if (t.get("rule") or "").strip():
            lines.append(f"  更新规则：{t['rule'].strip()}")
        if t.get("mode") == MODE_RETRIEVAL:
            lines.append("  （检索表：完整内容按剧情相关性召回，见上文记忆区；此处仅列结构）")
            keys = _key_values(t)
            if keys:
                lines.append("  已有条目（" + (key_col or "行") + "）：" + " / ".join(keys))
        elif t["rows"]:
            for i, r in enumerate(t["rows"]):
                cells = " | ".join(str(c) for c in r)
                lines.append(f"  [{i}] {cells}")
        else:
            lines.append("  （空）")
        parts.append("\n".join(lines))
    if not parts:
        return ""
    return "【当前数据表】\n" + "\n".join(parts)


def _key_values(t: dict[str, Any]) -> list[str]:
    """检索表的身份列已用值清单（无身份列→行号），供 AI 定位已存在的行做 update/delete。"""
    key_col = t.get("keyCol") or ""
    rows: list[list[str]] = t.get("rows") or []
    if key_col and key_col in t["columns"]:
        ci = t["columns"].index(key_col)
        return [str(r[ci]) for r in rows if ci < len(r) and str(r[ci]).strip()]
    return [str(i) for i in range(len(rows))]


def row_text(t: dict[str, Any], row: list[str]) -> str:
    """把一行渲染成"表名｜列=值"的可嵌入文本（供 RAG 索引/召回）。"""
    pairs = " ｜ ".join(f"{c}={row[i]}" for i, c in enumerate(t["columns"]) if i < len(row))
    return f"[{t['name']}] {pairs}"


def recall_retrieval_rows(tables: list[dict[str, Any]], query: str, *, k: int = 5,
                          max_chars: int = 4800) -> list[str]:
    """检索表独立候选池：身份列精确命中优先，其次确定性文本相关度。

    不依赖嵌入配置，因此切换检索模式或本地嵌入暂不可用时也不会静默空召回。
    """
    normalized_query = re.sub(r"\s+", "", query or "").casefold()
    if not normalized_query:
        return []
    ranked: list[tuple[int, int, int, str]] = []
    order = 0
    for table in retrieval_tables(tables):
        key_col = str(table.get("keyCol") or "")
        key_index = table.get("columns", []).index(key_col) if key_col in table.get("columns", []) else -1
        for row in table.get("rows") or []:
            rendered = row_text(table, row)
            compact = re.sub(r"\s+", "", rendered).casefold()
            key = str(row[key_index]).strip().casefold() if 0 <= key_index < len(row) else ""
            exact = 1 if key and key in normalized_query else 0
            terms = [term for term in re.split(r"[^\w\u4e00-\u9fff]+", normalized_query) if len(term) >= 2]
            overlap = sum(1 for term in terms if term in compact)
            if not exact and not overlap and not any(
                normalized_query[i:i + 3] in compact for i in range(max(0, len(normalized_query) - 2))
            ):
                order += 1
                continue
            ranked.append((exact, overlap, -order, rendered))
            order += 1
    out: list[str] = []
    used = 0
    for _exact, _overlap, _order, rendered in sorted(ranked, reverse=True):
        if len(out) >= max(0, k) or (out and used + len(rendered) > max_chars):
            break
        out.append(rendered)
        used += len(rendered)
    return out


def retrieval_tables(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """筛出 mode=retrieval 的表（行需索引进 RAG 的表）。"""
    return [t for t in tables if t.get("mode") == MODE_RETRIEVAL]


# ── I/O：唯一落盘接缝 ──

def tables_path(base: str, repo_id: str) -> Path:
    return Path(base) / safe_seg(repo_id, strip=False) / TABLES_FILE


def load(base: str, repo_id: str) -> list[dict[str, Any]]:
    """读某作品线的通用表；新作品无文件时返回剧情资料默认分类。"""
    if not (base and repo_id):
        return []
    p = tables_path(base, repo_id)
    if not p.is_file():
        return default_tables()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    tables = data.get("tables") if isinstance(data, dict) else None
    if not isinstance(tables, list):
        return []
    for table in tables:
        if isinstance(table, dict):
            _normalize_builtin_table(table)
    return tables


def _normalize_builtin_table(table: dict[str, Any]) -> None:
    """给存量默认表补当前策略，并把旧全局/选项结构无损归并为单卡。"""
    name = str(table.get("name") or "")
    table["rowPolicy"] = "singleton" if name in _SINGLETON_TABLES else "keyed"
    table["alwaysFill"] = name in _ALWAYS_FILL_TABLES
    table["deletePolicy"] = "retain" if name in _RETAIN_ON_DELETE_TABLES else "delete"
    columns = list(table.get("columns") or [])
    rows = list(table.get("rows") or [])
    if name == GLOBAL_TABLE and columns == ["字段", "值", "说明"]:
        values = {str(row[0]): str(row[1]) for row in rows if len(row) >= 2}
        def collect(*marks: str) -> str:
            return "；".join(value for key, value in values.items() if any(mark in key for mark in marks))
        known = {key for key in values if any(mark in key for mark in ("时间", "地点", "位置", "规则", "机制"))}
        world = "；".join(f"{key}：{value}" for key, value in values.items() if key not in known)
        table["columns"] = ["时间", "地点", "世界状态", "世界规则"]
        table["rows"] = [[collect("时间"), collect("地点", "位置"), world, collect("规则", "机制")]] if rows else []
        table["keyCol"] = ""
    elif name == SKILLS_TABLE and "状态" not in columns:
        table["columns"] = [*columns, "状态"]
        table["rows"] = [[*list(row), "可用"] for row in rows]
    elif name == OPTIONS_TABLE and columns == ["选项", "条件", "可能影响", "状态"]:
        lines = []
        for row in rows:
            cells = [str(value) for value in row]
            if cells:
                detail = "；".join(value for value in cells[1:] if value)
                lines.append(cells[0] + (f"（{detail}）" if detail else ""))
        table["columns"] = ["后续动作选项", "推导依据"]
        table["rows"] = [["\n".join(lines), "由既有选项迁移"]] if lines else []
        table["keyCol"] = ""


def save(base: str, repo_id: str, tables: list[dict[str, Any]]) -> None:
    """把通用表写入 <base>/<repo_id>/tables.json。base/repo_id 空则跳过。"""
    if not (base and repo_id):
        return
    p = tables_path(base, repo_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"tables": tables}, ensure_ascii=False, indent=2), encoding="utf-8")


def config_path(base: str, repo_id: str) -> Path:
    return Path(base) / safe_seg(repo_id, strip=False) / CONFIG_FILE


def load_config(base: str, repo_id: str) -> dict[str, int]:
    """读填表 6 参数；缺文件/损坏/缺键 → 回退 DEFAULT_CONFIG。只认已知键，值强转 int。"""
    cfg = dict(DEFAULT_CONFIG)
    if not (base and repo_id):
        return cfg
    p = config_path(base, repo_id)
    if not p.is_file():
        return cfg
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return cfg
    if isinstance(data, dict):
        for k in DEFAULT_CONFIG:
            if k in data:
                try:
                    cfg[k] = int(data[k])
                except (TypeError, ValueError):
                    pass
    return cfg


def save_config(base: str, repo_id: str, cfg: dict[str, Any]) -> dict[str, int]:
    """写填表 6 参数（只落已知键，值强转 int，负数归 0）。返回落盘后的完整配置。"""
    merged = dict(DEFAULT_CONFIG)
    for k in DEFAULT_CONFIG:
        if k in cfg:
            try:
                merged[k] = max(0, int(cfg[k]))
            except (TypeError, ValueError):
                pass
    if base and repo_id:
        p = config_path(base, repo_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def import_template(base: str, repo_id: str, data: Any, *, replace: bool = False) -> int:
    """导入 TavernDB 模板定义通用表 schema。replace=True 覆盖现有表，False 只补新表（按表名去重）。
    返回导入的表数。"""
    parsed = parse_template(data)
    if not parsed:
        return 0
    if replace:
        save(base, repo_id, parsed)
        return len(parsed)
    existing = load(base, repo_id)
    names = {t.get("name") for t in existing}
    added = [t for t in parsed if t.get("name") not in names]
    if added:
        save(base, repo_id, existing + added)
    return len(added)
