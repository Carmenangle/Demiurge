"""LLM 场景卡抽取（2026-11-12）。

规则抽取 `scene_card.extract_scene_card` 只能覆盖词典里写到的物件；自由正文里的
结构、陈设、方位与关系必须靠对话模型补全。本模块把「场景窗口 + 规则基线」交给
模型，产出**可审计的结构化场景卡**：

- 内容：建筑结构 / 固定陈设 / 颜色 / 光线；
- 结构：空间类型、门窗墙面、地面与顶面；
- 排列：`layout` 分区 + `relations` 物件间关系（从哪到哪、谁靠着谁）。

防幻觉：每条建筑/陈设/关系都必须带正文逐字或近逐字的 `evidence`；过不了证据
校验的条目直接丢弃。模型失败/JSON 非法时返回规则卡，不阻塞出图。
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable

GenerateText = Callable[[str, str], str]

_SYSTEM = """\
你是影视美术指导，只做“空场景地理勘察”，不写人物、动作、剧情或心理。
从用户给出的正文里提取这个场景的可见环境信息，输出**纯 JSON**（不要 markdown 代码块）。

字段：
- space_type: 空间类型英文短词（warehouse / bedroom / chapel / alley …）。
- architecture: 建筑结构（门、窗、墙、门框、横梁、地板、天花板、楼梯、栏杆…）。
- fixed_props: 固定或半固定陈设（台子、红毯、桌子、床、柜子、梯子、花桶、吊灯…）。
- layout: 分区方位（left/right/center/back/entrance/foreground/background/upper/lower）。
- relations: 物件之间的空间关系，每条一句完整英文，如
  "red carpet runs from the side door to the wooden steps at the central platform"。
- lighting: sources/direction/quality/time/weather。
- palette: 环境主色英文短词。
- camera: shot_size/height/angle/depth（场景建立图默认 wide / eye level / deep）。
- missing: 正文完全没写、但出图需要的信息。
- inferred: 正文没明说、你按常识补的默认（例如垫高的台子需要台阶）。

硬规则：
1. 每条 architecture / fixed_props / relations 必须有 `evidence`，且是正文**逐字片段**
   （10–60 字）；找不到原文证据的条目不要输出。
2. item / position / condition 用英文短词；position 优先用上面 layout 的枚举，
   写不清就 "unspecified"。
3. condition 写物件状态：half-dismantled with missing planks / ajar / open / closed /
   worn / remaining / raised 等；正文没写就留空。
4. 只写这个场景里真实出现的东西；不要补剧情人物、不要写人数、不要写动作。
5. 输出 JSON 对象，不要解释。"""


_SCHEMA_HINT = {
    "space_type": "warehouse",
    "architecture": [
        {"item": "wooden door", "position": "entrance",
         "condition": "half-dismantled with missing planks",
         "evidence": "正文逐字片段"},
    ],
    "fixed_props": [
        {"item": "raised wooden platform", "position": "center", "condition": "raised",
         "evidence": "正文逐字片段"},
    ],
    "layout": {"entrance": "wooden door", "center": "raised wooden platform"},
    "relations": [
        {"text": "red carpet runs from the side door to the wooden steps at the platform",
         "evidence": "正文逐字片段"},
    ],
    "lighting": {"sources": [], "direction": "", "quality": "", "time": "", "weather": ""},
    "palette": [],
    "camera": {"shot_size": "wide", "height": "eye level", "angle": "eye level",
               "depth": "deep"},
    "missing": [],
    "inferred": ["platform_steps"],
}


def _clean(text: str) -> str:
    return re.sub(r"[\s，。！？!?；;：:、“”\"'（）()…—\-]+", "", str(text or ""))


def _evidence_ok(evidence: str, window: str) -> bool:
    """证据必须是正文逐字片段；轻微改写按 4 字片段重合 ≥ 0.5 放行。"""
    clean_evidence = _clean(evidence)
    clean_window = _clean(window)
    if len(clean_evidence) < 2 or not clean_window:
        return False
    if clean_evidence in clean_window:
        return True
    grams = {clean_evidence[index:index + 4] for index in range(len(clean_evidence) - 3)}
    if not grams:
        return False
    return sum(1 for gram in grams if gram in clean_window) / len(grams) >= 0.5


def _parse_json(raw: str) -> dict | None:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _as_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clean_entries(raw: object, window: str, *, kind: str) -> list[dict]:
    entries: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = _as_str(item.get("item"))
        evidence = _as_str(item.get("evidence"))
        if not name or not _evidence_ok(evidence, window):
            continue
        entries.append({
            "item": name,
            "kind": kind,
            "position": _as_str(item.get("position")) or "unspecified",
            "condition": _as_str(item.get("condition")),
            "evidence": evidence,
        })
    return entries


def _clean_relations(raw: object, window: str) -> list[dict]:
    relations: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text = _as_str(item.get("text"))
        evidence = _as_str(item.get("evidence"))
        if text and _evidence_ok(evidence, window):
            relations.append({"text": text, "evidence": evidence})
    return relations


def _clean_layout(raw: object) -> dict[str, str]:
    allowed = {"left", "right", "center", "back", "entrance", "foreground", "background",
               "upper", "lower"}
    layout: dict[str, str] = {}
    for key, value in (raw if isinstance(raw, dict) else {}).items():
        name = _as_str(key)
        text = _as_str(value)
        if name in allowed and text:
            layout[name] = text
    return layout


def _clean_lighting(raw: object, fallback: dict) -> dict:
    result = dict(fallback or {})
    if not isinstance(raw, dict):
        return result
    sources = raw.get("sources")
    if isinstance(sources, list):
        result["sources"] = [str(item).strip() for item in sources if str(item).strip()]
    for key in ("direction", "quality", "time", "weather"):
        value = _as_str(raw.get(key))
        if value:
            result[key] = value
    return result


def _clean_camera(raw: object, fallback: dict) -> dict:
    result = dict(fallback or {})
    if isinstance(raw, dict):
        for key in ("shot_size", "height", "angle", "depth"):
            value = _as_str(raw.get(key))
            if value:
                result[key] = value
    result.setdefault("shot_size", "wide")
    result.setdefault("height", "eye level")
    result.setdefault("angle", "eye level")
    result.setdefault("depth", "deep")
    return result


_SYNONYMS = {
    "gauze": "drapery",
    "drape": "drapery",
    "draperies": "drapery",
    "rug": "carpet",
    "mat": "carpet",
    "settee": "sofa",
    "couch": "sofa",
    "wardrobe": "closet",
}


def _tokens(text: str) -> set[str]:
    # 只去冠词：保留 wooden/raised 等限定词，否则「wooden door」会被误判成「side door」。
    stop = {"the", "a", "an"}
    return {
        _SYNONYMS.get(token, token)
        for token in re.split(r"[^a-z0-9]+", str(text or "").lower())
        if token and token not in stop
    }


def _same_item(left: str, right: str) -> bool:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return str(left).strip().lower() == str(right).strip().lower()
    return left_tokens <= right_tokens or right_tokens <= left_tokens


def _merge_condition(old: str, new: str) -> str:
    if not old:
        return new
    if not new or new in old:
        return old
    if old in new:
        return new
    return f"{old}; {new}"


def _merge_entries(primary: list[dict], secondary: list[dict]) -> list[dict]:
    merged = [dict(entry) for entry in primary]
    for entry in secondary:
        match = next(
            (item for item in merged if _same_item(item["item"], entry["item"])), None,
        )
        if match is None:
            merged.append(dict(entry))
            continue
        if str(match.get("position") or "unspecified") == "unspecified":
            position = str(entry.get("position") or "")
            if position and position != "unspecified":
                match["position"] = position
        match["condition"] = _merge_condition(
            str(match.get("condition") or ""), str(entry.get("condition") or ""),
        )
    return merged


def _merge_across_lists(architecture: list[dict], fixed_props: list[dict]) -> list[dict]:
    """同一物件同时被写进建筑和陈设时只留建筑表，避免梯子/门画两遍。"""
    props: list[dict] = []
    for prop in fixed_props:
        match = next(
            (item for item in architecture if _same_item(item["item"], prop["item"])), None,
        )
        if match is None:
            props.append(prop)
            continue
        match["condition"] = _merge_condition(
            str(match.get("condition") or ""), str(prop.get("condition") or ""),
        )
        if str(match.get("position") or "unspecified") == "unspecified":
            position = str(prop.get("position") or "")
            if position and position != "unspecified":
                match["position"] = position
    return props


def _dedupe_list(items: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        key = re.sub(r"[\s_]+", " ", text.lower())
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _recompute_missing(card: dict) -> list[str]:
    missing: list[str] = []
    if not card.get("space_type"):
        missing.append("space_type")
    if not card.get("architecture"):
        missing.append("architecture")
    if not card.get("fixed_props"):
        missing.append("fixed_props")
    lighting = card.get("lighting") or {}
    if not (
        lighting.get("sources") or lighting.get("quality")
        or lighting.get("direction") or lighting.get("time")
    ):
        missing.append("lighting")
    if not card.get("palette"):
        missing.append("palette")
    for item in card.get("missing") or []:
        text = str(item or "").strip()
        low = text.lower()
        if not text:
            continue
        if low in {"space_type", "architecture", "fixed_props", "lighting", "palette"}:
            continue
        if "lighting" in low and "lighting" not in missing:
            continue
        if "palette" in low and "palette" not in missing:
            continue
        missing.append(text)
    return _dedupe_list(missing)


def _finalize(card: dict, *, extractor: str) -> dict:
    landmarks = [str(item) for item in (card.get("landmarks") or []) if str(item).strip()]
    items = [
        str(entry.get("item") or "").strip()
        for entry in [*(card.get("architecture") or []), *(card.get("fixed_props") or [])]
        if str(entry.get("item") or "").strip()
    ]
    card["do_not_change"] = list(dict.fromkeys([*items, *landmarks]))
    card["extractor"] = extractor
    return card


def enrich_scene_card(card: dict, *, generate_text: GenerateText) -> dict:
    """规则卡 → LLM 结构化卡（失败/证据不足时返回原规则卡）。"""
    window = str(card.get("window") or "").strip()
    if not window:
        return dict(card)
    baseline = {
        key: card.get(key)
        for key in (
            "space_type", "architecture", "fixed_props", "layout", "relations",
            "lighting", "palette", "camera", "missing", "inferred",
        )
    }
    user = "\n".join([
        f"场景键：{card.get('scene_key') or 'scene'}",
        f"地点：{card.get('locale') or '未注明'}",
        "",
        "正文：",
        window,
        "",
        "规则抽取基线（可补充/纠正；有证据的项不能删）：",
        json.dumps(baseline, ensure_ascii=False, indent=1),
        "",
        "输出 JSON 结构示例：",
        json.dumps(_SCHEMA_HINT, ensure_ascii=False, indent=1),
    ])
    try:
        raw = generate_text(_SYSTEM, user)
    except Exception:  # noqa: BLE001 [EXC-保留] 模型失败不能让出图链中断
        return dict(card)
    data = _parse_json(raw)
    if data is None:
        return dict(card)

    architecture = _clean_entries(data.get("architecture"), window, kind="architecture")
    fixed_props = _clean_entries(data.get("fixed_props"), window, kind="fixed_prop")

    enriched = dict(card)
    enriched["space_type"] = _as_str(data.get("space_type")) or card.get("space_type", "")
    enriched["architecture"] = _merge_entries(
        architecture, [entry for entry in (card.get("architecture") or []) if isinstance(entry, dict)],
    )
    enriched["fixed_props"] = _merge_entries(
        fixed_props, [entry for entry in (card.get("fixed_props") or []) if isinstance(entry, dict)],
    )
    # 同一物件被模型同时写进建筑和陈设（梯子/门）时只留建筑表，避免画两遍。
    enriched["fixed_props"] = _merge_across_lists(
        enriched["architecture"], enriched["fixed_props"],
    )
    enriched["layout"] = _clean_layout(data.get("layout")) or card.get("layout", {})
    enriched["relations"] = (
        _clean_relations(data.get("relations"), window) or card.get("relations", [])
    )
    enriched["lighting"] = _clean_lighting(data.get("lighting"), card.get("lighting") or {})
    palette = data.get("palette")
    if isinstance(palette, list) and palette:
        enriched["palette"] = [str(item).strip() for item in palette if str(item).strip()]
    enriched["camera"] = _clean_camera(data.get("camera"), card.get("camera") or {})
    missing = data.get("missing")
    inferred = data.get("inferred")
    enriched["missing"] = _dedupe_list([
        *(card.get("missing") or []),
        *([str(item) for item in missing if str(item).strip()] if isinstance(missing, list) else []),
    ])
    enriched["inferred"] = _dedupe_list([
        *(card.get("inferred") or []),
        *([str(item) for item in inferred if str(item).strip()] if isinstance(inferred, list) else []),
    ])
    enriched["missing"] = _recompute_missing(enriched)
    return _finalize(enriched, extractor="llm")


def enrich_scene_cards(cards: dict[str, dict], *, generate_text: GenerateText) -> dict[str, dict]:
    """逐场景富化；单场景失败只回退该场景的规则卡。"""
    enriched: dict[str, dict] = {}
    for key, card in (cards or {}).items():
        if not isinstance(card, dict):
            continue
        enriched[key] = enrich_scene_card(card, generate_text=generate_text)
    return enriched
