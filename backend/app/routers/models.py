"""模型下载路由：浏览(CivitAI/HF) + 从 HuggingFace / Civitai 下载到 ComfyUI models 目录。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import model_downloader, model_browser, workflow_downloader
from app.services.model_browser import BrowseError

router = APIRouter()


def _browse_http_error(what: str, e: Exception) -> HTTPException:
    """把浏览异常映射成带准确提示的 HTTPException。
    区分上游 5xx（对方临时不可用，别误导为代理问题）与连接层（代理没开/不通）。"""
    if isinstance(e, BrowseError):
        if e.kind == "upstream":
            return HTTPException(status_code=503, detail=f"{what}：{e}")
        if e.kind == "network":
            return HTTPException(status_code=502, detail=f"{what}：{e}")
        return HTTPException(status_code=502, detail=f"{what}：{e}")
    return HTTPException(status_code=502, detail=f"{what}：{e}（确认代理已开启）")


class DownloadRequest(BaseModel):
    url: str
    model_type: str = "checkpoint"     # checkpoint|lora|vae|controlnet|embedding|upscale|clip
    models_dir: str = ""               # ComfyUI models 目录（前端从设置透传）
    hf_token: str = ""
    civitai_token: str = ""
    name: str = ""                     # 展示名（各 tab 传模型名，供下载面板显示）
    proxy: str = ""                    # 外网代理（前端从设置透传，空=直连）


@router.post("/download")
def download(req: DownloadRequest) -> dict[str, object]:
    """启动后台下载，返回 task_id；用 /status 轮询进度。"""
    try:
        task_id = model_downloader.start_download(
            req.url, req.models_dir, req.model_type, req.hf_token, req.civitai_token,
            req.name, req.proxy,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "task_id": task_id}


class WorkflowDownloadRequest(BaseModel):
    url: str
    workflow_dir: str = ""             # 默认工作流文件夹（前端从设置透传）
    name: str = ""
    civitai_token: str = ""
    proxy: str = ""


@router.post("/download/workflow")
def download_workflow(req: WorkflowDownloadRequest) -> dict[str, object]:
    """下载工作流模板到默认工作流文件夹（.json 直落，.zip 抽 json）。进度走共享下载面板。"""
    try:
        task_id = workflow_downloader.start_download(
            req.url, req.workflow_dir, req.name, req.civitai_token, req.proxy,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "task_id": task_id}


@router.get("/download/status")
def status(task_id: str) -> dict[str, object]:
    """查询下载进度：{status, downloaded, total, filename, error}。"""
    return model_downloader.get_status(task_id)


@router.get("/download/tasks")
def download_tasks() -> dict[str, object]:
    """全部下载任务（跨 tab 共享的下载面板轮询）。"""
    return {"items": model_downloader.list_tasks()}


@router.get("/types")
def types() -> dict[str, object]:
    """可选模型类型及其落盘子目录。"""
    return {"items": model_downloader.TYPE_DIRS}


class InfoRequest(BaseModel):
    url: str
    hf_token: str = ""
    civitai_token: str = ""
    proxy: str = ""                    # 外网代理（前端从设置透传，空=直连）


@router.post("/info")
def info(req: InfoRequest) -> dict[str, object]:
    """拉取模型预览图 + 介绍（下载前预览）。"""
    try:
        return model_downloader.fetch_info(req.url, req.hf_token, req.civitai_token, req.proxy)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"获取信息失败：{e}")


class CivitaiBrowseRequest(BaseModel):
    proxy: str = ""                    # 访问外网的代理（前端从设置透传）
    query: str = ""
    types: str = ""                    # Checkpoint/LORA/VAE/Controlnet/TextualInversion/Upscaler
    sort: str = "Highest Rated"        # Highest Rated / Most Downloaded / Newest
    period: str = "AllTime"            # AllTime / Month / Week / Day
    base_models: str = ""              # SDXL 1.0 / Pony / Flux.1 D …
    nsfw: bool = False
    cursor: str = ""
    limit: int = 24
    civitai_token: str = ""


@router.post("/browse/civitai")
def browse_civitai(req: CivitaiBrowseRequest) -> dict[str, object]:
    """浏览/搜索 CivitAI 模型（走代理）。返回 {items, next_cursor}。"""
    try:
        return model_browser.civitai_browse(
            proxy=req.proxy, query=req.query, types=req.types, sort=req.sort,
            period=req.period, base_models=req.base_models, nsfw=req.nsfw,
            cursor=req.cursor, limit=req.limit, token=req.civitai_token,
        )
    except Exception as e:
        raise _browse_http_error("CivitAI 浏览失败", e)


class CivArchiveSearchRequest(BaseModel):
    proxy: str = ""
    query: str = ""
    type: str = ""                     # Checkpoint/LORA/VAE/TextualInversion…
    page: int = 1
    nsfw: bool = False


@router.post("/browse/civarchive")
def browse_civarchive(req: CivArchiveSearchRequest) -> dict[str, object]:
    """搜索 CivArchive（跨平台归档，走代理）。返回 {items, total}。"""
    try:
        return model_browser.civarchive_search(
            proxy=req.proxy, query=req.query, type=req.type, page=req.page, nsfw=req.nsfw,
        )
    except Exception as e:
        raise _browse_http_error("CivArchive 搜索失败", e)


class CivArchiveSourcesRequest(BaseModel):
    proxy: str = ""
    sha256: str


@router.post("/browse/civarchive/sources")
def civarchive_sources(req: CivArchiveSourcesRequest) -> dict[str, object]:
    """按 sha256 拿模型的全部下载源（civitai/huggingface/镜像）。"""
    try:
        return model_browser.civarchive_sources(req.proxy, req.sha256)
    except Exception as e:
        raise _browse_http_error("获取下载源失败", e)
