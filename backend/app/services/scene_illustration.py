"""剧情插画纯逻辑：触发判定 + 组装 SceneRequest + renderer 注册表（0 I/O、0 LLM，全单测）。

设计见 ARCHITECTURE.md「剧情能动性引擎」支柱 3 + 「明确不做的」。核心立场：
**触发是状态驱动的规则，不是导演 Agent**——好感度跨档 / user_agency 失控 / 每 N 段兜底 / 用户显式。
本模块只判「该不该出图」和「出图 prompt 是什么」，**不拥有出图管线**：真正生成由 renderer
插件（ComfyUI 复用 workflow_submission，云侧复用 image_gen）承接，本模块只持有注册表（纯 dict）。

依赖方向（importlinter scene-illustration-purity 合同将强制）：本模块吃传入的**标量快照**
（好感度前后值、段落文本、core 外观、state 衣着/场景字段），跨档判定复用 `agency.crossed_tier`，
**不 import character_state / agent_graph / workflow_submission / image_gen**，因此可独立单测。
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from typing import Callable

from app.services import agency, image_prompt_extract

# 目标渲染格式（renderer 注册表的 key）
FMT_COMFY = "comfyui"
FMT_GPT_IMAGE = "gpt-image"

# 触发原因（可观测 trace 用；优先级由高到低）
TRIGGER_EXPLICIT = "用户显式"
TRIGGER_AGENCY_LOST = "主导权失控"
TRIGGER_CROSS_TIER = "好感度跨档"
TRIGGER_CADENCE = "每N段兜底"
TRIGGER_SCENE = "高潮场景"
TRIGGER_ENCOUNTER = "新角色登场"

# 触发出图的场景标签（scene_classify 的标签子集：高潮/情色是天然出图点）
_TRIGGER_SCENES = ("nsfw", "climax")
_ANCHOR_TRAILING_PUNCTUATION = frozenset("。！？!?…」』”’")
_FALLBACK_ANCHOR_TERMS = (
    (8, ("高潮", "绝顶", "射精", "决战", "最终战", "生死关头", "关键转折")),
    (5, ("剧烈", "颤抖", "痉挛", "抽插", "插入")),
    (5, ("失去平衡", "跪倒", "跌倒", "倒下")),
    (5, ("放下", "放在", "搁在", "递出", "取出", "留下")),
    (4, ("俯身", "抬手", "抬起", "平视", "对视", "凝视", "注视", "转身", "回眸")),
    (3, ("悬在", "伸手", "抓住", "拔剑", "跃起", "跪下", "拥入")),
    (3, ("喘息", "呻吟", "汗", "拥抱", "亲吻")),
    (3, ("匣子", "信物", "徽章")),
    (2, ("光", "影", "火焰", "雷", "鲜血", "长发", "眼眸", "瞳", "衣摆")),
)
_STATE_CHANGE_ACTION_TERMS = (
    "写下", "书写", "下令", "下达", "对折", "折叠", "化作", "变成",
    "飞出", "穿出", "送出", "发送", "掷出", "射出", "消失",
)
_STATIC_RESOLUTION_TERMS = (
    "靠回", "靠着", "静坐", "沉默", "目光", "凝视", "嘴角", "浅笑", "微笑", "余韵",
)


def _anchor_score(paragraph: str) -> int:
    visible = image_prompt_extract.restore_jailbreak(paragraph)
    score = sum(
        weight for weight, words in _FALLBACK_ANCHOR_TERMS
        if any(word in visible for word in words)
    )
    score += 5 * sum(word in visible for word in _STATE_CHANGE_ACTION_TERMS)
    if any(word in visible for word in _STATIC_RESOLUTION_TERMS):
        score -= 3
    return score


@dataclass
class Trigger:
    """一次触发判定结果。fire=False 时 reason 为空串。"""
    fire: bool
    reason: str = ""


@dataclass
class SceneRequest:
    """组装好的插画请求，交给 renderer 套格式出图。本模块只造它、不执行它。"""
    prompt: str               # 场景描述（段落动作 + core外观 + state衣着/场景 拼成）
    actors: list[str] = field(default_factory=list)  # 在场角色名
    reason: str = ""          # 触发原因（trace）
    fmt: str = FMT_COMFY      # 目标渲染格式


def decide_trigger(
    *,
    explicit: bool = False,
    agency_lost: bool = False,
    tier_before: float | None = None,
    tier_after: float | None = None,
    thresholds: list[float] | None = None,
    turn: int = 0,
    cadence: int = 0,
    scene: str = "",
    character_encounter: bool = False,
) -> Trigger:
    """判本段该不该配图（纯规则，命中优先级最高的一条即返回）。

    优先级：显式 > 失控 > 高潮场景 > 跨档 > 每N段兜底。
    - explicit：用户本回合显式要图。
    - agency_lost：本回合世界 Agent 提案得手、用户短期失去主导（如被下药）——高潮点。
    - scene：场景分类判为 nsfw/climax（P2 场景分类器产出）——天然出图点。
    - 跨档：好感度从 tier_before 到 tier_after 越过档位边界（复用 `agency.crossed_tier`）。
    - 每N段：cadence>0 且 turn>0 且 turn % cadence == 0 时兜底出一张。
    全不命中 → fire=False。
    """
    if explicit:
        return Trigger(True, TRIGGER_EXPLICIT)
    if character_encounter:
        return Trigger(True, TRIGGER_ENCOUNTER)
    if agency_lost:
        return Trigger(True, TRIGGER_AGENCY_LOST)
    if scene in _TRIGGER_SCENES:
        return Trigger(True, TRIGGER_SCENE)
    if (tier_before is not None and tier_after is not None and thresholds
            and agency.crossed_tier(tier_before, tier_after, thresholds)):
        return Trigger(True, TRIGGER_CROSS_TIER)
    if cadence > 0 and turn > 0 and turn % cadence == 0:
        return Trigger(True, TRIGGER_CADENCE)
    return Trigger(False, "")


def encounter_illustration_context(
    text: str,
) -> tuple[str, str, list[str], dict[str, str]]:
    """提取新角色登场的插入锚点、完整视觉上下文、角色名与结构化事实。"""
    empty: tuple[str, str, list[str], dict[str, str]] = ("", "", [], {})
    source = re.sub(
        r"<think\b[^>]*>[\s\S]*?</think>\s*|<think\b[^>]*>[\s\S]*$",
        "", text or "", flags=re.I,
    )
    content = re.search(r"<content\b[^>]*>(.*?)</content>", source, re.I | re.S)
    body = content.group(1) if content else source
    encounter = re.search(r"<encounter\b[^>]*>(.*?)</encounter>", body, re.I | re.S)
    if not encounter:
        return empty
    facts = {
        match.group(1).strip().lower(): match.group(2).strip()
        for match in re.finditer(
            r"^\s*\[([A-Z_]+)\]\s*([^\r\n]+)", encounter.group(1), re.I | re.M,
        )
        if match.group(2).strip()
    }
    actor = re.split(r"[（(]", facts.get("who", ""), maxsplit=1)[0].strip()
    if not actor:
        return empty

    def visible_paragraphs(value: str) -> list[str]:
        return [
            part.strip()
            for part in re.split(r"(?:\r?\n){2,}", value)
            if part.strip() and not part.lstrip().startswith("<")
        ]

    before = visible_paragraphs(body[:encounter.start()])
    after = visible_paragraphs(body[encounter.end():])
    anchor = after[0] if after else (before[-1] if before else "")
    if not anchor:
        return empty
    fact_text = "；".join(
        f"{key.upper()}：{value}" for key, value in facts.items()
    )
    narrative_parts = [
        part.strip()
        for part in [before[-1] if before else "", fact_text, *after[:3]]
        if part.strip()
    ]
    narrative = "\n\n".join(narrative_parts)[:2500]
    return anchor, narrative, [actor], facts


def build_scene_request(
    *,
    paragraph: str,
    appearance: str = "",
    wardrobe: str = "",
    locale: str = "",
    actors: list[str] | None = None,
    reason: str = "",
    fmt: str = FMT_COMFY,
) -> SceneRequest:
    """从「段落动作 + core外观 + state衣着/场景」组装 SceneRequest（纯字符串拼装）。

    - paragraph：本段叙述里的动作/构图（临时，来自当前输出，不入 state）。
    - appearance：core 外观（视觉锚，来自卡文件，永不自动更）。
    - wardrobe/locale：state 的衣着/所在（随剧情走的动态字段）。
    非空片段按「动作 → 外观 → 衣着 → 场景」顺序用「，」连接；全空则 prompt 为空串。
    """
    parts = [s.strip() for s in (paragraph, appearance, wardrobe, locale) if s and s.strip()]
    return SceneRequest(
        prompt="，".join(parts),
        actors=list(actors or []),
        reason=reason,
        fmt=fmt,
    )


def infer_aspect_ratio(text: str, actors: list[str] | None = None) -> str:
    """主计划缺失时按可见空间关系选择画幅，避免所有降级插画固定为 2:3。"""
    source = image_prompt_extract.restore_jailbreak(text or "")
    actor_count = len(list(dict.fromkeys(actors or [])))
    if any(word in source for word in ("全景", "远景", "战场", "群山", "天际线", "辽阔", "横跨")):
        return "16:9"
    if actor_count >= 2 or any(word in source for word in ("两人", "多人", "对峙", "隔着", "并肩", "围坐")):
        return "4:3"
    if any(word in source for word in ("躺", "横卧", "长榻", "床上", "横向", "铺开")):
        return "3:2"
    if any(word in source for word in ("面部特写", "脸部特写", "眼睛特写", "紧凑特写", "头像")):
        return "1:1"
    if any(word in source for word in ("高塔", "坠落", "俯冲", "向上延伸", "垂直", "纵深高耸")):
        return "9:16"
    if any(word in source for word in ("全身", "站立", "站在", "跪", "跃起", "向上扬起", "纵向")):
        return "2:3"
    return "3:4"


def fallback_illustration_anchor(text: str) -> str:
    """插画计划缺失时选出视觉高潮段，返回原文锚点。"""
    source = text or ""
    content = re.search(r"<content\b[^>]*>(.*?)</content>", source, re.I | re.S)
    if content:
        body = content.group(1)
    else:
        body, _ = image_prompt_extract.extract_illustration_plan(source)
        body = re.sub(r"<think\b[^>]*>[\s\S]*?</think>\s*", "", body, flags=re.I)
        body = re.sub(r"<(?:status|状态更新|表格更新)\b[\s\S]*$", "", body, flags=re.I)
    candidates = [part.strip() for part in re.split(r"(?:\r?\n){2,}", body) if part.strip()]
    scored: list[tuple[int, int, str]] = []
    for index, paragraph in enumerate(candidates):
        score = _anchor_score(paragraph)
        if score:
            scored.append((score, -index, paragraph))
    return max(scored)[2] if scored else (candidates[0] if candidates else "")


def resolve_illustration_anchor(text: str, requested_anchor: str = "") -> str:
    """纠正模型误选的静态收束或低强度结尾钩子，保证插画仍落在视觉高潮。"""
    requested = image_prompt_extract.restore_jailbreak(requested_anchor or "").strip()
    fallback = image_prompt_extract.restore_jailbreak(
        fallback_illustration_anchor(text),
    ).strip()
    if not requested or not fallback:
        return requested or fallback
    requested_excerpt = illustration_scene_excerpt(text, requested)
    fallback_score = _anchor_score(fallback)
    requested_score = _anchor_score(requested_excerpt)
    if fallback != requested_excerpt and fallback_score >= requested_score + 3:
        return fallback
    fallback_has_action = any(word in fallback for word in _STATE_CHANGE_ACTION_TERMS)
    requested_has_action = any(word in requested_excerpt for word in _STATE_CHANGE_ACTION_TERMS)
    requested_is_static = any(word in requested_excerpt for word in _STATIC_RESOLUTION_TERMS)
    if fallback_has_action and requested_is_static and not requested_has_action:
        return fallback
    return requested


def illustration_scene_excerpt(text: str, requested_anchor: str = "") -> str:
    """提取锚点所在高潮段作为 Profile 内容真源，避免重传整篇回复。"""
    source = text or ""
    content = re.search(r"<content\b[^>]*>(.*?)</content>", source, re.I | re.S)
    body = content.group(1) if content else re.sub(
        r"(?:\r?\n){2,}\s*<(?:status|状态更新|表格更新)\b[\s\S]*$", "", source,
        flags=re.I,
    )
    visible = image_prompt_extract.restore_jailbreak(body)
    paragraphs = [part.strip() for part in re.split(r"(?:\r?\n){2,}", visible)
                  if part.strip() and not part.lstrip().startswith("<")]
    if not paragraphs:
        return visible.strip()[-2500:]
    needle = image_prompt_extract.restore_jailbreak(requested_anchor or "").strip()
    if needle:
        for paragraph in paragraphs:
            if needle in paragraph:
                return paragraph[-2500:]
        normalized_needle = re.sub(r"\s+", "", needle)
        best = max(
            paragraphs,
            key=lambda paragraph: SequenceMatcher(
                None, normalized_needle, re.sub(r"\s+", "", paragraph),
            ).ratio(),
        )
        return best[-2500:]
    fallback = image_prompt_extract.restore_jailbreak(
        fallback_illustration_anchor(source),
    ).strip()
    return (fallback or paragraphs[-1])[-2500:]


def protected_illustration_scene_excerpt(text: str, visible_excerpt: str) -> str:
    """找回与可见高潮段对应的原始防拦截正文，供独立 Profile 沿用当前预设。"""
    body = image_prompt_extract.protected_narrative_text(text)
    paragraphs = [
        part.strip() for part in re.split(r"(?:\r?\n){2,}", body)
        if part.strip() and not part.lstrip().startswith("<")
    ]
    if not paragraphs:
        return body[-2500:]
    target = re.sub(r"\s+", "", visible_excerpt or "")
    for paragraph in paragraphs:
        if target and target in re.sub(
            r"\s+", "", image_prompt_extract.restore_jailbreak(paragraph),
        ):
            return paragraph[-2500:]
    best = max(
        paragraphs,
        key=lambda paragraph: SequenceMatcher(
            None, target,
            re.sub(r"\s+", "", image_prompt_extract.restore_jailbreak(paragraph)),
        ).ratio(),
    )
    return best[-2500:]


def illustration_anchor_offset(text: str, requested_anchor: str = "") -> int | None:
    """优先取主生成指定高潮段；指定锚点无效时失败关闭，避免图片落到末尾。"""
    source = text or ""
    content = re.search(r"<content\b[^>]*>(.*?)</content>", source, re.I | re.S)
    if content:
        start, end = content.start(1), content.end(1)
    else:
        tail = re.search(r"(?:\r?\n){2,}\s*<(?:status|状态更新|表格更新)\b", source, re.I)
        start, end = 0, tail.start() if tail else len(source)
    needle = (requested_anchor or "").strip()

    def complete_sentence(offset: int) -> int:
        while offset < end and source[offset] in _ANCHOR_TRAILING_PUNCTUATION:
            offset += 1
        return offset

    if needle:
        found = source.rfind(needle, start, end)
        if found >= start:
            return complete_sentence(found + len(needle))
        visible, offsets = image_prompt_extract.restore_jailbreak_with_offsets(source[start:end])
        normalized_needle = image_prompt_extract.restore_jailbreak(needle)
        normalized_found = visible.rfind(normalized_needle)
        normalized_end = normalized_found + len(normalized_needle)
        if normalized_found >= 0 and normalized_end > 0:
            return complete_sentence(start + offsets[normalized_end - 1])
        def normalized(value: str) -> str:
            return re.sub(r"[\s，。！？!?、；;：:…'\"“”‘’（）()]", "", value)

        normalized_needle = normalized(normalized_needle)
        best: tuple[float, re.Match[str]] | None = None
        for paragraph in re.finditer(r"\S(?:.*?\S)?(?=(?:\r?\n){2,}|\s*\Z)", visible, re.S):
            if paragraph.group(0).lstrip().startswith("<"):
                continue
            paragraph_text = normalized(paragraph.group(0))
            if not normalized_needle or not paragraph_text:
                continue
            match = SequenceMatcher(None, normalized_needle, paragraph_text).find_longest_match()
            score = match.size / len(normalized_needle)
            if score >= 0.6 and (best is None or score > best[0]):
                best = (score, paragraph)
        if best is not None:
            paragraph_end = best[1].end()
            return complete_sentence(start + offsets[paragraph_end - 1])
        return None
    body = source[start:end]
    matches = list(re.finditer(r"\S(?:.*?\S)?(?=(?:\r?\n){2,}|\s*\Z)", body, re.S))
    candidates = [match for match in matches if not match.group(0).lstrip().startswith("<")]
    chosen = candidates[-1] if candidates else (matches[-1] if matches else None)
    if chosen is None:
        return end
    anchor = start + chosen.end()
    while anchor > start and source[anchor - 1].isspace():
        anchor -= 1
    return anchor


# ── renderer 注册表：纯 dict，注册/查询无 I/O；concrete 渲染器（有 I/O）在别处注册进来 ──

Renderer = Callable[[SceneRequest], str]  # 吃 SceneRequest，返回图片地址（url 或 data URI）
_RENDERERS: dict[str, Renderer] = {}


def register_renderer(fmt: str, fn: Renderer) -> None:
    """注册一个渲染器。新增图像格式 = 在这里登记一项，不改触发/组装逻辑。"""
    _RENDERERS[fmt] = fn


def get_renderer(fmt: str) -> Renderer | None:
    """取渲染器；未注册返回 None（调用方决定降级或跳过出图）。"""
    return _RENDERERS.get(fmt)


def available_formats() -> list[str]:
    """已注册的渲染格式（供前端选择/前端能力探测）。"""
    return sorted(_RENDERERS)
