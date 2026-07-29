from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.config import COMFYUI_BASE_URL
from app.services import (
    comfyui_client, comfy_launcher, generation_store, image_store,
    local_media, workflow_submission,
)
from app.services.url_guard import validate_comfyui_url
from app.services.comfyui_client import ComfyError
from app.services.comfy_launcher import LaunchError

router = APIRouter()

_is_up = comfyui_client.is_up


@router.get("/")
def list_comfyui() -> dict[str, object]:
    return {"items": []}


class StartRequest(BaseModel):
    path: str          # ComfyUI 目录（含 main.py）
    url: str = COMFYUI_BASE_URL
    python_path: str = ""


@router.get("/status")
def status(url: str = COMFYUI_BASE_URL) -> dict[str, object]:
    return {"running": _is_up(url), "managed": comfy_launcher.is_managed()}


class ComfyConfig(BaseModel):
    path: str = ""
    url: str = COMFYUI_BASE_URL
    python_path: str = ""


@router.get("/config")
def get_config() -> ComfyConfig:
    """读 ComfyUI 路径/地址配置（供 start-dev 脚本等读取）。"""
    return ComfyConfig(**comfy_launcher.load_config())


@router.post("/config")
def set_config(cfg: ComfyConfig) -> ComfyConfig:
    """保存 ComfyUI 路径/地址，落盘到 data/comfy_config.json（ps1 脚本据此启动）。"""
    return ComfyConfig(**comfy_launcher.save_config(cfg.path, cfg.url, cfg.python_path))


@router.post("/start")
def start(req: StartRequest) -> dict[str, object]:
    try:
        return comfy_launcher.start(req.path, req.url, req.python_path)
    except LaunchError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@router.post("/stop")
def stop(req: StartRequest) -> dict[str, object]:
    """关闭 ComfyUI（装插件/依赖后需先关）。"""
    return comfy_launcher.stop(req.url)


@router.post("/restart")
def restart(req: StartRequest) -> dict[str, object]:
    """重启 ComfyUI：先关再起（装完插件生效）。需提供 path 以重新拉起。"""
    try:
        return comfy_launcher.restart(req.path, req.url, req.python_path)
    except LaunchError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


class SubmitRequest(BaseModel):
    template_id: str
    values: dict[str, object] = {}   # key = "node_id.field" -> 覆盖值
    prompt: str = ""                 # 可选：自动注入到模板 prompt_node_id 的文本字段（/g 用）
    url: str = COMFYUI_BASE_URL
    client_id: str = ""              # 前端 WebSocket clientId，回传给 ComfyUI 定向推进度


@router.post("/submit")
def submit(req: SubmitRequest) -> dict[str, object]:
    """/s 启动：读模板原始工作流 → 转 API 格式 → 套用用户填的值 → 提交 /prompt。"""
    try:
        return workflow_submission.submit_template(
            req.template_id, req.values, req.prompt, req.url, req.client_id,
        )
    except workflow_submission.WorkflowSubmissionError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


class SubmitGraphRequest(BaseModel):
    workflow: dict[str, object]      # iframe 回传的完整工作流 JSON（含用户改过的 widget 值）
    url: str = COMFYUI_BASE_URL
    client_id: str = ""              # 前端 WebSocket clientId，回传给 ComfyUI 定向推进度


@router.post("/submit_graph")
def submit_graph(req: SubmitGraphRequest) -> dict[str, object]:
    """从锁定画布回传的完整工作流直接转 API 并提交（用户在真实节点里改的值都在里面）。"""
    try:
        return workflow_submission.submit_graph(req.workflow, req.url, req.client_id)
    except workflow_submission.WorkflowSubmissionError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


@router.post("/upload")
def upload_image(
    file: UploadFile = File(...),
    url: str = Form(COMFYUI_BASE_URL),
) -> dict[str, object]:
    """把图片转发上传到 ComfyUI 的 input 目录，返回其文件名（供 LoadImage 引用）。"""
    try:
        url = validate_comfyui_url(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not _is_up(url):
        raise HTTPException(status_code=400, detail="ComfyUI 未运行，无法上传图片")
    try:
        data = file.file.read()
        ref = comfyui_client.upload_image(url, file.filename, data, file.content_type or "image/png")
    except ComfyError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
    return {"name": ref}


@router.get("/result")
def result(prompt_id: str, url: str = COMFYUI_BASE_URL, node_ids: str = "") -> dict[str, object]:
    """轮询某次生成的状态与产出图片。
    node_ids 逗号分隔时，只保留这些节点的产物（主输出节点过滤）。
    """
    filter_ids = [n.strip() for n in node_ids.split(",") if n.strip()] if node_ids else None
    try:
        url = validate_comfyui_url(url)
        return comfyui_client.fetch_result(url, prompt_id, filter_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ComfyError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


class FinalizeGenerationImage(BaseModel):
    filename: str
    subfolder: str = ""
    type: str = "output"


class FinalizeGenerationRequest(BaseModel):
    thread_id: str
    repo_id: str
    prompt_id: str
    prompt: str = ""
    images: list[FinalizeGenerationImage] = []
    videos: list[FinalizeGenerationImage] = []
    output_dir: str = ""
    comfyui_url: str = COMFYUI_BASE_URL
    embed_base: str = ""
    embed_key: str = ""
    embed_model: str = "text-embedding-3-small"
    chat_base: str = ""
    chat_key: str = ""
    chat_model: str = ""
    regeneration: dict | None = None


@router.post("/finalize-generation")
def finalize_generation(req: FinalizeGenerationRequest) -> dict[str, object]:
    """持久化一批已完成的工作流产出；查询结果的 GET 路由保持无副作用。"""
    try:
        return generation_store.finalize_workflow_batch(
            thread_id=req.thread_id,
            repo_id=req.repo_id,
            prompt_id=req.prompt_id,
            prompt=req.prompt,
            images=[image.model_dump() for image in req.images],
            videos=[video.model_dump() for video in req.videos],
            output_dir=req.output_dir,
            comfyui_url=req.comfyui_url,
            embed_base=req.embed_base,
            embed_key=req.embed_key,
            embed_model=req.embed_model,
            chat_base=req.chat_base,
            chat_key=req.chat_key,
            chat_model=req.chat_model,
            regeneration=req.regeneration,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class InterruptRequest(BaseModel):
    url: str = COMFYUI_BASE_URL
    prompt_id: str = ""            # 有则先从队列删除未执行项，再中断正在执行的


@router.post("/interrupt")
def interrupt(req: InterruptRequest) -> dict[str, object]:
    """强行停止 ComfyUI 生图（人工打断工作流用）。

    prompt_id 有值：先 POST /queue {"delete":[id]} 删掉排队中未执行项，
    再 POST /interrupt 中断正在执行的任务（覆盖排队/执行两种状态）。
    容错：ComfyUI 未起/已完成都不报错，返回 ok。
    """
    try:
        req.url = validate_comfyui_url(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    res = comfyui_client.interrupt(req.url, req.prompt_id)
    return {"ok": True, **res}


@router.get("/view")
def view(filename: str, type: str = "output", subfolder: str = "", url: str = COMFYUI_BASE_URL):
    """代理 ComfyUI 的 /view，返回图片二进制（避免前端跨域直连 8188）。"""
    from fastapi.responses import Response

    try:
        url = validate_comfyui_url(url)
        data, ctype = comfyui_client.fetch_view(url, filename, type, subfolder)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ComfyError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
    return Response(content=data, media_type=ctype)


class SaveLocalRequest(BaseModel):
    filename: str = ""                   # ComfyUI 产物文件名（ComfyUI 模式）
    subfolder: str = ""
    type: str = "output"
    repo_id: str = "home"
    output_dir: str = ""                 # 设置里的输出图片路径
    url: str = COMFYUI_BASE_URL
    src: str = ""                        # 通用模式：完整图片 URL 或 data URI（云端生图用）
    subdir: str = ""                     # 落到 <repo>/<subdir>/ 子夹（用户上传参考图 → reference）


@router.post("/save-local")
def save_local(req: SaveLocalRequest) -> dict[str, object]:
    """把原图存到设置的 outputDir（全分辨率，不降质），返回本地访问 URL。

    两种来源：
    - ComfyUI 模式：给 filename，从 ComfyUI /view 取原图。
    - 通用模式：给 src（http(s) URL 或 data:image/...;base64,...），直接下载/解码（云端生图）。
    ComfyUI 未起/清理 output 后仍可显示与「再改进」，且不依赖在线。
    """
    try:
        path = image_store.save_local(
            req.output_dir,
            req.repo_id,
            src=req.src,
            filename=req.filename,
            subfolder=req.subfolder,
            type=req.type,
            url=validate_comfyui_url(req.url),
            subdir=req.subdir,
        )
    except ComfyError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
    return {"ok": True, "path": path}


@router.get("/local-view")
def local_view(path: str, request: Request):
    """读取已留存到 outputDir 的本地文件，支持 Range 请求（视频拖动进度需要）。

    安全：只服务媒体文件（图片/视频）。该端点按设计要读任意本地路径（对话背景图允许
    用户填任意图片完整路径），无法按目录 jail，故用扩展名白名单挡住读取 .env/.db/源码等
    敏感文件的 LFI 攻击面。
    """
    try:
        media = local_media.open_local_media(path, request.headers.get("Range"))
    except local_media.LocalMediaError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail, headers=exc.headers)
    if not media.partial:
        from fastapi.responses import FileResponse
        return FileResponse(media.path, media_type=media.media_type, headers=media.headers)
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        media.iter_bytes(), status_code=206, media_type=media.media_type, headers=media.headers,
    )
