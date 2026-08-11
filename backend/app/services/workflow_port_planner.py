from __future__ import annotations

import json
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


class WorkflowPortPlanError(ValueError):
    pass


class WorkflowPortPlanResponseError(RuntimeError):
    pass


_PORTS_SYSTEM = (
    "你是 ComfyUI 工作流输入/输出口编排助手。用户选定了若干节点，下面给出这些节点的结构：\n"
    "- inputs：左侧输入口（含 name/type/是否已连线 connected/连线来源 source_type/上游源节点 id source_node_id）\n"
    "- widgets：节点自身可填参数（name/type/当前值 value）\n"
    "- outputs：右侧输出口（含 name/type/已连到的下游 targets=[{node_id,node_type,input_name}]）\n"
    "- neighbor：若某节点带此字段（upstream=某选中节点的直接上游源，downstream=直接下游），"
    "它是为「修改选中节点左侧接线内容」附带的邻居节点，可直接对它出操作。\n"
    "★左侧接线内容改到上游：选中节点的连线输入（如 KSampler 的 latent/positive/negative/model）本身没有"
    "可填值——它的内容由上游源节点决定。要改这类内容，就对该输入 source_node_id 指向的上游节点出 set_widget：\n"
    "  · latent 宽高/批次 → 改上游 EmptyLatentImage 的 width/height/batch_size；\n"
    "  · 正/负提示词 → 改上游 CLIPTextEncode（正接 positive、负接 negative）的 text；\n"
    "  · 模型/CLIP/VAE 名 → 改上游对应 Loader 的 widget。\n"
    "  这些上游节点已作为 neighbor=upstream 一并给你，node_id 用上游节点的 id，不要往连线输入本身写值。\n"
    "★换模型必须认准链上的哪一环。采样器的 model 常常不是直连加载器，而是一条链，例如\n"
    "  KSampler → LoraLoaderModelOnly(lora_name) → UNETLoader(unet_name)，\n"
    "  中间还可能夹 ModelSampling*/CFGNorm 这类直通节点（它们没有模型 widget，不要往上面写）。\n"
    "  整条链已作为 neighbor=model-chain 一并给你。规则：\n"
    "  · 用户要换【底模/大模型/checkpoint】→ 改链末端的 CheckpointLoaderSimple.ckpt_name "
    "或 UNETLoader.unet_name，绝不要写到 lora_name 上；\n"
    "  · 用户要换【LoRA】→ 改 LoraLoader/LoraLoaderModelOnly 的 lora_name；\n"
    "  · 只能改已存在的加载器 widget。若用户要的模型类型与链上加载器对不上"
    "（例如链上是 UNETLoader 但用户点名一个 checkpoint——换过去要重接 CLIP/VAE、增删节点），"
    "这属于改拓扑，不要放进 ops，写进 summary 说明需要手动改图及原因；\n"
    "  · 文件名必须逐字用 widget 当前值里出现过的同类文件名或用户明确给出的名字，不要凭印象编造。\n"
    "  · 链上出现条件分支节点（如 ImpactConditionalBranch）时，生效的模型取决于运行时状态，"
    "静态判断不了——不要猜，写进 summary 让用户自己确认。\n"
    "你的任务：根据用户需求，规划如何填充/替换这些口，输出一个【操作计划】，由前端确认后执行。\n"
    "★最重要原则：选定的节点只是【可操作范围】，不是必须全填。很多口已经填好/接好线，"
    "用户没明确要求改的，一律不要动、不要放进 ops。只对用户本轮明确想改的口出操作。\n"
    "动作规则：\n"
    "- 文本/数值类 widget（如提示词 text、seed、steps、cfg、宽高）→ action=set_widget，value 为要填的值。\n"
    "- 图像输入口（type 为 IMAGE 的连线口）→ action=set_image，image_index 指第几张用户图（从 1 开始）；"
    "前端会新建 LoadImage 接入该口并顶替原连线。\n"
    "- 图像加载节点（type 为 LoadImage，或含名为 image 的 combo/图像 widget）→ 当用户想用自己提供的图替换它时，"
    "对该 image widget 用 action=set_image、image_index 指第几张用户图（从 1 开始），不要用 set_widget 去编造文件名；"
    "前端会把用户图上传到 ComfyUI 并把该 widget 设为真实文件名。\n"
    "- 替换某节点的【输出口】内容（让用户提供的图/文本顶替该输出口、随工作流流入下游）→ "
    "action=replace_output，output 填输出口的 name；图像输出口(type=IMAGE)用 image_index 指第几张用户图、"
    "并设 kind=\"image\"；文本输出口(type=STRING)用 value 填要输出的文本、并设 kind=\"text\"。"
    "注意：CONDITIONING 等张量输出口无法用图/文本直接替换，遇到时写进 summary 建议人工，不要放进 ops。\n"
    "- 只操作确有把握的口；拿不准或需要删节点/大改拓扑的，不要放进 ops，写进 summary 里建议用户手动处理。\n"
    "- 用户没提到的口不要乱填。提示词风格按下面的模型提示。\n"
    "★意图判定：先判断用户这句话到底是不是想【编排/修改这个工作流的输入输出口】。\n"
    "  是编排（如「把提示词改成…」「用图1替换输入图」「seed 改成 5」「这个口接我的图」）→ is_orchestration=true，正常出 ops。\n"
    "  不是编排、只是普通绘画问答/让你润色或翻译提示词文本/闲聊（如「帮我把这串提示词精练成中文」「这画风怎么形容」）\n"
    "  → is_orchestration=false，ops 留空，summary 留空。这类交给对话模型处理，不要硬编排。\n"
    "只输出一个 JSON 对象，不要解释、不要代码块标记，格式：\n"
    '{"is_orchestration":true,"summary":"一句话说明你做了什么（中文，逐口说明）","ops":['
    '{"node_id":"节点id","input":"输入口或widget名","output":"输出口名(replace_output时)",'
    '"action":"set_widget|set_image|replace_output","value":"set_widget/文本replace_output的值",'
    '"image_index":1,"kind":"replace_output时填 image 或 text","reason":"为什么这么填（中文简短）"}]}'
)


def plan(
    *, scene: str, image_count: int, node_schema: list[dict], model_name: str,
    style: str, style_template: str, force: bool, repo_id: str,
    base_url: str, api_key: str, model: str, proxy: str,
    chat_fn: Callable[..., str],
) -> dict[str, object]:
    if not scene.strip() and image_count == 0:
        raise WorkflowPortPlanError("内容为空")
    if not node_schema:
        raise WorkflowPortPlanError("没有可操作的节点结构")

    from app.services.image_prompt_style import guidance_for

    system = _PORTS_SYSTEM + "\n\n模型提示：" + guidance_for(style, model_name, style_template)
    user = (
        f"用户需求：{scene or '（未给文字，仅给了图片）'}\n"
        f"本轮用户提供的图片数量：{image_count}（按顺序记为图1、图2…）\n"
        f"选中节点的输入口结构（JSON）：\n{json.dumps(node_schema, ensure_ascii=False)}"
    )
    raw = chat_fn(base_url, api_key, model, system, user, temperature=0.3, proxy=proxy)
    result = parse_plan_json(raw)
    if result is None:
        raise WorkflowPortPlanResponseError(f"AI 未返回可解析的计划：{raw[:200]}")
    if force:
        result["is_orchestration"] = True
    _validate_models(result, node_schema)
    _inject_lora(result, node_schema, scene)
    _inject_colors(result, node_schema, scene, repo_id)
    return result


def parse_plan_json(raw: str) -> dict[str, object] | None:
    try:
        from app.services import structured_output

        parsed = structured_output.parse_object(raw)
    except structured_output.StructuredOutputError:
        return None
    parsed.setdefault("summary", "")
    parsed.setdefault("ops", [])
    parsed.setdefault("is_orchestration", True)
    if not isinstance(parsed["ops"], list):
        parsed["ops"] = []
    return parsed


def _validate_models(result: dict[str, object], node_schema: list[dict]) -> None:
    if not result.get("is_orchestration"):
        return
    try:
        from app.config import COMFYUI_BASE_URL
        from app.services.model_options import validate_plan

        validate_plan(result, node_schema, COMFYUI_BASE_URL)
    except Exception:
        logger.warning("模型名校验失败，已跳过", exc_info=True)


def _inject_lora(result: dict[str, object], node_schema: list[dict], scene: str) -> None:
    if not result.get("is_orchestration"):
        return
    try:
        from app.services.lora_index import get_triggers_map
        from app.services.lora_inject import inject as inject_lora

        triggers = get_triggers_map()
        if triggers:
            inject_lora(result, node_schema, scene, triggers)
    except Exception:
        logger.warning("LoRA 触发词注入失败，已跳过", exc_info=True)


def _inject_colors(
    result: dict[str, object], node_schema: list[dict], scene: str, repo_id: str,
) -> None:
    if not result.get("is_orchestration") or not repo_id.strip():
        return
    try:
        from app.services import palette_pref
        from app.services.palette_inject import inject as inject_palette

        colors = palette_pref.load(repo_id.strip()).get("colors") or []
        if colors:
            inject_palette(result, node_schema, scene, colors)
    except Exception:
        logger.warning("色彩约束注入失败，已跳过", exc_info=True)
