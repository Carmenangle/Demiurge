"""AI 搭工作流端点：同步节点知识库 + 按需求自动搭建工作流。

搭建流程（用户「必接口优先」思路，AI 在 workflow_builder 的校验闭环内迭代）：
  需求 → 检索相关节点包(node_index) → 喂精简节点清单给对话模型 →
  AI 生成 API 格式 graph → validate_graph 校验 → 有错回喂 AI 重连(最多 N 次) →
  合法则存到 workflowDir。
节点库同步是独立端点，供前端「同步节点库」按钮与首次使用调用。
业务编排（检索→拼 prompt→调模型→校验重试）已下沉到 services/workflow_builder；
本层只做 HTTP 适配：收参 → 调服务 → 把 ValueError/ComfyError 包成 HTTPException。
"""
from typing import Literal

from fastapi import APIRouter, HTTPException

from pydantic import BaseModel

from app.config import COMFYUI_BASE_URL
from app.routers.ai_common import EmbedModelReq, chat
from app.services import (
    node_index, workflow_builder, skeleton_store, build_session_store, workflow_build_tasks,
)
from app.services.comfyui_client import ComfyError

router = APIRouter()


def _build_chat(*args, **kwargs):
    """搭建层自行控制重试轮次，传输层每轮只发起一次上游请求。"""
    kwargs["retries"] = 1
    return chat(*args, **kwargs)


class SyncNodesRequest(EmbedModelReq):
    comfy_url: str = COMFYUI_BASE_URL
    full: bool = False                 # True 全量重建，False 增量


@router.post("/nodes/sync")
def sync_nodes(req: SyncNodesRequest) -> dict:
    """启动后台同步：扫描 ComfyUI 已装节点，按包逐个入库。ComfyUI 未运行报 502。
    立即返回 {total_packs}，进度经 /nodes/sync-progress 轮询。"""
    try:
        return node_index.start_sync(req.comfy_url, req.embed_cfg(), full=req.full)
    except ComfyError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@router.get("/nodes/sync-progress")
def sync_progress() -> dict:
    """同步进度快照：{running, done, total, current, synced, skipped, error, finished}。"""
    return node_index.sync_progress()


class NodeStatsRequest(EmbedModelReq):
    pass


@router.post("/nodes/stats")
def node_stats(req: NodeStatsRequest) -> dict:
    """节点知识库现状：包数 + 节点数。空库提示先同步。"""
    return node_index.stats(req.embed_cfg())


@router.post("/nodes/packs")
def node_packs(req: NodeStatsRequest) -> dict:
    """列出全部节点包（管理页展示，含节点数/来源）。"""
    return {"packs": node_index.list_packs(req.embed_cfg())}


class PackIdReq(EmbedModelReq):
    pack_id: str = ""


@router.post("/nodes/pack")
def node_pack(req: PackIdReq) -> dict:
    """读单个包完整内容（含用途正文，供查看/编辑）。"""
    p = node_index.get_pack(req.embed_cfg(), req.pack_id)
    if p is None:
        raise HTTPException(status_code=404, detail="节点包不存在")
    return p


class UpdatePackReq(EmbedModelReq):
    pack_id: str = ""
    content: str = ""


@router.post("/nodes/pack/update")
def update_node_pack(req: UpdatePackReq) -> dict:
    """人工修订某包的用途正文并重嵌入。"""
    ok = node_index.update_pack_content(req.embed_cfg(), req.pack_id, req.content)
    if not ok:
        raise HTTPException(status_code=404, detail="节点包不存在")
    return {"ok": True}


# —— 骨架底座：AI 搭工作流的正确起点 ——

class SkeletonListReq(BaseModel):
    workflow_dir: str = ""


@router.post("/skeletons")
def skeletons(req: SkeletonListReq) -> dict:
    """列出骨架候选：内置精简骨架 + 用户工作流文件夹里的 .json。"""
    return {"skeletons": skeleton_store.list_skeletons(req.workflow_dir)}


class SkeletonGraphReq(BaseModel):
    skeleton_id: str = ""
    workflow_dir: str = ""


@router.post("/skeleton/graph")
def skeleton_graph(req: SkeletonGraphReq) -> dict:
    """按 id 取骨架 graph（load 进画布用）。内置直接返回，文件只读不改。"""
    g = skeleton_store.get_skeleton_graph(req.skeleton_id, req.workflow_dir)
    if g is None:
        raise HTTPException(status_code=404, detail="骨架不存在")
    return {"graph": g}


class BuildRequest(EmbedModelReq):
    need: str = ""                     # 自然语言需求，如"文生图基础流"
    comfy_url: str = COMFYUI_BASE_URL
    workflow_dir: str = ""             # 落盘目录（settings.workflowDir）
    name: str = ""                     # 工作流文件名（空则用 need 派生）
    max_retries: int = 4               # 校验失败回喂 AI 重连次数（widget 候选纠错多留 1 轮收敛）
    current_graph: dict = {}           # 当前右侧画布(API格式)，非空=在其基础上增量改
    save: bool = True                  # 是否落盘到 workflow_dir（多轮迭代中途可传 False 只回图）
    history: list[dict] = []           # 搭建页多轮对话


@router.post("/build")
def build(req: BuildRequest) -> dict:
    """按需求自动搭工作流：检索节点→AI 生成→校验重试→落盘。返回 {ok, path, graph, errors}。"""
    try:
        return workflow_builder.build_graph(
            _build_chat, base_url=req.base_url, api_key=req.api_key, model=req.model, proxy=req.proxy,
            cfg=req.embed_cfg(), need=req.need, comfy_url=req.comfy_url,
            workflow_dir=req.workflow_dir, name=req.name, max_retries=req.max_retries,
            current_graph=req.current_graph, save=req.save, history=req.history,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ComfyError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


class SaveRequest(EmbedModelReq):
    graph: dict = {}                   # 手改后的画布(API格式)，直接落盘不经 AI
    workflow_dir: str = ""
    name: str = ""


@router.post("/build/save")
def save(req: SaveRequest) -> dict:
    """把前端手改后的画布 graph 直接落盘，复用 save_workflow，不经 AI。返回 {ok, path}。"""
    if not req.graph:
        raise HTTPException(status_code=400, detail="画布为空")
    try:
        path = workflow_builder.save_workflow(req.graph, req.name, req.workflow_dir)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "path": path}


class ModuleRequest(EmbedModelReq):
    need: str = ""                     # 本模块需求，如"加图生图分支"
    comfy_url: str = COMFYUI_BASE_URL
    current_graph: dict = {}           # 当前冻结图（AI 不许改，只在其上加模块）
    max_retries: int = 2               # 增量模式重试：慢中转下每轮 opus 都慢，4 次易累计爆 240s→降 2(首次+1次纠错，配 slim 通常够)
    history: list[dict] = []


@router.post("/build/module")
def build_module(req: ModuleRequest) -> dict:
    """分模块增量搭建：AI 只出新模块+锚点 → 后端 ID 安全合并进当前图 → 校验整图 → 重试。
    不落盘，返回 {ok, graph, errors}（graph 为合并后完整图，前端写回画布）。"""
    try:
        return workflow_builder.build_module(
            _build_chat, base_url=req.base_url, api_key=req.api_key, model=req.model, proxy=req.proxy,
            cfg=req.embed_cfg(), need=req.need, comfy_url=req.comfy_url,
            current_graph=req.current_graph, max_retries=req.max_retries, history=req.history,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ComfyError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


class DirectRequest(EmbedModelReq):
    need: str = ""
    comfy_url: str = COMFYUI_BASE_URL
    current_graph: dict = {}           # 当前画布，非空=在其基础上改并输出完整新图
    history: list[dict] = []


@router.post("/build/direct")
def build_direct(req: DirectRequest) -> dict:
    """精简直连模式：信任强模型(Opus 等)一次到位。**只调 1 次模型**输出完整图，
    不查询重写、不 audit 自修、不整图回喂重试——避免多次串行调用在慢中转上超时。
    校验只做一遍：不通过则如实报错(附错误)，由用户看后自己改或重发，不来回折腾。
    返回 {ok, graph, errors, warnings}。"""
    try:
        return workflow_builder.build_direct(
            _build_chat, base_url=req.base_url, api_key=req.api_key, model=req.model, proxy=req.proxy,
            cfg=req.embed_cfg(), need=req.need, comfy_url=req.comfy_url,
            current_graph=req.current_graph, history=req.history,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ComfyError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


class PlanRequest(EmbedModelReq):
    need: str = ""
    comfy_url: str = COMFYUI_BASE_URL
    current_graph: dict = {}           # 当前画布，非空=在其基础上讨论增量改动
    history: list[dict] = []


@router.post("/build/plan")
def build_plan(req: PlanRequest) -> dict:
    """顾问模式：只产出给人看的中文方案文本，不生成/不改画布。用户看后点『同意执行』再走 build/module。"""
    try:
        return workflow_builder.build_plan(
            _build_chat, base_url=req.base_url, api_key=req.api_key, model=req.model, proxy=req.proxy,
            cfg=req.embed_cfg(), need=req.need, comfy_url=req.comfy_url,
            current_graph=req.current_graph, history=req.history,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ComfyError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


# —— 搭建会话：进度保存 + 多开 ——

@router.get("/build/sessions")
def build_sessions() -> dict:
    """列出全部搭建会话元信息（供会话选择器）。"""
    return {"sessions": build_session_store.list_sessions()}


@router.get("/build/session")
def build_session_get(id: str = "") -> dict:
    """读取单个会话完整内容（msgs + graph），供恢复进度。"""
    s = build_session_store.get_session(id)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return s


class SaveSessionReq(BaseModel):
    id: str = ""                       # 空=新建
    name: str = ""
    msgs: list = []
    graph: dict = {}
    skeleton_id: str = ""


@router.post("/build/session/save")
def build_session_save(req: SaveSessionReq) -> dict:
    """保存/覆盖搭建会话（对话 + 当前画布图）。返回会话元信息（含 id）。"""
    return build_session_store.save_session(req.id, req.name, req.msgs, req.graph, req.skeleton_id)


class DeleteSessionReq(BaseModel):
    id: str = ""


@router.post("/build/session/delete")
def build_session_delete(req: DeleteSessionReq) -> dict:
    """删除搭建会话。"""
    ok = build_session_store.delete_session(req.id)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}


# —— 持久化后台搭建任务 ——

class BuildTaskRequest(EmbedModelReq):
    session_id: str = "draft"
    mode: Literal["direct", "module", "workflow", "plan"] = "direct"
    need: str = ""
    comfy_url: str = COMFYUI_BASE_URL
    workflow_dir: str = ""
    current_graph: dict = {}
    history: list[dict] = []


@router.post("/build/tasks")
def build_task_enqueue(req: BuildTaskRequest) -> dict:
    """创建持久化搭建任务，立即返回任务快照；实际模型调用由后台 worker 执行。"""
    if not req.need.strip():
        raise HTTPException(status_code=400, detail="需求不能为空")
    return workflow_build_tasks.enqueue(req.model_dump())


@router.get("/build/tasks")
def build_task_list(session_id: str = "", limit: int = 100) -> dict:
    return {"tasks": workflow_build_tasks.list_tasks(session_id, limit)}


@router.get("/build/task")
def build_task_get(id: str = "") -> dict:
    task = workflow_build_tasks.get(id)
    if task is None:
        raise HTTPException(status_code=404, detail="搭建任务不存在")
    return task


class BuildTaskActionReq(BaseModel):
    id: str = ""


@router.post("/build/task/cancel")
def build_task_cancel(req: BuildTaskActionReq) -> dict:
    task = workflow_build_tasks.cancel(req.id)
    if task is None:
        raise HTTPException(status_code=404, detail="搭建任务不存在")
    return task

