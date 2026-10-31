"""与 ComfyUI 的 HTTP 对话集中于此：探活、提交 /prompt、轮询 /history、
取图 /view、打断、上传图片。协议怪癖（端点、错误模式、响应结构）只此一处。
路由层只做适配（读模板、落盘、进程），不再直接拼 ComfyUI 请求。
"""
import json
import shutil
import socket
import time
import uuid
from threading import Lock
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import ProxyHandler, Request, build_opener

import requests

from app.config import COMFYUI_DEFAULT_PORT
from app.services.url_guard import validate_comfyui_url

import logging

_LOG = logging.getLogger("uvicorn.error")


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
    """探测 ComfyUI 是否已启动。

    TCP 端口探测优先：ComfyUI 高负载时 HTTP 请求可能排队超时，但监听端口通常
    立即可连。端口通即视为已启动，避免「节点/参数都能通、一提交却报未启动」
    的误判；端口探测失败时再退化为 HTTP 探测（兼容端口非本机监听但 HTTP 可达
    的罕见部署）。
    """
    try:
        normalized = validate_comfyui_url(url)
    except ValueError:
        return False
    parsed = urlparse(normalized)
    host = parsed.hostname or "127.0.0.1"
    # https/wss 的缺省端口是 443（云端 ComfyUI 常省略端口）：沿用 ComfyUI 默认端口
    # 会先白等一次必然失败的 TCP 探测，再退回 HTTP 探测才通。
    port = parsed.port or (443 if parsed.scheme in ("https", "wss") else COMFYUI_DEFAULT_PORT)
    try:
        with socket.create_connection((host, port), timeout=min(timeout, 3.0)):
            return True
    except Exception:  # noqa: BLE001 [EXC-保留] 探活失败=False 即语义（审计明确保留）
        pass
    try:
        with urlopen(normalized, timeout=timeout) as r:
            return r.status < 500
    except Exception as exc:
        _LOG.debug("comfyui_client::is_up 兜底异常: %s", exc)
        return False


def fetch_object_info(
    url: str, node: str = "", timeout: float = 30, *, force: bool = False,
) -> dict:
    """拉取 /object_info（全量节点 schema）或 /object_info/{node}（单节点）。
    全量结果短时缓存，避免连续搭建重复下载巨大 schema；单节点查询不缓存。
    返回 {节点名: schema}。失败抛 ComfyError。自动搭工作流的地基。"""
    base = _base(url)
    # 节点类型名常带空格与冒号（LayerUtility: ImageScaleByAspectRatio V2 这类），
    # 直接拼进路径段会打出非法 URL，探测静默失败 ⇒ 转换以为「没问到」而降级到内置表。
    endpoint = base + "/object_info" + (f"/{quote(node, safe='')}" if node else "")

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


# 逐类型探测的超时与总预算（2026-10-31 本机实测校准）。
# 全量 /object_info 在本机 4474 类 / 9.4 MB，冷缓存要 92.9 s——任何固定秒级上限都是
# 「白等一整个超时还拿不到」。改成只问这张图用到的类型后，真实代价集中在**第一次**：
# ComfyUI 收到 /object_info/{node} 也要先重建整份映射，实测首问 22.15 s、其后每个
# 0.00~0.02 s（映射已被它自己缓存）。所以：
#   首问给 30 s（焐热服务端，之后都是免费的）；后续每个 5 s 足够；
#   总预算 40 s 封顶，用完剩下的按「未知」处理——绝不让探测把提交时间拖成类型数 × 超时；
#   首问超时的那几个类型补问一轮：服务端这次已经焐热了，补问几乎不花时间。
# 客户端再叠一层「基址+类型」短缓存：批量插画连发十几张，这份开销只付一次。
_OBJECT_INFO_TYPE_TIMEOUT = 5.0
_OBJECT_INFO_FIRST_TYPE_TIMEOUT = 30.0
_OBJECT_INFO_TYPE_BUDGET = 40.0
_OBJECT_INFO_TYPE_CACHE: dict[str, tuple[float, dict | None]] = {}
_TYPE_CACHE_MISS = object()


def _cached_type_info(base: str, node: str) -> object:
    key = f"{base}|{node}"
    with _OBJECT_INFO_LOCK:
        entry = _OBJECT_INFO_TYPE_CACHE.get(key)
        if entry and time.monotonic() - entry[0] < _OBJECT_INFO_CACHE_TTL:
            return entry[1]
    return _TYPE_CACHE_MISS


def _remember_type_info(base: str, node: str, value: dict | None) -> None:
    with _OBJECT_INFO_LOCK:
        _OBJECT_INFO_TYPE_CACHE[f"{base}|{node}"] = (time.monotonic(), value)


def fetch_object_info_types(
    url: str, types: tuple[str, ...],
    *, timeout: float = _OBJECT_INFO_TYPE_TIMEOUT,
    first_timeout: float = _OBJECT_INFO_FIRST_TYPE_TIMEOUT,
    budget_seconds: float = _OBJECT_INFO_TYPE_BUDGET,
) -> dict[str, dict | None]:
    """按节点类型逐个拉 /object_info/{type}，合并成清单。

    值语义分三档，调用方必须区分：
      dict   = 该 ComfyUI 已注册此类型（拿到真实输入顺序）
      None   = **确认**未注册（未注册类型回的是 200 空对象，不是 404）
      键缺失 = 这一轮没问到（超时/断连/预算用完）＝未知，不得当成未注册

    缓存的是 schema 的形状而不是模型文件名列表：/object_info/{node} 的枚举会被
    前端「模型选项」直接消费，那一路必须新鲜，所以缓存在本函数而不是 fetch_object_info。
    """
    base = _base(url)
    manifest: dict[str, dict | None] = {}
    unasked: list[str] = []
    deadline = time.monotonic() + budget_seconds

    def ask(node: str, patient: bool) -> bool:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        cap = first_timeout if patient else timeout
        try:
            payload = fetch_object_info(url, node, timeout=min(cap, remaining))
        except ComfyError:
            return True  # 没问到，但预算还在：继续问下一个
        entry = payload.get(node)
        value = entry if isinstance(entry, dict) and entry else None
        _remember_type_info(base, node, value)
        manifest[node] = value
        return True

    heated = False  # 服务端是否已经交过答案（交过一次 ⇒ 那份映射已重建完、后续几乎免费）
    for node in dict.fromkeys(t for t in types if t):
        cached = _cached_type_info(base, node)
        if cached is not _TYPE_CACHE_MISS:
            manifest[node] = cached  # type: ignore[assignment]
            continue
        if not ask(node, patient=not heated):
            unasked.append(node)  # 预算用完：后面的一律按「未知」处理
            break
        if node in manifest:
            heated = True
        else:
            unasked.append(node)

    # 补问一轮：超时发生在「服务端正在重建映射」时，此刻它已经重建完了
    for node in list(unasked):
        if heated and ask(node, patient=False) and node in manifest:
            unasked.remove(node)
    return manifest


def _prompt_accepted(url: str, prompt_id: str, timeout: float = 8) -> bool:
    """查 history 与 queue，确认任务是否已被 ComfyUI 接收（响应丢失后的救回判定）。"""
    for path in (f"/history/{prompt_id}", "/queue"):
        try:
            with urlopen(_base(url) + path, timeout=timeout) as r:
                data = json.loads(r.read())
        except Exception as exc:
            _LOG.debug("comfyui_client::_prompt_accepted 兜底异常: %s", exc)
            continue
        if path.startswith("/history"):
            if data.get(prompt_id):
                return True
        else:
            ids = _queue_prompt_ids(data, "queue_running") + _queue_prompt_ids(data, "queue_pending")
            if prompt_id in ids:
                return True
    return False


# 提交响应超时后的**接单宽限窗口**（2026-10-20 实锤）。
# 旧实现只在超时那一刻查一次队列：大型工作流此刻还在 ComfyUI 里做节点校验/模型加载，
# 尚未进 queue，于是查不到 ⇒ 判「提交失败」并**删掉插画槽**；可 ComfyUI 稍后照常出图
# ⇒ 图生成了却无处回填、也进不了资产库（用户实锤 19:12:18 提交 / 19:13:10 失败，
# 共 52s，ComfyUI 侧确有产出）。改成在窗口内轮询确认，宁可多等几秒也不误杀任务。
PROMPT_ACCEPT_GRACE_SECONDS = 25.0
PROMPT_ACCEPT_POLL_INTERVAL = 2.5
PROMPT_ACCEPT_PROBE_TIMEOUT = 3.0


def _await_prompt_accepted(url: str, prompt_id: str) -> bool:
    """在宽限窗口内反复确认任务是否已被接收；命中即返回 True。

    探测用短超时（3s）而不是 8s：窗口内要多轮探测，单轮太慢会吃掉整个窗口。
    """
    started = time.monotonic()
    while True:
        if _prompt_accepted(url, prompt_id, timeout=PROMPT_ACCEPT_PROBE_TIMEOUT):
            return True
        if time.monotonic() - started >= PROMPT_ACCEPT_GRACE_SECONDS:
            return False
        time.sleep(PROMPT_ACCEPT_POLL_INTERVAL)


def submit_prompt(url: str, api: dict, client_id: str = "") -> str | None:
    """POST /prompt，返回 prompt_id。HTTPError 透出 ComfyUI 校验详情。

    客户端预生成 prompt_id 随请求带上：响应超时 ≠ 提交失败——大型工作流冷启动时
    节点校验/模型加载可能慢于客户端超时，请求已到 ComfyUI 却在返回 prompt_id 前
    被掐断。此时查队列与历史确认任务是否已接收，在则救回 prompt_id，避免前端放弃
    一个实际正在生成的任务（2026-08-29 验收「提交了任务、ComfyUI 出了图、对话无回填」）。
    """
    prompt_id = str(uuid.uuid4())  # ComfyUI 校验 prompt_id 必须是标准带横线 UUID
    payload: dict[str, object] = {"prompt": api, "prompt_id": prompt_id}
    if client_id:
        payload["client_id"] = client_id
    body = json.dumps(payload).encode("utf-8")
    rq = Request(_base(url) + "/prompt", data=body,
                 headers={"Content-Type": "application/json"})
    try:
        with urlopen(rq, timeout=30) as r:
            res = json.loads(r.read())
        return res.get("prompt_id") or prompt_id
    except HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception as exc:
            _LOG.debug("comfyui_client::submit_prompt 兜底异常: %s", exc)
            detail = str(e)
        raise ComfyError(detail, 500)
    except Exception as e:
        if _await_prompt_accepted(url, prompt_id):
            return prompt_id
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
                # 仍在排队，节点尚未开始加载（无动弹）。
                # 第 59 刀（队列感知停顿）：附队列活性 —— running 非空 ⇒ 队列在推进，
                # 排队是合法等待；前端停顿守卫据此豁免，不再误杀深队列任务。
                return {"status": "pending", "queue_busy": bool(running),
                        "images": [], "videos": [], "audios": [], "texts": []}
            # 不在历史也不在队列：任务已丢失（ComfyUI 重启等原因）
            return {"status": "not_found", "images": [], "videos": [], "audios": [], "texts": []}
        except Exception as exc:
            _LOG.debug("comfyui_client::fetch_result 兜底异常: %s", exc)
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


def fetch_view_to_file(url: str, filename: str, dest: str, type: str = "output",
                       subfolder: str = "", timeout: float = 120) -> str:
    """流式把 /view 产物写进 `dest`，返回 Content-Type。

    云端视频等大产物（数百 MB）不能整块读进内存；`timeout` 是**单次 socket 读**的
    上限（长时间无数据才判超时，持续来流不受总时长限制），默认给云端留足余量。
    失败抛 ComfyError；`dest` 由调用方负责清理。
    """
    base = _base(url)  # 白名单/协议错误按 400 透出，别被下面的兜底吞成 502
    qs = urlencode({"filename": filename, "type": type, "subfolder": subfolder})
    try:
        with urlopen(base + f"/view?{qs}", timeout=timeout) as r, open(dest, "wb") as f:
            ctype = r.headers.get("Content-Type", "application/octet-stream")
            shutil.copyfileobj(r, f, 1024 * 1024)
            return ctype
    except Exception as e:
        raise ComfyError(f"取图失败：{e}", 502) from e


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
    except Exception as exc:
        _LOG.debug("comfyui_client::probe_view 兜底异常: %s", exc)
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
        except Exception:  # noqa: BLE001 [EXC-保留] 取消路径 best-effort：ComfyUI 未起/已完成均不报错
            pass
    try:
        _DIRECT_SESSION.post(base + "/interrupt", timeout=5)
        interrupted = True
    except Exception:  # noqa: BLE001 [EXC-保留] 取消路径 best-effort：ComfyUI 未起/已完成均不报错
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
