"""关键事件 / 纪念装扮的持久账本（2026-11-10）。

背景：纪要每 cadence 个回合压成一条 300 字摘要，婚礼/婚纱这类关键事件可能被同窗口
更激烈的剧情挤掉；重建索引时模型也可能再次漏写。这里把「值得记的事件」从摘要里解耦：

1. **主路径（模型判断，不穷举词表）**：正文维护模型在更新表格的同一个 JSON 里顺带输出
   `key_events`（title/detail/turn/characters），按事件类型判断——关系里程碑、身份/命运
   转折、重大得失、关键仪式与装扮、重大冲突、第一次等；词表只做兜底。
2. **兜底路径（零 LLM）**：扫描用户明确点名且正文实际出现的高置信关键词（婚礼/婚纱/
   礼服等），防止维护模型没给 key_events 时漏掉最明显的事件。
3. 两条路径都写入「任务与事件表」永久行（该表规则本身禁止删除），同一回合按名称 upsert；
   私聊/群聊资料块优先注入，重建纪要、整理索引都不会冲掉。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, Protocol

from app.services import prompt_clean, table_store

_LOG = logging.getLogger("uvicorn.error")

# 兜底词表：只收「高置信仪式 / 纪念 / 关系里程碑 / 非常规装扮」，不要求穷举；
# 新增事件类型优先走 `table_update.KEY_EVENT_GUIDE` 让维护模型判断。
_KEY_EVENT_RE = re.compile(
    r"婚礼|婚纱照|婚纱|结婚照|结婚|誓约|订婚|求婚|蜜月|纪念日|生日|成人礼|毕业典礼|"
    r"告白|表白|初吻|第一次约会|初次约会|第一次见面|初次见面|见家长|"
    r"嫁衣|头纱|晚礼服|礼服|旗袍|和服|兔女郎|泳装|女仆装"
)
_THINK_RE = re.compile(r"<think\b[^>]*>[\s\S]*?</think\s*>", re.IGNORECASE)
_STATUS_RE = re.compile(r"<status\b[^>]*>[\s\S]*?</status\s*>", re.IGNORECASE)
_CONTENT_TAG_RE = re.compile(r"</?content\b[^>]*>", re.IGNORECASE)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_MAX_USER_CHARS = 120
_MAX_PROGRESS_CHARS = 400
_MAX_TITLE_CHARS = 40
# 摘要摘录优先抓「关键装扮」而不是仪式名：用户最怕丢的是婚纱/礼服长什么样。
_OUTFIT_PRIORITY = (
    "婚纱照", "婚纱", "嫁衣", "头纱", "晚礼服", "礼服", "旗袍", "和服",
    "兔女郎", "泳装", "女仆装",
)
_EVENT_NOTE = "关键事件/关键装扮；重建索引与整理索引都必须保留，禁止删除或覆盖。"


class _TurnLike(Protocol):
    turn: int
    user: str
    assistant: str


def _clean(text: str) -> str:
    """去掉思考/状态块并还原预设拆字标记，只留可读正文。"""
    value = _THINK_RE.sub("", text or "")
    value = _STATUS_RE.sub("", value)
    value = _CONTENT_TAG_RE.sub("", value)
    return prompt_clean.restore_jailbreak(value).strip()


def detect(text: str) -> list[str]:
    """返回文本里出现的关键事件兜底关键词（去重、保序）。"""
    seen: list[str] = []
    for match in _KEY_EVENT_RE.finditer(text or ""):
        word = match.group(0)
        if word not in seen:
            seen.append(word)
    return seen


def parse_events(raw: str) -> list[dict[str, Any]]:
    """从维护/填表响应的 JSON 对象里取 `key_events`（兼容单对象）。

    只做形状提取，不校验业务字段；坏 JSON/缺键返回 []，不影响表格 ops 与正文。
    """
    text = _FENCE_RE.sub("", (raw or "").strip())
    if not text:
        return []
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    value = data.get("key_events") if isinstance(data, dict) else None
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _excerpt(text: str, labels: Iterable[str], limit: int = _MAX_PROGRESS_CHARS) -> str:
    """优先截取关键词附近的原文（保留装扮/仪式细节），没有关键词就取开头。"""
    clean = _clean(text)
    if not clean:
        return ""
    ordered = sorted(
        labels,
        key=lambda label: _OUTFIT_PRIORITY.index(label)
        if label in _OUTFIT_PRIORITY else len(_OUTFIT_PRIORITY),
    )
    for label in ordered:
        index = clean.find(label)
        if index < 0:
            continue
        start = max(0, index - 80)
        end = min(len(clean), index + 140)
        snippet = clean[start:end].strip()
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(clean) else ""
        return (prefix + snippet + suffix)[:limit]
    return clean[:limit]


def _field_index(table: dict[str, Any], name: str) -> int:
    columns = [str(column) for column in (table.get("columns") or [])]
    return columns.index(name) if name in columns else -1


def _first_cell(tables: list[dict[str, Any]], table_name: str, column: str) -> str:
    for table in tables:
        if str(table.get("name") or "") != table_name:
            continue
        index = _field_index(table, column)
        if index < 0:
            continue
        for row in table.get("rows") or []:
            if index < len(row) and str(row[index]).strip():
                return str(row[index]).strip()
    return ""


def _characters_in(tables: list[dict[str, Any]], text: str) -> str:
    for table in tables:
        if str(table.get("name") or "") != table_store.CHARACTERS_TABLE:
            continue
        index = _field_index(table, "姓名")
        if index < 0:
            return ""
        names = [
            str(row[index]).strip()
            for row in table.get("rows") or []
            if index < len(row) and str(row[index]).strip()
            and str(row[index]).strip() in text
        ]
        return "、".join(dict.fromkeys(names))
    return ""


def _as_turn(value: Any, default: int) -> int:
    try:
        turn = int(value)
    except (TypeError, ValueError):
        turn = 0
    if turn > 0:
        return turn
    try:
        return max(0, int(default or 0))
    except (TypeError, ValueError):
        return 0


def _row_from_values(
    tables: list[dict[str, Any]], *, turn: int, title: str, detail: str,
    characters: str = "", goal: str = "", note: str = _EVENT_NOTE,
) -> dict[str, str]:
    clean_title = (title or "").strip()[:_MAX_TITLE_CHARS] or "关键事件"
    return {
        "名称": f"[关键事件] 第{turn}回合·{clean_title}",
        "类型": "关键事件",
        "状态": "已完成",
        "目标": (goal or "").strip()[:_MAX_USER_CHARS],
        "进展": (detail or "").strip()[:_MAX_PROGRESS_CHARS],
        "相关人物": characters or _characters_in(tables, clean_title + detail),
        "地点": _first_cell(tables, table_store.GLOBAL_TABLE, "地点"),
        "备注": note,
    }


def _row_from_turn(
    tables: list[dict[str, Any]], turn: _TurnLike, labels: list[str],
) -> dict[str, str]:
    return _row_from_values(
        tables, turn=turn.turn, title="/".join(labels[:3]),
        detail=_excerpt(turn.assistant, labels), goal=turn.user,
    )


def _row_from_event(
    tables: list[dict[str, Any]], event: dict[str, Any], *, default_turn: int,
) -> dict[str, str] | None:
    title = str(event.get("title") or event.get("name") or "").strip()
    if not title:
        return None
    detail = str(
        event.get("detail") or event.get("description")
        or event.get("progress") or ""
    ).strip() or title
    raw_characters = event.get("characters")
    characters = ""
    if isinstance(raw_characters, list):
        characters = "、".join(
            str(name).strip() for name in raw_characters if str(name).strip()
        )
    return _row_from_values(
        tables,
        turn=_as_turn(event.get("turn"), default_turn),
        title=title,
        detail=detail,
        characters=characters,
        goal=str(event.get("goal") or ""),
    )


def _upsert_row(table: dict[str, Any], values: dict[str, str]) -> bool:
    """按「名称」身份列 upsert；返回是否发生变化。"""
    columns = [str(column) for column in (table.get("columns") or [])]
    if not columns:
        return False
    row = [values.get(column, "") for column in columns]
    name_index = _field_index(table, "名称")
    if name_index < 0:
        name_index = 0
    key = row[name_index] if name_index < len(row) else ""
    rows = table.setdefault("rows", [])
    for index, existing in enumerate(rows):
        if name_index < len(existing) and str(existing[name_index]).strip() == key:
            if list(existing) == row:
                return False
            rows[index] = row
            return True
    rows.append(row)
    return True


def _write_rows(base: str, repo_id: str, tables: list[dict[str, Any]],
                quests: dict[str, Any], rows: list[dict[str, str]]) -> int:
    changed = sum(1 for row in rows if _upsert_row(quests, row))
    if not changed:
        return 0
    try:
        table_store.save(base, repo_id, tables)
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 账本失败不阻断正文/重建
        _LOG.warning("关键事件账本写入失败 repo=%s: %s", repo_id, exc)
        return 0
    return changed


def record_judged(
    base: str, repo_id: str, events: list[dict[str, Any]], *, default_turn: int = 0,
) -> int:
    """写入维护模型判定的 key_events；返回新增/更新行数。"""
    if not (base and repo_id and events):
        return 0
    tables = table_store.load(base, repo_id)
    quests = next(
        (table for table in tables if str(table.get("name") or "") == table_store.QUESTS_TABLE),
        None,
    )
    if quests is None:
        return 0
    rows = [
        row for event in events
        if isinstance(event, dict)
        and (row := _row_from_event(tables, event, default_turn=default_turn))
    ]
    return _write_rows(base, repo_id, tables, quests, rows)


def record_turn(
    base: str, repo_id: str, *, turn: int, user: str, assistant: str,
    events: list[dict[str, Any]] | None = None,
) -> int:
    """记录单个回合；模型给了 key_events 就按判定写，否则走高置信词表兜底。"""
    if not (base and repo_id) or turn <= 0:
        return 0
    if events:
        return record_judged(base, repo_id, events, default_turn=turn)
    labels = detect(user)
    if not labels:
        return 0
    # 用户点了名、但正文没有实际发生（例如「以后想办婚礼」）⇒ 不记成已完成事件。
    # 只看清洗后的正文：<think> 里提到婚纱不算实际发生。
    if not detect(_clean(assistant)):
        return 0
    tables = table_store.load(base, repo_id)
    quests = next(
        (table for table in tables if str(table.get("name") or "") == table_store.QUESTS_TABLE),
        None,
    )
    if quests is None:
        return 0
    row = _row_from_values(
        tables, turn=turn, title="/".join(labels[:3]),
        detail=_excerpt(assistant, labels), goal=user,
    )
    return _write_rows(base, repo_id, tables, quests, [row])


def backfill_turns(
    base: str,
    repo_id: str,
    turns: Iterable[_TurnLike],
    *,
    ranges: Iterable[tuple[int, int]] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> int:
    """扫描给定回合补齐关键事件；优先用模型判定，词表只补模型没覆盖的回合。

    供「重建索引」复用：即使纪要摘要漏掉婚礼/婚纱，任务与事件表仍会留下永久记录。
    """
    if not (base and repo_id):
        return 0
    spans = list(ranges or [])
    selected = [
        turn for turn in turns
        if not spans or any(start <= turn.turn <= end for start, end in spans)
    ]
    if not selected and not events:
        return 0
    tables = table_store.load(base, repo_id)
    quests = next(
        (table for table in tables if str(table.get("name") or "") == table_store.QUESTS_TABLE),
        None,
    )
    if quests is None:
        return 0
    rows: list[dict[str, str]] = []
    covered_turns: set[int] = set()
    for event in events or []:
        if not isinstance(event, dict):
            continue
        event_turn = _as_turn(event.get("turn"), 0)
        if spans and event_turn and not any(
                start <= event_turn <= end for start, end in spans):
            continue
        row = _row_from_event(tables, event, default_turn=0)
        if row is not None:
            rows.append(row)
            if event_turn:
                covered_turns.add(event_turn)
    for turn in selected:
        if turn.turn in covered_turns:
            continue
        labels = detect(turn.user)
        if not labels or not detect(_clean(turn.assistant)):
            continue
        rows.append(_row_from_turn(tables, turn, labels))
    return _write_rows(base, repo_id, tables, quests, rows)
