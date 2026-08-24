"""图像智能体端点：SSE 流式生成 + 后台运行状态 + 打断。
生成跑在后台线程（agent_runner），与 HTTP 连接解耦。
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Literal

from app.routers.ai_common import EmbedModelReq
from app.generated.wire_contracts import AGENT_INVOCATION_WIRE_FIELDS
from app.services.sse import sse_response

router = APIRouter()


class ImageMaskRequest(BaseModel):
    image: str
    mask: str


class ImageAgentRequest(EmbedModelReq):
    thread_id: str = "home"            # 对话线 = 仓库 id
    message: str = ""                  # 本轮用户输入
    images: list[str] = []             # 随文图片（data URI 或 URL）
    image_mask: ImageMaskRequest | None = None  # 原图与独立 Alpha 蒙版
    gen_base_url: str = ""             # 生图模型（imageModels）
    gen_api_key: str = ""
    gen_model: str = ""
    video_base_url: str = ""           # 视频模型（videoModels）
    video_api_key: str = ""
    video_model: str = ""
    size: str = "1024x1024"            # 生图尺寸（前端比例+分辨率档算好的 宽x高）
    image_quality: Literal["auto", "low", "medium", "high"] = "high"
    output_dir: str = ""               # 输出图片路径（后端落盘留存云图）
    repo_id: str = ""                  # 留存/入库归属仓库（空则用 thread_id）
    message_id: str = ""               # 前端 botId：最终文本按此 id 落盘去重
    proxy_url: str = ""                # 联网搜索代理（search_inspiration 工具用）
    chat_proxy_url: str = ""           # 当前对话模型代理（空=直连）
    gen_proxy_url: str = ""            # 当前生图模型代理（空=直连）
    video_proxy_url: str = ""          # 当前视频模型代理（空=直连）
    style: str = ""                    # 用户手动选的提示词风格 sd/gpt/banana/""(自动)
    style_template: str = ""           # 自定义风格存档的整段内容（非空时优先于 style）
    agent_id: str = ""                 # 多 Agent：选中的 Agent 预设 id（空=内置默认行为）
    stream_output: bool = False         # 智能体正文是否通过 SSE 增量输出
    approval_id: str = ""              # 历史提示词审批卡 id
    approval_action: str = ""          # submit / change / cancel
    edited_prompt: str = ""            # change 时用户在卡片内修改后的提示词
    forced_route: str = ""              # 主管选择卡点击后的显式路由
    user_message_id: str = ""            # 选择卡关联的原用户消息 id
    context_max_tokens: int = Field(default=20_000, ge=0)  # 0=无上限（历史全量不裁剪），去掉 le 上限
    history_per_role: int = Field(default=6, ge=1, le=50)  # 每角色最近历史条数
    provider_profile: Literal["openai_compatible", "claude_compatible"] = "openai_compatible"


# 单 agent 生成入口（POST /ai/image-agent → agent_runner.run_stream）已下线。
# 其 ReAct 大脑降级为多 Agent 的 tool_agent 专家节点（承接 MCP/工具串联），自由文本一律走 /multi-agent。
# 下方 /image-agent/running 与 /image-agent/cancel 保留：后台化的共用机制，多 Agent 同用同一 thread 计数/取消信号。


class MultiAgentRequest(ImageAgentRequest):
    workspace_mode: Literal["story", "generate", "edit"] = "story"
    route_model: str = ""   # supervisor 判分派用的（快）模型，空则用主对话模型
    character_dir: str = ""  # 角色卡文件夹根（前端设置 characterDir），供剧情扮演读卡
    card_name: str = ""      # 本作品关联的角色卡名（空=非扮演）
    card_names: list[str] = []  # 本作品绑定的全部角色卡
    opening_card_name: str = "" # 新会话开场卡；card_name 为兼容别名
    preset_dir: str = ""     # 偏置预设文件夹根（前端设置 presetDir）
    preset_name: str = ""    # 当前激活预设名（空=不用预设）
    user_name: str = ""      # 用户人设名（填 {{user}} 宏）
    user_persona: str = ""   # 用户人设描述（填 personaDescription marker）
    persona_bound: bool = False  # 仓库显式绑定了人设：为真时不被作品快照 persona.json 覆盖
    worldbook_dir: str = ""  # 独立世界书文件夹根（前端设置 worldbookDir）
    worldbook_name: str = "" # 仓库绑定的独立世界书名（空=不绑独立书；与卡内嵌世界书合并）
    illustrate: bool = False  # 剧情插画开关（开=能动性 D 阶段自动配图）
    comfy_illustrate: bool = False  # 前端已预设 ComfyUI 工作流模板：高潮点改发 illustrate_request 事件走异步闭环
    comfy_audio: bool = False  # 前端已预设音频模板（IndexTTS）：剧情产出后发 audio_request 事件逐角色配音
    prompt_profile: str = "krea2"
    appearance_source: Literal["worldbook", "character_card"] = "worldbook"
    character_base_images: dict[str, str] = {}  # ⑥ 角色名→底图（gpt-image 系按在场角色取底图锁一致性）
    illustration_actor_names: list[str] = []  # 自动插画可从正文机械识别的已配置角色名
    style_base_image: str = ""  # ⑥ 无角色底图时的兜底风格底图（gpt-image 系）
    history: list[dict] | None = None  # 前端当前可见历史；显式 [] 禁止回退旧 checkpoint


class TraceReplayRequest(BaseModel):
    repo_id: str
    turn_id: str = ""
    limit: int = Field(default=200, ge=1, le=200)


_missing_wire_fields = AGENT_INVOCATION_WIRE_FIELDS.difference(MultiAgentRequest.model_fields)
if _missing_wire_fields:
    raise RuntimeError(f"MultiAgentRequest 缺少共享 wire 字段：{sorted(_missing_wire_fields)}")


@router.post("/multi-agent")
def multi_agent(req: MultiAgentRequest) -> StreamingResponse:
    """Supervisor 多 Agent（LangGraph）：默认普通对话，明确执行时分派图片/视频/工具专家。SSE 流式，
    透出节点流转({trace})供前端展示协作过程。生成同样跑在 agent_runner 后台线程里。"""
    from app.services import agent_runner
    from app.services.agent_request_context import from_payload

    if not req.message.strip() and not req.images and not req.image_mask:
        raise HTTPException(status_code=400, detail="内容为空")

    context = from_payload(req.model_dump())
    try:
        q = agent_runner.run_multi_stream(context)
    except agent_runner.RunAlreadyActive as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return sse_response(lambda: agent_runner.drain(q))


@router.post("/trace/replay")
def replay_trace(req: TraceReplayRequest) -> dict[str, object]:
    """离线校验既有 Trace；不调用模型，不写会话、资产或数据库。"""
    from app.services import trace_replay

    if not req.repo_id.strip():
        raise HTTPException(status_code=400, detail="repo_id 不能为空")
    return trace_replay.replay_recent(
        req.repo_id.strip(), turn_id=req.turn_id.strip(), limit=req.limit,
    )


class RegenerateImageRequest(BaseModel):
    thread_id: str
    repo_id: str
    prompt: str
    images: list[str] = []
    image_mask: ImageMaskRequest | None = None
    gen_base_url: str
    gen_api_key: str
    gen_model: str
    gen_proxy_url: str = ""
    size: str = "1024x1024"
    image_quality: Literal["auto", "low", "medium", "high"] = "high"
    output_dir: str = ""
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_model: str = "embedding-3"
    embed_proxy_url: str = ""


@router.post("/regenerate-image")
def regenerate_image(req: RegenerateImageRequest) -> dict[str, object]:
    """按结果消息保存的不可变参数直接重放，不经过 Supervisor 或提示词改写。"""
    from app.services import generation_store, image_gen

    regeneration = {
        "kind": "ai-image", "prompt": req.prompt, "images": list(req.images),
        **({"imageMask": req.image_mask.model_dump()} if req.image_mask else {}),
        "size": req.size, "quality": req.image_quality,
        "model": {"baseUrl": req.gen_base_url, "modelName": req.gen_model},
    }
    try:
        if req.images or req.image_mask:
            images = list(req.images)
            if req.image_mask and req.image_mask.image not in images:
                images.insert(0, req.image_mask.image)
            kwargs = {"size": req.size, "quality": req.image_quality}
            if req.image_mask:
                kwargs["mask"] = req.image_mask.mask
            if req.gen_proxy_url:
                kwargs["proxy"] = req.gen_proxy_url
            url = image_gen.generate_with_images(
                req.gen_base_url, req.gen_api_key, req.gen_model,
                req.prompt, images, **kwargs,
            )
        else:
            proxy_kw = {"proxy": req.gen_proxy_url} if req.gen_proxy_url else {}
            url = image_gen.generate(
                req.gen_base_url, req.gen_api_key, req.gen_model,
                req.prompt, size=req.size, quality=req.image_quality, **proxy_kw,
            )
        persist_kw = {"embed_proxy": req.embed_proxy_url} if req.embed_proxy_url else {}
        rec = generation_store.persist_image(
            req.thread_id, req.repo_id, req.prompt, url, req.output_dir,
            req.embed_base_url, req.embed_api_key, req.embed_model,
            regeneration, **persist_kw,
        )
        return {"ok": True, **rec}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"重新生图失败：{exc}") from exc


@router.get("/image-agent/running")
def image_agent_running(thread_id: str = "home") -> dict[str, object]:
    """该 thread 是否有后台生成任务在跑。前端切回/刷新时据此轮询快照等落盘。"""
    from app.services import agent_runner
    return {"running": agent_runner.is_running(thread_id)}


@router.get("/image-agent/running-threads")
def image_agent_running_threads() -> dict[str, object]:
    """当前有后台生成任务在跑的所有 thread（仓库 id），供后台活动面板列出。"""
    from app.services import thread_admission
    return {"threads": thread_admission.active_threads()}


class ChatQueueEnqueueRequest(MultiAgentRequest):
    pass


@router.post("/chat-queue/enqueue")
def chat_queue_enqueue(req: ChatQueueEnqueueRequest) -> dict[str, object]:
    """把一条忙时排队消息落后端队列；worker 在前一条结束后串行认领执行（刷新/重开仍继续）。"""
    from app.services import chat_agent_queue
    payload = req.model_dump()
    if req.image_mask:
        payload["image_mask"] = req.image_mask.model_dump()
    task = chat_agent_queue.enqueue(payload)
    return {"task": task}


@router.get("/chat-queue")
def chat_queue_list(thread_id: str = "") -> dict[str, object]:
    """列出某仓库（或全部）的排队消息，供前端持久化队列条与后台面板显示。"""
    from app.services import chat_agent_queue
    return {"tasks": chat_agent_queue.list_tasks(thread_id)}


class ChatQueueCancelRequest(BaseModel):
    task_id: str


@router.post("/chat-queue/cancel")
def chat_queue_cancel(req: ChatQueueCancelRequest) -> dict[str, object]:
    """取消一条尚未发出的排队消息。"""
    from app.services import chat_agent_queue
    task = chat_agent_queue.cancel(req.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="排队消息不存在")
    return {"task": task}


class CancelRequest(BaseModel):
    thread_id: str = "home"


class IllustrationFailureRequest(BaseModel):
    thread_id: str
    repo_id: str = ""
    message_id: str
    slot_id: str
    stage: str
    error: str
    prompt_id: str = ""


class IllustrationSubmissionRequest(BaseModel):
    thread_id: str
    repo_id: str = ""
    turn_id: str = ""
    message_id: str
    slot_id: str
    template_id: str
    prompt_id: str
    prompt: str
    prompt_profile: str = ""
    lora_name: str = ""
    lora_weight: float | None = None
    lora_mode: str = "single"
    lora_names: list[str] = Field(default_factory=list)
    latent_width: int | None = Field(default=None, ge=1, le=16384)
    latent_height: int | None = Field(default=None, ge=1, le=16384)
    value_keys: list[str] = Field(default_factory=list)
    source: Literal["automatic", "manual"] = "automatic"


class AudioSubmissionRequest(BaseModel):
    thread_id: str
    repo_id: str = ""
    turn_id: str = ""
    message_id: str
    slot_id: str
    speaker: str
    text: str
    voice_ref: str = ""          # 参考音轨本地路径（排查音色来源）
    template_id: str
    prompt_id: str
    emotion: dict[str, float] = Field(default_factory=dict)  # 8 维情感向量
    value_keys: list[str] = Field(default_factory=list)
    source: Literal["automatic", "manual"] = "automatic"


class IllustrationClaimRequest(BaseModel):
    thread_id: str
    message_id: str
    slot_id: str


class EnsureAudioSlotRequest(BaseModel):
    thread_id: str
    message_id: str
    slot_id: str
    speaker: str = ""
    seq: int | None = None
    total: int | None = None


@router.post("/image-agent/ensure-audio-slot")
def ensure_audio_slot(req: EnsureAudioSlotRequest) -> dict[str, bool]:
    """音频对白槽补写快照（追加式，保留已有图片/视频槽）。前端提交配音前调用，
    保证 finalize 时 resolve_media_slot 能找到槽位并原位回填音频 URL。"""
    from app.services import generation_store
    generation_store.persist_audio_slot(
        thread_id=req.thread_id, message_id=req.message_id, slot_id=req.slot_id,
        speaker=req.speaker or None, seq=req.seq, total=req.total,
    )
    return {"ok": True}


@router.post("/image-agent/illustration-claim")
def illustration_claim(req: IllustrationClaimRequest) -> dict[str, bool]:
    """ComfyUI 提交前认领权威插画槽；重复事件不得产生第二个任务。"""
    from app.services import generation_store
    claimed = generation_store.claim_illustration_submission(
        thread_id=req.thread_id, message_id=req.message_id, slot_id=req.slot_id,
    )
    return {"ok": True, "claimed": claimed}


@router.post("/image-agent/illustration-submission")
def illustration_submission(req: IllustrationSubmissionRequest) -> dict[str, bool]:
    """记录前端最终提交给 ComfyUI 的实际参数；追踪失败不影响生图。"""
    from app.services import generation_store, run_trace
    slot_bound = generation_store.persist_illustration_submission(
        thread_id=req.thread_id,
        message_id=req.message_id,
        slot_id=req.slot_id,
        prompt_id=req.prompt_id,
    )
    ctx = {
        "thread_id": req.thread_id,
        "repo_id": req.repo_id or req.thread_id,
        "turn_id": req.turn_id,
    }
    run_trace.emit(
        ctx,
        "illustration.submitted",
        message_id=req.message_id,
        slot_id=req.slot_id,
        template_id=req.template_id,
        prompt_id=req.prompt_id,
        prompt=req.prompt,
        prompt_chars=len(req.prompt),
        prompt_profile=req.prompt_profile,
        lora_name=req.lora_name,
        lora_weight=req.lora_weight,
        lora_mode=req.lora_mode,
        lora_names=req.lora_names,
        latent={"width": req.latent_width, "height": req.latent_height},
        value_keys=req.value_keys,
        source=req.source,
        slot_bound=slot_bound,
    )
    return {"ok": True}


@router.post("/image-agent/audio-submission")
def audio_submission(req: AudioSubmissionRequest) -> dict[str, bool]:
    """记录前端最终提交给 ComfyUI 的音频配音参数（台词/音色/情感），追踪失败不影响配音。"""
    from app.services import run_trace
    ctx = {
        "thread_id": req.thread_id,
        "repo_id": req.repo_id or req.thread_id,
        "turn_id": req.turn_id,
    }
    run_trace.emit(
        ctx,
        "audio.submitted",
        message_id=req.message_id,
        slot_id=req.slot_id,
        speaker=req.speaker,
        text=req.text,
        text_chars=len(req.text),
        voice_ref=req.voice_ref,
        template_id=req.template_id,
        prompt_id=req.prompt_id,
        emotion=req.emotion,
        value_keys=req.value_keys,
        source=req.source,
    )
    return {"ok": True}


@router.post("/image-agent/illustration-failure")
def illustration_failure(req: IllustrationFailureRequest) -> dict[str, object]:
    """记录自动插画失败并移除持久化槽，不向对话正文追加错误。"""
    from app.services import generation_store
    removed = generation_store.persist_illustration_failure(
        thread_id=req.thread_id,
        repo_id=req.repo_id or req.thread_id,
        message_id=req.message_id,
        slot_id=req.slot_id,
        stage=req.stage,
        error=req.error,
        prompt_id=req.prompt_id,
    )
    return {"ok": True, "removed": removed}


@router.post("/image-agent/cancel")
def image_agent_cancel(req: CancelRequest) -> dict[str, object]:
    """打断该 thread 的后台生成：协作式取消，半成品文本落盘并补进记忆供下一轮续写=合并。"""
    from app.services import agent_runner
    running = agent_runner.cancel(req.thread_id)
    return {"ok": True, "running": running}
