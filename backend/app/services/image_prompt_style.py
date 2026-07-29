"""生图提示词风格：按生图模型名识别家族，给出「该模型偏好的提示词写法」指引。

主流云端生图模型分两类写法：
- 自然语言系（gpt-image / nano-banana 等）：要连贯英文句子，忌逗号标签堆砌与 SD 质量咒。
- 标签系（SD/SDXL/Pony 等）：要 Danbooru 逗号标签 + 质量词。

中转常把模型改名（如 gpt-image-2-all / gemini-2.5-flash-image-xxx），故用子串匹配识别。
本模块是「如何指导大脑写提示词」的单一属主；image_agent 据此组装系统提示词。
"""

# 家族关键词（小写子串匹配，命中即判定）。顺序敏感：按 dict 插入序逐个家族匹配，先命中先返回。
# 云端(gpt/banana)与本地(flux/sd3 归自然语言、sdxl/pony 归标签)共用此判定，作单一属主。
_FAMILY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "gpt": ("gpt-image", "gpt_image", "gptimage", "gpt-4o-image", "gpt4o-image", "dall-e", "dalle", "dall_e"),
    "banana": ("banana", "gemini", "flash-image", "flash_image", "imagen"),
    # 标签系放在自然语言系之前判：sdxl/pony 等必须先命中，避免被 generic 兜底。
    # 含宽松的 "xl"（Pony/Illustrious 等本地 checkpoint 名常带 XL 后缀）
    "tag": ("sdxl", "sd-xl", "sd1.5", "sd15", "xl", "pony", "animagine", "illustrious", "noob"),
    # flux / sd3 是自然语言系本地模型，显式归入 natural（避免 stable-diffusion 子串误入 tag）
    "natural": ("flux", "sd3", "stable-diffusion-3", "stable_diffusion_3"),
}


def detect_family(model: str) -> str:
    """按模型名判定家族：gpt / banana / tag / natural / generic（未识别，按自然语言处理）。"""
    name = (model or "").lower()
    for family, keys in _FAMILY_KEYWORDS.items():
        if any(k in name for k in keys):
            return family
    return "generic"


_GUIDANCE: dict[str, str] = {
    "gpt": (
        "当前生图模型是 GPT-Image 系列（自然语言模型）：用连贯的英文句子描述画面"
        "（主体外观、动作姿态、场景环境、光影氛围、镜头视角与构图、艺术风格/媒介），"
        "不要用逗号堆砌标签，也不要加 masterpiece/8k/best quality 这类 SD 质量咒。"
        "可直接下达指令；需要画面内出现文字时用引号标出文字内容。"
    ),
    "banana": (
        "当前生图模型是 Nano-Banana（Gemini 图像模型，自然语言/对话式）：用自然、具体的英文"
        "描述画面（主体、场景、光线、情绪、构图、风格），不要用逗号标签堆砌，也不要加 SD 质量咒。"
        "改图时明确说明「保留什么、修改什么」——它的强项是图像编辑与角色/风格一致性。"
    ),
    "tag": (
        "当前生图模型是标签系（SD/SDXL/Pony 等）：用英文 Danbooru 风格标签、逗号分隔，"
        "涵盖主体、画风、光影、构图，并补质量词（如 masterpiece, best quality, highly detailed）。"
    ),
    "natural": (
        "当前生图模型偏好自然语言完整句子描述（英文，如 Flux/SD3）：用连贯句子描述画面"
        "（主体、场景、光影、构图、风格），不要用 Danbooru 逗号标签堆叠，也不要加 SD 质量咒。"
    ),
    "generic": (
        "当前生图模型按主流自然语言模型处理：用连贯的英文句子描述画面"
        "（主体、场景、光影、构图、风格），不要用逗号标签堆砌，也不要加 SD 质量咒。"
    ),
}


def gen_guidance(family: str) -> str:
    """取某家族的提示词写法指引。未知家族回退 generic。"""
    return _GUIDANCE.get(family, _GUIDANCE["generic"])


def gen_guidance_for(model: str) -> str:
    """按模型名直接取写法指引（detect_family + gen_guidance 的便捷组合）。"""
    return gen_guidance(detect_family(model))


# 用户手动选的风格 → 家族。空串/未知 = 自动（按模型名判）。对齐前端下拉取值。
_STYLE_TO_FAMILY: dict[str, str] = {
    "sd": "tag",
    "gpt": "gpt",
    "banana": "banana",
    "auto": "",   # 显式的「自动」
}


def guidance_for(style: str, model: str, template: str = "") -> str:
    """取提示词写法指引，优先级：自定义存档模板 > 手动选的 style > 按模型名自动判。

    - template 非空：用户自定义风格存档，让大脑模仿其组织结构/画风/负面词写法。
    - style 取值 sd/gpt/banana/auto/""，来自前端风格切换器。
    - 都没有：按模型名自动判。
    两条生图路径（云端 image_agent / 工作流 workflow-ports）共用。
    """
    if template and template.strip():
        return (
            "参照下面这份「风格模板」来写提示词——模仿它的组织结构、画风描述方式、"
            "分区（如画风/动作/衣着/构图/光影）与负面词写法，按当前画面主题填充内容，"
            "不要照抄模板里的具体角色/场景细节：\n\n" + template.strip()
        )
    fam = _STYLE_TO_FAMILY.get((style or "").lower().strip(), "")
    if fam:
        return gen_guidance(fam)
    return gen_guidance_for(model)
