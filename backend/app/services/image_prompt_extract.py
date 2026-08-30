"""NSFW 高潮点出图提示词提取（纯逻辑：破甲标记还原 + 分析 system + JSON 解析 + booru 拼装）。

设计见 [[roleplay-quality-plan]] P3。核心立场：
- 破甲预设一视同仁套整个扮演，生成的正文已"过甲"（带 @()@ / i分隔 / <i>分隔 等破甲标记）。
- 提取只是**结构化分析**已生成的高潮段落，不需重发整套预设、不需专用破甲片段。
- 本模块 0 I/O、0 LLM（LLM 调用在 caller，用 chat_fn）：只提供还原正则、system 组装、解析、拼装。

依赖方向：只 import 标准库（不 import character_state/agent_graph/llm），可独立单测。
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable

from app.services import prompt_clean
from app.services.prompt_clean import (
    REFUSAL_RE,  # noqa: F401 重导出：拒答识别单源，调用方沿用旧引用名
    restore_jailbreak,
    # 重导出：scene_illustration 经本模块引用（F401 误报，noqa 只写代码）
    restore_jailbreak_with_offsets,  # noqa: F401
)

# 破甲标记还原：由共享模块 prompt_clean 提供（规则文档 docs/PROMPT-CLEANING-RULES.md）。
# 对齐用户给的正则 /@\(([^()]*)\)(?=@)|@\(([^()]*)\)|\(([^()]*)\)@|@/g → $1$2$3
# 覆盖 @(x)@ 包裹式；剩余裸 @ 直接删。i/<i> 等分隔符另由用户在 IMAGE_PROMPT 正则里配（此处只兜底 @ 系）。
_ILLUSTRATION_RE = re.compile(r"\s*<illustration>\s*([\s\S]*?)\s*</illustration>\s*", re.I)
_ILLUSTRATION_OPEN_TAIL_RE = re.compile(r"\s*<illustration>\s*[\s\S]*\Z", re.I)
_THINK_RE = re.compile(r"\s*<think\b[^>]*>[\s\S]*?</think>\s*", re.I)
_THINK_OPEN_TAIL_RE = re.compile(r"\s*<think\b[^>]*>[\s\S]*\Z", re.I)
_CONTENT_RE = re.compile(r"<content\b[^>]*>([\s\S]*?)</content>", re.I)
_CONTENT_OPEN_RE = re.compile(r"<content\b[^>]*>\s*", re.I)
_CONTROL_RE = re.compile(
    r"\s*<(?P<tag>status|状态更新|表格更新)\b[^>]*>[\s\S]*?</(?P=tag)>\s*",
    re.I,
)
_CONTROL_OPEN_TAIL_RE = re.compile(r"\s*<(?:status|状态更新|表格更新)\b[^>]*>[\s\S]*\Z", re.I)
ASPECT_RATIOS = frozenset(("1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9"))
DEFAULT_ASPECT_RATIO = "2:3"

COMFY_QUALITY_TAGS = (
    "masterpiece, best quality, score_9, score_8, highres, absurdres, "
    "anime official art, scnr, @sabotennman, @mochizuki kei, very aesthetic"
)

def protected_narrative_text(text: str) -> str:
    """清除隐藏/控制块，但保留正文中的防拦截标记供同预设 Profile 使用。"""
    source = _ILLUSTRATION_RE.sub("", text or "")
    source = _ILLUSTRATION_OPEN_TAIL_RE.sub("", source)
    content = _CONTENT_RE.search(source)
    content_open = _CONTENT_OPEN_RE.search(source)
    if content:
        body = content.group(1)
    elif content_open:
        body = source[content_open.end():]
    else:
        body = _THINK_RE.sub("", source)
        body = _THINK_OPEN_TAIL_RE.sub("", body)
    body = _CONTROL_RE.sub("", body)
    body = _CONTROL_OPEN_TAIL_RE.sub("", body)
    return body.strip()


def visible_narrative_text(text: str) -> str:
    """只返回用户可见剧情，供场景分类与本地事实识别使用。"""
    return restore_jailbreak(protected_narrative_text(text)).strip()


def infer_motion(text: str) -> int:
    """从正文保守估算连续动作强度，供自动插画在图/视频间选择，0 LLM。"""
    source = (text or "").lower()
    levels = (
        (3, ("爆炸", "剧烈", "搏斗", "厮打", "狂奔", "高速追逐")),
        (2, ("奔跑", "追逐", "跳跃", "挥舞", "转身", "冲刺", "行走")),
        (1, ("呼吸", "眨眼", "摇曳", "颤动", "微笑", "抬头")),
    )
    for level, words in levels:
        if any(word in source for word in words):
            return level
    return 0


def format_comfy_prompt(content: str) -> str:
    """把英文内容 tags 固定格式化为「质量行\n内容行」；非 tag 内容失败关闭。"""
    line = " ".join((content or "").strip().splitlines()).strip()
    line = re.sub(r"\s*;\s*", ", ", line)
    sentence_punctuation = re.search(r"(?<!\d)\.(?!\d)|[!?;。！？；]", line)
    if not line or not line.isascii() or sentence_punctuation:
        return ""
    tags = [tag.strip() for tag in line.split(",") if tag.strip()]
    if len(tags) < 2:
        return ""
    return f"{COMFY_QUALITY_TAGS}\n{', '.join(tags)}"


def build_fallback_content_tags(text: str) -> str:
    """主模型漏掉插画计划时，从明确场景词生成保守的英文内容 tags。"""
    source = visible_narrative_text(text).lower()
    adult_terms = (
        "情色", "成人场景", "裸露", "性行为", "肉戏", "床戏", "做爱", "性交",
        "性爱", "交媾", "饥渴难耐", "淫液", "精液", "插入", "抽插", "射精",
    )
    explicit = any(word in source for word in adult_terms)
    tags = (
        ["adult characters", "explicit", "intimate scene", "dramatic composition",
         "close-up", "flushed", "sweat"]
        if explicit else
        ["dramatic scene", "climactic moment", "dynamic composition", "action pose",
         "cinematic lighting"]
    )
    groups = (
        (("高潮", "射精", "绝顶"), ("orgasm", "trembling") if explicit else ()),
        (("肉戏", "做爱", "性交", "性爱", "交媾", "插入", "抽插"),
         ("sex", "intercourse")),
        (("饥渴难耐", "发情", "淫荡"), ("aroused", "heavy breathing")),
        (("征服", "支配", "屈服"), ("dominant", "submissive")),
    )
    for words, additions in groups:
        if any(word in source for word in words):
            tags.extend(additions)
    return ", ".join(dict.fromkeys(tags))


def _repair_json_string_controls(payload: str) -> str:
    """仅转义 JSON 字符串内部的裸控制符；结构错误仍交给 json.loads 拒绝。"""
    output: list[str] = []
    in_string = False
    escaped = False
    for char in payload:
        if not in_string:
            output.append(char)
            if char == '"':
                in_string = True
            continue
        if escaped:
            output.append(char)
            escaped = False
        elif char == "\\":
            output.append(char)
            escaped = True
        elif char == '"':
            output.append(char)
            in_string = False
        elif char == "\n":
            output.append("\\n")
        elif char == "\r":
            output.append("\\r")
        elif char == "\t":
            output.append("\\t")
        else:
            output.append(char)
    return "".join(output)


def extract_illustration_plan(
    reply: str,
    block_filter: Callable[[str], str] | None = None,
) -> tuple[str, dict]:
    """从主生成回复剥离并校验插画计划；可在 JSON 解析前复用正文正则。"""
    source = reply or ""
    matches = list(_ILLUSTRATION_RE.finditer(source))
    clean = _ILLUSTRATION_RE.sub("", source)
    unterminated = _ILLUSTRATION_OPEN_TAIL_RE.search(clean)
    if unterminated:
        clean = clean[:unterminated.start()].rstrip()
        return clean, {}
    clean = clean.rstrip()
    if not matches:
        return clean, {}
    try:
        payload = matches[-1].group(1)
        # Claude 等模型可能在同一个 illustration JSON 字符串中插入一次新的
        # <think>...</think> 后再续写。该段是续写控制文本，不是画面事实；先剥离，
        # 可恢复前后本来连续的 JSON，而不是把整份同轮高潮计划降级为空。
        payload = _THINK_RE.sub(" ", payload)
        if block_filter is not None:
            payload = block_filter(payload)
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError:
            raw = json.loads(_repair_json_string_controls(payload))
    except (json.JSONDecodeError, TypeError, ValueError):
        return clean, {}
    if not isinstance(raw, dict):
        return clean, {}

    anchor = str(raw.get("anchor") or "").strip()
    composition = str(raw.get("composition") or "").strip()
    camera = str(raw.get("camera") or "").strip()
    prompt = str(raw.get("prompt") or "").strip()
    profile_prompt = str(raw.get("profile_prompt") or "").strip()
    aspect_ratio = str(raw.get("aspect_ratio") or "").strip()
    if aspect_ratio not in ASPECT_RATIOS:
        aspect_ratio = DEFAULT_ASPECT_RATIO
    art_direction = {
        key: str(raw.get(key) or "").strip()
        for key in ("visual_thesis", "hierarchy", "palette_material", "lighting_logic")
        if str(raw.get(key) or "").strip()
    }
    actors: list[str] = []
    weighted: list[str] = []
    normalized_subjects: list[dict[str, object]] = []
    subjects = raw.get("subjects")
    if isinstance(subjects, list):
        for item in subjects:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            description = str(item.get("description") or name).strip()
            if name and name not in actors:
                actors.append(name)
            if not description:
                continue
            try:
                weight = max(0.5, min(2.0, float(item.get("weight", 1.0))))
            except (TypeError, ValueError):
                weight = 1.0
            normalized_subjects.append({
                "name": name,
                "description": description,
                "weight": weight,
            })
            weighted.append(f"({description}:{weight:g})")
    visible_story = restore_jailbreak(visible_narrative_text(clean))
    normalized_visual_facts: list[dict[str, str]] = []
    visual_facts = raw.get("visual_facts")
    if isinstance(visual_facts, list):
        for item in visual_facts[:12]:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "visual").strip()[:48]
            fact = str(item.get("fact") or "").strip()
            evidence = restore_jailbreak(str(item.get("evidence") or "")).strip()
            if not fact or not evidence or not fact.isascii() or evidence not in visible_story:
                continue
            normalized_visual_facts.append({
                "kind": kind or "visual", "fact": fact, "evidence": evidence,
            })
    motion_raw = raw.get("motion", 0)
    motion = max(0, min(3, int(motion_raw))) if isinstance(motion_raw, (int, float)) else 0
    normalized_action_sequence: list[dict[str, str]] = []
    action_sequence = raw.get("action_sequence")
    if isinstance(action_sequence, list):
        for item in action_sequence[:8]:
            if not isinstance(item, dict):
                continue
            beat = str(item.get("beat") or "").strip()
            desc = restore_jailbreak(str(item.get("desc") or "")).strip()
            if not desc or prompt_clean.REFUSAL_RE.search(desc):
                continue
            normalized_action_sequence.append({"beat": beat or "延伸", "desc": desc})
    assembled = ", ".join(part for part in (
        art_direction.get("visual_thesis", ""),
        art_direction.get("hierarchy", ""),
        art_direction.get("palette_material", ""),
        art_direction.get("lighting_logic", ""),
        camera, composition, *weighted,
        *(item["fact"] for item in normalized_visual_facts), prompt,
    ) if part)
    if not (anchor and assembled):
        return clean, {}
    return clean, {
        "anchor": anchor,
        "camera": camera,
        "composition": composition,
        "prompt": assembled,
        "profile_prompt": profile_prompt,
        "art_direction": art_direction,
        "subjects": normalized_subjects,
        "visual_facts": normalized_visual_facts,
        "aspect_ratio": aspect_ratio,
        "actors": actors,
        "motion": motion,
        "action_sequence": normalized_action_sequence,
    }


# 分析产出的合法维度（caller 按此建 schema / 校验）
FIELDS = ("composition", "characters", "action", "lighting", "nsfw_level", "motion")

_EXTRACT_SYSTEM = (
    "你是出图提示词分析器。读下面这段剧情正文（可能含 @()@ 或字符分隔的防拦截标记，"
    "如 @(乳)@尖 视作『乳尖』、H<i>e<i>llo 视作『Hello』——请还原其原义再分析），"
    "只提取本段**画面**要素，输出 JSON：\n"
    "{\"composition\":\"构图/镜头(如 close-up, from above, pov)\","
    "\"characters\":[\"在场角色名\"],"
    "\"action\":\"动作/姿态/表情\",\"lighting\":\"光影/氛围\","
    "\"nsfw_level\":0到3的整数(0全年龄,3最露骨),"
    "\"motion\":0到3的整数(0静止画面,1轻微动作,2明显连续动作,3剧烈动态)}。\n"
    "motion 判据：画面是否包含适合做成短视频的连续动作（走动/摇晃/律动等）。\n"
    "用英文 Danbooru 风格标签、逗号分隔填 composition/action/lighting。"
    "不描述外观发色瞳色(由角色卡锚定)、不加质量词。只输出 JSON。"
)


def build_extract_system() -> str:
    """提取用 system 提示词。破甲能力由 caller 复用对话预设承接，此处只给分析指令。"""
    return _EXTRACT_SYSTEM


def parse_analysis(reply: str) -> dict:
    """解析提取 LLM 的 JSON 回复，规整成 {composition,characters,action,lighting,nsfw_level}。
    解析失败 → 空 dict（caller 决定降级）。"""
    raw = (reply or "").strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    for k in ("composition", "action", "lighting"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    chars = data.get("characters")
    if isinstance(chars, list):
        out["characters"] = [str(c).strip() for c in chars if str(c).strip()]
    lvl = data.get("nsfw_level")
    if isinstance(lvl, (int, float)):
        out["nsfw_level"] = max(0, min(3, int(lvl)))
    motion = data.get("motion")
    if isinstance(motion, (int, float)):
        out["motion"] = max(0, min(3, int(motion)))
    return out


def assemble_prompt(
    analysis: dict, *, appearance: str = "", wardrobe: str = "", locale: str = "",
    quality: str = "", intensity_tags: str = "", natural: bool = False,
) -> str:
    """按固定顺序拼提示词：质量 + 构图 + 外观锚(卡) + 动作 + 衣着/所在(state) + 光影 + 强度。

    analysis 的动态部分(构图/动作/光影)来自 LLM；外观/衣着/所在复用现成锚点(省token+保一致)。
    - natural=False（默认，ComfyUI/标签系）：非空片段用「, 」连接成 booru 串。
    - natural=True（gpt-image 等自然语言系）：质量词/强度词不适用，各字段已是自然语言短句，
      用「. 」连接成连贯描述（caller 据 gen_model 家族传入）。
    全空 → 空串（caller 据此跳过出图）。
    """
    if natural:
        # 自然语言系：丢质量咒/强度标签（LLM 已按 guidance 产连贯句），字段间用句号连接
        parts = [
            analysis.get("composition", ""),
            appearance,
            analysis.get("action", ""),
            wardrobe,
            locale,
            analysis.get("lighting", ""),
        ]
        return ". ".join(p.strip() for p in parts if p and p.strip())
    parts = [
        quality,
        analysis.get("composition", ""),
        appearance,
        analysis.get("action", ""),
        wardrobe,
        locale,
        analysis.get("lighting", ""),
        intensity_tags,
    ]
    return ", ".join(p.strip() for p in parts if p and p.strip())
