"""按目标模型协议把剧情场景渲染为最终生图提示词。"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping


ANIMA_QUALITY_TAGS = (
    "masterpiece, best quality, score_7, score_9, very aesthetic, ultra detailed, "
    "fair skin, high contrast, amazing quality, newest, absurdres, 8k, high resolution, "
    "refined details, good anatomy, good shading, sharp focus, anime coloring"
)
ANIMA_NEGATIVE_TAGS = (
    "worst quality, low quality, score_1, score_2, score_3, artist name, lowres, jpeg, "
    "cropped, bad composition, artifacts, bad proportions, error, inaccurate limb, "
    "bad anatomy, bad, ugly, terrible, extra fingers, fewer fingers, missing fingers, "
    "extra arms, extra legs, inaccurate eyes, extra digit, fewer digits, trademark, "
    "username, watermark, signature, text, words"
)

PROFILE_IDS = ("anima_tags", "krea2", "natural_language", "niji_sections")

_ART_DIRECTION = (
    "所有模式都必须先在内部完成同一套艺术决策，再转换成目标格式，但不要输出分析过程："
    "第一，从高潮事实中选出唯一视觉命题，即一眼能记住且最能代表本段转折的动作、道具、接触点、"
    "空间关系或视觉反差；禁止用‘唯美画面’‘戏剧性场景’等空泛主题。"
    "第二，确定主体层级，只设一个第一视觉中心和必要的次级引导；最高细节、最强对比和最清晰边缘"
    "集中在剧情关键主体或关键部位，其余人物、服装、头发和背景按层级逐渐概括，禁止全画面等密度。"
    "人物互动高潮中，第一视觉中心必须是人物的面部、目光、动作、接触点或人物关系；"
    "物件只能作为辅助视觉装置，通过反射、遮挡、引导线、色彩重复或材质呼应把视线导回人物。"
    "只有发现、开启、争夺或取得物件本身就是剧情高潮时，物件才允许成为第一视觉中心，"
    "且仍须用人物反应或动作建立剧情关系。"
    "第三，统一色彩与材质母题，从场景事实中选择受控的主色、辅色和少量强调色，选择一至两种关键材质，"
    "让服装、道具和环境互相呼应；禁止堆叠互不相关的颜色、材质、画风和装饰词。"
    "第四，设计可解释的光影因果，明确光源、方向、光质、照亮对象、材质反应、阴影去向及其如何强化"
    "视觉中心和情绪；禁止只罗列 cinematic lighting、rim light 等孤立术语。"
    "第五，再选能服务上述命题的镜头、视角、景深和构图，引导视线经过次级元素回到视觉中心。"
    "转换格式不得削弱上述色彩材质母题、光影因果、镜头构图与主体层级。"
)

_VISUAL_BLUEPRINT = (
    _ART_DIRECTION
    + "格式化时先锁定不可改变的剧情事实：在场人物、世界书稳定外貌、当前外观变化、服装状态、"
    "实际动作和地点；不得因艺术化而改写事实。只展开服务唯一视觉命题的发型、妆容、服装、背景和装饰细节，"
    "无关项简写或省略，不得为了填满栏目凭空创造。人物、动作、镜头、光影、背景和构图必须形成同一视觉系统。"
)

_COMMON = (
    "输入是当前剧情高潮画面的 JSON。只能描写输入中已经出现或可以直接推出的事实，"
    "不得借用其他会话、历史图片或固定成人模板，不得创造未出场人物。"
    "人物、服装、动作、镜头、光影、背景和构图必须彼此一致。"
    + _VISUAL_BLUEPRINT
)

_ANIMA_SYSTEM = _COMMON + (
    "你在为 Anima 系动漫模型生成正向内容提示词。必须先把剧情高潮转换为一个具体可见的图像概念，"
    "而不是把高潮中出现的人物、道具、车辆和建筑全部并列画出。为此设计一个视觉装置，例如反射、框架、"
    "遮挡、尺度反差、前景引导、重复色彩、材质呼应或负空间，让剧情关系能直接被看见；不要照搬示例中的"
    "眼镜、沙漏、飞蛾或其他具体物件。普通剧情清单、标准中景群像和多个竞争中心都视为失败。"
    "只输出 JSON 对象，字段必须为 visual_hook、primary_focus、supporting_elements、content。"
    "visual_hook 用英文写清具体可见的视觉装置；primary_focus 只能是一个第一视觉中心；"
    "supporting_elements 是英文字符串数组，最多两个，且每个都必须直接服务第一视觉中心。"
    "次要人物与背景应降为轮廓、倒影、遮挡、模糊层或负空间，不得与第一视觉中心争夺清晰度和对比。"
    "content 是最终英文内容段：先写英文 Danbooru tags，"
    "再紧接一至三句英文自然语言画面描述。tags 用英文逗号分隔，负责人数与角色、稳定外貌与当前变化、"
    "发型、妆容、表情、服装、姿态、镜头、视角、景深、构图、背景、光影和风格等硬约束。"
    "自然语言不得机械复述 tags，而要落实已经选定的唯一视觉命题，补足主体与环境的空间关系、材质与反射、"
    "光线传播、细节主次和叙事情绪；最高细节留给剧情关键主体或关键部位，其余内容按层级逐渐概括。"
    "content 中不要输出质量词、LoRA 触发词、LoRA 权重、参数、标题、Markdown 或负面提示词；质量行由程序添加。"
)

_KREA_BASE = _COMMON + (
    "你在为 Krea2 Unlimited 生成人像提示词。最终提示词必须是一个300到600字的自然语言单段，"
    "按拍摄角度、光影色调氛围、人物、发型、妆容、表情、服装、姿态、背景、构图的十段顺序自然衔接。"
    "软配额为拍摄角度≤55字、光影≤85字、人物≤45字、发型≤25字、妆容≤30字、表情≤30字、"
    "服装≤130字、姿态≤40字、背景≤45字、构图≤45字；总长600字是硬上限，超限先压缩装饰和背景。"
    "十段是输出顺序而不是十项等权清单：开头即建立唯一视觉命题，视觉中心相关段落获得主要篇幅，"
    "其余段落只保留识别和空间成立所需信息；禁止把每一段都写成同等密度的华丽词库。"
    "镜头从广角、超广角、长焦压缩、中焦、微距、移轴中选一种，并从低角度仰拍、高角度俯拍、"
    "侧后斜45度、荷兰式倾斜、过肩偷窥、镜面反射、框架内选至少一种非常规视角，"
    "搭配浅景深虚化、大景深全景锐利或选择性对焦；禁止平视正面标准镜头。"
    "光影按光源方向与光质、主色调与明暗饱和度、氛围情绪依次写，强化层次、轮廓光、"
    "局部高光与冷暖关系，整体必须呈现精致二次元插画美学，画风与实际角色LoRA协调，避免写实摄影人脸。"
    "人物从气质、年龄身份、人种肤色、体型和第一印象展开；发型按长度、颜色、卷直、扎法、刘海展开；"
    "妆容写类型和一至两个妆面细节，表情必须与光影氛围一致。"
    "服装描述款式、材质、穿着状态及光线互动；姿态只保留一到两个明确动作；"
    "服装按场景定位、风格定调、主件、材质与光线互动、装饰、层次、鞋袜配饰的决策链组织，"
    "从真丝珍珠柔光、缎面明暗渐变、丝绒温润哑光、雪纺逆光通透、蕾丝花纹投影、皮革锐利高光等"
    "材质效果中选择与原设服装相容的细节，禁止擅自换掉世界书规定的主件。"
    "背景不能抢主体；构图从三分法、对角线、框架构图、引导线、中心对称、前景遮挡、负空间、"
    "镜面反射构图中选一种，明确人物位置和视觉引导。禁止逗号标签堆砌、(tag:1.2)权重、"
    "masterpiece、best quality、画师名、Markdown、解释和自检内容。使用角色LoRA时不描写五官。"
)

_KREA_SFW = _KREA_BASE + (
    "本次判定为SFW：擦边、性感服装、诱惑姿势仍属于SFW，所有段落都必须使用中文，"
    "服装和姿态也必须使用中文。"
)

_KREA_NSFW = _KREA_BASE + (
    "本次判定为NSFW，严格按八段顺序输出：场景（中文）、拍摄角度（中文）、光影色调氛围（中文）、"
    "人物基础（中文）、服装（英文整句）、姿态（英文整句）、背景（中文）、构图（中文）。"
    "人物基础必须包含输入提供的稳定外貌锚点，不能只写角色名。"
    "安全段落继续使用中文，仅服装段和姿态/动作段改写为英文完整句子。"
    "这两个英文段必须各自包含主语、动词和补语，忠实总结当前高潮情节；禁止写成英文标签串。"
)

_NATURAL_SYSTEM = _COMMON + (
    "你在为 GPT Image、Banana 等自然语言图像模型生成提示词。输出一段连贯、具体的自然语言画面描述，"
    "以唯一视觉命题开场，而不是从人物属性清单开场；用自然语言清楚说明主体稳定外貌与当前变化、"
    "气质、发型、妆容、表情、服装款式和材质受光、动作、镜头视角与景深、构图、光影、背景和画风。"
    "必须明确第一视觉中心、次级引导、受控色彩与材质母题，以及光从何处照到何物并产生何种材质和阴影效果；"
    "不服务视觉命题的细节应省略。"
    "不要使用标签堆砌、质量咒语、权重语法、参数后缀、标题、解释或Markdown。"
)

_NIJI_SYSTEM = _COMMON + (
    "你在为 Niji 生成四段提示词。只输出 JSON 对象，字段必须是 subject、style、additions、suffix。"
    "把共同视觉骨架映射到四段：subject 承载稳定外貌与当前变化、气质、发型、妆容、表情、服装材质和姿态；"
    "style 承载画风、媒介、材质表现和审美；additions 承载镜头类型、非常规视角、景深、光源方向、"
    "色调氛围、背景、构图、人物位置和视觉引导；"
    "四段必须围绕同一个视觉命题：subject 给出第一视觉中心和关键关系，style 只保留统一色材母题，"
    "additions 用镜头与光影因果建立层级，禁止三段各写一套互不相干的审美。"
    "suffix 只放参数指令，可用 --ar、--chaos、--iw、--stylize、--seed、--no、--sref、--sw、--weird、--niji。"
    "不得输出 JSON 之外的内容，不得把参数混入前三段。"
)

_SYSTEMS = {
    "anima_tags": _ANIMA_SYSTEM,
    "natural_language": _NATURAL_SYSTEM,
    "niji_sections": _NIJI_SYSTEM,
}

_INLINE_ART_DIRECTION = (
    "先从高潮事实选出唯一而具体的视觉命题，并用反射、框架、遮挡、尺度反差、前景引导、重复色彩、"
    "材质呼应或负空间等手法把剧情关系转化为具体可见的视觉装置；不要照搬任何示例物件。"
    "人物互动高潮必须以人物的面部、目光、动作、接触点或人物关系为第一视觉中心；"
    "物件只能作为辅助视觉装置并把视线导回人物。只有发现、开启、争夺或取得物件本身就是剧情高潮时，"
    "物件才允许成为第一视觉中心，且仍须保留人物反应或动作。"
    "再确定一个第一视觉中心和最多两个直接服务它的辅助元素，统一主辅色和一至两种关键材质，"
    "并写清光源→受光对象→材质反应/阴影→视觉中心的因果；最后才按目标格式表达。"
    "最高细节和对比只给剧情关键主体或关键部位，无关细节简写或省略，禁止把字段等密度填满。"
    "禁止把所有出场人物、道具、车辆和建筑并列成普通剧情清单或标准中景群像。"
)

_INLINE_RULES = {
    "krea2": (
        _INLINE_ART_DIRECTION
        +
        "profile_prompt 直接写 Krea2 最终提示词：基于同一对象中的 camera、composition、subjects、prompt，"
        "按场景、拍摄角度、光影色调氛围、人物基础、服装、姿态、背景、构图顺序合并成自然语言单段；"
        "人物基础必须写稳定外貌锚点并合并当前变化，不能只写姓名。"
        "十段软配额：拍摄角度≤55、光影≤85、人物≤45、发型≤25、妆容≤30、表情≤30、服装≤130、姿态≤40、背景≤45、构图≤45，总长300到600字。"
        "镜头必须选镜头类型、非常规视角和景深；光影必须写光源方向、光质、冷暖明暗层次并形成精致二次元美学；"
        "服装遵循场景→风格→主件→材质与光线互动→装饰→层次→鞋袜配饰，不得擅改角色基础主件；"
        "构图必须选明确策略并写视觉引导。SFW 全中文；NSFW 仅服装与姿态为两句英文完整句，其他段严禁英文。"
    ),
    "anima_tags": (
        _INLINE_ART_DIRECTION
        +
        "profile_prompt 只写 Anima 的英文内容段，不写质量词；先写英文 Danbooru tags，再写一至三句英文自然语言画面描述。"
        "必须合并 camera、composition、subjects、prompt，"
        "把稳定外貌与当前变化、气质、发型、妆容、表情、服装款式和材质、姿态、镜头视角与景深、"
        "光影、背景、构图和画风先转换为逗号分隔的英文 tags；随后用自然语言补充视觉中心、空间关系、"
        "材质反射、光影层次、细节主次与叙事情绪，不能只重复 tags。"
        "剧情事实含明确动作时，动作 tag 和人物之间的作用关系不得省略或退化为静态特写。"
    ),
    "natural_language": (
        _INLINE_ART_DIRECTION
        +
        "profile_prompt 写 GPT Image/Banana 可直接使用的完整自然语言画面描述，必须合并 camera、composition、"
        "subjects、prompt，并完整展开稳定外貌与当前变化、气质、发型、妆容、表情、服装款式、"
        "材质与光线互动、姿态、镜头视角与景深、光影、背景、构图、视觉引导和画风，不写标签堆砌或参数。"
    ),
    "niji_sections": (
        _INLINE_ART_DIRECTION
        +
        "profile_prompt 写四行字符串，依次为主体与动作、风格、构图镜头光影环境、只含 -- 参数的后缀；"
        "必须合并 camera、composition、subjects、prompt。第一行承载稳定外貌与当前变化、气质、发型、妆容、"
        "表情、服装材质和姿态；第二行承载画风、媒介和材质审美；第三行承载镜头视角与景深、光影、"
        "背景、构图、人物位置和视觉引导；换行须按 JSON 字符串转义。"
    ),
}


def inline_instruction(profile: str) -> str:
    """供主 Roleplay 同轮生成最终模式提示词，避免二次模型重新理解剧情。"""
    return _INLINE_RULES.get(profile, _INLINE_RULES["krea2"])


def _strip_wrapping(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"^```(?:json|text)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _krea_prompt(raw: str) -> str:
    text = _strip_wrapping(raw).split("——自检——", 1)[0].strip()
    return re.sub(r"\s*\r?\n\s*", "", text)


def _json_object(raw: str) -> dict[str, object]:
    text = _strip_wrapping(raw)
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def anima_quality_tags(rating: str = "sfw") -> str:
    if rating == "nsfw":
        return ANIMA_QUALITY_TAGS.replace(
            "score_9,", "score_9, sensitive, explicit,", 1,
        )
    return ANIMA_QUALITY_TAGS


def negative_prompt(profile: str, _scene: Mapping[str, object] | None = None) -> str:
    return ANIMA_NEGATIVE_TAGS if profile == "anima_tags" else ""


def profile_defaults(profile: str, rating: str = "nsfw") -> dict[str, str]:
    if profile not in PROFILE_IDS:
        raise ValueError(f"未知提示词模式：{profile}")
    return {
        "quality_prompt": anima_quality_tags(rating) if profile == "anima_tags" else "",
        "negative_prompt": negative_prompt(profile),
    }


def _normalize(
    profile: str, raw: str, scene: Mapping[str, object] | None = None,
) -> str:
    if profile == "krea2":
        return _krea_prompt(raw)
    if profile == "anima_tags":
        value = _json_object(raw)
        source = str(value.get("content") or "") if value else raw
        content = " ".join(_strip_wrapping(source).splitlines()).strip()
        rating = str((scene or {}).get("rating") or "sfw")
        return f"{anima_quality_tags(rating)}\n{content}"
    if profile == "natural_language":
        return _strip_wrapping(raw)
    if profile == "niji_sections":
        value = _json_object(raw)
        return "\n".join(str(value.get(key) or "").strip()
                         for key in ("subject", "style", "additions", "suffix"))
    raise ValueError(f"未知提示词模式：{profile}")


_REFUSAL_RE = re.compile(
    r"\bI\s+(?:can't|cannot|can not|won't|will not)\s+(?:help|assist|comply)\b|"
    r"无法(?:协助|帮助|满足)|不能(?:协助|帮助|满足)",
    re.I,
)

_ANIMA_VISUAL_DEVICE_RE = re.compile(
    r"\b(?:reflection|mirror|frame|framing|occlusion|silhouette|negative space|foreground|"
    r"repetition|repeated|contrast|scale|perspective|diagonal|symmetry|shadow|highlight|"
    r"refraction|translucent|layer|leading line|gaze line|contact point|motion trail)\b",
    re.I,
)
_ANIMA_VAGUE_HOOKS = {
    "dramatic scene", "dramatic departure scene", "beautiful scene", "cinematic scene",
    "emotional scene", "climax scene", "character portrait", "group scene",
}

_ANIMA_ACTION_RE = re.compile(
    r"\b(?:reaching|gripping|holding|pulling|pushing|walking|running|turning|looking|"
    r"raising|lowering|standing|sitting|kneeling|leaning|bending|lying|embracing|kissing|"
    r"fighting|attacking|swinging|drawing|opening|handing|climbing|falling|jumping)\b",
    re.I,
)
_SCENE_ACTION_RULES = (
    (r"伸手", "reaching out"),
    (r"抓住.{0,4}手腕|握住.{0,4}手腕", "gripping wrist"),
    (r"拉向|拽向|拖向", "pulling another person"),
    (r"推开|推向|推动", "pushing"),
    (r"走向|走进|步入|行走", "walking"),
    (r"奔跑|跑向|冲向", "running"),
    (r"转身", "turning"),
    (r"回头", "looking back"),
    (r"注视|凝视|看向|望向", "looking at"),
    (r"抬头|仰头", "raising head"),
    (r"低头|垂首", "lowering head"),
    (r"站起|站立|站在", "standing"),
    (r"坐下|坐在", "sitting"),
    (r"跪下|跪在", "kneeling"),
    (r"靠在|倚在", "leaning"),
    (r"俯身|弯腰", "bending forward"),
    (r"躺下|躺在|卧在", "lying down"),
    (r"拥抱|抱住", "embracing"),
    (r"亲吻|吻住", "kissing"),
    (r"攻击|交战|战斗", "fighting"),
    (r"挥剑|挥刀|劈砍", "swinging a weapon"),
    (r"拔剑|拔刀", "drawing a weapon"),
    (r"打开|开启", "opening"),
    (r"递给|交给", "handing over"),
)


def _scene_action_tags(scene: Mapping[str, object]) -> list[str]:
    """从事实底座保守映射可见动作；只识别明确动词，不为纯肖像编造姿态。"""
    source = "\n".join((
        str(scene.get("narrative") or ""),
        str(scene.get("draft_prompt") or ""),
    ))
    tags = [tag for pattern, tag in _SCENE_ACTION_RULES if re.search(pattern, source)]
    if source.isascii() and _ANIMA_ACTION_RE.search(source):
        tags.extend(match.group(0).lower() for match in _ANIMA_ACTION_RE.finditer(source))
    return list(dict.fromkeys(tags))


def _anima_contract_errors(raw: str) -> list[str]:
    value = _json_object(raw)
    if not value:
        return ["必须输出包含艺术决策字段的JSON对象"]
    errors: list[str] = []
    hook = str(value.get("visual_hook") or "").strip()
    focus = str(value.get("primary_focus") or "").strip()
    supporting = value.get("supporting_elements")
    content = str(value.get("content") or "").strip()
    if (len(hook) < 18 or hook.lower() in _ANIMA_VAGUE_HOOKS
            or not _ANIMA_VISUAL_DEVICE_RE.search(hook)):
        errors.append("visual_hook必须写出具体可见的光学或构图装置")
    if not focus or len(focus) > 160:
        errors.append("primary_focus必须且只能描述一个第一视觉中心")
    if not isinstance(supporting, list) or len(supporting) > 2:
        errors.append("supporting_elements辅助元素最多两个")
    elif any(not isinstance(item, str) or not item.strip() for item in supporting):
        errors.append("supporting_elements必须是非空英文字符串")
    if not content:
        errors.append("content不能为空")
    return errors


def _inline_krea_errors(prompt: str, scene: Mapping[str, object]) -> list[str]:
    if _REFUSAL_RE.search(prompt):
        return ["模型返回拒答"]
    rating = str(scene.get("rating") or "sfw")
    if rating == "sfw":
        return ["SFW必须全中文"] if re.search(r"[A-Za-z]{2,}", prompt) else []
    spans = list(re.finditer(r"\b(?:She|He|They|The (?:adult )?character)\b[^.!?]*[.!?]", prompt))
    if len(spans) != 2:
        return ["NSFW服装与姿态必须分别是英文完整句子"]
    clothing, pose = (match.group(0) for match in spans)
    if not re.search(r"\b(?:wears?|is dressed|has .*clothing)\b", clothing, re.I):
        return ["第一句英文必须是服装"]
    if not re.search(r"\b(?:rests?|leans?|stands?|sits?|lies?|kneels?|raises?|holds?|poses?|bends?|arches?)\b", pose, re.I):
        return ["第二句英文必须是姿态"]
    chinese_regions = "".join(
        prompt[cursor:(spans[index].start() if index < len(spans) else len(prompt))]
        for index, cursor in enumerate([0, spans[0].end(), spans[1].end()])
    )
    return ["NSFW其他六段严禁英文"] if re.search(r"[A-Za-z]{2,}", chinese_regions) else []


def normalize_inline(profile: str, raw: str, scene: Mapping[str, object] | None = None) -> str:
    """归一主模型随剧情生成的 profile_prompt；不调用第二个模型。"""
    if profile not in PROFILE_IDS:
        profile = "krea2"
    if profile == "niji_sections":
        text = _strip_wrapping(raw)
        prompt = _normalize(profile, text, scene) if text.lstrip().startswith("{") else text
    else:
        prompt = _normalize(profile, raw, scene)
    if profile == "krea2":
        return "" if _inline_krea_errors(prompt, scene or {}) else prompt
    return "" if _errors(profile, prompt, scene or {}) else prompt


def _errors(profile: str, prompt: str, scene: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    if _REFUSAL_RE.search(prompt):
        errors.append("模型返回拒答")
    if profile == "krea2":
        if not 300 <= len(prompt) <= 600:
            errors.append("总长度必须为300到600字")
        if "\n" in prompt:
            errors.append("必须是单个自然段")
        if re.search(r"\([^)]*:\s*\d+(?:\.\d+)?\)", prompt):
            errors.append("禁止权重语法")
        if re.search(r"masterpiece|best quality", prompt, re.I):
            errors.append("禁止质量标签")
        if str(scene.get("rating") or "sfw") == "sfw" and re.search(r"[A-Za-z]{2,}", prompt):
            errors.append("SFW必须全中文")
        if str(scene.get("rating") or "sfw") == "nsfw":
            english_sentences = re.findall(r"\b(?:She|He|They|The character)\b[^.!?]*[.!?]", prompt)
            if len(english_sentences) < 2:
                errors.append("NSFW服装与姿态必须分别是英文完整句子")
    elif profile == "anima_tags":
        lines = prompt.splitlines()
        content = lines[1] if len(lines) == 2 else ""
        if len(lines) != 2:
            errors.append("必须正好两行")
        if not content.isascii() or len([tag for tag in content.split(",") if tag.strip()]) < 6:
            errors.append("内容行必须以至少六个英文tags开头")
        if not re.search(r"(?:^|\.\s+)[A-Z][^.?!]{24,}[.?!](?:\s|$)", content):
            errors.append("内容行必须包含英文自然语言画面描述")
        if _scene_action_tags(scene) and not _ANIMA_ACTION_RE.search(content):
            errors.append("剧情存在明确动作，content必须保留具体动作或姿态")
    elif profile == "natural_language":
        if len(prompt) < 20:
            errors.append("自然语言描述过短")
        if prompt.startswith("```") or not re.search(r"[。.!?]", prompt):
            errors.append("必须是完整自然语言段落")
    elif profile == "niji_sections":
        lines = prompt.splitlines()
        if len(lines) != 4 or any(not line.strip() for line in lines):
            errors.append("必须包含主体、风格、附加提示词、后缀指令四段")
        elif not lines[3].startswith("--"):
            errors.append("第四段必须只包含后缀指令")
    return errors


def _system(profile: str, scene: Mapping[str, object]) -> str:
    if profile == "krea2":
        return _KREA_NSFW if str(scene.get("rating") or "sfw") == "nsfw" else _KREA_SFW
    try:
        return _SYSTEMS[profile]
    except KeyError as exc:
        raise ValueError(f"未知提示词模式：{profile}") from exc


def _krea_scene_fallback(scene: Mapping[str, object]) -> str:
    """模型无输出时直接用高潮场景事实兜底，不回退英文 tags。"""
    parts: list[str] = []
    narrative = str(scene.get("narrative") or "").strip()
    if narrative:
        parts.append(narrative)
    actors = scene.get("actors")
    actor_values = actors if isinstance(actors, list) else []
    if isinstance(actors, list):
        names = "、".join(str(name).strip() for name in actors if str(name).strip())
        if names:
            parts.append(f"画面人物为{names}")
    wardrobe = str(scene.get("wardrobe") or "").strip()
    if wardrobe:
        parts.append(f"服装状态为{wardrobe}")
    locale = str(scene.get("locale") or "").strip()
    if locale:
        parts.append(f"场景位于{locale}")
    if str(scene.get("rating") or "sfw") == "nsfw":
        narrative = narrative or "当前高潮场景"
        names = "、".join(str(name).strip() for name in actor_values if str(name).strip()) or "成年角色"
        locale = locale or "当前剧情地点"
        return (
            f"{narrative.rstrip('。')}。中景低机位拍摄，使用适中的景深。"
            f"侧向柔光塑造主体轮廓并保持环境氛围。人物基础为{names}，保留世界书提供的稳定外貌识别特征。"
            "The adult character wears clothing in the exact current condition established by the scene. "
            "The adult character holds the exact pose and action established by the climax. "
            f"背景位于{locale.rstrip('。')}。主体置于视觉中心，前后景层次清楚。"
        )
    if parts:
        return "。".join(part.rstrip("。") for part in parts) + "。"
    return "当前高潮场景采用中景构图，人物外貌、服装、动作、光影与背景均严格遵循剧情。"


def _anima_scene_fallback(scene: Mapping[str, object]) -> str:
    """独立 Profile 拒答时复用主剧情链已生成的英文高潮内容。"""
    draft = str(scene.get("draft_prompt") or "").strip()
    lines = [line.strip() for line in draft.splitlines() if line.strip()]
    content = lines[-1] if lines else ""
    content = re.sub(r"\s*;\s*", ", ", content)
    valid = content.isascii() and not _REFUSAL_RE.search(content)
    if not valid or len([tag for tag in content.split(",") if tag.strip()]) < 6:
        content = "1girl, solo, dramatic composition, cinematic lighting, detailed anime illustration, sharp focus"
    action_tags = _scene_action_tags(scene)
    if action_tags and not _ANIMA_ACTION_RE.search(content):
        head, dot, tail = content.partition(".")
        content = f"{head.rstrip(',. ')}, {', '.join(action_tags)}{dot}{tail}"
    if not re.search(r"(?:^|\.\s+)[A-Z][^.?!]{24,}[.?!](?:\s|$)", content):
        content = (
            f"{content.rstrip(',. ')}. The subject remains the visual focus while layered light, "
            "materials, and background depth preserve the relationships established by the scene."
        )
    return _normalize("anima_tags", content, scene)


def generate(
    profile: str,
    scene: Mapping[str, object],
    generate_text: Callable[[str, str], str],
) -> str:
    """调用文本模型并验证目标协议；语义格式错误时携带原因重写一次。"""
    if profile not in PROFILE_IDS:
        raise ValueError(f"未知提示词模式：{profile}")
    system = _system(profile, scene)
    source = json.dumps(dict(scene), ensure_ascii=False, separators=(",", ":"))
    raw = generate_text(system, source)
    prompt = _normalize(profile, raw, scene)
    errors = _errors(profile, prompt, scene)
    if profile == "anima_tags":
        errors.extend(_anima_contract_errors(raw))
    if not errors:
        return prompt
    repair = (
        f"{source}\n\n上次输出未通过：{'；'.join(errors)}。请严格按系统协议重写。"
        f"\n上次输出：{raw}"
    )
    repaired_raw = generate_text(system, repair)
    prompt = _normalize(profile, repaired_raw, scene)
    errors = _errors(profile, prompt, scene)
    if profile == "anima_tags":
        errors.extend(_anima_contract_errors(repaired_raw))
    if errors:
        # Krea2 是自然语言模型，长度与句式只用于引导纠错，不能阻断已有高潮画面出图。
        # 第二次有内容就直接采用；连续空输出才从高潮场景事实组装兜底。
        if profile == "krea2":
            return prompt if prompt and not _REFUSAL_RE.search(prompt) else _krea_scene_fallback(scene)
        if profile == "anima_tags":
            return _anima_scene_fallback(scene)
        raise ValueError("提示词格式校验失败：" + "；".join(errors))
    return prompt


def generate_result(
    profile: str,
    scene: Mapping[str, object],
    generate_text: Callable[[str, str], str],
) -> dict[str, str]:
    return {
        "prompt": generate(profile, scene, generate_text),
        "negative_prompt": negative_prompt(profile, scene),
    }
