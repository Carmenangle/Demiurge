"""与 ComfyUI 的 HTTP 对话集中于此：探活、提交 /prompt、轮询 /history、
取图 /view、打断、上传图片。协议怪癖（端点、错误模式、响应结构）只此一处。
路由层只做适配（读模板、落盘、进程），不再直接拼 ComfyUI 请求。
"""
import json
import socket
import time
from threading import Lock
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import ProxyHandler, Request, build_opener

import requests

from app.services.url_guard import validate_comfyui_url


# ComfyUI 只能指向本机/局域网白名单地址。显式禁用环境代理，避免 localhost
# 被 HTTP_PROXY/HTTPS_PROXY 劫持成 502；保留 urlopen 名称作为测试与调用接缝。
_NO_PROXY_HANDLER = ProxyHandler({})
_DIRECT_OPENER = build_opener(_NO_PROXY_HANDLER)
urlopen = _DIRECT_OPENER.open
_DIRECT_SESSION = requests.Session()
_DIRECT_SESSION.trust_env = False
_OBJECT_INFO_CACHE_TTL = 120.0
_OBJECT_INFO_CACHE: dict[str, tuple[float, dict]] = {}
_OBJECT_INFO_LOCK = Lock()


class ComfyError(Exception):
    """ComfyUI 通信/校验错误。detail 供路由透出，status 建议 HTTP 码。"""

    def __init__(self, detail: str, status: int = 502):
        super().__init__(detail)
        self.detail = detail
        self.status = status


def _base(url: str) -> str:
    try:
        return validate_comfyui_url(url).rstrip("/")
    except ValueError as e:
        raise ComfyError(str(e), 400)


def is_up(url: str, timeout: float = 5.0) -> bool:
    """探测 ComfyUI 是否在响应；HTTP 失败则退化为 TCP 端口探测。"""
    try:
        normalized = validate_comfyui_url(url)
    except ValueError:
        return False
    try:
        with urlopen(normalized, timeout=timeout) as r:
            return r.status < 500
    except Exception:
        try:
            p = urlparse(normalized)
            host = p.hostname or "127.0.0.1"
            port = p.port or 8188
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            return False


def fetch_object_info(
    url: str, node: str = "", timeout: float = 30, *, force: bool = False,
) -> dict:
    """拉取 /object_info（全量节点 schema）或 /object_info/{node}（单节点）。
    全量结果短时缓存，避免连续搭建重复下载巨大 schema；单节点查询不缓存。
    返回 {节点名: schema}。失败抛 ComfyError。自动搭工作流的地基。"""
    base = _base(url)
    endpoint = base + "/object_info" + (f"/{node}" if node else "")

    def fetch() -> dict:
        try:
            with urlopen(endpoint, timeout=timeout) as response:
                result = json.loads(response.read())
        except HTTPError as exc:
            raise ComfyError(f"取 object_info 失败：{exc}", 502) from exc
        except Exception as exc:
            raise ComfyError(str(exc), 502) from exc
        if not isinstance(result, dict):
            raise ComfyError("取 object_info 失败：响应不是节点对象", 502)
        return result

    if node:
        return fetch()

    with _OBJECT_INFO_LOCK:
        cached = _OBJECT_INFO_CACHE.get(base)
        if not force and cached and time.monotonic() - cached[0] < _OBJECT_INFO_CACHE_TTL:
            return cached[1]
        result = fetch()
        _OBJECT_INFO_CACHE[base] = (time.monotonic(), result)
        return result


def submit_prompt(url: str, api: dict, client_id: str = "") -> str | None:
    """POST /prompt，返回 prompt_id。HTTPError 透出 ComfyUI 校验详情。
    client_id 非空时随请求带上，ComfyUI 会把该任务进度只推给同 clientId 的 WebSocket。"""
    payload: dict[str, object] = {"prompt": api}
    if client_id:
        payload["client_id"] = client_id
    body = json.dumps(payload).encode("utf-8")
    rq = Request(_base(url) + "/prompt", data=body,
                 headers={"Content-Type": "application/json"})
    try:
        # 大型工作流在 /prompt 入口会同步完成节点校验；冷启动时 10 秒不足，
        # 请求可能已到 ComfyUI 却在返回 prompt_id 前被客户端掐断。
        with urlopen(rq, timeout=30) as r:
            res = json.loads(r.read())
        return res.get("prompt_id")
    except HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:
            detail = str(e)
        raise ComfyError(detail, 500)
    except Exception as e:
        raise ComfyError(str(e), 500)


def _queue_prompt_ids(queue: dict, key: str) -> list[str]:
    """从 /queue 的 queue_running / queue_pending 列表提取 prompt_id。

    兼容两种队列项形态：`[index, "prompt-id"]`（字符串）与
    `[index, {"prompt_id": "prompt-id", ...}]`（对象）。"""
    ids: list[str] = []
    for item in queue.get(key) or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            item = item[1]
        if isinstance(item, dict):
            pid = str(item.get("prompt_id") or "")
        else:
            pid = str(item or "")
        if pid:
            ids.append(pid)
    return ids


def fetch_result(url: str, prompt_id: str,
                 filter_node_ids: list[str] | None = None) -> dict:
    """轮询 /history/{id}，归一为 {status, images, videos, audios, texts}。

    视频节点（VHS_VideoCombine 等）的产物落在 outputs 的 gifs 键（含 mp4/webm/gif），
    音频节点（SaveAudio / VHS_AudioCombine 等）落在 outputs 的 audio 键（wav/mp3/flac…），
    与图片同结构({filename,subfolder,type})但单列 videos/audios，供前端用 <video>/<audio> 渲染。
    filter_node_ids 非空时只保留指定节点的产物（多输出工作流主输出节点过滤）。
    """
    try:
        with urlopen(_base(url) + f"/history/{prompt_id}", timeout=10) as r:
            hist = json.loads(r.read())
    except Exception as e:
        raise ComfyError(f"查询历史失败：{e}", 502)

    entry = hist.get(prompt_id)
    if not entry:
        # 历史里没有：再查队列确认任务是否还在等待/执行中
        # 若队列里也不存在，说明 ComfyUI 重启后任务丢失
        try:
            with urlopen(_base(url) + "/queue", timeout=5) as qr:
                q = json.loads(qr.read())
            running = _queue_prompt_ids(q, "queue_running")
            pending_q = _queue_prompt_ids(q, "queue_pending")
            if prompt_id in running:
                # 已进入执行队列（节点开始加载/运转 = 有动弹）
                return {"status": "running", "images": [], "videos": [], "audios": [], "texts": []}
            if prompt_id in pending_q:
                # 仍在排队，节点尚未开始加载（无动弹）
                return {"status": "pending", "images": [], "videos": [], "audios": [], "texts": []}
            # 不在历史也不在队列：任务已丢失（ComfyUI 重启等原因）
            return {"status": "not_found", "images": [], "videos": [], "audios": [], "texts": []}
        except Exception:
            # 队列查询失败时保守返回 pending，避免误判
            return {"status": "pending", "images": [], "videos": [], "texts": []}

    status_data = entry.get("status", {})
    completed = status_data.get("completed", False)
    if status_data.get("status_str") == "error":
        error = "ComfyUI 工作流执行失败"
        for message in reversed(status_data.get("messages", [])):
            if not isinstance(message, list) or len(message) < 2 or message[0] != "execution_error":
                continue
            detail = message[1] if isinstance(message[1], dict) else {}
            node_id = str(detail.get("node_id") or "").strip()
            node_type = str(detail.get("node_type") or "").strip()
            exception = str(detail.get("exception_message") or "").strip()
            node = node_type + (f" (#{node_id})" if node_id else "")
            error = f"{node}: {exception}" if node and exception else exception or node or error
            break
        return {
            "status": "failed",
            "error": error,
            "images": [],
            "videos": [],
            "audios": [],
            "texts": [],
        }
    images: list[dict[str, str]] = []
    videos: list[dict[str, str]] = []
    audios: list[dict[str, str]] = []
    texts: list[str] = []

    def _as_ref(item: dict) -> dict[str, str]:
        return {
            "filename": item.get("filename", ""),
            "subfolder": item.get("subfolder", ""),
            "type": item.get("type", "output"),
        }

    _video_ext = (".mp4", ".webm", ".gif", ".mov", ".mkv", ".webp")
    _audio_ext = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma")
    allowed = set(filter_node_ids) if filter_node_ids else None

    def _collect(node_filter: set[str] | None) -> tuple[list[dict[str, str]],
                                                          list[dict[str, str]],
                                                          list[dict[str, str]],
                                                          list[str]]:
        found_images: list[dict[str, str]] = []
        found_videos: list[dict[str, str]] = []
        found_audios: list[dict[str, str]] = []
        found_texts: list[str] = []
        for node_id, node_out in entry.get("outputs", {}).items():
            if node_filter and node_id not in node_filter:
                continue
            for img in node_out.get("images", []):
                ext = str(img.get("filename", "")).lower()
                if ext.endswith(_audio_ext):
                    found_audios.append(_as_ref(img))
                elif ext.endswith(_video_ext[:-1]):
                    found_videos.append(_as_ref(img))
                else:
                    found_images.append(_as_ref(img))
            for vid in node_out.get("gifs", []) or []:
                found_videos.append(_as_ref(vid))
            for aud in node_out.get("audio", []) or []:
                found_audios.append(_as_ref(aud))
            for output_text in node_out.get("text", []) or []:
                if isinstance(output_text, str) and output_text.strip():
                    found_texts.append(output_text)
        return found_images, found_videos, found_audios, found_texts

    images, videos, audios, texts = _collect(allowed)
    saved_images = [image for image in images if image.get("type") != "temp"]
    saved_videos = [video for video in videos if video.get("type") != "temp"]
    saved_audios = [audio for audio in audios if audio.get("type") != "temp"]
    if allowed and (not saved_images and images):
        all_images, _, _, _ = _collect(None)
        saved_images = [image for image in all_images if image.get("type") != "temp"]
    if allowed and (not saved_videos and videos):
        _, all_videos, _, _ = _collect(None)
        saved_videos = [video for video in all_videos if video.get("type") != "temp"]
    if allowed and (not saved_audios and audios):
        _, _, all_audios, _ = _collect(None)
        saved_audios = [audio for audio in all_audios if audio.get("type") != "temp"]
    # 过滤节点完全匹配不到任何产物（模板 primary_output_node_id 与实际 SaveImage 节点 id 不一致等）
    # → 兜底全量收集，否则任务 completed 却拿空结果，结果不进对话/资产库（卡 SaveImage 表象）。
    # 兜底后须重算 saved（过滤 temp 预览图），否则全量 images 直接透传 temp。
    if allowed and not images and not videos and not audios and not texts:
        images, videos, audios, texts = _collect(None)
        saved_images = [image for image in images if image.get("type") != "temp"]
        saved_videos = [video for video in videos if video.get("type") != "temp"]
        saved_audios = [audio for audio in audios if audio.get("type") != "temp"]
    if saved_images:
        images = saved_images
    if saved_videos:
        videos = saved_videos
    if saved_audios:
        audios = saved_audios
    return {
        "status": "completed" if completed else "running",
        "images": images,
        "videos": videos,
        "audios": audios,
        "texts": texts,
    }


def fetch_view(url: str, filename: str, type: str = "output", subfolder: str = "",
               timeout: int = 15) -> tuple[bytes, str]:
    """代理取 /view 图片二进制，返回 (data, content_type)。"""
    qs = urlencode({"filename": filename, "type": type, "subfolder": subfolder})
    try:
        with urlopen(_base(url) + f"/view?{qs}", timeout=timeout) as r:
            return r.read(), r.headers.get("Content-Type", "image/png")
    except Exception as e:
        raise ComfyError(f"取图失败：{e}", 502)


def probe_view(url: str, filename: str, type: str = "output", subfolder: str = "",
               timeout: float = 8) -> str:
    """轻探测 /view 某产物文件是否仍存在（不下载内容，裂图清理判定用）。

    返回三态："ok"（200，文件在）/"missing"（404，文件确已不在）/
    "unreachable"（其余任何状态或网络异常——无法判定，调用方必须按保留处理，
    防止 ComfyUI 未起时误删仍存在的记录）。
    """
    try:
        base = _base(url)
    except ComfyError:
        return "unreachable"
    qs = urlencode({"filename": filename, "type": type, "subfolder": subfolder})
    try:
        resp = _DIRECT_SESSION.get(f"{base}/view?{qs}", timeout=timeout, stream=True)
    except Exception:
        return "unreachable"
    with resp:
        if resp.status_code == 200:
            return "ok"
        return "missing" if resp.status_code == 404 else "unreachable"


def interrupt(url: str, prompt_id: str = "") -> dict:
    """先从队列删未执行项，再中断正在执行的。ComfyUI 未起/已完成均不报错。"""
    base = _base(url)
    deleted = False
    interrupted = False
    if prompt_id:
        try:
            _DIRECT_SESSION.post(base + "/queue", json={"delete": [prompt_id]}, timeout=5)
            deleted = True
        except Exception:
            pass
    try:
        _DIRECT_SESSION.post(base + "/interrupt", timeout=5)
        interrupted = True
    except Exception:
        pass
    return {"deleted": deleted, "interrupted": interrupted}


def upload_image(url: str, filename: str, data: bytes, content_type: str = "image/png") -> str:
    """转发上传到 ComfyUI 的 input 目录，返回 LoadImage 可引用的相对名。"""
    files = {"image": (filename, data, content_type or "image/png")}
    try:
        resp = _DIRECT_SESSION.post(
            _base(url) + "/upload/image",
            files=files,
            data={"overwrite": "true"},
            timeout=30,
        )
        resp.raise_for_status()
        res = resp.json()
    except requests.RequestException as e:
        raise ComfyError(f"上传失败：{e}", 500)
    name = res.get("name", "")
    sub = res.get("subfolder", "")
    ref = f"{sub}/{name}" if sub else name
    return ref
