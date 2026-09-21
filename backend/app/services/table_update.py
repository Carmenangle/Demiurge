"""通用表上下文、独立维护提示词与旧控制块清洗（纯逻辑）。

主 Roleplay 只读取表格上下文。剧情正文发出后，独立维护调用只返回 JSON ops；旧版
<表格更新> 仅作兼容清洗。本模块不碰 I/O、不碰 LLM、不碰存储（import-linter 强制）。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.services import prompt_clean, table_store

_TAG_OPEN = "<表格更新>"
_TAG_CLOSE = "</表格更新>"
_BLOCK_RE = re.compile(re.escape(_TAG_OPEN) + r"(.*?)" + re.escape(_TAG_CLOSE), re.DOTALL)
_OPEN_TAIL_RE = re.compile(re.escape(_TAG_OPEN) + r"[\s\S]*\Z")


def table_context(tables: list[dict[str, Any]]) -> str:
    """给主 Roleplay 的只读数据表上下文，不要求模型在正文中输出维护数据。"""
    if not tables:
        return ""
    return (
        "\n\n【当前数据表（只读剧情上下文）】\n"
        + table_store.render_tables_block(tables)
        + "\n这些数据只用于保持剧情事实一致；不要在正文中输出表格更新、JSON 或维护说明。"
    )


# ── 维护协议片段（旧数组协议与 2026-09-18 对象协议共用；拼接结果逐字等于旧文案）──
_OPS_FORMAT = (
    '[{"op":"insert","table":"表名","values":{"列名":"值"}},'
    '{"op":"update","table":"表名","row":0,"values":{"列名":"新值"}},'
    '{"op":"delete","table":"表名","key":"身份列值"}]'
)
_OPS_RULES = (
    "。有身份列的表优先用 key 定位，无身份列才用 0 基 row。values 的键必须是列名。"
    "全局数据表必须输出完整现值；单卡表只能保留一行。重要角色同名更新、新角色新增；"
    "技能废除只标不可用，任务完成只更新状态，二者禁止删除；只有背包用尽或丢失时删除。"
    "没有变化时输出 []。"
)
_DELTA_RULES = (
    "state_deltas 为本轮剧情导致的状态变化数组："
    '[{"field":"数值/角色名·好感度","op":"add","value":2,"evidence":"本轮证据"}]。'
    "增量相对当前现值（add 是加多少，不是改后的总数）。单主角好感度用 field=\"数值/好感度\"；"
    "态度/心情/所在等用 field=\"叙事/xxx\" op=\"set\"；同场有多名角色时，每个字段必须写成 "
    "field=\"数值/角色名·好感度\" 或 field=\"叙事/角色名·身体状态\"，"
    "用中点明确角色归属，禁止把身体、精神等状态类别拼进角色名。"
    "字段名必须稳定复用已有字段（好感度、心情、所在、身体状态等），禁止换说法或另建同义字段。"
    "角色名必须是剧情角色本人：禁止用角色卡名称/卡面标题代替角色名；"
    "第一人称的「我/主角/你/玩家」不是剧情角色，主角自身的伤势、情绪等状态只写进状态栏。"
    "每条必须带 evidence 引用本轮剧情依据；本轮无变化则输出 []。"
)
_SNAPSHOT_RULES = (
    "status_snapshot 为本轮完整状态栏文本（一个多行字符串，\\n 表示换行）："
    "依据【上轮状态栏】与本轮剧情推导，栏目与格式和上轮保持一致；必须包含 [好感] 行，"
    "好感数字沿用上轮状态栏现值（系统会用引擎现值覆盖）；"
    "其他数值以上轮状态栏为基叠加本轮变化，禁止整栏重编。"
)


def maintenance_instruction(
    tables: list[dict[str, Any]], *,
    extract_deltas: bool = False, extract_snapshot: bool = False,
) -> str:
    """独立表格维护调用的 system；输出只有 JSON，不与剧情正文共用响应。

    2026-09-18 兜底状态抽取：extract_deltas / extract_snapshot 任一开启时改收 JSON 对象
    {"ops","state_deltas","status_snapshot"}——主协议（<状态更新>/<status>）失守的轮次，
    由本轮本就在跑的维护调用顺带补齐状态写回，0 额外 LLM。开关全关时输出旧协议文案
    （拼接逐字不变，test_table_store 锁定）；无表且无抽取返回空串（上游据此跳过）。
    """
    extract = extract_deltas or extract_snapshot
    if not tables and not extract:
        return ""
    if not extract:
        return (
            "你是剧情数据表维护助手。根据本轮用户输入与已经生成完毕的剧情正文，更新下列数据表。\n"
            + table_store.render_tables_block(tables)
            + "\n\n只输出 JSON 数组，不要解释、不要代码块、不要标签。操作格式："
            + _OPS_FORMAT + _OPS_RULES
        )
    if tables:
        head = (
            "你是剧情数据表维护助手。根据本轮用户输入与已经生成完毕的剧情正文，更新下列数据表。\n"
            + table_store.render_tables_block(tables)
            + "\n\n只输出一个 JSON 对象，不要解释、不要代码块、不要标签："
            + '{"ops":[…表格操作…],"state_deltas":[…状态增量…],"status_snapshot":"…状态栏文本…"}。'
            + "ops 为表格操作数组，操作格式：" + _OPS_FORMAT + _OPS_RULES
        )
    else:
        head = (
            "你是剧情状态维护助手。根据本轮用户输入与已经生成完毕的剧情正文，抽取本轮状态变化。\n"
            "本轮没有数据表，ops 固定输出 []。\n"
            "只输出一个 JSON 对象，不要解释、不要代码块、不要标签："
            + '{"ops":[…],"state_deltas":[…状态增量…],"status_snapshot":"…状态栏文本…"}。'
        )
    tail = [
        _DELTA_RULES if extract_deltas
        else "本轮状态增量已由主协议取得，state_deltas 固定输出 []。",
        _SNAPSHOT_RULES if extract_snapshot
        else "本轮状态栏已由主协议取得，status_snapshot 固定输出空串 \"\"。",
    ]
    return head + "\n" + "\n".join(tail)


def sanitize_ops(ops: list[Any]) -> list[Any]:
    """对维护 ops 的文本值做破甲还原（2026-09-13 用户定案）。

    模型按防拦截预设写正文，ops 的 values / key 引用正文时会把 `@` 拆字标记一并带出；
    表格文本**不参与网关检测** ⇒ 落库必须是可读原文。只清模型输出侧——
    用户在前端手动编辑的表格不经过这里。
    """
    cleaned: list[Any] = []
    for item in ops:
        if not isinstance(item, dict):
            cleaned.append(item)
            continue
        entry = dict(item)
        values = entry.get("values")
        if isinstance(values, dict):
            entry["values"] = {
                key: prompt_clean.sanitize_table_text(val) if isinstance(val, str) else val
                for key, val in values.items()
            }
        key_value = entry.get("key")
        if isinstance(key_value, str):
            entry["key"] = prompt_clean.sanitize_table_text(key_value)
        cleaned.append(entry)
    return cleaned


def _strip_json_fence(raw: str) -> str:
    value = (raw or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I)
    return value


def _sanitize_state_deltas(deltas: list[Any]) -> list[Any]:
    """state_deltas 只清模型输出侧文本（field/evidence 破甲还原，同 sanitize_ops 口径）；
    非字典项丢弃（状态增量结构非法时宁缺勿滥，交由 parse_deltas 兜底）。"""
    cleaned: list[Any] = []
    for item in deltas:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        for key in ("field", "evidence"):
            value = entry.get(key)
            if isinstance(value, str):
                entry[key] = prompt_clean.sanitize_table_text(value)
        cleaned.append(entry)
    return cleaned


def parse_maintenance_payload(raw: str) -> tuple[list[Any] | None, list[Any], str]:
    """解析独立维护响应，兼容两种形态（2026-09-18 兜底状态抽取扩展）。

    - JSON 数组（旧协议）→ (ops, [], "")
    - JSON 对象（扩展协议）{"ops","state_deltas","status_snapshot"} → 三元组，缺键取默认；
      status_snapshot 必须含 "[好感]" 行才采纳（防散文污染快照真源）。
    首位 None 表示截断/坏格式/顶层标量；对象内 ops 键非数组按无表格操作处理
    （state_deltas / status_snapshot 照常生效，状态安全优先于报错）。
    """
    try:
        data = json.loads(_strip_json_fence(raw))
    except (json.JSONDecodeError, ValueError):
        return None, [], ""
    if isinstance(data, list):
        return sanitize_ops(data), [], ""
    if isinstance(data, dict):
        ops = data.get("ops")
        ops = sanitize_ops(ops) if isinstance(ops, list) else []
        deltas = data.get("state_deltas")
        deltas = _sanitize_state_deltas(deltas) if isinstance(deltas, list) else []
        snapshot = data.get("status_snapshot")
        snapshot = snapshot.strip() if isinstance(snapshot, str) else ""
        if "[好感]" not in snapshot:
            snapshot = ""
        return ops, deltas, snapshot
    return None, [], ""


def parse_table_block(reply: str) -> tuple[str, list[Any]]:
    """从叙述里剥离 <表格更新> 块，返回（去块正文, 原始 ops 列表）。

    缺块 → 原文 + []；块内 JSON 坏 → 去块正文 + []（叙述照常，不因解析失败丢内容）。
    """
    m = _BLOCK_RE.search(reply or "")
    if not m:
        tail = _OPEN_TAIL_RE.search(reply or "")
        if tail:
            return (reply or "")[:tail.start()].strip(), []
        return reply, []
    clean = _BLOCK_RE.sub("", reply).strip()
    try:
        raw = json.loads(m.group(1).strip())
    except (json.JSONDecodeError, ValueError):
        return clean, []
    return clean, sanitize_ops(raw) if isinstance(raw, list) else []


def has_table_block(reply: str) -> bool:
    value = reply or ""
    return _BLOCK_RE.search(value) is not None or _OPEN_TAIL_RE.search(value) is not None
