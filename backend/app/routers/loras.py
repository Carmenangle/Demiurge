"""LoRA 触发词库：扫 loras 目录同步 + 增删改查。

触发词主存 SQLite（编排注入要按 lora_name 精确查），向量库只作检索镜像。
业务逻辑在 services/lora_index，本层只做入参校验与转发。
"""
from fastapi import APIRouter, HTTPException

from app.routers.ai_common import EmbedModelReq
from app.services import lora_index

router = APIRouter()


@router.get("/")
def list_loras() -> dict[str, object]:
    """列出全部已记录的 LoRA 触发词条目（缺失文件排最后）。"""
    return {"items": lora_index.list_items()}


class SyncRequest(EmbedModelReq):
    models_dir: str = ""       # ComfyUI 的 models 目录；前端留空时已回退成 comfyuiPath/models
    full: bool = False         # true=连手填条目一并重提（用户显式要求重建）


@router.post("/sync")
def sync_loras(req: SyncRequest) -> dict[str, object]:
    """启动后台同步：扫 loras 目录、逐个提触发词。手填过的条目默认不覆盖。"""
    if not req.models_dir.strip():
        raise HTTPException(status_code=400, detail="models 目录为空，请先在设置里填 ComfyUI 路径")
    return lora_index.start_sync(req.models_dir, req.embed_cfg(), req.full)


@router.get("/sync-progress")
def sync_progress() -> dict[str, object]:
    """同步进度快照，供前端轮询。"""
    return lora_index.sync_progress()


class SaveRequest(EmbedModelReq):
    lora_name: str = ""
    triggers: list[str] = []
    note: str = ""


@router.put("/item")
def save_item(req: SaveRequest) -> dict[str, object]:
    """新增或修改一条触发词。保存即标记为手填，此后同步不再覆盖。"""
    if not req.lora_name.strip():
        raise HTTPException(status_code=400, detail="lora_name 为空")
    return lora_index.save_item(req.lora_name.strip(), req.triggers,
                                req.note, req.embed_cfg())


class DeleteRequest(EmbedModelReq):
    lora_name: str = ""


@router.post("/delete")
def delete_item(req: DeleteRequest) -> dict[str, object]:
    """删除一条触发词记录（不动磁盘上的模型文件）。

    用 POST 而非 DELETE：嵌入配置要随请求体传（对齐 rag.py 的全 POST 约定），
    DELETE 带 body 在部分代理上会被剥掉。
    """
    if not req.lora_name.strip():
        raise HTTPException(status_code=400, detail="lora_name 为空")
    lora_index.delete_item(req.lora_name.strip(), req.embed_cfg())
    return {"ok": True}
