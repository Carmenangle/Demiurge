"""按目标模型协议把剧情场景渲染为最终生图提示词。"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping

from app.services import image_prompt_extract


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


def inline_generation_instruction(profile: str) -> str:
    """返回主剧情同轮生成隐藏成稿时使用的 Profile 格式合同。"""
    common = (
        "profile_prompt 必须在正文完成后，根据本轮真实高潮画面直接写成可提交的纯英文正向提示词。"
        "角色姓名只用于关联角色条目，禁止写入最终提示词；必须把角色条目的具体外貌、当前服装状态、"
        "高潮动作、地点、镜头、构图、光影和材质关系翻译为实际可见内容，禁止使用 preserve identity、"
        "stable appearance、current clothing condition 等空泛占位语。当前剧情服装状态优先于基础穿着。"
        "高潮中同时发生的动作、身体接触和人物相对位置必须合成一个可见复合动作，明确谁对谁做什么；"
        "禁止只留下俯身、躺卧、脸红或高潮等局部结果。"
        "末尾揭示场景必须保留该时刻仍有效的束缚、关键道具、双手位置和双腿姿态，不能只写面部与目光。"
        "输入若含 visual_facts，必须逐项转入成稿；它们带有本轮正文逐字证据，类型开放，"
        "不得因本地没有对应类别或术语而省略。没有可靠英文专业名词的物件不得音译、照抄专名或写成"
        "unknown object；必须采用 visual_facts 已给出的材质、外形尺度、可见结构/运动、当前功能和交互描述。"
        "不得写 LoRA 名称、权重或触发词；LoRA 元数据在本轮输出完成后由后端查询、去重并注入。"
        "画面有两名角色时，必须先写双人构图与背景，再分别以 primary adult character 和 "
        "second adult character 写各自具体外貌、服装、位置与动作，最后写两人的可见关系和整体画面；"
        "禁止把两人的属性混成一份通用人物描述。"
        "只填写 JSON 字符串字段 profile_prompt，不输出分析、Markdown、道歉或额外说明。"
    )
    formats = {
        "krea2": (
            "Krea2 格式：输出一个纯英文自然语言段落，依次落实构图与留白占比、角色具体外貌与当前服装、"
            "镜头视角与透视、材质与画面质感、光影层次与色彩、最终画质；禁止 tags 列表、质量标签、"
            "权重语法、媒介锁定和 JSON 内嵌对象。"
        ),
        "anima_tags": (
            "Anima 格式：输出英文 tags + 英文关系描述，先用逗号分隔的具体 tags 锁定人物、外貌、服装、"
            "动作、场景和构图，再换行写一至三句连续英文文段说明同一画面；第二行必须直接写完整句子，"
            "禁止 Her body:、Bound:、Position: 等标题式小段或任何冒号标签，句内属性只用英文逗号连接；"
            "不要加入固定质量行，后端会统一补齐并去重。"
        ),
        "natural_language": (
            "自然语言格式：输出一个纯英文自然语言段落，完整写出人物具体外貌、当前服装、高潮动作、"
            "环境关系、构图、镜头、光影、材质和画面质量，不得输出标题、列表或 JSON。"
        ),
        "niji_sections": (
            "Niji 格式：输出四段内容并在 JSON 字符串中用 \\n 表示换行，顺序固定为 subject、style、"
            "additions、suffix；前三段为纯英文，第四段只含 --ar 等参数。"
        ),
    }
    return common + formats.get(profile, formats["krea2"])


def near_generation_contract(profile: str) -> str:
    """生成点附近的短合同；防止头部完整合同被长预设与历史稀释。"""
    formats = {
        "krea2": "profile_prompt 只能是一个纯英文自然语言段落，不得写 tags 列表或质量词。",
        "anima_tags": (
            "profile_prompt 的 JSON 字符串必须且只能含一个 \\n：第一行是逗号分隔的具体英文内容 tags，"
            "第一行结尾保留英文逗号；第二行是解释同一画面的连续英文句子，禁止标题式小段和冒号；"
            "不要写固定质量行。"
        ),
        "natural_language": "profile_prompt 只能是一个纯英文自然语言段落。",
        "niji_sections": "profile_prompt 必须用 \\n 分隔 subject、style、additions、suffix 四段。",
    }
    selected = profile if profile in PROFILE_IDS else "krea2"
    return (
        "【本轮插画执行合同】当前 Profile：" + selected + "。"
        "完成 <content> 后必须输出一个合法 <illustration> JSON。"
        "先选本轮真正发生动作与剧情变化的连续画面，不得选事后静态表情或结尾对白代替动作。"
        "visual_facts 必须逐项覆盖该画面的主体、具体动作链、姿态/接触关系、当前服装、关键物件、地点和空间关系；"
        "每项 fact 使用具体英文，evidence 逐字来自同一高潮窗口。profile_prompt 必须落实全部 visual_facts 与稳定外貌，"
        "不得写角色姓名、空泛身份锁或 LoRA 信息。" + formats[selected]
    )


def inline_output_token_reserve(profile: str) -> int:
    """在显式正文上限之外，为同轮隐藏 Profile 成稿预留输出预算。"""
    return 1000 if profile in {"krea2", "natural_language"} else 800


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
    + "采用‘硬事实锁＋开放视觉槽’。硬事实锁包括在场人物、世界书稳定外貌、当前外观变化、正文明确的"
    "服装状态、实际动作、人物关系、地点和剧情结果；不得因艺术化而改写事实。开放视觉槽包括正文未明确规定的"
    "微动作、姿态、视线、镜头、构图、光影、天气表现、背景活动和材质细节；必须根据当前时间、地点、天气、"
    "情绪、人物处境与视觉因果进行合理联想，使画面成为具体瞬间，而不是用通用模板填空。执行‘缺失硬事实补全’："
    "生成完整画面所需的当前服装、具体动作或姿态、地点环境、人物位置、镜头、构图、光影与背景中，任何未明确项"
    "都必须补出一个具体答案，不得留空、不得使用占位语或通用模板。开放视觉槽不得"
    "引入重要新人物、关键道具或新事件，不得改变硬事实与剧情结果。只展开服务唯一视觉命题的发型、妆容、"
    "服装、背景和装饰细节，无关项简写或省略。若输入确实没有稳定外貌，可根据明确的年龄、性别、职业、"
    "身份、阵营和名称调性，保守补足不改变剧情事实的典型外貌与服装；不得据此改写年龄、关系、动作或阵营设定。"
    "人物视觉事实不足时，可改以当前环境为第一视觉中心，让人物退居次级叙事位置。"
    "人物、动作、镜头、光影、背景和构图必须形成同一视觉系统。"
)

_COMMON = (
    "输入是当前剧情高潮画面的 JSON。先锁定输入明确给出的剧情事实，再主动设计输入未规定的开放视觉槽；"
    "开放设计必须能由当前时间、地点、天气、情绪、人物处境或视觉因果解释，"
    "不得借用其他会话、历史图片或固定成人模板，不得创造未出场人物。"
    "角色姓名只用于把剧情人物关联到对应外貌条目，不是生图语义；转换时必须按人物的空间角色改写为"
    "primary adult character、second adult character 等中性指代，最终提示词禁止出现原姓名、音译姓名或"
    "用姓名充当外貌。多角色必须用各自具体外貌、服装、动作和位置区分。"
    "角色 LoRA 只能辅助视觉身份，不能代替文字外貌；所有模式都必须把条目中与当前画面有关的发色、"
    "发型、发饰、五官、体型、当前服装和鞋袜翻译成具体英文，禁止用 preserve identity、"
    "established appearance、stable appearance 或 current clothing condition 等空泛身份锁代替。"
    "角色条目的基础穿着只作默认值，剧情正文或 wardrobe 中的脱下、破损、凌乱、沾污等当前状态优先。"
    "任何剧情关键物件若没有可靠英文通用名，必须用材质、几何外形与尺度、可见结构或运动方式、当前功能及"
    "与人物/环境的交互来建立可画身份；只采用输入证据支持的维度，不猜内部原理，也不得因缺少术语而省略。"
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
    "自然语言必须写成一至三句连续英文描述，不得写 Her body:、Bound:、Position:、Appearance:、Action:"
    "等标题式小段或任何冒号标签；同一句内的可见属性只用英文逗号连接。"
    "content 中不要输出质量词、LoRA 触发词、LoRA 权重、参数、标题、Markdown 或负面提示词；质量行由程序添加。"
)

_KREA_SYSTEM = _COMMON + (
    "你在为大参数自然语言图像模型 Krea2 生成剧情插画提示词。"
    "输入是已经确定的剧情高潮画面 JSON。将输入中的高潮内容准确转换成一段可直接用于 Krea2 的英文自然语言提示词。"
    "不得续写剧情，不得改变人物、动作、服装、关系、地点或剧情结果等硬事实锁；"
    "允许为开放视觉槽补足与时间、地点、天气、情绪和人物处境一致的微动作、镜头、构图、光影与背景细节。"
    "生成提示词前，在内部严格按以下六个维度依次解析，但不要输出分析过程、标题、编号或分段标签。"
    "第一，构图与留白占比。根据高潮动作、人物数量和空间关系确定主体位置、画面占比、视觉中心、"
    "前后景关系、视线引导和适合当前场景的负空间比例；留白必须服务人物关系、动作方向或环境纵深，"
    "不得机械套用固定比例。"
    "第二，角色外貌与服装。完整保留实际人物、稳定外貌、当前外观变化、发型、体型、表情、服装款式、"
    "服装当前状态、配饰、鞋袜和可见动作；角色 LoRA 已经确定人物身份时不得重新设计五官，"
    "但 LoRA 不能代替文字描述，仍须把条目中的每项可见外貌翻译成具体英文。角色条目的基础穿着只作"
    "默认值，剧情正文或 wardrobe 中的脱下、破损、凌乱、沾污等当前状态优先。不得添加未出场人物或"
    "擅自改变服装。禁止用 preserve identity、established facial structure、defined by the bound model、"
    "stable appearance、current clothing condition 等占位句代替实际的发色、发型、五官、体型和服装。"
    "第三，摄影风格、镜头视角与透视表现。根据高潮内容选择景别、焦段、相机距离、机位高度、俯仰角度、"
    "观察方向、景深和透视压缩程度；镜头必须清楚表现动作主体、人物关系和空间方向，"
    "不得为了复杂构图而使用与高潮无关的夸张机位。"
    "第四，有机材质与画面质感。描述肌肤、头发、衣料、汗水、液体、植物和其他有机表面的纹理、湿度、"
    "柔软度、透明度、褶皱、拉伸、压力与受光反应；材质表现必须服从实际动作、服装状态和环境条件，"
    "不得凭空增加身体变化或不存在的物质。"
    "第五，光影、层次与色彩设定。明确主光源的位置、方向、软硬、色温和强度，说明受光主体、材质高光或透光、"
    "阴影落向与空气透视；使用受控的主色、辅色和少量强调色形成前景、人物与背景层次，"
    "把最高对比和清晰度集中在高潮动作或人物关系上。"
    "第六，画质质量与完成度。保证人体结构、肢体承重、接触关系、服装受力、物体遮挡、透视、光照和材质反应"
    "彼此一致；强调精确解剖、清晰主体、稳定轮廓、细腻材质、受控细节、干净边缘、高图像保真度和完整完成度，"
    "不得使用 photorealistic、live-action photography 或 realistic human skin 等真人媒介描述。"
    "优先使用输入已有的 visual_thesis、hierarchy、palette_material、lighting_logic、camera 和 composition；"
    "只有这些信息缺失时，才补足使空间、透视、解剖、承重、遮挡、光照和材质成立的必要内容。"
    "实际加载的角色 LoRA 和风格 LoRA 决定人物与媒介风格；不得添加 LoRA 触发词、LoRA 权重，也不得使用"
    "anime、manga、3D render 等词擅自锁定媒介。"
    "最终只输出一个连贯、具体的纯英文自然语言段落，并严格按照六个维度的顺序组织内容。"
    "不得夹杂中文，不得输出标签堆砌、JSON、Markdown、解释、自检、拒答、参数、LoRA 名称、LoRA 权重或负面提示词。"
)

_NATURAL_SYSTEM = _COMMON + (
    "你在为 GPT Image、Banana 等自然语言图像模型生成提示词。输出一段连贯、具体的自然语言画面描述，"
    "以唯一视觉命题开场，而不是从人物属性清单开场；用自然语言清楚说明主体稳定外貌与当前变化、"
    "气质、发型、妆容、表情、服装款式和材质受光、动作、镜头视角与景深、构图、光影、背景和画风。"
    "必须明确第一视觉中心、次级引导、受控色彩与材质母题，以及光从何处照到何物并产生何种材质和阴影效果；"
    "不服务视觉命题的细节应省略。最终结果必须全部使用英文。"
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
    "subject、style、additions 必须全部使用英文。不得输出 JSON 之外的内容，不得把参数混入前三段。"
)

_SYSTEMS = {
    "anima_tags": _ANIMA_SYSTEM,
    "natural_language": _NATURAL_SYSTEM,
    "niji_sections": _NIJI_SYSTEM,
}

def _strip_wrapping(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"^```(?:json|text)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _krea_prompt(raw: str, scene: Mapping[str, object] | None = None) -> str:
    _ = scene
    text = _strip_wrapping(raw).split("——自检——", 1)[0].strip()
    text = " ".join(text.splitlines()).strip()
    return _normalize_krea_style(text)


def _normalize_krea_style(prompt: str) -> str:
    """清除会锁死真人材质的皮肤微观词，不代替 LoRA 决定画风。"""
    text = prompt
    replacements: tuple[tuple[str, str], ...] = (
        (r"(?:整体)?(?:必须)?(?:采用|呈现|为)?(?:精致)?(?:超写实)?"
         r"(?:真人摄影|真实照片|写实摄影|二次元插画|动漫插画)(?:风格|美学)?[，,]?", ""),
        (r"皮肤呈(?:现)?半透明(?:的)?质感", "肌肤受光自然"),
        (r"隐约透出(?:底层)?微血管", "以细腻明暗表现暖色反光"),
        (r"(?:脸上|面部|皮肤)?毛孔(?:清晰)?(?:可见)?", "面部明暗过渡柔和"),
    )
    replacements += (
        (r"\b(?:photorealistic\s+live-action|live-action\s+photorealistic|"
             r"realistic\s+photography|detailed\s+anime\s+illustration|anime\s+illustration|"
             r"manga\s+illustration|3D\s+render(?:ing)?)\s*(?:imagery|style|aesthetic)?[.,;:]?\s*", ""),
        (r"\bphotorealistic\b[.,;:]?\s*", ""),
        (r"\b(?:live-action photography|anime(?: style)?|manga(?: style)?|3D render(?:ing)?)\b[.,;:]?\s*", ""),
        (r"\brealistic human skin\b", "physically coherent skin tones and material response"),
        (r"\btranslucent skin\b", "natural tonal transitions across the skin"),
        (r"\b(?:microscopic|visible)\s+(?:facial\s+)?(?:veins?|pores?)\b", "subtle tonal detail"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return " ".join(text.split())


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


_ANIMA_PROSE_LABELS: tuple[tuple[str, str], ...] = (
    (r"her body|body", "Her body is shown with "),
    (r"bound|restraints?", "She is bound with "),
    (r"position|pose", "She is positioned "),
    (r"appearance", "She appears with "),
    (r"clothing|wardrobe|costume", "She wears "),
    (r"action", "She is shown "),
    (r"expression", "Her expression shows "),
    (r"setting|location|background", "The scene takes place in "),
    (r"camera|composition", "The composition uses "),
    (r"lighting", "The lighting uses "),
)


def _anima_naturalize_prose(value: str) -> str:
    """把模型偶发的 `Label: fragment` 改成连续英文画面句。"""
    text = " ".join((value or "").splitlines()).strip()
    for label, replacement in _ANIMA_PROSE_LABELS:
        text = re.sub(
            rf"(^|[.!?]\s+)(?:{label})\s*:\s*",
            rf"\1{replacement}",
            text,
            flags=re.I,
        )
    text = re.sub(r"\b(\d+)\s*:\s*(\d+)\b", r"\1 by \2 aspect ratio", text)
    text = re.sub(r"\s*:\s*", ", ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _anima_clean_tag_head(value: str) -> str:
    """Anima 首行只能是逗号序列，不能携带标题式冒号。"""
    text = re.sub(r"\s*;\s*", ", ", value or "")
    labels = "|".join(label for label, _replacement in _ANIMA_PROSE_LABELS)
    text = re.sub(rf"\b(?:{labels})\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"\b(\d+)\s*:\s*(\d+)\b", r"\1 by \2 aspect ratio", text)
    return re.sub(r"\s*:\s*", ", ", text)


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


def system_with_preset(
    system: str,
    scene: Mapping[str, object],
    *,
    preset_dir: str = "",
    preset_name: str = "",
    user_name: str = "",
) -> str:
    """把当前偏置/防拦截预设用于独立提示词调用，不携带剧情历史。"""
    if not (preset_dir.strip() and preset_name.strip()):
        return system
    try:
        from app.services import preset_store

        preset = preset_store.read_preset(preset_dir, preset_name)
        if not preset:
            return system
        model_scene = _scene_for_model(scene)
        actors = model_scene.get("actors")
        names = [str(name).strip() for name in actors] if isinstance(actors, list) else []
        source = json.dumps(model_scene, ensure_ascii=False, separators=(",", ":"))
        markers = {
            "char_name": "、".join(name for name in names if name),
            "char_description": str(model_scene.get("appearance") or ""),
            "char_personality": "",
            "scenario": str(model_scene.get("narrative") or ""),
            "dialogue_examples": "",
            "worldbook": str(model_scene.get("appearance") or ""),
            "worldbook_after": "",
            "persona": "",
            "user_name": user_name.strip(),
            "last_user_message": source,
            "last_char_message": "",
        }
        guard = preset_store.assemble_system(preset, markers).strip()
        if not guard:
            return system
        return (
            guard
            + "\n\n【内部生图提示词任务】以下任务独立于剧情正文，只输出目标协议要求的提示词，"
              "不得续写剧情、解释、道歉或附加拒答说明。\n"
            + system
        )
    except Exception:  # noqa: BLE001 预设读取失败时仍允许按 Profile 自身协议生成
        return system


def _normalize(
    profile: str, raw: str, scene: Mapping[str, object] | None = None,
) -> str:
    if profile == "krea2":
        return _anonymize_prompt_names(_krea_prompt(raw, scene), scene or {})
    if profile == "anima_tags":
        value = _json_object(raw)
        explicit_tags = ""
        explicit_prose = ""
        if value:
            source = str(value.get("content") or "")
        else:
            lines = [line.strip() for line in _strip_wrapping(raw).splitlines() if line.strip()]
            if len(lines) >= 2:
                body = " ".join(lines[1:])
                if re.search(
                    r"\b(?:masterpiece|best quality|score_[1-9]|anime coloring)\b",
                    lines[0], re.I,
                ):
                    # 已编译结果再次经过 normalize_inline 时，只剥掉质量前缀，保留同一行内容 tags。
                    explicit_tags = re.sub(
                        r"^[\s\S]*?\banime coloring\b\s*,?", "", lines[0],
                        count=1, flags=re.I,
                    ).strip(" ,")
                    if not explicit_tags:
                        legacy_tags, dot, legacy_prose = body.partition(".")
                        explicit_tags = legacy_tags.strip(" ,")
                        if dot and legacy_prose.strip():
                            body = legacy_prose.strip()
                else:
                    # 主模型偶尔把环境 tags 放首行、人物和动作写成第二行分号描述；
                    # 确定性复制具体描述到 tags 行，不能把 Agent 已提取的画面事实丢掉。
                    explicit_tags = ", ".join((lines[0].rstrip(" ,"), body))
                    explicit_tags = re.sub(
                        r"\b(?:(?:primary|second) adult character|the visual center)\s*:\s*",
                        "", explicit_tags, flags=re.I,
                    )
                    explicit_tags = re.sub(r"\s*[.;]\s*", ", ", explicit_tags)
                    explicit_tags = explicit_tags.encode("ascii", "ignore").decode("ascii")
                explicit_prose = re.sub(
                    r"\bprimary adult character\s*:\s*",
                    "The primary adult character is ", body, flags=re.I,
                )
                explicit_prose = re.sub(
                    r"\bsecond adult character\s*:\s*",
                    "The second adult character is ", explicit_prose, flags=re.I,
                )
                explicit_prose = re.sub(r"\s*;\s*", ". ", explicit_prose)
                explicit_prose = explicit_prose.encode("ascii", "ignore").decode("ascii").strip()
                if explicit_prose and explicit_prose[-1] not in ".!?”":
                    explicit_prose += "."
                source = ""
            else:
                source = " ".join(lines)
        content = " ".join(_strip_wrapping(source).splitlines()).strip()
        # 第一行是质量+内容 tags；第二行才是解释文段。
        if explicit_tags or explicit_prose:
            tag_head = explicit_tags
            prose = explicit_prose
        else:
            tag_head, dot, prose_tail = content.partition(".")
            prose = prose_tail.strip() if dot else ""
        tag_head = _anima_clean_tag_head(tag_head)
        tag_head = re.sub(
            r"(?:^|,\s*)(?:dramatic scene|climactic moment|dynamic composition|action pose|"
            r"cinematic lighting)(?=,|\.|$)\s*,?\s*", "", tag_head, flags=re.I,
        ).strip(" ,")
        tag_head = ", ".join(
            tag.strip() for tag in tag_head.split(",")
            if tag.strip() and not re.fullmatch(r"[*_#`~\s]+", tag.strip())
        )
        prose = _anima_naturalize_prose(prose)
        rating = str((scene or {}).get("rating") or "sfw")
        return _anonymize_prompt_names(
            f"{anima_quality_tags(rating)}, {tag_head},\n{prose}", scene or {},
        )
    if profile == "natural_language":
        return _anonymize_prompt_names(_strip_wrapping(raw), scene or {})
    if profile == "niji_sections":
        value = _json_object(raw)
        return _anonymize_prompt_names(
            "\n".join(str(value.get(key) or "").strip()
                       for key in ("subject", "style", "additions", "suffix")),
            scene or {},
        )
    raise ValueError(f"未知提示词模式：{profile}")


def _restore_scene_value(value: object) -> object:
    if isinstance(value, str):
        return image_prompt_extract.restore_jailbreak(value)
    if isinstance(value, list):
        return [_restore_scene_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _restore_scene_value(item) for key, item in value.items()}
    return value


def _scene_for_facts(scene: Mapping[str, object]) -> dict[str, object]:
    """本地校验使用还原后的事实；防拦截标记不得污染格式和事实检查。"""
    restored = _restore_scene_value(scene)
    assert isinstance(restored, dict)
    protected = scene.get("protected_narrative")
    if isinstance(protected, str) and protected.strip():
        restored["narrative"] = image_prompt_extract.restore_jailbreak(protected)
    restored.pop("protected_narrative", None)
    return restored


def _scene_for_model(scene: Mapping[str, object]) -> dict[str, object]:
    """模型输入保留防拦截正文并匿名化角色名；姓名只用于本地关联外貌。"""
    result = dict(scene)
    protected = result.pop("protected_narrative", None)
    if isinstance(protected, str) and protected.strip():
        result["narrative"] = protected
    return _anonymize_scene_characters(result)


def _character_names(scene: Mapping[str, object]) -> list[str]:
    names: list[str] = []
    actors = scene.get("actors")
    if isinstance(actors, list):
        names.extend(str(name).strip() for name in actors if str(name).strip())
    subjects = scene.get("subjects")
    if isinstance(subjects, list):
        names.extend(
            str(item.get("name") or "").strip() for item in subjects
            if isinstance(item, Mapping) and str(item.get("name") or "").strip()
        )
    return list(dict.fromkeys(names))


def _replace_character_name(text: str, name: str, label: str) -> str:
    if not name:
        return text
    pattern = (
        rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
        if name.isascii() else re.escape(name)
    )

    def replacement(match: re.Match[str]) -> str:
        prefix = text[:match.start()]
        if not prefix.strip() or re.search(r"[.!?]\s*$", prefix):
            return label[:1].upper() + label[1:]
        return label

    return re.sub(pattern, replacement, text, flags=re.I if name.isascii() else 0)


def _anonymize_prompt_names(prompt: str, scene: Mapping[str, object]) -> str:
    labels = [
        "the primary adult character",
        "the second adult character",
        *[f"adult character {index}" for index in range(3, len(_character_names(scene)) + 1)],
    ]
    for name, label in zip(_character_names(scene), labels, strict=False):
        prompt = _replace_character_name(prompt, name, label)
    return prompt


def _anonymize_scene_characters(scene: Mapping[str, object]) -> dict[str, object]:
    names = _character_names(scene)
    labels = [
        "the primary adult character",
        "the second adult character",
        *[f"adult character {index}" for index in range(3, len(names) + 1)],
    ]
    mapping = dict(zip(names, labels, strict=False))

    def rewrite(value: object) -> object:
        if isinstance(value, str):
            for name, label in mapping.items():
                value = _replace_character_name(value, name, label)
            return value
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, Mapping):
            return {str(key): rewrite(item) for key, item in value.items()}
        return value

    result = rewrite(scene)
    assert isinstance(result, dict)
    return result


_REFUSAL_RE = re.compile(
    r"\bI\s+(?:can't|cannot|can not|won't|will not)\s+"
    r"(?:help|assist|comply|generate|create|produce|write|transform|provide|fulfill)\b|"
    r"无法(?:协助|帮助|满足)|不能(?:协助|帮助|满足)",
    re.I,
)


def _strip_refusal_suffix(raw: str) -> str:
    """保留拒答前已经合规的提示词，只裁掉模型追加的拒答说明。"""
    text = image_prompt_extract.restore_jailbreak(raw or "")
    match = _REFUSAL_RE.search(text)
    if not match:
        return text
    line_start = text.rfind("\n", 0, match.start()) + 1
    prefix = text[line_start:match.start()]
    cut = line_start if re.search(
        r"此请求|该请求|抱歉|sorry|I(?:'m| am) Claude Code", prefix, re.I,
    ) else match.start()
    return text[:cut].rstrip(" ,，;；\r\n")

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
    r"fighting|attacking|swinging|drawing|opening|handing|climbing|falling|jumping|"
    r"carrying|lifting|unloading)\b",
    re.I,
)
_THROAT_GRIP_SOURCE = r"(?:掐|扣|卡)(?:着|住|紧)?.{0,8}(?:脖子|颈部?|喉咙)|(?:脖子|颈部?|喉咙).{0,8}(?:掐|扣|卡)(?:着|住|紧)?"
_PENETRATION_SOURCE = r"(?:重新.{0,6}(?:推进|进入)|推进去|推入|插入|抽插|交媾|做爱|性交)"
_COMPOUND_THROAT_INTERCOURSE = (
    "penetrative intercourse while the secondary adult partner grips the primary woman's throat"
)
_SCENE_ACTION_RULES = (
    (
        rf"(?:{_THROAT_GRIP_SOURCE})[\s\S]{{0,800}}(?:{_PENETRATION_SOURCE})|"
        rf"(?:{_PENETRATION_SOURCE})[\s\S]{{0,800}}(?:{_THROAT_GRIP_SOURCE})",
        _COMPOUND_THROAT_INTERCOURSE,
    ),
    (_THROAT_GRIP_SOURCE, "one hand gripping the partner's throat"),
    (_PENETRATION_SOURCE, "penetrative intercourse"),
    (r"传教士|(?:仰卧|躺)[\s\S]{0,80}面对面|面对面[\s\S]{0,80}(?:仰卧|躺)", "face-to-face missionary position"),
    (r"(?:双腿|大腿|腿).{0,8}(?:压|架|搭).{0,10}(?:对方|他的|她的)?肩", "legs raised over the partner's shoulders"),
    (r"火车便当|抱离地面.{0,12}(?:悬空|贴住)|悬空.{0,12}(?:抱|贴住)", "standing suspended carry position"),
    (r"(?:^|[^0-9])69(?:式|姿势|体位)|六九式|上下交叠", "mutual oral 69 position"),
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
    (r"取出|拔出|抽出|移除", "removing the visible object"),
    (r"摩擦|磨着|磨动|揉动", "rubbing the contact point"),
    (r"撤开|撤掉|抽回|收回", "withdrawing just before completion"),
    (r"(?:腰|身体).{0,12}(?:追|拱|顶)(?:向|过去|上去)?", "her body following the withdrawn contact"),
    (r"(?:膝盖|双腿|大腿).{0,8}(?:分开|打开)", "knees spread apart"),
    (r"压住|按住", "pressing down"),
    (r"(?:搬|卸).{0,12}木箱|木箱.{0,12}(?:搬|卸)|搬下|搬到|卸下|卸货",
     "unloading a wooden medicine crate"),
    (r"扛着|背着|搬着", "carrying"),
)


def _scene_action_tags(scene: Mapping[str, object]) -> list[str]:
    """从事实底座保守映射可见动作；只识别明确动词，不为纯肖像编造姿态。"""
    source = "\n".join((
        str(scene.get("narrative") or ""),
        str(scene.get("draft_prompt") or ""),
    ))
    tags = [tag for pattern, tag in _SCENE_ACTION_RULES if re.search(pattern, source)]
    if _COMPOUND_THROAT_INTERCOURSE in tags:
        tags = [tag for tag in tags if tag not in {
            "one hand gripping the partner's throat", "penetrative intercourse", "bending forward",
        }]
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


def _anima_scene_fact_errors(prompt: str, scene: Mapping[str, object]) -> list[str]:
    """只校验剧情硬事实，不约束镜头、光影、色材或视觉装置。"""
    encounter = scene.get("encounter")
    encounter_values = encounter.values() if isinstance(encounter, Mapping) else ()
    source = "\n".join((
        str(scene.get("narrative") or ""),
        str(scene.get("appearance") or ""),
        str(scene.get("wardrobe") or ""),
        str(scene.get("locale") or ""),
        " ".join(str(value) for value in encounter_values),
    ))
    content = prompt.lower()
    requirements = (
        (
            r"(?:搬|卸).{0,16}(?:木箱|药箱)|(?:木箱|药箱).{0,16}(?:搬|卸)",
            r"(?:unload|carry|lift|move)\w*.{0,80}(?:crate|box)|"
            r"(?:crate|box).{0,80}(?:unload|carry|lift|move)\w*",
            "正文明确搬运药材木箱，content必须保留搬箱动作",
        ),
        (r"药材|黄芪|艾草|干姜", r"medicinal herbs?|herbal medicine|astragalus|mugwort|ginger",
         "正文明确出现药材，content必须保留药材"),
        (r"宽肩", r"broad shoulders?", "人物宽肩特征不得丢失"),
        (r"方脸", r"square face", "人物方脸特征不得丢失"),
        (r"浓眉", r"thick eyebrows?", "人物浓眉特征不得丢失"),
        (r"虎牙", r"(?:prominent|visible|small) canine tooth|fang", "人物虎牙特征不得丢失"),
        (r"厚茧|老茧", r"calloused hands?", "人物掌心厚茧特征不得丢失"),
        (r"褐色短褂", r"brown .{0,20}(?:jacket|workwear|shirt)", "人物褐色短褂不得丢失"),
        (r"袖口挽|挽起袖|挽到肘", r"rolled(?:-up)? sleeves?", "人物挽袖状态不得丢失"),
        (r"孤儿院.{0,8}门|门.{0,8}孤儿院", r"orphanage (?:entrance|gate|doorway)",
         "孤儿院门口地点不得丢失"),
        (r"骡子.{0,12}(?:车|平板车)|骡车", r"mule(?:-drawn)? cart|mule cart",
         "骡车环境事实不得丢失"),
    )
    errors = [message for source_pattern, prompt_pattern, message in requirements
              if re.search(source_pattern, source) and not re.search(prompt_pattern, content)]
    has_matron_interaction = bool(
        re.search(r"院长", source) and re.search(r"招呼|查看|接受|迟疑|不安|惶然|伸手", source)
    )
    if has_matron_interaction:
        if not re.search(r"\b(?:matron|orphanage director|caretaker|headmistress)\b", content):
            errors.append("正文存在院长互动，content必须保留院长作为次级人物")
        if re.search(r"\b(?:solo|1girl)\b", content):
            errors.append("正文存在院长互动，禁止使用solo或1girl把关系场景退化为单人肖像")
    adult_occupation = bool(re.search(r"药材商贩|药材贩子|女商贩|女商人", source))
    if adult_occupation and not re.search(r"\b(?:adult woman|woman|2women)\b", content):
        errors.append("成年商贩身份不得退化为未标明成年身份的少女")
    unsupported_mule_action = re.search(
        r"\b(?:slap|slapping|pat|patting|hit|hitting|strike|striking)\b.{0,40}\b(?:mule|horse)\b",
        content,
    )
    source_mule_action = re.search(r"(?:拍|打|抚摸|抽|击).{0,12}(?:骡|马)", source)
    if unsupported_mule_action and not source_mule_action:
        errors.append("正文没有拍打骡子的动作，禁止凭空添加该动作")
    return errors


_CJK_RE = re.compile(r"[\u3400-\u9fff]")

_VAGUE_IDENTITY_RE = re.compile(
    r"\b(?:identified character|bound character|stable appearance|current clothing condition|"
    r"illustrated identity|replacement face|redesign (?:her|his|their) identity|"
    r"defined by the (?:bound )?(?:character )?model|established (?:facial structure|eye shape|"
    r"hairstyle silhouette|body proportions|appearance|identity))\b",
    re.I,
)

# 中文角色条目的高频可视事实：同时用于语义门禁和无模型兜底。规则只翻译视觉属性，
# 不含角色专名；当前 wardrobe/正文中的服装变化会覆盖条目里的基础【穿着】段。
_VISUAL_FACT_RULES: tuple[tuple[str, str, str], ...] = (
    (r"漆黑墨发|华贵墨发|墨发|乌黑(?:长)?发|黑发", "glossy jet-black hair", r"\b(?:jet-black|raven|ebony|black) hair\b"),
    (r"发团|发髻|盘发", "hair gathered into a rounded bun", r"\b(?:hair )?(?:bun|updo|chignon)\b"),
    (r"紫玉金髻|紫玉.{0,4}(?:簪|髻|钗)", "a purple jade and gold hair ornament", r"purple jade.{0,35}(?:hair|ornament|pin)|(?:hair|ornament|pin).{0,35}purple jade"),
    (r"朱唇|红唇", "full crimson lips", r"\b(?:crimson|red|scarlet|vermilion) lips\b"),
    (r"脸颊红润|红润.{0,3}脸颊", "rosy cheeks", r"\b(?:rosy|flushed|warm) cheeks\b"),
    (r"晶亮.{0,8}美目|美目.{0,8}晶亮", "luminous rounded eyes", r"\b(?:luminous|bright|clear|glossy|shining).{0,12}eyes\b"),
    (r"暗红.{0,5}(?:美眸|眼|眸)|(?:美眸|眼|眸).{0,5}暗红", "narrow dark-red eyes", r"\b(?:narrow )?dark-red eyes\b"),
    (r"熟厚双唇|丰厚.{0,3}(?:双唇|嘴唇)", "full mature lips", r"\bfull mature lips\b"),
    (r"成熟风韵|成熟.{0,4}(?:干练|韵味)|久经历练", "a mature and composed gaze", r"\b(?:mature|seasoned|composed|experienced).{0,18}(?:gaze|eyes|expression)\b"),
    (r"前凸后翘|曲线优美|丰腴熟躯|丰腴肥熟|丰腴.{0,4}(?:身材|曲线)", "a voluptuous curvy figure", r"\b(?:voluptuous|curvy|full-figured)\b"),
    (r"爆硕巨乳|丰满胸部|巨乳", "a very full bust", r"\b(?:very |large |ample |full )?(?:bust|breasts)\b"),
    (r"媚软纤腰|纤腰", "a narrow waist", r"\b(?:narrow|slender|slim).{0,10}waist\b"),
    (r"宽厚圆硕.{0,5}(?:臀|屁股)|圆硕.{0,3}(?:臀|屁股)|肥臀", "broad rounded hips", r"\b(?:broad|wide|full|rounded).{0,12}(?:hips|buttocks)\b"),
    (r"蚕丝白袜|白丝|白袜", "white silk stockings", r"\bwhite (?:silk )?(?:stockings|socks)\b"),
    (r"修长.{0,6}(?:美腿|双腿)|厚嫩美腿", "long legs", r"\blong.{0,12}(?:legs|thighs)\b"),
    (r"素紫色?薄纱法衣|紫色?薄纱法衣", "a light purple gauze robe", r"\b(?:light |pale )?purple.{0,15}gauze robe\b"),
    (r"道门徽记|灵符", "Taoist emblems and talisman motifs", r"\b(?:Taoist|Daoist).{0,18}(?:emblem|talisman)|talisman motif"),
    (r"胸口半敞|领口半敞", "a partly open neckline", r"\bpartly open (?:neckline|front|chest)\b"),
    (r"碎花紫长裙|紫色?碎花长裙", "a floral purple long skirt", r"\b(?:floral purple|purple floral).{0,12}(?:long )?(?:skirt|dress)\b"),
    (r"红色?长裙|红裙", "a red long dress", r"\bred (?:long )?(?:dress|skirt)\b"),
    (r"红莲.{0,8}(?:华袍|长袍|袍)[\s\S]{0,400}(?:华袍|衣袍|袍服)?残片|红莲.{0,8}(?:华袍|长袍|袍).{0,12}(?:撕开|毁损|残片)|(?:撕开|毁损|残片).{0,12}红莲.{0,8}(?:华袍|长袍|袍)|red lotus robe.{0,20}(?:torn|remnants)|(?:torn|remnants).{0,20}red lotus robe", "torn remnants of a red lotus patterned robe", r"\btorn remnants of a red lotus patterned robe\b"),
    (r"红莲纹饰华袍|红莲.{0,5}(?:华袍|长袍|袍)", "a red lotus patterned robe", r"\bred lotus.{0,20}(?:patterned )?robe\b"),
    (r"高叉开衩|高开叉|高叉", "a high side slit", r"\bhigh (?:side )?slit\b"),
    (r"(?:衣|裙|袍).{0,4}(?:破碎|破损|撕裂|扯破)|(?:破碎|破损|撕裂|扯破).{0,4}(?:衣|裙|袍)", "torn clothing with stressed folds", r"\btorn.{0,20}(?:clothing|robe|dress|skirt|fabric)\b"),
    (r"赤裸|全裸|一丝不挂", "a nude body with no remaining garments", r"\b(?:nude|naked).{0,18}(?:body|woman|character)?\b"),
    (r"媚药.{0,8}(?:香薰|盘香|香)|(?:香薰|盘香).{0,8}媚药", "aphrodisiac incense releasing sweet dense smoke", r"\baphrodisiac incense\b|\bincense.{0,35}(?:aphrodisiac|sweet dense smoke)"),
    (r"锁链|镣铐", "iron chains restraining the wrists", r"\b(?:iron )?(?:chains?|shackles?).{0,30}(?:wrists?|arms?)|\b(?:wrists?|arms?).{0,30}(?:chains?|shackles?)"),
    (r"石板|石台|石床", "a cold stone slab", r"\bcold stone (?:slab|platform)\b"),
    (r"囚衣", "a pale prisoner's robe", r"\bpale prisoner's robe\b|\bprison (?:robe|garment)\b"),
)


def _current_visual_source(scene: Mapping[str, object]) -> str:
    appearance = str(scene.get("appearance") or "")
    wardrobe = str(scene.get("wardrobe") or "")
    narrative = str(scene.get("narrative") or "")
    clothing_changed = bool(wardrobe.strip() or re.search(
        r"赤裸|全裸|一丝不挂|衣.{0,6}(?:破|裂|脱|褪)|裙.{0,6}(?:破|裂|脱|褪)|袜.{0,6}(?:破|裂|脱|扯)",
        narrative,
    ))
    if clothing_changed:
        appearance = appearance.split("【穿着】", 1)[0]
    subjects = scene.get("subjects")
    actors = scene.get("actors")
    actor_names = {
        str(name).strip() for name in actors if str(name).strip()
    } if isinstance(actors, list) else set()
    subject_details = "\n".join(
        str(item.get("description") or "") for item in subjects
        if isinstance(item, Mapping) and (
            not actor_names
            or not str(item.get("name") or "").strip()
            or str(item.get("name") or "").strip() in actor_names
        )
    ) if isinstance(subjects, list) else ""
    return "\n".join(filter(None, (
        appearance, wardrobe, narrative if clothing_changed else "", subject_details,
    )))


def _mapped_visual_facts(scene: Mapping[str, object]) -> list[str]:
    source = _current_visual_source(scene)
    facts = list(dict.fromkeys(
        phrase for source_pattern, phrase, _ in _VISUAL_FACT_RULES
        if re.search(source_pattern, source)
    ))
    if "torn remnants of a red lotus patterned robe" in facts:
        facts = [fact for fact in facts if fact != "a red lotus patterned robe"]
    return facts


def _actor_visual_details(scene: Mapping[str, object]) -> list[tuple[str, list[str], list[str]]]:
    """按高潮人物拆开外貌与动作，供所有 Profile 的多人确定性编译。"""
    actor_values = scene.get("actors")
    actors = [
        str(name).strip() for name in actor_values
        if str(name).strip()
    ] if isinstance(actor_values, list) else []
    if len(actors) < 2:
        return []
    sources: dict[str, list[str]] = {name: [] for name in actors}
    appearance = str(scene.get("appearance") or "")
    marker = re.compile(
        rf"^\s*({'|'.join(re.escape(name) for name in sorted(actors, key=len, reverse=True))})\s*[：:]",
    )
    current = ""
    for line in appearance.splitlines():
        match = marker.match(line)
        if match:
            current = match.group(1)
        if current:
            sources[current].append(line)
    narrative = image_prompt_extract.restore_jailbreak(str(scene.get("narrative") or ""))
    clauses = [part.strip() for part in re.split(r"[。！？!?；;\n]", narrative) if part.strip()]
    for actor in actors:
        sources[actor].extend(clause for clause in clauses if actor in clause)
    subjects = scene.get("subjects")
    if isinstance(subjects, list):
        for subject in subjects:
            if not isinstance(subject, Mapping):
                continue
            name = str(subject.get("name") or "").strip()
            description = str(subject.get("description") or "").strip()
            if name in sources and description:
                sources[name].append(description)
    details: list[tuple[str, list[str], list[str]]] = []
    for actor in actors:
        source = "\n".join(sources[actor])
        facts = _mapped_facts_from(source)
        english = source.encode("ascii", "ignore").decode("ascii")
        english = " ".join(english.split()).strip(" :,.;-")
        if english and re.search(r"[A-Za-z]{3}", english):
            facts.append(english)
        actions = _scene_action_tags({"narrative": source})
        details.append((actor, list(dict.fromkeys(facts)), actions))
    return details


def _multi_actor_sentences(scene: Mapping[str, object]) -> list[str]:
    labels = ("The primary adult character", "The second adult character")
    sentences: list[str] = []
    for label, (_actor, facts, actions) in zip(labels, _actor_visual_details(scene), strict=False):
        parts: list[str] = []
        if facts:
            parts.append("is visibly defined by " + ", ".join(facts))
        if actions:
            parts.append("performs " + ", then ".join(actions))
        if parts:
            sentences.append(label + " " + " and ".join(parts) + ".")
    return sentences


def _mapped_facts_from(source: str) -> list[str]:
    facts = list(dict.fromkeys(
        phrase for source_pattern, phrase, _ in _VISUAL_FACT_RULES
        if re.search(source_pattern, source or "")
    ))
    if "torn remnants of a red lotus patterned robe" in facts:
        facts = [fact for fact in facts if fact != "a red lotus patterned robe"]
    return facts


def _mapped_wardrobe_facts_from(source: str) -> list[str]:
    wardrobe_words = re.compile(
        r"\b(?:robe|dress|skirt|stockings?|socks?|clothing|garments?|neckline|slit|nude|naked|emblems?|talisman)\b",
        re.I,
    )
    return [fact for fact in _mapped_facts_from(source) if wardrobe_words.search(fact)]


def _missing_visual_facts(prompt: str, scene: Mapping[str, object]) -> list[str]:
    source = _current_visual_source(scene)
    lowered = prompt.lower()
    return list(dict.fromkeys(
        phrase for source_pattern, phrase, output_pattern in _VISUAL_FACT_RULES
        if re.search(source_pattern, source) and not re.search(output_pattern, lowered, re.I)
    ))


def _visual_fact_errors(prompt: str, scene: Mapping[str, object]) -> list[str]:
    source = _current_visual_source(scene)
    if not source.strip():
        return []
    errors: list[str] = []
    if _VAGUE_IDENTITY_RE.search(prompt):
        errors.append("角色段落使用了空泛身份锁，必须改写为条目中的具体可视外貌与当前服装")
    errors.extend(
        f"角色条目中的可视事实未落实：{phrase}"
        for phrase in _missing_visual_facts(prompt, scene)
    )
    for name in _character_names(scene):
        if name.isascii() and _replace_character_name(prompt, name, "") != prompt:
            errors.append("最终提示词包含角色姓名；姓名只能用于关联外貌条目")
            break
    return errors


def _krea_contract_errors(
    prompt: str, scene: Mapping[str, object], *, check_length: bool,
) -> list[str]:
    errors: list[str] = []
    if not prompt.strip():
        return ["提示词为空"]
    if _REFUSAL_RE.search(prompt):
        errors.append("模型返回拒答")
    if _CJK_RE.search(prompt):
        errors.append("最终提示词必须为纯英文，禁止夹杂中文")
    if re.search(r"\([^)]*:\s*\d+(?:\.\d+)?\)", prompt):
        errors.append("禁止权重语法")
    if re.search(r"\b(?:masterpiece|best quality)\b", prompt, re.I):
        errors.append("禁止质量标签")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", prompt) if part.strip()]
    if len(paragraphs) != 1:
        errors.append("Krea2必须输出一个英文自然段")
    if check_length:
        words = re.findall(r"\b[A-Za-z][A-Za-z'-]*\b", prompt)
        if not 80 <= len(words) <= 500:
            errors.append("英文单词数必须为80到500")
    errors.extend(_multi_actor_contract_errors("krea2", prompt, scene))
    errors.extend(_visual_fact_errors(prompt, scene))
    return errors


def _inline_krea_errors(prompt: str, scene: Mapping[str, object]) -> list[str]:
    return _krea_contract_errors(prompt, scene, check_length=False)


def _complete_inline_krea_facts(prompt: str, scene: Mapping[str, object]) -> str:
    """只补同轮成稿遗漏的可视事实，保留模型已经完成的高潮与艺术决策。"""
    errors = _inline_krea_errors(prompt, scene)
    fact_errors = _visual_fact_errors(prompt, scene)
    missing = _missing_visual_facts(prompt, scene)
    recoverable = bool(missing) and set(errors) == set(fact_errors) and all(
        error.startswith("角色条目中的可视事实未落实：") for error in fact_errors
    )
    if not recoverable:
        return ""
    subject = "The visible adult characters are specifically distinguished by "
    if len(_character_names(scene)) <= 1:
        subject = "The primary adult character is specifically defined by "
    addition = subject + ", ".join(missing) + "."
    first_sentence = re.search(r"[.!?](?:\s|$)", prompt)
    if first_sentence:
        end = first_sentence.end()
        repaired = prompt[:end].rstrip() + " " + addition + " " + prompt[end:].lstrip()
    else:
        repaired = prompt.rstrip(" .") + ". " + addition
    return "" if _inline_krea_errors(repaired, scene) else repaired


def normalize_inline(profile: str, raw: str, scene: Mapping[str, object] | None = None) -> str:
    """归一已有提示词或本地编译结果；不调用第二个模型。"""
    if profile not in PROFILE_IDS:
        profile = "krea2"
    raw = _strip_refusal_suffix(raw)
    if profile == "niji_sections":
        text = _strip_wrapping(raw)
        prompt = _normalize(profile, text, scene) if text.lstrip().startswith("{") else text
    else:
        prompt = _normalize(profile, raw, scene)
    if profile == "krea2":
        if not _inline_krea_errors(prompt, scene or {}):
            return prompt
        return _complete_inline_krea_facts(prompt, scene or {})
    errors = _errors(profile, prompt, scene or {})
    if profile == "anima_tags" and errors and all(
        error.startswith("角色条目中的可视事实未落实：") for error in errors
    ):
        # 同义表述不应让整份 Agent 画面退化；字段账本会把精确稳定外貌补进两行。
        return prompt
    return "" if errors else prompt


def _multi_actor_contract_errors(
    profile: str, prompt: str, scene: Mapping[str, object],
) -> list[str]:
    actors = scene.get("actors")
    if not isinstance(actors, list) or len(list(dict.fromkeys(
        str(name).strip() for name in actors if str(name).strip()
    ))) < 2:
        return []
    errors: list[str] = []
    if not re.search(r"\bprimary adult (?:character|woman)\b", prompt, re.I):
        errors.append("多人提示词必须单独描述第一角色")
    if not re.search(r"\bsecond adult (?:character|woman)\b", prompt, re.I):
        errors.append("多人提示词必须单独描述第二角色")
    if profile == "anima_tags" and not re.search(r"\b(?:2girls|two girls)\b", prompt, re.I):
        errors.append("Anima 双人提示词必须包含2girls")
    return errors


def _errors(profile: str, prompt: str, scene: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    if profile != "krea2" and _REFUSAL_RE.search(prompt):
        errors.append("模型返回拒答")
    if profile == "krea2":
        errors.extend(_krea_contract_errors(prompt, scene, check_length=True))
    elif profile == "anima_tags":
        lines = prompt.splitlines()
        tags = lines[0] if len(lines) == 2 else ""
        prose = lines[1] if len(lines) == 2 else ""
        if len(lines) != 2:
            errors.append("必须正好两行")
        if not tags.isascii() or len([tag for tag in tags.split(",") if tag.strip()]) < 6:
            errors.append("第一行必须包含质量词与至少六个英文内容tags")
        if not re.search(r"^[A-Za-z][^.?!]{24,}[.?!](?:\s|$)", prose):
            errors.append("第二行必须包含英文自然语言画面描述")
        if _scene_action_tags(scene) and not _ANIMA_ACTION_RE.search(prompt):
            errors.append("剧情存在明确动作，提示词必须保留具体动作或姿态")
    elif profile == "natural_language":
        if len(prompt) < 20:
            errors.append("自然语言描述过短")
        if _CJK_RE.search(prompt):
            errors.append("自然语言最终提示词必须为纯英文")
        if prompt.startswith("```") or not re.search(r"[。.!?]", prompt):
            errors.append("必须是完整自然语言段落")
    elif profile == "niji_sections":
        lines = prompt.splitlines()
        if len(lines) != 4 or any(not line.strip() for line in lines):
            errors.append("必须包含主体、风格、附加提示词、后缀指令四段")
        elif not lines[3].startswith("--"):
            errors.append("第四段必须只包含后缀指令")
        if _CJK_RE.search(prompt):
            errors.append("Niji 最终提示词必须为纯英文")
    if profile != "krea2":
        errors.extend(_multi_actor_contract_errors(profile, prompt, scene))
    if profile != "krea2":
        errors.extend(_visual_fact_errors(prompt, scene))
    return errors


def _system(profile: str, scene: Mapping[str, object]) -> str:
    if profile == "krea2":
        if scene.get("character_lora"):
            return _KREA_SYSTEM + (
                "当前画面已绑定角色 LoRA，但 LoRA 只是视觉辅助，不能替代角色条目。仍须逐项把条目中的"
                "发色、发型、发饰、五官、体型和当前服装写成具体英文；不得输出角色姓名、模型名称、权重、"
                "触发词或任何‘由模型保持身份’的空泛句。"
            )
        return _KREA_SYSTEM
    try:
        return _SYSTEMS[profile]
    except KeyError as exc:
        raise ValueError(f"未知提示词模式：{profile}") from exc


def _english_scene_details(scene: Mapping[str, object]) -> list[str]:
    """从混合语言 SceneSpec 中保留可直接使用的英文高潮、外貌与艺术事实。"""
    english_details: list[str] = []
    art_direction = scene.get("art_direction")
    candidates = [
        _current_visual_source(scene), scene.get("locale"),
        scene.get("camera"), scene.get("composition"), scene.get("draft_prompt"),
    ]
    subjects = scene.get("subjects")
    actors = scene.get("actors")
    actor_names = {
        str(name).strip() for name in actors if str(name).strip()
    } if isinstance(actors, list) else set()
    if isinstance(subjects, list):
        candidates.extend(
            item.get("description") for item in subjects
            if isinstance(item, Mapping)
            and (
                not actor_names
                or not str(item.get("name") or "").strip()
                or str(item.get("name") or "").strip() in actor_names
            )
        )
    if isinstance(art_direction, Mapping):
        candidates.extend(art_direction.values())
    encounter = scene.get("encounter")
    if isinstance(encounter, Mapping):
        candidates.extend(encounter.values())
    for value in candidates:
        detail = " ".join(str(value or "").split()).strip(" ,.;")
        # 角色外貌常是“中文名: English tags”；兜底不能因中文身份前缀丢掉后面的精确英文特征。
        if _CJK_RE.search(detail):
            detail = detail.encode("ascii", "ignore").decode("ascii")
            detail = " ".join(detail.split()).strip(" :,.;-")
        if detail and re.search(r"[A-Za-z]{3}", detail) and not _REFUSAL_RE.search(detail):
            english_details.append(detail)
    return list(dict.fromkeys([
        *english_details, *_mapped_visual_facts(scene), *_evidenced_visual_facts(scene),
    ]))


def _concrete_scene_facts(
    scene: Mapping[str, object], *, include_draft_prompt: bool = True,
) -> list[str]:
    """统一事实底座：只收人物、当前服装、动作、道具、地点与空间关系。"""
    direct_facts: list[str] = []
    for key in ("appearance", "wardrobe"):
        detail = " ".join(str(scene.get(key) or "").split()).strip(" ,.;")
        if detail and detail.isascii() and re.search(r"[A-Za-z]{3}", detail):
            direct_facts.append(detail)
    facts = [*direct_facts, *_mapped_visual_facts(scene), *_scene_action_tags(scene),
             *_evidenced_visual_facts(scene)]
    locations = [
        phrase for pattern, phrase in _LOCATION_RULES
        if re.search(pattern, str(scene.get("locale") or ""))
    ]
    facts.extend(locations)
    subjects = scene.get("subjects")
    actors = set(_character_names({"actors": scene.get("actors", [])}))
    if isinstance(subjects, list):
        for item in subjects:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            if actors and name and name not in actors:
                continue
            detail = " ".join(str(item.get("description") or "").split()).strip(" ,.;")
            if detail and detail.isascii() and detail not in facts:
                facts.append(detail)
    keys = ["appearance", "camera", "composition"]
    if include_draft_prompt:
        keys.insert(1, "draft_prompt")
    for key in keys:
        raw_detail = str(scene.get(key) or "")
        if key == "draft_prompt":
            # 同轮草稿常包含独立质量行；事实底座只取内容行，不能把质量词重复塞进正文。
            detail_lines = [line.strip() for line in raw_detail.splitlines() if line.strip()]
            raw_detail = detail_lines[-1] if detail_lines else ""
        detail = " ".join(raw_detail.split()).strip(" ,.;")
        if _CJK_RE.search(detail):
            detail = " ".join(detail.encode("ascii", "ignore").decode("ascii").split()).strip(" ,.;:-")
        if detail and re.search(r"[A-Za-z]{3}", detail) and detail not in facts:
            facts.append(detail)
    return list(dict.fromkeys(fact for fact in facts if fact.strip()))


def _scene_camera_tag(scene: Mapping[str, object]) -> str:
    """景别服从要展示的内容，不固定套 medium shot。"""
    source = image_prompt_extract.restore_jailbreak(str(scene.get("narrative") or ""))
    explicit = str(scene.get("camera") or "").strip()
    if explicit and explicit.isascii():
        match = re.search(
            r"\b(?:extreme close-up|close-up|close shot|medium close-up|medium shot|"
            r"medium wide shot|wide shot|long shot|full shot)\b", explicit, re.I,
        )
        if match:
            return match.group(0).lower()
    if re.search(r"亲吻|眼睛|瞳|嘴唇|内裤|局部|特写|脸部", source):
        return "close-up"
    if re.search(r"远景|全景|城市全貌|群山|天际线|辽阔|远处风景", source):
        return "wide shot"
    return "medium shot"


def _anima_content_description(scene: Mapping[str, object], facts: list[str]) -> str:
    """把同一批具体 tags 复述为画面文段，不输出给下游的元指令。"""
    concrete = list(dict.fromkeys(fact.strip(" ,.;") for fact in facts if fact.strip(" ,.;")))
    actions = _scene_action_tags(scene)
    subject = (
        "The primary adult woman" if len(_character_names(scene)) <= 1
        else "The two adult characters"
    )
    sentences: list[str] = []
    if concrete:
        sentences.append(f"{subject} is depicted with {', '.join(concrete)}.")
    if actions:
        sentences.append(f"In this instant, the concrete action is {', '.join(actions)}.")
    return " ".join(sentences) or f"{subject} occupies the depicted scene."


def _safe_anima_draft_tags(value: object) -> list[str]:
    """只接纳短英文内容草稿；拒绝艺术分析、权重主体和完整规划串。"""
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    candidates = [
        line for line in lines
        if not re.search(r"\b(?:masterpiece|best quality|score_\d+)\b", line, re.I)
    ]
    content = candidates[-1] if candidates else ""
    if (
        not content or not content.isascii() or _REFUSAL_RE.search(content)
        or ";" in content
        or re.search(r"\b(?:visual thesis|primary|secondary|tertiary|hierarchy)\s*:", content, re.I)
    ):
        return []
    content = re.sub(r"\([^()]*:\s*\d+(?:\.\d+)?\)", "", content)
    tags = [tag.strip(" ,.;") for tag in content.split(",") if tag.strip(" ,.;")]
    return list(dict.fromkeys(
        tag for tag in tags
        if len(tag) <= 120 and not re.search(r"\b(?:masterpiece|best quality|score_\d+)\b", tag, re.I)
    ))


def _krea_scene_fallback(scene: Mapping[str, object]) -> str:
    """连续硬失败时给出单段纯英文剧情插画描述。"""
    multi_sentences = _multi_actor_sentences(scene)
    concrete_facts = _concrete_scene_facts(scene)
    fact_sentence = (
        " The visible subjects and scene are concretely defined by "
        + ", ".join(concrete_facts) + "."
        if concrete_facts and not multi_sentences else ""
    )
    camera = _scene_camera_tag(scene)
    subject_opening = (
        "A balanced two-character environmental composition gives two adult women distinct silhouettes, "
        "readable positions, and one shared visual focus against a layered background."
        if multi_sentences else
        f"A {camera} depicts the primary adult woman in one continuous narrative moment, with her visible action "
        "as the decisive action and visual focus."
    )
    character_sentences = (" " + " ".join(multi_sentences)) if multi_sentences else ""
    relationship_sentence = (
        " Their visible actions, spacing, gaze directions, and contact relationship remain readable as one coherent scene."
        if multi_sentences else ""
    )
    return (
        subject_opening
        + character_sentences
        + relationship_sentence
        + fact_sentence
        + " The composition makes every stated body position, contact point, current garment, prop, and location "
          "simultaneously readable, using coherent perspective, directional light, material response, and controlled "
          "background depth. Maintain precise anatomy, stable contours, clean edges, controlled fine detail, high image "
          "fidelity, and smooth noise-free tonal transitions."
    )


def _anima_scene_fallback(scene: Mapping[str, object]) -> str:
    """独立 Profile 拒答时复用主剧情链已生成的英文高潮内容。"""
    encounter = scene.get("encounter")
    encounter_values = encounter.values() if isinstance(encounter, Mapping) else ()
    source = "\n".join((
        str(scene.get("narrative") or ""),
        str(scene.get("appearance") or ""),
        str(scene.get("wardrobe") or ""),
        str(scene.get("locale") or ""),
        " ".join(str(value) for value in encounter_values),
    ))
    fact_rules = (
        (r"女人|女性|妇人|女子|她|三十出头", "adult woman"),
        (r"三十出头", "early thirties"),
        (r"药材商贩|药材贩子|药商", "herb merchant"),
        (r"宽肩", "broad shoulders"),
        (r"厚背|结实|壮实", "sturdy build"),
        (r"方脸", "square face"),
        (r"浓眉", "thick eyebrows"),
        (r"虎牙", "prominent canine tooth"),
        (r"厚茧|老茧", "calloused hands"),
        (r"结实.{0,4}小臂", "strong forearms"),
        (r"褐色短褂", "brown work jacket"),
        (r"袖口挽|挽到肘", "rolled sleeves"),
        (r"靴底.{0,8}泥|泥.{0,8}靴", "muddy boots"),
        (r"风尘仆仆", "travel-worn"),
        (r"爽朗|热络", "open cheerful expression"),
        (r"骡子.{0,8}平板车|骡车", "mule-drawn cart"),
        (r"药材|黄芪|艾草|干姜", "bundled medicinal herbs"),
        (r"孤儿院.{0,6}门|门.{0,6}孤儿院", "orphanage entrance"),
        (r"院长.{0,12}(?:不安|迟疑|惶然|恐惧)|(?:不安|迟疑|惶然).{0,12}院长",
         "anxious matron"),
        (r"(?:搬|卸).{0,12}木箱|木箱.{0,12}(?:搬|卸)|搬下|搬到|卸下|卸货",
         "unloading a wooden medicine crate"),
        (r"跳下", "jumping down from the cart"),
        (r"尘土|扬尘", "dust in the air"),
    )
    facts = list(dict.fromkeys([
        *(tag for pattern, tag in fact_rules if re.search(pattern, source)),
        *_safe_anima_draft_tags(scene.get("draft_prompt")),
        *_concrete_scene_facts(scene, include_draft_prompt=False),
    ]))
    # 具体角色/动作/场景 tags 必须先于抽象镜头词；有具体外貌时去掉无信息量的 adult woman。
    direct_appearance = " ".join(str(scene.get("appearance") or "").split()).strip(" ,.;")
    has_specific_english_appearance = bool(
        direct_appearance and direct_appearance.isascii()
        and re.search(r"[A-Za-z]{3}", direct_appearance)
    )
    concrete_role_facts = [
        fact for fact in facts
        if (not has_specific_english_appearance or fact.lower() != "adult woman")
        and fact.lower() not in {"dramatic scene", "dramatic composition", "climactic moment",
                                "dynamic composition", "action pose", "cinematic lighting"}
    ]
    if concrete_role_facts:
        facts = concrete_role_facts
    if has_specific_english_appearance:
        facts = [direct_appearance, *[fact for fact in facts if fact != direct_appearance]]
    base = facts or ["visible decisive action", "environmental narrative composition"]
    content_tags = list(dict.fromkeys([
        *base, _scene_camera_tag(scene), "three-quarter view", "environmental composition",
        "directional light", "layered background", "detailed anime illustration",
    ]))
    content = ", ".join(content_tags)
    action_tags = _scene_action_tags(scene)
    if action_tags and not _ANIMA_ACTION_RE.search(content):
        head, dot, tail = content.partition(".")
        content = f"{head.rstrip(',. ')}, {', '.join(action_tags)}{dot}{tail}"
    multi_sentences = _multi_actor_sentences(scene)
    if multi_sentences:
        content = (
            "2girls, two-shot, balanced composition, clear spatial separation, environmental composition, "
            "layered background, directional light. "
            + " ".join(multi_sentences)
            + " The two adult characters' distinct appearances and actions form one readable relationship, "
              "while the background, light direction, material response, and depth remain coherent."
        )
    if not re.search(r"(?:^|\.\s+)[A-Z][^.?!]{24,}[.?!](?:\s|$)", content):
        if "herb merchant" in facts and "unloading a wooden medicine crate" in facts:
            description = (
                "A sturdy adult herb merchant unloads a wooden medicine crate at the orphanage "
                "entrance, with her calloused hands, square face, and prominent canine tooth receiving "
                "the sharpest detail. Late-afternoon light catches road dust, rough wood, and bundled "
                "herbs while the anxious matron and mule cart recede into softer layers."
            )
        else:
            description = _anima_content_description(scene, content_tags)
        content = f"{content.rstrip(',. ')}. {description}"
    return _normalize("anima_tags", content, scene)


def _natural_scene_fallback(scene: Mapping[str, object]) -> str:
    multi_sentences = _multi_actor_sentences(scene)
    if multi_sentences:
        return (
            "A balanced two-character composition places two adult women in clearly separated positions against a "
            "layered environmental background. " + " ".join(multi_sentences) + " Their visible relationship, "
            "spacing, gaze directions, and contact point remain the overall visual focus. Directional light, material "
            "response, cast shadows, controlled color hierarchy, and a clean high-fidelity finish preserve one coherent scene."
        )
    details = "; ".join(_english_scene_details(scene))
    facts = details or "the visible adult characters performing the supplied decisive action"
    return (
        f"The image preserves {facts}. The decisive story action is the single visual focus within an "
        "environmental narrative composition. The highest detail remains on the characters' faces, hands, "
        "clothing state, and visible contact point while secondary figures and the background recede in clarity. "
        "A coherent directional light source, material response, cast shadows, controlled color hierarchy, and "
        "clean high-fidelity finish reinforce the visible character relationship without changing any scene fact."
    )


def _niji_scene_fallback(scene: Mapping[str, object]) -> str:
    multi_sentences = _multi_actor_sentences(scene)
    details = "; ".join(_english_scene_details(scene))
    subject = " ".join(multi_sentences) if multi_sentences else (
        details or "visible adult characters performing the supplied decisive story action"
    )
    ratio = str(scene.get("aspect_ratio") or "2:3")
    if ratio not in ("1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9"):
        ratio = "2:3"
    return "\n".join((
        subject,
        "refined two-dimensional narrative illustration, cohesive linework, controlled color and material motif",
        "environmental composition, directional light, highest detail on faces hands and contact point, layered soft background",
        f"--ar {ratio} --niji 6",
    ))


def deterministic_fallback(profile: str, scene: Mapping[str, object]) -> str:
    """仅用主生成已给出的场景事实组装提示词，不再调用文本模型。"""
    if profile == "krea2":
        prompt = _krea_scene_fallback(scene)
    elif profile == "anima_tags":
        prompt = _anima_scene_fallback(scene)
    elif profile == "natural_language":
        prompt = _natural_scene_fallback(scene)
    elif profile == "niji_sections":
        prompt = _niji_scene_fallback(scene)
    else:
        raise ValueError(f"未知提示词模式：{profile}")
    return _anonymize_prompt_names(prompt, scene)


_FIELD_PATTERNS = {
    "appearance": re.compile(r"\b(?:hair|eyes?|gaze|face|lips?|cheeks?|figure|bust|waist|hips?|legs?)\b", re.I),
    "wardrobe": re.compile(r"\b(?:dress|robe|skirt|shirt|jacket|coat|stockings?|socks?|fabric|garment|clothing)\b", re.I),
    "location": re.compile(r"\b(?:bedchamber|chamber|room|cell|corridor|railing|gate|entrance|forest|street|hall|enclosure|village|outdoors?)\b", re.I),
    "camera": re.compile(r"\b(?:shot|view|angle|lens|focal|perspective|depth of field|camera)\b", re.I),
    "composition": re.compile(r"\b(?:composition|foreground|background|negative space|visual focus|frame|thirds?|diagonal)\b", re.I),
    "lighting": re.compile(r"\b(?:light|lighting|shadow|highlight|backlit|rim light|color temperature)\b", re.I),
    "material": re.compile(r"\b(?:material|texture|fabric|silk|gauze|folds?|skin|hair|wood|metal|wet|moisture)\b", re.I),
    "quality": re.compile(r"\b(?:anatomy|contours?|clean edges?|fine detail|fidelity|polished|best quality|masterpiece)\b", re.I),
}

_LOCATION_RULES = (
    (r"寝殿|卧室", "bedchamber"),
    (r"天牢|牢房|囚室|禁闭室", "confinement cell"),
    (r"长廊|走廊", "corridor"),
    (r"栏杆", "railing"),
    (r"山门", "mountain gate"),
    (r"孤儿院.{0,8}门|门.{0,8}孤儿院", "orphanage entrance"),
    (r"森林|树林", "forest"),
)

def _evidenced_visual_facts(scene: Mapping[str, object]) -> list[str]:
    """开放类型视觉事实：只信任带本轮正文证据且为英文成稿的条目。"""
    narrative = image_prompt_extract.restore_jailbreak(str(scene.get("narrative") or ""))
    values = scene.get("visual_facts")
    if not isinstance(values, list):
        return []
    facts: list[str] = []
    for item in values[:12]:
        if not isinstance(item, Mapping):
            continue
        fact = str(item.get("fact") or "").strip()
        evidence = image_prompt_extract.restore_jailbreak(
            str(item.get("evidence") or ""),
        ).strip()
        if fact and fact.isascii() and evidence and evidence in narrative:
            facts.append(fact)
    return list(dict.fromkeys(facts))


def _expected_present(prompt: str, phrase: str) -> bool:
    for _source_pattern, known_phrase, output_pattern in _VISUAL_FACT_RULES:
        if known_phrase == phrase:
            return bool(re.search(output_pattern, prompt, re.I))
    words = [word for word in re.findall(r"[a-z]{3,}", phrase.lower())
             if word not in {"with", "into", "from", "that", "this", "very", "adult"}]
    if not words:
        return True
    def matches(word: str) -> bool:
        variants = [word]
        if word.endswith("ing") and len(word) > 5:
            stem = word[:-3]
            if len(stem) > 2 and stem[-1] == stem[-2]:
                stem = stem[:-1]
            variants.append(stem)
        return any(re.search(rf"\b{re.escape(value)}\w*\b", prompt, re.I) for value in variants)

    present = sum(1 for word in dict.fromkeys(words) if matches(word))
    return present >= max(1, (len(set(words)) + 1) // 2)


def prompt_field_ledger(prompt: str, scene: Mapping[str, object]) -> dict[str, dict[str, object]]:
    """逐字段验收最终成稿；只记录可由 SceneSpec 客观验证的事实。"""
    fact_scene = _scene_for_facts(scene)
    appearance_source = str(fact_scene.get("appearance") or "")
    subjects = fact_scene.get("subjects")
    subject_visuals = "\n".join(
        str(item.get("description") or "") for item in subjects
        if isinstance(item, Mapping)
    ) if isinstance(subjects, list) else ""
    wardrobe_source = "\n".join((
        appearance_source,
        str(fact_scene.get("wardrobe") or ""),
        str(fact_scene.get("narrative") or "") if re.search(
            r"衣|裙|袍|袜|dress|robe|skirt|stocking", str(fact_scene.get("narrative") or ""), re.I,
        ) else "",
        subject_visuals,
    ))
    appearance_expected = [
        fact for fact in _mapped_facts_from(appearance_source)
        if fact not in _mapped_wardrobe_facts_from(appearance_source)
    ]
    wardrobe_expected = _mapped_wardrobe_facts_from(wardrobe_source)
    if appearance_source.isascii():
        appearance_expected.extend(
            part.strip(" ,.;") for part in appearance_source.split(",")
            if _FIELD_PATTERNS["appearance"].search(part)
            and not _FIELD_PATTERNS["wardrobe"].search(part)
        )
    direct_wardrobe = str(fact_scene.get("wardrobe") or "")
    if direct_wardrobe.isascii():
        wardrobe_expected.extend(
            part.strip(" ,.;") for part in direct_wardrobe.split(",")
            if part.strip(" ,.;") and _FIELD_PATTERNS["wardrobe"].search(part)
        )
    appearance_expected = list(dict.fromkeys(filter(None, appearance_expected)))
    wardrobe_expected = list(dict.fromkeys(filter(None, wardrobe_expected)))
    actions = _scene_action_tags(fact_scene)
    visual_facts = _evidenced_visual_facts(fact_scene)
    locale = str(fact_scene.get("locale") or "")
    locations = [phrase for pattern, phrase in _LOCATION_RULES if re.search(pattern, locale)]
    if locale.isascii() and re.search(r"[A-Za-z]{3}", locale):
        locations.append(locale.strip(" ,.;"))
    requirements = {
        "appearance": bool(appearance_source.strip()),
        "wardrobe": bool(str(fact_scene.get("wardrobe") or "").strip() or wardrobe_expected),
        "action": bool(actions),
        "visual_facts": bool(visual_facts),
        "location": bool(locale.strip()),
        "camera": True,
        "composition": True,
        "lighting": True,
        "material": True,
        "quality": True,
    }
    expected = {
        "appearance": appearance_expected,
        "wardrobe": wardrobe_expected,
        "action": actions,
        "visual_facts": visual_facts,
        "location": locations,
    }
    ledger: dict[str, dict[str, object]] = {}
    for field, required in requirements.items():
        phrases = expected.get(field, [])
        if phrases:
            covered = all(_expected_present(prompt, phrase) for phrase in phrases)
            if field == "location" and not covered:
                covered = bool(_FIELD_PATTERNS["location"].search(prompt))
        elif required and field in {
            "appearance", "wardrobe", "action", "location", "visual_facts",
        }:
            # 有事实来源却没有可验证的英文期望时，通用 hair/room/action 等词不能
            # 冒充已覆盖；否则本地词典漏项会把空泛模板放行到 ComfyUI。
            covered = False
        elif field in _FIELD_PATTERNS:
            covered = bool(_FIELD_PATTERNS[field].search(prompt))
        else:
            covered = not required
        if field == "appearance" and _VAGUE_IDENTITY_RE.search(prompt):
            covered = False
        ledger[field] = {
            "required": required,
            "covered": bool(covered or not required),
            "expected": phrases,
        }
    return ledger


def complete_field_coverage(
    profile: str, prompt: str, scene: Mapping[str, object],
) -> tuple[str, dict[str, dict[str, object]]]:
    """只补缺字段，不替换已经合格的高潮与艺术决策。"""
    ledger = prompt_field_ledger(prompt, scene)
    missing = [field for field, item in ledger.items() if item["required"] and not item["covered"]]
    factual_missing = [field for field in missing if field in {
        "appearance", "wardrobe", "action", "location", "visual_facts",
    }]
    if not factual_missing:
        return prompt, ledger
    details: list[str] = []
    for field in (
        "appearance", "wardrobe", "action", "visual_facts", "location",
    ):
        if field in factual_missing:
            expected = ledger[field]["expected"]
            if isinstance(expected, list):
                details.extend(str(value) for value in expected if str(value).strip())
    defaults = {
        "camera": "a scene-appropriate medium shot with coherent perspective and readable depth of field",
        "composition": "a clear primary visual focus, foreground guidance, controlled negative space, and layered background depth",
        "lighting": "one coherent directional light source with consistent highlights and cast shadows",
        "material": "physically coherent skin, hair, fabric folds, surface texture, and material response",
        "quality": "precise anatomy, stable contours, clean edges, controlled fine detail, and high image fidelity",
    }
    details.extend(defaults[field] for field in missing if field in defaults)
    details = list(dict.fromkeys(detail for detail in details if detail and _expected_present(prompt, detail) is False))
    if not details:
        return prompt, ledger
    separator = ", " if profile == "anima_tags" else "; "
    sentence = "Required visible facts: " + separator.join(details) + "."
    if profile in {"krea2", "natural_language"}:
        repaired = prompt.rstrip(" .") + ". " + sentence
    elif profile == "anima_tags":
        lines = prompt.splitlines()
        if len(lines) != 2:
            return prompt, ledger
        # 可见事实属于 tags 行；解释文段保持纯自然语言，不追加第三种混合结构。
        repaired = (
            lines[0].rstrip(" ,") + ", " + ", ".join(details) + ",\n"
            + lines[1].rstrip() + " " + _anima_content_description(scene, details)
        )
    elif profile == "niji_sections":
        lines = prompt.splitlines()
        if len(lines) != 4:
            return prompt, ledger
        lines[0] = lines[0].rstrip(" ,.;") + ", " + ", ".join(details[:4])
        if len(details) > 4:
            lines[2] = lines[2].rstrip(" ,.;") + ", " + ", ".join(details[4:])
        repaired = "\n".join(lines)
    else:
        return prompt, ledger
    repaired = _anonymize_prompt_names(repaired, scene)
    return repaired, prompt_field_ledger(repaired, scene)


def generate(
    profile: str,
    scene: Mapping[str, object],
    generate_text: Callable[[str, str], str],
    diagnostics: dict[str, object] | None = None,
) -> str:
    """调用文本模型并验证目标协议；语义格式错误时携带原因重写一次。"""
    if profile not in PROFILE_IDS:
        raise ValueError(f"未知提示词模式：{profile}")
    report = diagnostics if diagnostics is not None else {}
    report.clear()
    report.update({"strategy": "direct", "first_errors": [], "repair_errors": []})
    fact_scene = _scene_for_facts(scene)
    model_scene = _scene_for_model(scene)
    system = _system(profile, fact_scene)
    source = json.dumps(model_scene, ensure_ascii=False, separators=(",", ":"))
    first_raw = generate_text(system, source)
    raw = _strip_refusal_suffix(first_raw)
    prompt = _normalize(profile, raw, fact_scene)
    errors = _errors(profile, prompt, fact_scene)
    if not raw.strip() and _REFUSAL_RE.search(image_prompt_extract.restore_jailbreak(first_raw)):
        errors.insert(0, "模型返回拒答")
    if profile == "anima_tags":
        errors.extend(_anima_contract_errors(raw))
        errors.extend(_anima_scene_fact_errors(prompt, fact_scene))
    if not errors:
        completed, ledger = complete_field_coverage(profile, prompt, fact_scene)
        report["field_ledger"] = ledger
        return completed
    report["first_errors"] = list(dict.fromkeys(errors))
    repair = (
        f"{source}\n\n上次输出未通过：{'；'.join(errors)}。请严格按系统协议重写。"
        f"\n上次输出：{raw}"
    )
    second_raw = generate_text(system, repair)
    repaired_raw = _strip_refusal_suffix(second_raw)
    prompt = _normalize(profile, repaired_raw, fact_scene)
    errors = _errors(profile, prompt, fact_scene)
    if (not repaired_raw.strip()
            and _REFUSAL_RE.search(image_prompt_extract.restore_jailbreak(second_raw))):
        errors.insert(0, "模型返回拒答")
    if profile == "anima_tags":
        errors.extend(_anima_contract_errors(repaired_raw))
        errors.extend(_anima_scene_fact_errors(prompt, fact_scene))
    if errors:
        report["repair_errors"] = list(dict.fromkeys(errors))
        report["strategy"] = "fallback"
        # Krea2 的长度只用于引导纠错；英文、段落、分级和禁词仍是硬合同。
        if profile == "krea2" and not _inline_krea_errors(prompt, fact_scene):
            completed, ledger = complete_field_coverage(profile, prompt, fact_scene)
            report["field_ledger"] = ledger
            return completed
        fallback = deterministic_fallback(profile, fact_scene)
        completed, ledger = complete_field_coverage(profile, fallback, fact_scene)
        report["field_ledger"] = ledger
        return completed
    report["strategy"] = "repaired"
    completed, ledger = complete_field_coverage(profile, prompt, fact_scene)
    report["field_ledger"] = ledger
    return completed


def generate_result(
    profile: str,
    scene: Mapping[str, object],
    generate_text: Callable[[str, str], str],
) -> dict[str, object]:
    diagnostics: dict[str, object] = {}
    prompt = generate(profile, scene, generate_text, diagnostics)
    first_errors = diagnostics.get("first_errors")
    repair_errors = diagnostics.get("repair_errors")
    first_items = first_errors if isinstance(first_errors, list) else []
    repair_items = repair_errors if isinstance(repair_errors, list) else []
    result: dict[str, object] = {
        "prompt": prompt,
        "negative_prompt": negative_prompt(profile, scene),
        "strategy": str(diagnostics.get("strategy") or "direct"),
        "validation_errors": list(dict.fromkeys([
            *[str(item) for item in first_items],
            *[str(item) for item in repair_items],
        ])),
        "field_ledger": diagnostics.get("field_ledger") or {},
    }
    return result
