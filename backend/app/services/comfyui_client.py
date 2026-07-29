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
        with urlopen(rq, timeout=10) as r:
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


def fetch_result(url: str, prompt_id: str,
                 filter_node_ids: list[str] | None = None) -> dict:
    """轮询 /history/{id}，归一为 {status, images, videos, texts}。

    视频节点（VHS_VideoCombine 等）的产物落在 outputs 的 gifs 键（含 mp4/webm/gif），
    与图片同结构({filename,subfolder,type})但单列 videos，供前端用 <video> 渲染。
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
            running = [item[1] for item in q.get("queue_running", [])]
            pending_q = [item[1] for item in q.get("queue_pending", [])]
            if prompt_id in running or prompt_id in pending_q:
                return {"status": "pending", "images": [], "videos": [], "texts": []}
            # 不在历史也不在队列：任务已丢失（ComfyUI 重启等原因）
            return {"status": "not_found", "images": [], "videos": [], "texts": []}
        except Exception:
            # 队列查询失败时保守返回 pending，避免误判
            return {"status": "pending", "images": [], "videos": [], "texts": []}

    completed = entry.get("status", {}).get("completed", False)
    images: list[dict[str, str]] = []
    videos: list[dict[str, str]] = []
    texts: list[str] = []

    def _as_ref(item: dict) -> dict[str, str]:
        return {
            "filename": item.get("filename", ""),
            "subfolder": item.get("subfolder", ""),
            "type": item.get("type", "output"),
        }

    _video_ext = (".mp4", ".webm", ".gif", ".mov", ".mkv", ".webp")
    allowed = set(filter_node_ids) if filter_node_ids else None
    for node_id, node_out in entry.get("outputs", {}).items():
        if allowed and node_id not in allowed:
            continue
        for img in node_out.get("images", []):
            # 个别视频节点把产物塞进 images，按扩展名甄别改归 videos
            if str(img.get("filename", "")).lower().endswith(_video_ext[:-1]):
                videos.append(_as_ref(img))
            else:
                images.append(_as_ref(img))
        # gifs：VHS_VideoCombine 等的标准视频/动图输出键
        for vid in node_out.get("gifs", []) or []:
            videos.append(_as_ref(vid))
        for t in node_out.get("text", []) or []:
            if isinstance(t, str) and t.strip():
                texts.append(t)
    return {
        "status": "completed" if completed else "running",
        "images": images,
        "videos": videos,
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
