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

from app.services.prompt_clean import (
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

_INLINE_PLAN_INSTRUCTION = (
    "\n\n【自动插画计划】正文仍按剧情自然推进，并把完整可见正文放在 <content>...</content> 中。"
    "正文要求的篇幅只统计 <content> 内实际向用户展示的剧情文字；think、状态块、表格块、"
    "illustration 块及 profile_prompt 都不计入正文，必须先让 <content> 独立达到预设或用户要求的篇幅，"
    "不得因为同轮还要生成提示词而缩短剧情。写正文前，先在内部推演中确定本轮冲突、转折结果、"
    "唯一高潮画面时刻及该时刻必须可见的人物关系；正文围绕这个已规划高潮自然推进，但不得为迁就画面篡改剧情。"
    "每轮必须选择本轮正文中视觉张力最强、"
    "最能代表剧情变化的高潮画面，在全部正文与状态块之后追加一个内部块，不得省略。"
    "安静对话也必须选择人物关系、目光、动作或关键物件发生变化的最强瞬间，不得改画成无关静态肖像。"
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
    "subjects 必须列出高潮画面中每一名实际可见的角色，name 必须逐字使用角色基础外貌清单中的角色名；"
    "不得漏掉互动中的次要角色，不得加入未出场角色，并用 weight 表示视觉权重（0.5~2.0）；"
    "上述字段与 subjects.description、prompt 是结构化画面草稿；prompt 只补充未覆盖的动作和环境事实。"
    "禁止所有字段等密度堆词。采用‘硬事实锁＋开放视觉槽’：在场人物、稳定外貌、正文明确的当前服装、"
    "动作、关系、地点和剧情结果是硬事实锁，必须逐项保留且禁止改写；正文没有明确规定的微动作、姿态、"
    "视线、镜头、构图、光影、天气表现、背景活动与材质细节是开放视觉槽，必须根据当前时间、地点、天气、"
    "情绪、人物处境和视觉因果进行合理联想。执行‘缺失硬事实补全’：生成完整画面所需的当前服装、具体动作或姿态、"
    "地点环境、人物位置等任一项若正文没有给出，必须补出一个具体答案，并以现有上下文作为依据；不得留空、不得使用占位语、"
    "不得退回通用模板。例如正午初到城市且正文未规定姿势时，可联想到人物抬手遮阳，"
    "并用俯视视角展示城市纵深。开放视觉槽不得引入重要新人物、关键道具、新事件，不得改变硬事实或剧情结果。"
    "唯一高潮视觉命题必须保留本轮造成剧情状态变化的动作；必须保留动作主体、关键道具和动作结果的因果链，"
    "同一时刻并行发生的身体动作、接触关系和人物相对位置必须作为一个复合画面同时写出，"
    "禁止只保留其中最容易翻译的俯身、躺卧或表情；必须明确谁对谁做什么、另一人处于什么位置和状态。"
    "若画面是时间跳转后的末尾揭示场景，必须以末尾场景为锚点，并把该时刻仍然持续有效的束缚方式、"
    "关键道具及四肢姿态逐项写入 subjects.description 和 profile_prompt；禁止回跳到前一时段的高潮。"
    "visual_facts 是通用画面事实清单，必须覆盖高潮窗口里能直接画出的主体、动作、姿态、接触关系、"
    "当前服装、关键道具、地点环境和空间关系，最多12项；每项包含 kind、英文 fact、正文逐字 evidence。"
    "kind 可按本轮内容自由命名，不限人物、动作、姿态、束缚、服装、道具、环境、损伤或空间关系；"
    "fact 必须是能直接画出的具体英文事实，evidence 必须逐字摘自同一高潮画面附近正文。"
    "所有 visual_facts 必须逐项进入 profile_prompt；不得加入没有正文证据的事实。正文明确出现的任何关键物件"
    "都不得因词典缺词、专名难译或没有可靠英文专业名词而丢弃。已有可靠通用英文名时直接使用；没有时禁止音译、"
    "照抄专名或写成 unknown object，而要按可见身份展开为具体英文描述：先写材质，再写几何外形与尺寸尺度，"
    "再写可见结构或运动方式，最后写它在当前画面中的功能及与人物/环境的实际交互。只写正文能够支持的维度；"
    "不可见的内部原理不得猜测。该规则适用于器具、机关、法器、刑具、容器、载具、建筑构件及其他所有物件，"
    "不是任何单个物件的特例。"
    "静态肖像、表情特写或事后姿态只能作为次级视觉信息，不得替代该动作链。"
    "subjects.description 必须先写世界书中的稳定基础外貌身份锚点，再合并本轮当前情况；"
    "披头散发、饰品松脱、服装变化只能覆盖对应当前状态，禁止因此丢掉发色、原发型/饰品、面容、体型等基础识别特征；"
    "除 anchor 和 subjects.name 必须保留正文原文或角色原名外，camera、visual_thesis、hierarchy、"
    "palette_material、lighting_logic、composition、subjects.description 与 prompt 必须使用简洁英文视觉描述；"
    "这样独立提示词模型拒答时仍可直接保留角色身份、动作与画面事实，不得使用中文空话代替；"
    "motion 为0~3。action_sequence 是本轮高潮画面的动作延伸序列，描述从高潮图定格动作到剧情完整动作的流程，"
    "最多8步；每项含 beat（节奏名：定格起点/延伸/收尾）与 desc（动作描述，可用中文）。"
    "desc[0] 必须对应当前高潮图的定格动作，desc[1..] 必须基于本轮剧情描述后续动作，"
    "剧情没写的动作不得补（剧情写了「吃下去」才可写吃下，写了「喂给主角」才可写喂向镜头）。"
    "纯静态/纯对话无动作延伸时，action_sequence 只含一条定格动作，或省略该字段。"
    "完成全部正文后，必须在同一个内部 illustration 块的 profile_prompt 中生成当前 Profile"
    "可直接提交的完整英文正向提示词；它只供后端出图，不向用户展示。"
    "只允许以下格式：\n"
    '<illustration>{"anchor":"正文原句","camera":"镜头",'
    '"visual_thesis":"唯一视觉命题","hierarchy":"主体层级","palette_material":"色彩材质母题",'
    '"lighting_logic":"光影因果","composition":"构图","aspect_ratio":"2:3","subjects":[{"name":"角色名","description":"视觉描述",'
    '"weight":1.2}],"visual_facts":[{"kind":"action_or_prop","fact":"concrete visible fact in English",'
    '"evidence":"正文逐字证据"}],"prompt":"动作, 环境, 光影, 氛围","profile_prompt":"完整英文成稿","motion":0,'
    '"action_sequence":[{"beat":"定格起点","desc":"动作描述"},{"beat":"延伸","desc":"动作描述"}]}</illustration>'
)


def build_inline_plan_instruction(
    profile: str = "krea2",
    visual_profiles: str = "",
    *,
    profile_instruction: str = "",
) -> str:
    """返回主 Roleplay 同次生成使用的画面计划与隐藏成稿契约。"""
    _ = profile
    instruction = _INLINE_PLAN_INSTRUCTION
    if profile_instruction.strip():
        instruction += "\n【当前 Profile 成稿格式】\n" + profile_instruction.strip()
    if visual_profiles.strip():
        instruction += (
            "\n【本轮角色基础外貌（稳定底座）】\n" + visual_profiles.strip()
            + "\n必须与最近的状态块和高潮正文所示当前情况合并，不得只写角色名。"
        )
    return instruction


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
            if not desc:
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
