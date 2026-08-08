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

# 破甲标记还原：对齐用户给的正则 /@\(([^()]*)\)(?=@)|@\(([^()]*)\)|\(([^()]*)\)@|@/g → $1$2$3
# 覆盖 @(x)@ 包裹式；剩余裸 @ 直接删。i/<i> 等分隔符另由用户在 IMAGE_PROMPT 正则里配（此处只兜底 @ 系）。
_MARKER_RE = re.compile(r"@\(([^()]*)\)(?=@)|@\(([^()]*)\)|\(([^()]*)\)@|@")
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

_INLINE_PLAN_INSTRUCTION = (
    "\n\n【自动插画计划】正文仍按剧情自然推进。仅当本轮正文实际出现值得配图的视觉高潮时，"
    "在全部正文与状态块之后追加一个内部块；无适合画面的高潮则不要输出该块。"
    "anchor 必须逐字摘录提示词所描绘的高潮动作所在段落的最后一句，供图片插回原位；"
    "禁止选择事后余韵、收束段、对话尾句、状态块或全文最后一句作为 anchor；"
    "先根据高潮事实完成艺术决策：visual_thesis 写唯一、具体、可见的视觉命题；hierarchy 写第一视觉中心、"
    "次级引导以及哪些区域概括；palette_material 写统一的主辅色、强调色和一至两种关键材质；"
    "lighting_logic 写光源、方向、受光对象、材质反应、阴影去向及其如何强化视觉中心。"
    "人物互动高潮的第一视觉中心必须是人物的面部、目光、动作、接触点或人物关系；"
    "物件只能作为辅助视觉装置，以反射、遮挡、引导线、色彩重复或材质呼应把视线导回人物。"
    "只有发现、开启、争夺或取得物件本身就是本段剧情高潮时，物件才允许成为第一视觉中心；"
    "此时仍须用人物反应或动作建立剧情关系。"
    "camera 写服务视觉命题的景别/机位/焦段，composition 写构图、空间层次和视线引导；"
    "aspect_ratio 必须根据唯一视觉命题、主体层级与空间关系，从 1:1、2:3、3:2、3:4、4:3、9:16、16:9 中只选一个；"
    "单人全身与纵向动势优先竖幅，多角色横向关系与环境叙事优先横幅，中心对称或紧凑特写可用方幅，不得输出像素尺寸；"
    "subjects 列出画面主体并用 weight 表示视觉权重（0.5~2.0）；"
    "上述字段与 subjects.description、prompt 是结构化画面草稿；prompt 只补充未覆盖的动作和环境事实。"
    "禁止所有字段等密度堆词，禁止为填字段创造剧情不存在的物体、衣着或动作。"
    "唯一高潮视觉命题必须保留本轮造成剧情状态变化的动作；必须保留动作主体、关键道具和动作结果的因果链，"
    "静态肖像、表情特写或事后姿态只能作为次级视觉信息，不得替代该动作链。"
    "subjects.description 必须先写世界书中的稳定基础外貌身份锚点，再合并本轮当前情况；"
    "披头散发、饰品松脱、服装变化只能覆盖对应当前状态，禁止因此丢掉发色、原发型/饰品、面容、体型等基础识别特征；"
    "motion 为0~3。profile_prompt 必须把上述 camera、composition、subjects、prompt 全部合并为当前所选模式的最终提示词。"
    "只允许以下格式：\n"
    '<illustration>{"anchor":"正文原句","camera":"镜头",'
    '"visual_thesis":"唯一视觉命题","hierarchy":"主体层级","palette_material":"色彩材质母题",'
    '"lighting_logic":"光影因果","composition":"构图","aspect_ratio":"2:3","subjects":[{"name":"角色名","description":"视觉描述",'
    '"weight":1.2}],"prompt":"动作, 环境, 光影, 氛围",'
    '"profile_prompt":"所选模式最终提示词","motion":0}</illustration>'
)


def build_inline_plan_instruction(profile: str = "krea2", visual_profiles: str = "") -> str:
    """返回主 Roleplay 同次生成使用的插画计划契约。"""
    from app.services import image_prompt_profiles
    instruction = _INLINE_PLAN_INSTRUCTION + "\n【当前提示词模式】" + image_prompt_profiles.inline_instruction(profile)
    if visual_profiles.strip():
        instruction += (
            "\n【本轮角色基础外貌（稳定底座）】\n" + visual_profiles.strip()
            + "\n必须与最近的状态块和高潮正文所示当前情况合并，不得只写角色名。"
        )
    return instruction


def restore_jailbreak_with_offsets(text: str) -> tuple[str, list[int]]:
    """还原破甲标记，并返回每个可见字符在原文中的结束偏移。"""
    if not text:
        return text, []
    visible: list[str] = []
    offsets: list[int] = []
    cursor = 0
    for match in _MARKER_RE.finditer(text):
        for index in range(cursor, match.start()):
            visible.append(text[index])
            offsets.append(index + 1)
        group_index = next((i for i in (1, 2, 3) if match.group(i) is not None), None)
        if group_index is not None:
            value = match.group(group_index) or ""
            group_start = match.start(group_index)
            for index, char in enumerate(value):
                visible.append(char)
                offsets.append(group_start + index + 1)
        elif offsets:
            offsets[-1] = match.end()
        cursor = match.end()
    for index in range(cursor, len(text)):
        visible.append(text[index])
        offsets.append(index + 1)
    return "".join(visible), offsets


def restore_jailbreak(text: str) -> str:
    """还原 @()@ 系破甲标记为正常文字（防拦截用的拆字/包裹去掉，保留原义）。"""
    return restore_jailbreak_with_offsets(text)[0]


def visible_narrative_text(text: str) -> str:
    """只返回用户可见剧情，供场景分类与降级提示词使用。"""
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
    return restore_jailbreak(body).strip()


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


def extract_illustration_plan(reply: str) -> tuple[str, dict]:
    """从主生成回复剥离并校验插画计划；坏块只剥离，不触发生图。"""
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
        raw = json.loads(matches[-1].group(1))
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
            weighted.append(f"({description}:{weight:g})")
    motion_raw = raw.get("motion", 0)
    motion = max(0, min(3, int(motion_raw))) if isinstance(motion_raw, (int, float)) else 0
    assembled = ", ".join(part for part in (
        art_direction.get("visual_thesis", ""),
        art_direction.get("hierarchy", ""),
        art_direction.get("palette_material", ""),
        art_direction.get("lighting_logic", ""),
        camera, composition, *weighted, prompt,
    ) if part)
    if not (anchor and assembled):
        return clean, {}
    return clean, {
        "anchor": anchor,
        "prompt": assembled,
        "profile_prompt": profile_prompt,
        "art_direction": art_direction,
        "aspect_ratio": aspect_ratio,
        "actors": actors,
        "motion": motion,
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
