"""对话端点：多轮对话流(LangGraph+RAG)、历史/清空/追加、消息流快照存取、AI 客服。"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.routers.ai_common import EmbedModelReq, build_chat_model
from app.services.sse import sse_response

router = APIRouter()


class ChatRequest(EmbedModelReq):
    thread_id: str = "home"        # 对话线 = 仓库 id（首页用 "home"）
    message: str                   # 本轮用户输入（历史由后端 checkpoint 载入）
    images: list[str] = []         # 本轮随文附带的图片（data URI 或可访问 URL），送 VLM
    system: str = ""               # 可选 system 提示词
    temperature: float = 0.7
    use_rag: bool = True           # 是否检索本仓库知识库拼进上下文


_CHAT_SYSTEM = (
    "你是本地 AI 绘画助手，简洁、专业地回答用户关于绘画、提示词、工作流的问题。\n"
    "当用户明确想用专业工作流时，在回复末尾用如下标记给出可点击的指令建议（前端渲染成按钮，用户点击才执行）：\n"
    "  [[cmd:/w 模板名]]  选择工作流模板\n"
    "  [[cmd:/s]]          启动已选定工作流出图\n"
    "规则：仅在用户意图明确时给标记；一条建议一个标记，可给多个；标记须单独成行、原样输出，不要加引号或代码块。"
    "日常生图/反推交给智能体即可，无需指令。若只是普通问答，不要输出任何标记。"
)


@router.post("/chat")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    """多轮对话流式接口（LangGraph + SqliteSaver 持久记忆 + 仓库 RAG）。

    历史按 thread_id 自动从 checkpoint 载入并续写落盘；前端只传本轮输入。
    """
    from app.services import chat_memory, rag_store

    if not req.message.strip() and not req.images:
        raise HTTPException(status_code=400, detail="对话内容为空")
    llm = build_chat_model(req.base_url, req.api_key, req.model,
                           temperature=req.temperature, streaming=True)

    # 检索本仓库知识库，把相关片段拼进 system
    system_text = req.system or _CHAT_SYSTEM
    if req.use_rag:
        hits = rag_store.retrieve(req.thread_id, req.embed_cfg(), req.message, k=4)
        if hits:
            ctx = "\n\n".join(f"- {h}" for h in hits)
            system_text += f"\n\n以下是本仓库的相关历史与参考资料，可据此作答：\n{ctx}"

    return sse_response(lambda: (
        {"delta": delta}
        for delta in chat_memory.stream_chat(
            llm, req.thread_id, system_text, req.message, req.images or None
        )
    ))

class HistoryRequest(BaseModel):
    thread_id: str = "home"


@router.get("/chat/history")
def chat_history(thread_id: str = "home") -> dict[str, object]:
    """取某仓库已落盘的对话历史（刷新后回填）。"""
    from app.services import chat_memory
    return {"items": chat_memory.get_history(thread_id)}


@router.post("/chat/clear")
def chat_clear(req: HistoryRequest) -> dict[str, object]:
    """清空某仓库对话线。"""
    from app.services import chat_maintenance
    try:
        return chat_maintenance.clear(req.thread_id)
    except chat_maintenance.MaintenanceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except chat_maintenance.MaintenanceFailed as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class ClearCacheRequest(BaseModel):
    thread_id: str = "home"
    output_dir: str = ""        # 输出图片根路径（删 <repo>/reference/ 子夹用）


@router.post("/chat/clear-cache")
def chat_clear_cache(req: ClearCacheRequest) -> dict[str, object]:
    """清对话、快照和 reference；RAG 与生成资产不属于该维护操作。"""
    from app.services import chat_maintenance
    try:
        result = chat_maintenance.clear_cache(req.thread_id, req.output_dir)
        return {"ok": True, "removed": result.removed}
    except chat_maintenance.MaintenanceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except chat_maintenance.MaintenanceFailed as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class CompactRequest(EmbedModelReq):
    thread_id: str = "home"


@router.post("/chat/compact")
def chat_compact(req: CompactRequest) -> dict[str, object]:
    """压缩对话与快照为同一摘要；generation/RAG 仅作为只读摘要输入。"""
    from app.services import chat_maintenance
    llm = build_chat_model(req.base_url, req.api_key, req.model, temperature=0.3)
    try:
        return chat_maintenance.compact(req.thread_id, llm, req.embed_cfg())
    except chat_maintenance.NothingToCompact as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except chat_maintenance.MaintenanceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except chat_maintenance.MaintenanceFailed as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class AppendRequest(BaseModel):
    thread_id: str = "home"
    role: str = "assistant"        # user | assistant
    text: str = ""
    images: list[str] = []


@router.post("/chat/append")
def chat_append(req: AppendRequest) -> dict[str, object]:
    """把已生成的有价值消息（提示词/图片）直接落盘到 thread，刷新后保留。

    不调用模型，仅写 checkpoint。用于 /p、/s 等工作流产物持久化。
    """
    from app.services import chat_memory
    if not req.text and not req.images:
        return {"ok": False}
    chat_memory.append_message(req.thread_id, req.role, req.text, req.images or None)
    return {"ok": True}


class SnapshotSaveRequest(BaseModel):
    thread_id: str = "home"
    messages: list = []           # 前端完整消息流（slim 版，已去大字段）


@router.post("/chat/snapshot/save")
def chat_snapshot_save(req: SnapshotSaveRequest) -> dict[str, object]:
    """落盘前端完整消息流快照，作为可靠真源（关浏览器/清端口不丢）。"""
    from app.services import chat_snapshot
    chat_snapshot.save(req.thread_id, req.messages)
    return {"ok": True}


@router.get("/chat/snapshot")
def chat_snapshot_load(thread_id: str = "home") -> dict[str, object]:
    """读取某 thread 的消息流快照，刷新/换设备回填前端。"""
    from app.services import chat_snapshot
    return {"items": chat_snapshot.load(thread_id)}


class SupportRequest(EmbedModelReq):
    message: str
    repo_id: str = "home"          # 客服检索归属仓库（系统库 + 该仓库库）


_SUPPORT_SYSTEM = (
    "你是这个本地 AI 绘画工具的客服助手。根据下面的知识库内容，简洁、准确地回答用户"
    "关于工具使用、指令、工作流的问题。知识库没有的内容如实说明，不要编造。"
)


@router.post("/support")
def support_stream(req: SupportRequest) -> StreamingResponse:
    """右下角 AI 客服：检索全局知识库（系统指令+资料+生成历史）→ 流式回答。单轮，不落盘。"""
    from app.services import rag_store

    if not req.message.strip():
        raise HTTPException(status_code=400, detail="问题为空")
    llm = build_chat_model(req.base_url, req.api_key, req.model, streaming=True)
    system_text = _SUPPORT_SYSTEM
    hits = rag_store.retrieve(req.repo_id, req.embed_cfg(), req.message, k=5)
    if hits:
        ctx = "\n\n".join(f"- {h}" for h in hits)
        system_text += f"\n\n【知识库】\n{ctx}"

    from app.services import llm as _llm

    def events():
        for chunk in llm.stream([("system", system_text), ("user", req.message)]):
            delta = _llm.flatten_content(chunk.content)
            if delta:
                yield {"delta": delta}

    return sse_response(events)

