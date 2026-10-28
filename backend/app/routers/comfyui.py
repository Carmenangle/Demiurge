from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, WebSocket
from pydantic import BaseModel

from app.config import COMFYUI_BASE_URL
from app.services import (
    comfy_hosts, comfy_pending, comfyui_client, comfyui_ws_proxy, comfy_launcher, generation_store,
    image_store, inspiration_store, local_media, repo_meta, workflow_submission,
    comfy_watchdog,
)
from app.services.url_guard import validate_comfyui_url
from app.services import system_proxy
from app.services import remote_gate
from app.services.comfyui_client import ComfyError
from app.services.comfy_launcher import LaunchError

router = APIRouter()

_is_up = comfyui_client.is_up


def _state_proxy(req_proxy: str = "") -> str:
    """外网素材链路的代理解析：显式传入 > user_state 配置地址（探活）> 直连。

    ⚠ 2026-10-27 真机实锤：PC 的 Clash 地址（127.0.0.1:7897）随 user_state 进了手机，
    手机 VPN 模式代理不监听端口 ⇒ 原来直接把配置地址交给 requests 必然 Connection
    refused。探活不存活即降级直连——VPN(tun) 模式下直连流量由代理 App 在网络层接管。
    """
    from app.services.repo_meta import setting_str_from_state

    proxy = req_proxy.strip() or (setting_str_from_state("systemProxyUrl")
                                  or setting_str_from_state("proxyUrl"))
    return proxy if system_proxy.alive(proxy) else ""




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


@router.get("/resources")
def resource_status() -> dict[str, object]:
    from app.services import model_lease

    return model_lease.status()


class ComfyConfig(BaseModel):
    path: str = ""
    url: str = COMFYUI_BASE_URL
    python_path: str = ""


class ComfyConfigResult(ComfyConfig):
    """保存结果 + 白名单登记回执（登记失败不打断保存，但必须让用户看见原因）。"""

    registered: str = ""        # "loopback" / 主机名 / 空（未登记）
    register_error: str = ""    # 未登记的原因（上限已满、主机形态不合法……）


@router.get("/config")
def get_config() -> ComfyConfig:
    """读 ComfyUI 路径/地址配置（供 start-dev 脚本等读取）。"""
    return ComfyConfig(**comfy_launcher.load_config())


@router.post("/config")
def set_config(cfg: ComfyConfig) -> ComfyConfigResult:
    """保存 ComfyUI 路径/地址，落盘到 data/comfy_config.json（ps1 脚本据此启动）。

    顺带把地址里的主机登记进 url_guard 白名单并落盘（2026-10-27）：PC 此前只能靠
    导入期读一次的 COMFYUI_ALLOWED_HOSTS 环境变量，加一台云端/局域网 ComfyUI 就得改环境
    重启。登记失败**不影响保存成功**，原因通过 register_error 回给前端显示。
    """
    saved = comfy_launcher.save_config(cfg.path, cfg.url, cfg.python_path)
    try:
        return ComfyConfigResult(**saved, registered=comfy_hosts.register_from_url(cfg.url))
    except ValueError as exc:
        return ComfyConfigResult(**saved, register_error=str(exc))


@router.get("/hosts")
def list_hosts() -> dict[str, object]:
    """ComfyUI 白名单主机簿（运行期判定面 + 落盘真源 + 上限）。

    PC 独占：登记主机＝放宽 SSRF 白名单，这一面不能让远程客户端读（判定在
    remote_gate._DENY_PREFIXES，改这里要同步改那边）。
    """
    return comfy_hosts.snapshot()


class HostBody(BaseModel):
    url: str


@router.post("/hosts")
def add_host(body: HostBody) -> dict[str, object]:
    """登记一台额外的 ComfyUI 主机（地址簿「新增机台」）。

    与 `POST /config` 的分工：那条**同时改默认地址**（`comfy_config.json`，ps1 脚本据此
    启动），这条只放宽白名单 ⇒ 加第二、第三台机不会把当前默认机顶掉。
    """
    try:
        return {"registered": comfy_hosts.register_from_url(body.url)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/hosts")
def delete_host(url: str) -> dict[str, object]:
    """注销一台主机（地址簿删条目）。幂等：不在簿子里也返回 200。"""
    try:
        return {"removed": comfy_hosts.remove_from_url(url)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/start")
def start(req: StartRequest) -> dict[str, object]:
    try:
        result = comfy_launcher.start(req.path, req.url, req.python_path)
        comfy_watchdog.note_owned()
        return result
    except LaunchError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@router.post("/stop")
def stop(req: StartRequest) -> dict[str, object]:
    """关闭 ComfyUI（装插件/依赖后需先关）。"""
    return comfy_launcher.stop(req.url, req.path, req.python_path)


@router.post("/restart")
def restart(req: StartRequest) -> dict[str, object]:
    """重启 ComfyUI：先关再起（装完插件生效）。需提供 path 以重新拉起。"""
    try:
        result = comfy_launcher.restart(req.path, req.url, req.python_path)
        comfy_watchdog.note_owned()
        return result
    except LaunchError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


class SubmitRequest(BaseModel):
    template_id: str
    values: dict[str, object] = {}   # key = "node_id.field" -> 覆盖值
    prompt: str = ""                 # 可选：自动注入到模板 prompt_node_id 的文本字段（/g 用）
    url: str = COMFYUI_BASE_URL
    client_id: str = ""              # 前端 WebSocket clientId，回传给 ComfyUI 定向推进度
    loras: list[dict[str, object]] = []  # 自动插画 LoRA 链；首项复用模板节点，其余动态追加
    lora_mode: str = "single"


@router.post("/submit")
def submit(req: SubmitRequest) -> dict[str, object]:
    """/s 启动：读模板原始工作流 → 转 API 格式 → 套用用户填的值 → 提交 /prompt。"""
    try:
        return workflow_submission.submit_template(
            req.template_id, req.values, req.prompt, req.url, req.client_id,
            req.loras, req.lora_mode,
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


class PendingTrackRequest(BaseModel):
    prompt_id: str
    thread_id: str = "home"
    created_at: float = 0.0        # 秒；缺省用服务端当前时间


class PendingFinishRequest(BaseModel):
    prompt_id: str = ""


@router.post("/pending")
def track_pending(req: PendingTrackRequest) -> dict[str, object]:
    """登记一个在途出图任务（前端 WorkflowGenerationRuntime.track 上报）。

    此前只在浏览器 localStorage 里记，换浏览器/清缓存就丢了在跑任务（2026-10-19）。
    """
    try:
        item = comfy_pending.track(req.prompt_id, req.thread_id, req.created_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": item}


@router.post("/pending/finish")
def finish_pending(req: PendingFinishRequest) -> dict[str, object]:
    """终态清除（完成/取消/失败/超时）。幂等：不存在也返回 ok。"""
    return {"ok": True, "removed": comfy_pending.finish(req.prompt_id)}


@router.get("/pending")
def list_pending() -> dict[str, object]:
    """在途任务列表（后台活动面板跨端读取；顺手清 30 分钟以上的僵尸条目）。"""
    return {"items": comfy_pending.snapshot()}


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
        ref = comfyui_client.upload_image(url, file.filename or "upload.png", data,
                                          file.content_type or "image/png")
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
        result = comfyui_client.fetch_result(url, prompt_id, filter_ids)
        if result.get("status") in {"completed", "failed", "not_found"}:
            from app.services import model_lease

            model_lease.release_owner(f"comfyui:{prompt_id}")
        return result
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
    audios: list[FinalizeGenerationImage] = []
    output_dir: str = ""
    comfyui_url: str = COMFYUI_BASE_URL
    embed_base: str = ""
    embed_key: str = ""
    embed_model: str = "text-embedding-3-small"
    chat_base: str = ""
    chat_key: str = ""
    chat_model: str = ""
    regeneration: dict | None = None
    target_message_id: str = ""
    target_slot_id: str = ""
    template_name: str = ""
    model_name: str = ""
    lora_names: str = ""
    base_slot_ref: dict | None = None  # M2.1 视频首帧底图槽引用 {message_id, slot_id}


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
            audios=[audio.model_dump() for audio in req.audios],
            output_dir=req.output_dir,
            comfyui_url=req.comfyui_url,
            embed_base=req.embed_base,
            embed_key=req.embed_key,
            embed_model=req.embed_model,
            chat_base=req.chat_base,
            chat_key=req.chat_key,
            chat_model=req.chat_model,
            regeneration=req.regeneration,
            template_name=req.template_name,
            model_name=req.model_name,
            lora_names=req.lora_names,
            target_message_id=req.target_message_id,
            target_slot_id=req.target_slot_id,
            base_slot_ref=req.base_slot_ref,
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
    if req.prompt_id:
        from app.services import model_lease

        model_lease.release_owner(f"comfyui:{req.prompt_id}")
    return {"ok": True, **res}


@router.websocket("/ws")
async def comfyui_ws(websocket: WebSocket, clientId: str = "", url: str = "") -> None:
    """进度 WS 中继（薄路由；安全合同与生命周期见 comfyui_ws_proxy 模块 docstring）。"""
    await comfyui_ws_proxy.relay(websocket, url=url, client_id=clientId)


@router.get("/view")
def view(filename: str, type: str = "output", subfolder: str = "", url: str = COMFYUI_BASE_URL):
    """代理 ComfyUI 的 /view，返回图片二进制（避免前端跨域直连 ComfyUI 端口）。"""
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
def save_local(req: SaveLocalRequest, request: Request) -> dict[str, object]:
    """把原图存到设置的 outputDir（全分辨率，不降质），返回本地访问 URL。

    两种来源：
    - ComfyUI 模式：给 filename，从 ComfyUI /view 取原图。
    - 通用模式：给 src（http(s) URL 或 data:image/...;base64,...），直接下载/解码（云端生图）。
    ComfyUI 未起/清理 output 后仍可显示与「再改进」，且不依赖在线。
    """
    # L2：远程客户端的写路径收敛（PC 本机 no-op；令牌互锁由 remote_gate 中间件先行）
    try:
        remote_gate.ensure_write_scope(
            request.client.host if request.client else "", req.output_dir)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
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


class MergeAudioRequest(BaseModel):
    thread_id: str
    message_id: str
    force: bool = False


@router.post("/merge-audio")
def merge_audio(req: MergeAudioRequest) -> dict[str, object]:
    """把一条消息的音频分条按顺序拼接成完整版，落盘并回写快照（刷新后仍在）。

    幂等：已合并过（快照含 merged- 槽）时直接返回既有 URL；force=True 覆盖重拼。
    失败返回 400。
    """
    from app.services import audio_merge
    try:
        url = audio_merge.merge_audio_for_message(
            req.thread_id, req.message_id, force=req.force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "url": url}


# ===== 上网素材：联网搜索下载的图片存到 outputDir/_web_materials/ =====

class WebMaterialSaveRequest(BaseModel):
    output_dir: str = ""
    src: str = ""               # 图片 URL 或 data URI
    source_url: str = ""        # 来源网页 URL
    title: str = ""             # 图片标题
    thread_id: str = ""         # 灵感卡所属会话（重启后候选列表丢失时，允许保存快照内灵感卡图片）


class WebMaterialListRequest(BaseModel):
    output_dir: str = ""


class WebMaterialDeleteRequest(BaseModel):
    output_dir: str = ""
    filename: str = ""


@router.post("/web-materials/save")
def web_material_save(req: WebMaterialSaveRequest) -> dict[str, object]:
    """把联网搜索到的图片下载到 _web_materials/，返回 {path, url, source_url, title, filename}。"""
    try:
        return image_store.save_web_material(
            req.output_dir, req.src, req.source_url, req.title, req.thread_id,
        )
    except ComfyError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@router.post("/web-materials/list")
def web_material_list(req: WebMaterialListRequest) -> dict[str, object]:
    """列出 _web_materials/ 下所有图片。"""
    return {"items": image_store.list_web_materials(req.output_dir)}


class WebMaterialSaveSeriesRequest(BaseModel):
    output_dir: str = ""
    illust_id: int = 0
    source_url: str = ""
    title: str = ""
    proxy: str = ""
    thread_id: str = ""


@router.post("/web-materials/save-series")
def web_material_save_series(req: WebMaterialSaveSeriesRequest) -> dict[str, object]:
    """pixiv 多页作品整册保存：后端一次性拉全页 + 逐页落盘（前端崩溃/断连不影响，服务端继续写完）。"""
    from app.services.repo_meta import setting_str_from_state

    px = _state_proxy(req.proxy)
    try:
        return image_store.save_web_series(
            req.output_dir, req.illust_id, req.source_url, req.title,
            refresh_token=setting_str_from_state("pixivRefreshToken"), proxy=px,
            thread_id=req.thread_id,
        )
    except ComfyError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


# ===== 图站素材源（P2-1 切片）：pixiv / danbooru 搜索 → 登记候选 → 走 save 落盘 =====

class WebMaterialSearchRequest(BaseModel):
    source: str = "danbooru"    # danbooru | pixiv
    query: str = ""
    limit: int = 20
    page: int = 1
    proxy: str = ""
    mode: str = "search"        # pixiv: search | recommended | ranking | bookmarks | author
    mode_param: str = ""        # pixiv: ranking 周期(day/week/month) | author user_id


@router.post("/web-materials/search")
def web_material_search(req: WebMaterialSearchRequest) -> dict[str, object]:
    """搜索图站素材（danbooru 公开 API / pixiv SDK）。

    成功即把结果登记为受控下载候选 ⇒ 随后走既有 `/web-materials/save` 保存
    （安全模型不变：只落登记过的 URL）。凭证读「设置 → 凭证」面板。

    **空 query 合法**（danbooru 默认按最新返回）= 不选排行榜/收藏夹时的默认时间序浏览。
    ⚠ 代理兜底：前端传空时用设置里的 systemProxyUrl/proxyUrl（2026-10-16 实锤：
    前端探测值可能为空 ⇒ 直连 danbooru 超时，用户侧表现为「搜了没反应」）。
    """
    from app.services import material_sources
    from app.services.repo_meta import setting_str_from_state

    proxy = _state_proxy(req.proxy)
    return material_sources.search(
        req.source, req.query, max(1, min(req.limit, 50)),
        pixiv_token=setting_str_from_state("pixivRefreshToken"),
        danbooru_key=setting_str_from_state("danbooruApiKey"),
        danbooru_login=setting_str_from_state("danbooruLogin"),
        page=req.page,
        proxy=proxy,
        mode=req.mode,
        mode_param=req.mode_param,
    )


class WebMaterialFavoriteRequest(BaseModel):
    post_id: int = 0
    proxy: str = ""
    source: str = "danbooru"    # danbooru | pixiv
    remove: bool = False        # True=取消收藏（pixiv 支持；danbooru 需 favorite id，暂不支持）


@router.post("/web-materials/favorite")
def web_material_favorite(req: WebMaterialFavoriteRequest) -> dict[str, object]:
    """收藏 / 取消收藏（danbooru 需用户名+API Key；pixiv 用 refresh token，支持 remove 取消）。幂等。"""
    from app.services import material_sources
    from app.services.repo_meta import setting_str_from_state

    proxy = _state_proxy(req.proxy)
    if (req.source or "danbooru").strip().lower() == "pixiv":
        fn = material_sources.unfavorite_pixiv if req.remove else material_sources.favorite_pixiv
        return fn(
            req.post_id,
            refresh_token=setting_str_from_state("pixivRefreshToken"),
            proxy=proxy,
        )
    if req.remove:
        return {"ok": False, "already": False,
                "error": "danbooru 暂不支持从卡片取消收藏（需收藏条目的 favorite id）。pixiv 支持。"}
    return material_sources.favorite_danbooru(
        req.post_id,
        login=setting_str_from_state("danbooruLogin"),
        api_key=setting_str_from_state("danbooruApiKey"),
        proxy=proxy,
    )


@router.get("/web-materials/pixiv/following")
def web_material_pixiv_following(proxy: str = "", page: int = 1) -> dict[str, object]:
    """pixiv 关注作者列表（user_following）。返回 {ok, users:[{id,name,account,avatar_url}], error}。"""
    from app.services import material_sources
    from app.services.repo_meta import setting_str_from_state

    px = _state_proxy(proxy)
    return material_sources.pixiv_following(
        setting_str_from_state("pixivRefreshToken"), proxy=px, page=page,
    )


class PixivPagesRequest(BaseModel):
    illust_id: int = 0
    proxy: str = ""


@router.post("/web-materials/pixiv/pages")
def web_material_pixiv_pages(req: PixivPagesRequest) -> dict[str, object]:
    """多页作品全页 URL（illust_detail）——feed 只给 page_count 时按需补全，供翻页/下载全部页。"""
    from app.services import material_sources
    from app.services.repo_meta import setting_str_from_state

    px = _state_proxy(req.proxy)
    return material_sources.pixiv_pages(
        req.illust_id, refresh_token=setting_str_from_state("pixivRefreshToken"), proxy=px,
    )


@router.get("/web-materials/proxy-image")
def web_material_proxy_image(url: str, proxy: str = ""):
    """图站图片代理（「查看大图」用：浏览器不一定能直连 CDN / 有防盗链）。

    仅放行图站白名单 host；字节流回传并带 1h 缓存。
    """
    from fastapi import Response

    from app.services import material_sources
    from app.services.image_proxy import proxied_image_response

    if not url.strip():
        raise HTTPException(status_code=400, detail="缺少 url")
    px = _state_proxy(proxy)
    try:
        data, ctype = material_sources.fetch_image_bytes(url.strip(), proxy=px)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # 响应形态（nosniff / svg CSP sandbox / svg 类型归一）与 image-proxy 同一属主
    # image_proxy.proxied_image_response——白名单 host 只是收窄面，不是替代。
    ctype, headers = proxied_image_response(ctype)
    headers["Cache-Control"] = "public, max-age=3600"
    return Response(content=data, media_type=ctype, headers=headers)


@router.post("/web-materials/delete")
def web_material_delete(req: WebMaterialDeleteRequest) -> dict[str, object]:
    """删除 _web_materials/ 下的指定文件。"""
    if (err := repo_meta.works_root_violation(req.output_dir)):
        raise HTTPException(status_code=400, detail=err)
    ok = image_store.delete_web_material(req.output_dir, req.filename)
    return {"ok": ok}


# ===== 灵感卡资产库（M1.4）：会话灵感卡升级为资产库可管理成员 =====

class InspirationCardImage(BaseModel):
    full_url: str = ""
    source_url: str = ""
    title: str = ""


class InspirationCardSaveRequest(BaseModel):
    output_dir: str = ""
    card_id: str = ""                    # 空=新建；非空=覆盖更新（同卡重复保存）
    title: str = ""
    content: str = ""
    sources: list[dict] = []
    images: list[InspirationCardImage] = []
    thread_id: str = ""                  # 候选校验豁免（重启后保存旧卡图片）


class InspirationCardListRequest(BaseModel):
    output_dir: str = ""


class InspirationCardGetRequest(BaseModel):
    output_dir: str = ""
    card_id: str = ""


class InspirationCardUpdateRequest(BaseModel):
    output_dir: str = ""
    card_id: str = ""
    title: str | None = None
    content: str | None = None
    remove_image_urls: list[str] = []    # 删图只留文本（图文件保留在素材库）


class InspirationCardDeleteRequest(BaseModel):
    output_dir: str = ""
    card_id: str = ""


@router.post("/web-materials/inspiration/save")
def inspiration_card_save(req: InspirationCardSaveRequest) -> dict[str, object]:
    """把灵感卡登记为资产库成员（显式入库）。"""
    try:
        return inspiration_store.save_inspiration_card(
            req.output_dir,
            card_id=req.card_id,
            title=req.title,
            content=req.content,
            sources=req.sources,
            images=[img.model_dump() for img in req.images],
            thread_id=req.thread_id,
        )
    except ComfyError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@router.post("/web-materials/inspiration/list")
def inspiration_card_list(req: InspirationCardListRequest) -> dict[str, object]:
    """列出资产库灵感卡（新→旧）。"""
    return {"items": inspiration_store.list_inspiration_cards(req.output_dir)}


@router.post("/web-materials/inspiration/get")
def inspiration_card_get(req: InspirationCardGetRequest) -> dict[str, object]:
    """读取单张灵感卡详情。"""
    try:
        return inspiration_store.get_inspiration_card(req.output_dir, req.card_id)
    except ComfyError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@router.post("/web-materials/inspiration/update")
def inspiration_card_update(req: InspirationCardUpdateRequest) -> dict[str, object]:
    """编辑灵感卡（改文本 / 删图只留文本）。"""
    try:
        return inspiration_store.update_inspiration_card(
            req.output_dir,
            card_id=req.card_id,
            title=req.title,
            content=req.content,
            remove_image_urls=req.remove_image_urls,
        )
    except ComfyError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@router.post("/web-materials/inspiration/delete")
def inspiration_card_delete(req: InspirationCardDeleteRequest) -> dict[str, object]:
    """删除灵感卡资产（只删 JSON，图片保留）。"""
    ok = inspiration_store.delete_inspiration_card(req.output_dir, req.card_id)
    return {"ok": ok}


# ===== 参考图：聊天上传到 <repo>/reference/ 的图片，供画布自动导入 =====

class ReferenceImageListRequest(BaseModel):
    output_dir: str = ""
    repo_id: str = ""


@router.post("/reference-images/list")
def reference_image_list(req: ReferenceImageListRequest) -> dict[str, object]:
    """列出 <repo_id>/reference/ 下所有图片，供画布自动导入参考图节点。"""
    return {"items": image_store.list_reference_images(req.output_dir, req.repo_id)}
