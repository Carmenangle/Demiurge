"""图像智能体端点：SSE 流式生成 + 后台运行状态 + 打断。
生成跑在后台线程（agent_runner），与 HTTP 连接解耦。
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Literal

from app.routers.ai_common import EmbedModelReq
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
    style: str = ""                    # 用户手动选的提示词风格 sd/gpt/banana/""(自动)
    style_template: str = ""           # 自定义风格存档的整段内容（非空时优先于 style）
    agent_id: str = ""                 # 多 Agent：选中的 Agent 预设 id（空=内置默认行为）
    approval_id: str = ""              # 历史提示词审批卡 id
    approval_action: str = ""          # submit / change / cancel
    edited_prompt: str = ""            # change 时用户在卡片内修改后的提示词
    forced_route: str = ""              # 主管选择卡点击后的显式路由
    user_message_id: str = ""            # 选择卡关联的原用户消息 id
    context_max_tokens: int = Field(default=20_000, ge=4_000, le=200_000)


# 单 agent 生成入口（POST /ai/image-agent → agent_runner.run_stream）已下线。
# 其 ReAct 大脑降级为多 Agent 的 tool_agent 专家节点（承接 MCP/工具串联），自由文本一律走 /multi-agent。
# 下方 /image-agent/running 与 /image-agent/cancel 保留：后台化的共用机制，多 Agent 同用同一 thread 计数/取消信号。


class MultiAgentRequest(ImageAgentRequest):
    route_model: str = ""   # supervisor 判分派用的（快）模型，空则用主对话模型


@router.post("/multi-agent")
def multi_agent(req: MultiAgentRequest) -> StreamingResponse:
    """Supervisor 多 Agent（LangGraph）：默认普通对话，明确执行时分派图片/视频/工具专家。SSE 流式，
    透出节点流转({trace})供前端展示协作过程。生成同样跑在 agent_runner 后台线程里。"""
    from app.services import agent_runner
    from app.services.agent_contracts import ModelConfig, RunContext

    if not req.message.strip() and not req.images and not req.image_mask:
        raise HTTPException(status_code=400, detail="内容为空")

    context = RunContext(
        thread_id=req.thread_id,
        message=req.message,
        images=req.images or [],
        image_mask=req.image_mask.model_dump() if req.image_mask else None,
        chat=ModelConfig(req.base_url, req.api_key, req.model),
        generation=ModelConfig(req.gen_base_url, req.gen_api_key, req.gen_model),
        video=ModelConfig(req.video_base_url, req.video_api_key, req.video_model),
        embedding=ModelConfig(req.embed_base_url, req.embed_api_key, req.embed_model),
        size=req.size,
        image_quality=req.image_quality,
        output_dir=req.output_dir,
        repo_id=req.repo_id or req.thread_id,
        message_id=req.message_id,
        proxy_url=req.proxy_url,
        route_model=req.route_model,
        style_template=req.style_template,
        agent_id=req.agent_id,
        approval_id=req.approval_id,
        approval_action=req.approval_action,
        edited_prompt=req.edited_prompt,
        forced_route=req.forced_route,
        user_message_id=req.user_message_id,
        context_max_tokens=req.context_max_tokens,
    )
    try:
        q = agent_runner.run_multi_stream(context)
    except agent_runner.RunAlreadyActive as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return sse_response(lambda: agent_runner.drain(q))


class RegenerateImageRequest(BaseModel):
    thread_id: str
    repo_id: str
    prompt: str
    images: list[str] = []
    image_mask: ImageMaskRequest | None = None
    gen_base_url: str
    gen_api_key: str
    gen_model: str
    size: str = "1024x1024"
    image_quality: Literal["auto", "low", "medium", "high"] = "high"
    output_dir: str = ""
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_model: str = "embedding-3"


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
            url = image_gen.generate_with_images(
                req.gen_base_url, req.gen_api_key, req.gen_model,
                req.prompt, images, **kwargs,
            )
        else:
            url = image_gen.generate(
                req.gen_base_url, req.gen_api_key, req.gen_model,
                req.prompt, size=req.size, quality=req.image_quality,
            )
        rec = generation_store.persist_image(
            req.thread_id, req.repo_id, req.prompt, url, req.output_dir,
            req.embed_base_url, req.embed_api_key, req.embed_model,
            regeneration,
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


@router.post("/image-agent/cancel")
def image_agent_cancel(req: CancelRequest) -> dict[str, object]:
    """打断该 thread 的后台生成：协作式取消，半成品文本落盘并补进记忆供下一轮续写=合并。"""
    from app.services import agent_runner
    running = agent_runner.cancel(req.thread_id)
    return {"ok": True, "running": running}
