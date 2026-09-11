"""RAG 查询中间件：查询扩展、问题拆分和可选 LLM 查询重写。

本模块只处理 query，不依赖 Chroma、ComfyUI 或工作流编排；节点索引层可以复用
它生成多路查询，工作流构建层也可以把同一组查询写入模型上下文。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_QUERY_EXPANSIONS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("出图", "文生图", "生成图片", "text to image", "txt2img"),
     "CheckpointLoaderSimple CLIPTextEncode EmptyLatentImage KSampler VAEDecode SaveImage"),
    (("anima", "flux", "sd3", "unet", "分离式", "独立unet", "lora"),
     "UNETLoader DualCLIPLoader CLIPLoader VAELoader LoraLoaderModelOnly UNET MODEL CLIP VAE LoRA"),
    (("图生图", "img2img", "参考图"), "LoadImage VAEEncode KSampler VAEDecode"),
    (("分割", "yolo", "sam2", "抠图", "移除人物", "人物元素"),
     "AILab_YoloV8 SAM2Segment segmentation detection mask person background"),
    (("扩展遮罩", "遮罩裁剪", "grow mask", "crop by mask"),
     "GrowMask GrowMaskWithBlur ImageCropByMask LayerUtility CropByMask mask crop"),
    (("reference controlnet", "reference control", "姿势迁移", "参考控制"),
     "ACN_AdvancedControlNetApply_v2 ACN_ReferenceControlNet "
     "ACN_ReferencePreprocessor pose reference controlnet"),
    (("反推", "看图", "caption", "wd14", "llama", "描述图片"),
     "WD14Tagger Florence2 BLIPCaption llama_cpp_model_loader llama_cpp_instruct_adv"),
    (("d站", "d 站", "danbooru", "画廊", "画师标签"),
     "DanbooruGalleryNode Danbooru image tags artist tag metadata gallery"),
    (("画师标签", "artist tag", "标签拼接", "标签输出"),
     "1hew_TextListToString CR Text Concatenate ShowText text list string prompt concatenate"),
    (("切换", "开关", "模式选择", "二选一", "多选一"),
     "Any Switch rgthree ImpactConditionalBranch ImpactSwitch BOOLEAN switch branch"),
    (("放大", "upscale", "超分"), "UltimateSDUpscale ImageScale ImageUpscaleWithModel"),
    (("controlnet", "控制网", "姿态", "边缘"), "ControlNetApply ControlNetLoader AIO Aux"),
)

_MAX_QUERY_BRANCHES = 8


@dataclass(frozen=True)
class QueryBranch:
    query: str
    kind: str
    weight: float
    preserve_top: bool = False


def plan_queries(need: str) -> list[QueryBranch]:
    """生成带语义角色的查询分支，供融合层区别对待。"""
    text = (need or "").strip()
    if not text:
        return []
    branches = [QueryBranch(text, "original", 0.35)]
    low = text.lower()
    for aliases, expansion in _QUERY_EXPANSIONS:
        if any(alias.lower() in low for alias in aliases):
            branches.append(QueryBranch(
                f"{' '.join(aliases)} {expansion}",
                "capability", 2.0, preserve_top=True,
            ))
    for part in re.split(r"[，,；;。\n]|并且|同时|以及|然后|另外|还要", text):
        part = part.strip()
        if len(part) >= 3 and part.casefold() != text.casefold():
            branches.append(QueryBranch(part, "subquery", 1.0))
    out: list[QueryBranch] = []
    seen: dict[str, int] = {}
    for branch in branches:
        key = branch.query.casefold()
        previous = seen.get(key)
        if previous is None:
            seen[key] = len(out)
            out.append(branch)
        elif branch.weight > out[previous].weight:
            out[previous] = branch
    return out[:_MAX_QUERY_BRANCHES]


def expand_query(need: str) -> list[str]:
    """兼容旧调用：返回查询规划中的文本列表。"""
    return [branch.query for branch in plan_queries(need)]


_REWRITE_SYSTEM = (
    "你是 ComfyUI 检索助手。用户给一段搭工作流的需求，你只输出一行空格分隔的检索关键词，"
    "抽出模型架构、具体节点名、功能词和中英文同义词；不要解释、不要标点。"
)


def rewrite_query(need: str, chat_fn, base_url: str, api_key: str,
                  model: str, proxy: str = "") -> str:
    """可选的单轮 LLM 查询重写，失败时返回原需求，不阻断检索。"""
    if not (base_url and model):
        return need
    try:
        keywords = chat_fn(
            base_url, api_key, model, _REWRITE_SYSTEM, need,
            temperature=0.0, proxy=proxy, retries=1,
        )
        keywords = (keywords or "").strip().replace("\n", " ")
        return f"{need} {keywords}" if keywords else need
    except Exception:
        return need
