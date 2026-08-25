"""一次性文本类 AI 端点：提示词生成/关键词/灵感/反推/工作流描述/润色/翻译/输入口编排。
无状态、单轮，均委托 ai_common 的 chat/build_chat_model。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routers.ai_common import ChatModelReq, build_chat_model, chat

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
    preset_dir: str = ""
    preset_name: str = ""
    user_name: str = ""


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
                req.base_url, req.api_key, req.model,
                image_prompt_profiles.system_with_preset(
                    system, req.scene,
                    preset_dir=req.preset_dir,
                    preset_name=req.preset_name,
                    user_name=req.user_name,
                ),
                user,
                temperature=0.3, proxy=req.proxy,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        from app.services import run_trace

        run_trace.emit(
            {
                "turn_id": str(req.scene.get("turn_id") or ""),
                "thread_id": str(req.scene.get("thread_id") or req.scene.get("repo_id") or ""),
                "repo_id": str(req.scene.get("repo_id") or ""),
            },
            "illustration.profile",
            profile=req.profile,
            strategy=result.get("strategy", "direct"),
            validation_errors=result.get("validation_errors", []),
        )
    except Exception:  # noqa: BLE001 Trace 不得阻断提示词生成
        pass
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
    query: str                     # 用户想找的灵感（服装/发型/画风/角色设定/世界观等，主题不限）
    proxy_url: str = ""            # 联网搜索代理（访问外网）
    search_provider: str = ""      # 搜索源名称，空=注册表默认源


@router.post("/inspiration")
def inspiration(req: InspirationRequest) -> dict[str, object]:
    """联网找灵感 → 整理成「标题+内容」中文总结。返回 {title, content, sources[], images[]}，前端渲染成灵感卡。"""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="灵感主题为空")
    from app.services import inspiration as insp
    try:
        return insp.search_and_refine(req.query, req.base_url, req.api_key,
                                      req.model, proxy=req.proxy_url, chat_proxy=req.proxy,
                                      search_provider=req.search_provider or None)
    except insp.NoResults as e:
        raise HTTPException(status_code=502, detail=f"{e}，请重试或换关键词")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


class InspirationSelectRequest(BaseModel):
    thread_id: str
    message_id: str
    urls: list[str] = []           # 用户勾选的图片 full_url 列表


@router.post("/inspiration/select")
def inspiration_select(req: InspirationSelectRequest) -> dict[str, object]:
    """记录用户勾选的图片 URL 到灵感卡消息。

    会话快照只记选中项的 URL（全量搜索结果不落盘，防膨胀）。
    校验下沉到 chat_snapshot.select_inspiration。
    """
    from app.services import chat_snapshot
    return chat_snapshot.select_inspiration(req.thread_id, req.message_id, req.urls or [])


@router.get("/image-proxy")
def image_proxy(url: str, proxy: str = ""):
    """外网图片代理：中转灵感卡缩略图/原图。限 http(s) 与 5MB，防被当开放代理滥用。"""
    from app.services.image_proxy import ImageProxyError, fetch_remote_image
    try:
        data, ctype = fetch_remote_image(url, proxy=proxy)
    except ImageProxyError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
    from fastapi.responses import Response
    return Response(content=data, media_type=ctype)


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


@router.post("/workflow-ports")
def workflow_ports(req: WorkflowPortsRequest) -> dict[str, object]:
    """根据用户需求 + 选中节点的输入口结构，AI 规划输入口填充计划（不执行，交前端确认）。"""
    from app.services import workflow_port_planner

    try:
        return workflow_port_planner.plan(
            scene=req.scene, image_count=req.image_count, node_schema=req.node_schema,
            model_name=req.model_name, style=req.style, style_template=req.style_template,
            force=req.force, repo_id=req.repo_id, base_url=req.base_url,
            api_key=req.api_key, model=req.model, proxy=req.proxy, chat_fn=chat,
        )
    except workflow_port_planner.WorkflowPortPlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except workflow_port_planner.WorkflowPortPlanResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
