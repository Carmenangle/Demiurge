"""模型配置的无计费连接测试与本地完整性测试。"""
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import model_probe

router = APIRouter()


class ModelProbeRequest(BaseModel):
    kind: Literal["chat", "image", "video", "embedding", "embedding-local", "reranker-local"]
    base_url: str = ""
    api_key: str = ""
    model_name: str = ""
    model_dir: str = ""


@router.post("/model-probe")
def probe_model(req: ModelProbeRequest) -> dict[str, object]:
    if req.kind == "embedding-local":
        return model_probe.probe_local_embedding(req.model_dir)
    if req.kind == "reranker-local":
        return model_probe.probe_local_reranker(req.model_dir)
    return model_probe.probe_remote(req.kind, req.base_url, req.api_key, req.model_name)
