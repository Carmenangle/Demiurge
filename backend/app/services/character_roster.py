"""世界书角色名册（2026-11-10，纯逻辑）。

合集卡的作品绑定卡只是作品名，角色写在世界书条目里；聊天/群聊/多元数据插入
需要同一份「有哪些角色」的名册。本模块只做三件事：
1. 调 `worldbook_store.detect_character_entries` 拿自动识别结果；
2. 应用仓库级覆盖（include/exclude/rename/order，键为条目稳定 uid）；
3. 产出前端管理面板需要的候选清单（所有启用条目的标题 + 是否选中）。

I/O 由调用方负责（作品世界书快照读取在 `chat_room` / 路由层），本模块可纯单测。
"""
from __future__ import annotations

from typing import Any

from app.services import worldbook_edit, worldbook_store

_VALID_MODES = ("auto", "cards", "worldbook")
_DEFAULT_LIMIT = 64
_CONFIDENCE_RANK = {"manual": 4, "high": 3, "medium": 2, "low": 1}
_AUTO_MEMBER_CONFIDENCE = {"high", "medium", "manual"}


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def parse_overrides(raw: object) -> dict[str, Any]:
    """宽松解析仓库记录里的 `characterRoster`；非法字段回落默认，不抛错。"""
    data = raw if isinstance(raw, dict) else {}
    mode = str(data.get("mode") or "auto").strip().lower()
    rename_raw = data.get("rename")
    rename: dict[str, str] = {}
    if isinstance(rename_raw, dict):
        for uid, name in rename_raw.items():
            key = str(uid or "").strip()
            value = str(name or "").strip()
            if key and value:
                rename[key] = value[:32]
    return {
        "mode": mode if mode in _VALID_MODES else "auto",
        "include": _str_list(data.get("include")),
        "exclude": _str_list(data.get("exclude")),
        "order": _str_list(data.get("order")),
        "rename": rename,
    }


def build(
    book: dict[str, Any] | None, *, overrides: object = None,
    exclude_names: set[str] | frozenset[str] | None = None, limit: int = _DEFAULT_LIMIT,
) -> dict[str, Any]:
    """识别并应用覆盖，返回 `{mode, members, candidates}`。

    `members` 供聊天/多元数据插入使用，带 `content`（人设拼装用，API 层剔除）；
    `candidates` 是全部启用条目，供 UI 勾选/补选漏识别角色。
    低置信条目只进候选、不自动进成员池（避免阵营/地点条目污染聊天）；
    同名条目视为同一角色并合并正文（用户口径 2026-11-10）。
    """
    opts = parse_overrides(overrides)
    include = set(opts["include"])
    exclude = set(opts["exclude"])
    rename = opts["rename"]
    limit = max(1, int(limit or _DEFAULT_LIMIT))
    detected = worldbook_store.detect_character_entries(
        book, include_uids=include, exclude_names=exclude_names,
        limit=max(limit, len(include)),
    )
    by_uid = {str(item.get("uid") or ""): item for item in detected}

    merged: dict[str, dict[str, Any]] = {}
    for item in detected:
        uid = str(item.get("uid") or "")
        if uid in exclude:
            continue
        entry = dict(item)
        source_name = str(entry.get("name") or "").strip()
        name = rename.get(uid) or source_name
        if not name:
            continue
        entry["name"] = name
        # 改名兼容：记录条目原名，聊天线程可在「旧线程有历史」时继续沿用。
        entry["source_name"] = source_name or name
        confidence = str(entry.get("confidence") or "low")
        if confidence not in _AUTO_MEMBER_CONFIDENCE:
            continue
        entry["uids"] = [uid]
        existing = merged.get(name)
        if existing is None:
            merged[name] = entry
            continue
        # 同一名角色的多个条目：合并为一个人设，避免名册里出现两个同名头像。
        existing["content"] = (
            str(existing.get("content") or "") + "\n" + str(entry.get("content") or "")
        ).strip()[:2400]
        existing["has_visual"] = bool(existing.get("has_visual")) or bool(entry.get("has_visual"))
        existing["gender"] = existing.get("gender") or entry.get("gender") or ""
        uids = list(existing.get("uids") or [])
        uids.append(uid)
        existing["uids"] = uids
        if _CONFIDENCE_RANK.get(confidence, 0) > _CONFIDENCE_RANK.get(
            str(existing.get("confidence") or ""), 0
        ):
            existing["confidence"] = confidence
    members = list(merged.values())

    order = opts["order"]
    if order:
        rank = {uid: index for index, uid in enumerate(order)}
        members.sort(key=lambda item: (
            min((rank.get(uid, 10 ** 6) for uid in item.get("uids") or []), default=10 ** 6),
            int(item.get("index") or 0),
        ))
    members = members[:limit]

    included_uids = {
        str(uid) for item in members for uid in (item.get("uids") or [])
    }
    candidates: list[dict[str, Any]] = []
    for item in worldbook_edit.list_entries(book or {}):
        if not item.get("enabled"):
            continue
        uid = str(item.get("uid") or "")
        hit = by_uid.get(uid)
        candidates.append({
            "uid": uid,
            "index": int(item.get("index") or 0),
            "comment": str(item.get("comment") or ""),
            "name": str(hit.get("name") or "") if hit else "",
            "confidence": str(hit.get("confidence") or "") if hit else "",
            "selected": uid in included_uids,
        })
    return {"mode": opts["mode"], "members": members, "candidates": candidates}


def public_payload(roster: dict[str, Any]) -> dict[str, Any]:
    """去掉只为拼人设保留的 `content`，供 HTTP/SSE 下发。"""
    members = [
        {key: value for key, value in item.items() if key != "content"}
        for item in (roster.get("members") or [])
        if isinstance(item, dict)
    ]
    return {**roster, "members": members}
