"""一次性文本类 AI 端点：提示词生成/关键词/灵感/反推/工作流描述/润色/翻译/输入口编排。
无状态、单轮，均委托 ai_common 的 chat/build_chat_model。
"""
import json
import logging

from fastapi import APIRouter, HTTPException

from app.routers.ai_common import ChatModelReq, build_chat_model, chat

logger = logging.getLogger(__name__)

router = APIRouter()


class PromptRequest(ChatModelReq):
    scene: str                     # 用户描述的画面/场景
    style: str = "image_prompt"    # image_prompt=出图正向提示词


_SYSTEM = (
    "你是 AI 绘画提示词助手。根据用户描述的画面，输出适合 Stable Diffusion / "
    "ComfyUI 使用的英文正向提示词，用逗号分隔的标签或短语，突出主体、画风、光影、"
    "构图、画质。只输出提示词本身，不要解释、不要引号、不要换行。"
)


@router.post("/prompt")
def gen_prompt(req: PromptRequest) -> dict[str, object]:
    """根据场景描述生成出图提示词（调用用户配置的对话模型）。"""
    if not req.scene.strip():
        raise HTTPException(status_code=400, detail="场景描述为空")
    prompt = chat(
        req.base_url, req.api_key, req.model, _SYSTEM, req.scene, proxy=req.proxy,
    )
    return {"prompt": prompt}


class ProfilePromptRequest(ChatModelReq):
    profile: str
    scene: dict[str, object]


@router.get("/prompt/profile/defaults")
def profile_prompt_defaults(profile: str, rating: str = "nsfw") -> dict[str, str]:
    """设置页读取后端协议默认值，避免前端复制质量词真源。"""
    from app.services import image_prompt_profiles

    try:
        return image_prompt_profiles.profile_defaults(profile, rating)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/prompt/profile")
def gen_profile_prompt(req: ProfilePromptRequest) -> dict[str, object]:
    """按多元数据插入选择的模型协议生成最终生图提示词。"""
    from app.services import image_prompt_profiles

    try:
        result = image_prompt_profiles.generate_result(
            req.profile,
            req.scene,
            lambda system, user: chat(
                req.base_url, req.api_key, req.model, system, user,
                temperature=0.3, proxy=req.proxy,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**result, "profile": req.profile}


class KeywordsRequest(ChatModelReq):
    text: str                      # 提示词原文（中/英、有无分隔符均可）


_KEYWORDS_SYSTEM = (
    "你是标签提取助手。把给定的绘画提示词切分成 4-8 个简短关键词标签，"
    "覆盖主体、风格、场景、光影等要点。中文提示词输出中文标签。"
    "只输出标签本身，用英文逗号分隔，不要解释、不要编号、不要换行。"
)


@router.post("/extract-keywords")
def extract_keywords(req: KeywordsRequest) -> dict[str, object]:
    """把提示词轻量切分成关键词标签（纯文本，非反推，省 token）。返回 {tags:[...]}。"""
    if not req.text.strip():
        return {"tags": []}
    out = chat(req.base_url, req.api_key, req.model, _KEYWORDS_SYSTEM, req.text,
               temperature=0.2, proxy=req.proxy)
    import re as _re
    tags = [t.strip() for t in _re.split(r"[,，;；\n]+", out) if t.strip()][:8]
    return {"tags": tags}


class InspirationRequest(ChatModelReq):
    query: str                     # 用户想找的灵感（服装/发型/画风等）
    proxy_url: str = ""            # 联网搜索代理（访问外网）


@router.post("/inspiration")
def inspiration(req: InspirationRequest) -> dict[str, object]:
    """联网找灵感 → 提炼成英文提示词。返回 {query, prompt, tags[], sources[]}，前端渲染成灵感卡。"""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="灵感主题为空")
    from app.services import inspiration as insp
    try:
        return insp.search_and_refine(req.query, req.base_url, req.api_key,
                                      req.model, proxy=req.proxy_url, chat_proxy=req.proxy)
    except insp.NoResults as e:
        raise HTTPException(status_code=502, detail=f"{e}，请重试或换关键词")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


class DescribeImageRequest(ChatModelReq):
    images: list[str] = []         # 待反推图片（data URI 或可访问 URL），送 VLM
    hint: str = ""                 # 可选额外要求（如「侧重画风」）；model 须为支持视觉的模型


_REVERSE_SYSTEM = (
    "你是图像反推助手。仔细观察用户提供的图片，输出适合 Stable Diffusion / ComfyUI "
    "使用的英文正向提示词，用逗号分隔的 Danbooru 风格标签或短语，涵盖主体、人物特征、"
    "服饰、动作、画风、光影、构图、画质。只输出提示词本身，不要解释、不要引号、不要换行。"
)


@router.post("/describe-image")
def describe_image(req: DescribeImageRequest) -> dict[str, object]:
    """反推：看图输出提示词（/r）。需视觉模型，复用「对话模型」配置。"""
    if not req.images:
        raise HTTPException(status_code=400, detail="没有图片输入")
    llm = build_chat_model(
        req.base_url, req.api_key, req.model, temperature=0.3, proxy=req.proxy,
    )
    from langchain_core.messages import HumanMessage, SystemMessage
    content: list = [{"type": "text", "text": req.hint or "请反推这张图片的提示词"}]
    content += [{"type": "image_url", "image_url": {"url": u}} for u in req.images]
    try:
        resp = llm.invoke([SystemMessage(content=_REVERSE_SYSTEM),
                           HumanMessage(content=content)])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"反推失败（模型需支持视觉）：{e}")
    from app.services import llm as _llm
    return {"prompt": _llm.flatten_content(resp.content).strip()}

class DescribeRequest(ChatModelReq):
    name: str = ""                 # 工作流名
    nodes: list[dict] = []         # 节点结构 [{id,type,title}]


_DESCRIBE_SYSTEM = (
    "你是 ComfyUI 工作流分析助手。根据工作流的名称和节点列表，用一句中文（40 字内）"
    "概括这个工作流的能力，例如「反推图片得到 Danbooru 标签提示词」「局部重绘」"
    "「图像放大」「文生图」。只输出这句描述，不要解释、不要标点结尾、不要换行。"
)


@router.post("/describe-workflow")
def describe_workflow(req: DescribeRequest) -> dict[str, object]:
    """根据工作流节点结构，AI 生成一句能力描述（模板描述弹窗的「AI 辅助生成」）。"""
    lines = [f"#{n.get('id')} {n.get('type', '')} {n.get('title', '')}".strip()
             for n in req.nodes]
    user = f"工作流名称：{req.name}\n节点列表：\n" + "\n".join(lines)
    desc = chat(
        req.base_url, req.api_key, req.model, _DESCRIBE_SYSTEM, user,
        temperature=0.3, proxy=req.proxy,
    )
    return {"description": desc}


class PolishRequest(ChatModelReq):
    text: str = ""                 # 用户已写的能力描述


_POLISH_SYSTEM = (
    "你是文本润色助手。把用户写的 ComfyUI 工作流能力描述改写得更清晰、结构化、"
    "便于 AI 理解和调用：保留原意和关键名词（模型名、节点名、参数），去掉口语和"
    "冗余，突出「输入→处理→输出」。控制在 60 字内，只输出润色后的描述本身，"
    "不要解释、不要换行。"
)


@router.post("/polish-description")
def polish_description(req: PolishRequest) -> dict[str, object]:
    """基于用户已输入的能力描述文本润色，使其更便于 AI 理解。"""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="描述文本为空")
    desc = chat(
        req.base_url, req.api_key, req.model, _POLISH_SYSTEM, req.text,
        temperature=0.4, proxy=req.proxy,
    )
    return {"description": desc}


class TranslateRequest(ChatModelReq):
    text: str = ""
    target_lang: str = "中文"          # 目标语言（自由文本，如 中文/English/日本語）
    polish: bool = False               # true=翻译同时润色通顺


@router.post("/translate")
def translate(req: TranslateRequest) -> dict[str, object]:
    """把文本翻译成目标语言（可选润色）。用于模型介绍的翻译/润色。"""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="待翻译文本为空")
    extra = "，并润色得通顺自然" if req.polish else ""
    system = (f"你是专业翻译。把用户文本翻译成{req.target_lang}{extra}。"
              "保留专业术语、模型名、参数、代码原样。只输出译文，不要解释、不要加引号。")
    out = chat(
        req.base_url, req.api_key, req.model, system, req.text,
        temperature=0.3, proxy=req.proxy,
    )
    return {"text": out}

class WorkflowPortsRequest(ChatModelReq):
    scene: str = ""                    # 用户本轮的自然语言需求
    image_count: int = 0               # 本轮随文图片数量（图按序号 1..n 指代）
    node_schema: list[dict] = []       # 选中节点的输入口结构（扩展端 collectNodeSchema 回传）
    model_name: str = ""               # 工作流里的 checkpoint/模型名，用于定提示词风格
    style: str = ""                    # 用户手动选的提示词风格 sd/gpt/banana/""(自动，按 model_name 判)
    style_template: str = ""           # 自定义风格存档内容（非空时优先）
    force: bool = False                # true=用户明确要编排(/a 或点按钮)，跳过意图判定
    repo_id: str = ""                  # 小仓库 id，用于取该仓库的当前色彩约束（空=不注入）


def _prompt_style_hint(model_name: str, style: str = "", template: str = "") -> str:
    """按存档模板/用户选的风格/回退模型名给出正向提示词风格提示。判定收口在 services/image_prompt_style。"""
    from app.services.image_prompt_style import guidance_for
    return guidance_for(style, model_name, template)


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

@router.post("/workflow-ports")
def workflow_ports(req: WorkflowPortsRequest) -> dict[str, object]:
    """根据用户需求 + 选中节点的输入口结构，AI 规划输入口填充计划（不执行，交前端确认）。"""
    if not req.scene.strip() and req.image_count == 0:
        raise HTTPException(status_code=400, detail="内容为空")
    if not req.node_schema:
        raise HTTPException(status_code=400, detail="没有可操作的节点结构")
    system = _PORTS_SYSTEM + "\n\n模型提示：" + _prompt_style_hint(req.model_name, req.style, req.style_template)
    user = (
        f"用户需求：{req.scene or '（未给文字，仅给了图片）'}\n"
        f"本轮用户提供的图片数量：{req.image_count}（按顺序记为图1、图2…）\n"
        f"选中节点的输入口结构（JSON）：\n{json.dumps(req.node_schema, ensure_ascii=False)}"
    )
    raw = chat(
        req.base_url, req.api_key, req.model, system, user,
        temperature=0.3, proxy=req.proxy,
    )
    plan = _parse_plan_json(raw)
    if plan is None:
        raise HTTPException(status_code=502, detail=f"AI 未返回可解析的计划：{raw[:200]}")
    # force=True（用户点「AI 编排」或输入 /a，意图明确）时，强制按编排处理
    if req.force:
        plan["is_orchestration"] = True
    _validate_model_names(plan, req.node_schema)
    _inject_lora_triggers(plan, req.node_schema, req.scene)
    _inject_palette(plan, req.node_schema, req.scene, req.repo_id)
    return plan


def _validate_model_names(plan: dict, node_schema: list[dict]) -> None:
    """按 object_info 的枚举校验计划里的模型名，非法的剔掉并写进 summary。

    模型名必须逐字正确，而模型是最容易被编造的字段（文件名长、无语义）。写错的话
    工作流会跑到加载那一步才报 value not in list，用户看不出是计划的问题。
    查不到枚举（ComfyUI 未启动等）一律放行，不误拦。
    """
    if not plan.get("is_orchestration"):
        return
    try:
        from app.config import COMFYUI_BASE_URL
        from app.services.model_options import validate_plan
        validate_plan(plan, node_schema, COMFYUI_BASE_URL)
    except Exception:
        logger.warning("模型名校验失败，已跳过", exc_info=True)


def _inject_lora_triggers(plan: dict, node_schema: list[dict], scene: str) -> None:
    """把工作流里 LoRA 的触发词机械前置到正向提示词（判定收口在 services/lora_inject）。

    触发词不能交给大脑写——它会翻译/改写/漏词导致 LoRA 静默失效。查表失败一律静默跳过：
    触发词是增强项，不该因为它让整个编排请求失败。
    """
    if not plan.get("is_orchestration"):
        return
    try:
        from app.services.lora_index import get_triggers_map
        from app.services.lora_inject import inject
        triggers_map = get_triggers_map()
        if triggers_map:
            inject(plan, node_schema, scene, triggers_map)
    except Exception:
        logger.warning("LoRA 触发词注入失败，已跳过", exc_info=True)


def _inject_palette(plan: dict, node_schema: list[dict], scene: str,
                    repo_id: str) -> None:
    """把该仓库的当前色彩约束追加到正向提示词（判定收口在 services/palette_inject）。

    与触发词注入同样是增强项，失败一律静默跳过。注意顺序在触发词之后 ——
    触发词要前置、色板要追加，先注触发词才不会被色板挤开。
    """
    if not plan.get("is_orchestration") or not repo_id.strip():
        return
    try:
        from app.services import palette_pref
        from app.services.palette_inject import inject
        colors = palette_pref.load(repo_id.strip()).get("colors") or []
        if colors:
            inject(plan, node_schema, scene, colors)
    except Exception:
        logger.warning("色彩约束注入失败，已跳过", exc_info=True)


def _parse_plan_json(raw: str) -> dict | None:
    """从模型输出里抽出 JSON 对象（容忍代码块包裹/前后赘字）。"""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
    lo, hi = s.find("{"), s.rfind("}")
    if lo == -1 or hi == -1 or hi <= lo:
        return None
    try:
        obj = json.loads(s[lo:hi + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    obj.setdefault("summary", "")
    obj.setdefault("ops", [])
    obj.setdefault("is_orchestration", True)  # 缺省视为编排（老行为兜底）
    if not isinstance(obj["ops"], list):
        obj["ops"] = []
    return obj
