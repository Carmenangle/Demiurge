"""通用表上下文、独立维护提示词与旧控制块清洗（纯逻辑）。

主 Roleplay 只读取表格上下文。剧情正文发出后，独立维护调用只返回 JSON ops；旧版
<表格更新> 仅作兼容清洗。本模块不碰 I/O、不碰 LLM、不碰存储（import-linter 强制）。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.services import table_store

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


def maintenance_instruction(tables: list[dict[str, Any]]) -> str:
    """独立表格维护调用的 system；输出只有 JSON，不与剧情正文共用响应。"""
    if not tables:
        return ""
    return (
        "你是剧情数据表维护助手。根据本轮用户输入与已经生成完毕的剧情正文，更新下列数据表。\n"
        + table_store.render_tables_block(tables)
        + "\n\n只输出 JSON 数组，不要解释、不要代码块、不要标签。操作格式："
        + '[{"op":"insert","table":"表名","values":{"列名":"值"}},'
        + '{"op":"update","table":"表名","row":0,"values":{"列名":"新值"}},'
        + '{"op":"delete","table":"表名","key":"身份列值"}]。'
        + "有身份列的表优先用 key 定位，无身份列才用 0 基 row。values 的键必须是列名。"
        + "全局数据表必须输出完整现值；单卡表只能保留一行。重要角色同名更新、新角色新增；"
        + "技能废除只标不可用，任务完成只更新状态，二者禁止删除；只有背包用尽或丢失时删除。"
        + "没有变化时输出 []。"
    )


def parse_maintenance_ops(raw: str) -> list[Any] | None:
    """解析独立维护响应；None 表示截断/坏格式，[] 表示有效的无更新。"""
    value = (raw or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I)
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, list) else None


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
    return clean, raw if isinstance(raw, list) else []


def has_table_block(reply: str) -> bool:
    value = reply or ""
    return _BLOCK_RE.search(value) is not None or _OPEN_TAIL_RE.search(value) is not None
