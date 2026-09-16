"""JSON 小文件仓库的公共读写原语（P9 收口）。

全仓原有 ≥7 处逐行同构的「自己拼路径 → `json.loads` → `isinstance` 兜底 → 补 id →
整体覆盖写」（`mcp_store` / `regex_store` / `agent_store` / `skills_store` /
`builtin_agents` / `palette_pref` / `prose_style`）。本模块是这些**形状**的单一属主：
只收「怎么读、怎么兜、怎么写」，判定逻辑、字段语义、文案一律留调用方。

⚠ `errors` 刻意**不给默认值**，由调用方显式传入自己原有的容错面（见 `ERRORS_*`）：
这 7 处的 `except` 元组并不一致 —— 列表型四仓库与 `builtin_agents` 收尾是 `TypeError`，
`palette_pref` / `prose_style` 收尾是 `OSError`。**并成并集不是等价重构**：
并集会把 `OSError`（文件被占用 / 权限不足）从**裸异常 500** 静默成**空结果**，
属于行为改动。故原语不替调用方选容错面。
`tests/test_json_file_store.py::test_读盘OSError按各模块原容错面走` 把这条差异钉死。
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

# 异常元组的类型别名（`except <变量>` 需要 `tuple[type[BaseException], ...]`）
ErrorTuple = tuple[type[BaseException], ...]

# 历史容错面 A：列表型四仓库 + `builtin_agents.load_overrides` 的收尾是 `TypeError`，**不含** `OSError`
ERRORS_STANDARD: ErrorTuple = (json.JSONDecodeError, UnicodeDecodeError, TypeError)

# 历史容错面 B：`palette_pref` / `prose_style` 的收尾是 `OSError`（即多吞「读盘失败」这一档）
ERRORS_WITH_OS: ErrorTuple = (json.JSONDecodeError, UnicodeDecodeError, OSError)

# 落盘格式（**契约，不随调用方变**）：UTF-8 + ensure_ascii=False + indent=2。改这里等于改 7 处落盘格式。
_JSON_DUMP_ARGS: dict = {"ensure_ascii": False, "indent": 2}


def read_json(path: Path, *, errors: ErrorTuple) -> object | None:
    """读 JSON。文件缺失 → `None`；解析 / 编码失败 → `None`。

    **绝不抛**（容错面由 `errors` 决定），也**不**在这里做类型校验 —— 类型兜底见
    `read_list` / `read_dict`（各调用方原本就是「解析」与「类型」两段分开判的）。
    """
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except errors:
        return None


def read_list(path: Path, *, errors: ErrorTuple) -> list:
    """缺失 / 损坏 / 顶层非 list ⇒ `[]`（**不**抛、**不**强转）。"""
    data = read_json(path, errors=errors)
    return data if isinstance(data, list) else []


def read_dict(path: Path, *, errors: ErrorTuple) -> dict:
    """缺失 / 损坏 / 顶层非 dict ⇒ `{}`。"""
    data = read_json(path, errors=errors)
    return data if isinstance(data, dict) else {}


def normalize_items(items: Iterable[object] | None, *, id_field: str = "id") -> list[dict]:
    """丢弃非 dict 项，为缺 `id_field` 的项补 `uuid4().hex`。

    返回**新列表 + 新字典**：调用方传进来的 dict 不会被就地补 id（与内联实现同效）。
    """
    normalized: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        copy = dict(item)
        if not copy.get(id_field):
            copy[id_field] = uuid4().hex
        normalized.append(copy)
    return normalized


def write_json(path: Path, data: object) -> None:
    """整体覆盖写（父目录自动 `mkdir(parents=True)`）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, **_JSON_DUMP_ARGS), encoding="utf-8")


def write_list(path: Path, items: Iterable[object] | None, *, id_field: str = "id") -> list[dict]:
    """归一化（补 id / 丢非 dict）→ 整体覆盖写；返回落盘后的列表。"""
    normalized = normalize_items(items, id_field=id_field)
    write_json(path, normalized)
    return normalized


def find_by_id(items: list[dict], item_id: str) -> dict | None:
    """列表内按 `id` 取第一条命中，未命中 → `None`。

    刻意**不做** `isinstance` 校验：与内联写法逐字同形（脏文件里混进非 dict 时行为不变）。
    """
    for item in items:
        if item.get("id") == item_id:
            return item
    return None
