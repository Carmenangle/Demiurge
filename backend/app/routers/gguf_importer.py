"""GGUF 模型导入路由：扫描 / 解析 / 硬件适配 / 导入 Ollama / 注册 provider。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from pydantic import BaseModel, Field

from app.services import gguf_importer

router = APIRouter()


class ScanDirReq(BaseModel):
    directory: str = Field(..., min_length=1, max_length=1024)


class ImportReq(BaseModel):
    gguf_path: str = Field(..., min_length=1, max_length=2048)
    model_name: str = Field(default="", max_length=128)
    mmproj_path: str = Field(default="", max_length=2048)
    quantize: str = Field(default="", max_length=32)
    register_provider: bool = True


class FitReq(BaseModel):
    gguf_path: str = Field(..., min_length=1, max_length=2048)


@router.post("/scan")
def scan_gguf(req: ScanDirReq) -> dict:
    """扫描目录，返回 GGUF 文件、主模型与 mmproj 列表。"""
    try:
        return gguf_importer.scan_gguf_dir(req.directory)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"扫描失败：{exc}") from exc


@router.post("/parse")
def parse_gguf(req: FitReq) -> dict:
    """解析单个 GGUF 文件元数据 + 硬件适配建议。"""
    p = Path(req.gguf_path).expanduser().resolve()
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"文件不存在：{req.gguf_path}")
    meta = gguf_importer.parse_gguf(p)
    if meta is None:
        raise HTTPException(status_code=422, detail=f"无法解析 GGUF 文件：{p.name}")
    return {
        "meta": gguf_importer._meta_dict(meta),
        "fit": gguf_importer.fit_hardware(meta),
        "suggested_name": gguf_importer._suggest_model_name(meta),
    }


@router.post("/import")
def import_gguf(req: ImportReq) -> dict:
    """导入 GGUF 到 Ollama，可选注册 provider。阻塞执行（大模型可能耗时）。"""
    try:
        result = gguf_importer.import_gguf_flow(
            req.gguf_path,
            model_name=req.model_name,
            mmproj_path=req.mmproj_path,
            quantize=req.quantize,
            register=req.register_provider,
        )
        if not result["ok"]:
            raise HTTPException(status_code=422, detail=result["message"])
        return result
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"导入失败：{exc}") from exc


@router.get("/status")
def ollama_status() -> dict:
    """Ollama 运行状态与已安装模型。"""
    models = gguf_importer.ollama_list_models()
    return {
        "running": gguf_importer.is_ollama_running(),
        "models": models,
        "count": len(models),
    }
