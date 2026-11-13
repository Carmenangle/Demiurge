"""Cast（角色名册）/ Presence（在场）选角合同（2026-11-09）。

纯逻辑、零 I/O：调用方负责把「用户点名、世界书角色名、最近剧情回退、最新状态栏
在场」四类证据算好传进来；本模块只决定本轮注入哪些角色卡，以及每个角色的入选来源。

为什么单独成层：剧情、聊天、群聊与出图都需要同一份「谁在场 / 谁入选」，
继续散在 `_resolve_personas` 里会让聊天/群聊另写一套口径。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CastView:
    """一次选角结果。顺序即绑定卡顺序（开场卡在前）。"""
    members: list[str]
    present: list[str]
    selected: list[str]
    responders: list[str]
    sources: dict[str, str] = field(default_factory=dict)


def _unique(names: Sequence[str]) -> list[str]:
    out: list[str] = []
    for raw in names:
        name = str(raw or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def resolve_cast(
    members: Sequence[str],
    *,
    mentioned: Sequence[str] = (),
    worldbook: Sequence[str] = (),
    fallback: Sequence[str] = (),
    present: Sequence[str] = (),
    opening_only: bool = False,
    opening_name: str = "",
) -> CastView:
    """按证据强弱选角。

    证据顺序（强 → 弱）：
    1. 用户本轮点名 / 世界书本轮精确命中角色名；
    2. 最近一条 AI 剧情回退；
    3. 只绑一张卡时未点名也注入（合集卡兼容）；
    4. 最新 `<status>` 的 `[在场]`（本轮新增；只补未入选的在场卡）。

    多卡纪律：`present` 只补未入选的绑定卡，不把未在场/未点名的卡塞进来；
    是否真正注入仍由调用方的内容闸门（description 非空）与预算决定。
    """
    names = _unique(members)
    present_set = set(present)
    present_names = [name for name in names if name in present_set]
    if opening_only:
        selected = [opening_name] if opening_name in names else []
        return CastView(
            members=names,
            present=present_names,
            selected=selected,
            responders=list(selected),
            sources={opening_name: "opening"} if selected else {},
        )

    mentioned_set = set(mentioned)
    worldbook_set = set(worldbook)
    existing = [
        name for name in names
        if name in mentioned_set or name in worldbook_set
    ]
    sources: dict[str, str] = {}
    for name in existing:
        sources[name] = "mention" if name in mentioned_set else "worldbook"

    if not existing and fallback:
        fallback_set = set(fallback)
        existing = [name for name in names if name in fallback_set]
        for name in existing:
            sources[name] = "fallback"

    if not existing and len(names) == 1:
        existing = list(names)
        sources[names[0]] = "single_card"

    responders = list(existing) or list(present_names)
    selected = list(existing)
    for name in present_names:
        if name in selected:
            continue
        selected.append(name)
        sources[name] = "present"

    return CastView(
        members=names,
        present=present_names,
        selected=selected,
        responders=responders,
        sources=sources,
    )
