"""云端生图：OpenAI 兼容 images/generations。

httpx 直调（不依赖 langchain-community 的 DallEAPIWrapper，更可控）。
设置里的「生图模型」(imageModels) 透传 base_url/api_key/model。
返回图片地址：优先直链 URL；若接口只回 b64_json 则拼成 data URI。
trust_env=False 规避本地系统代理劫持 127.0.0.1 的坑（与 rag_store 一致）。
"""
import logging
import re
import time
import uuid

import httpx


_LOG = logging.getLogger(__name__)
_QUALITIES = {"auto", "low", "medium", "high"}
_SIZE_MIN = 64
_SIZE_MAX = 3840
_SIZE_STEP = 16


class UpstreamDeliveryUnknown(RuntimeError):
    """请求体已开始交付，但没有收到上游响应，无法确认任务是否创建。"""


class UpstreamRequestNotSent(RuntimeError):
    """连接阶段失败，可以确认请求没有发送到上游。"""


def _validated_size(size: str) -> str:
    match = re.fullmatch(r"(\d+)x(\d+)", (size or "").strip().lower())
    if not match:
        raise ValueError("图片尺寸必须使用 宽x高 格式")
    width, height = (int(value) for value in match.groups())
    if not (_SIZE_MIN <= width <= _SIZE_MAX and _SIZE_MIN <= height <= _SIZE_MAX):
        raise ValueError(f"图片宽高必须在 {_SIZE_MIN}–{_SIZE_MAX}px 之间")
    width = ((width + _SIZE_STEP // 2) // _SIZE_STEP) * _SIZE_STEP
    height = ((height + _SIZE_STEP // 2) // _SIZE_STEP) * _SIZE_STEP
    return f"{width}x{height}"


def _request_timeout(size: str) -> httpx.Timeout:
    try:
        longest = max(int(part) for part in (size or "").lower().split("x"))
    except (TypeError, ValueError):
        longest = 1024
    read_seconds = 900 if longest >= 3840 else 300
    return httpx.Timeout(connect=20, write=120, read=read_seconds, pool=20)


def _timeout_error(exc: httpx.TimeoutException, request_id: str, *, has_upload: bool) -> RuntimeError:
    if isinstance(exc, httpx.ConnectTimeout):
        return UpstreamRequestNotSent(
            f"连接上游生成服务超时，请求未发送（request_id={request_id}）"
        )
    if isinstance(exc, httpx.WriteTimeout):
        phase = "上传参考图" if has_upload else "上传请求"
        return UpstreamDeliveryUnknown(
            f"上游交付状态未知：{phase}超时，无法确认是否创建任务（request_id={request_id}）"
        )
    return UpstreamDeliveryUnknown(
        f"上游交付状态未知：请求体已发送，但未收到上游响应，无法确认是否创建任务"
        f"（request_id={request_id}）"
    )


def _raise_http_error(response, label: str, url: str, request_id: str) -> None:
    detail = response.text[:300]
    if response.status_code == 504:
        raise UpstreamDeliveryUnknown(
            f"上游交付状态未知：{label} 504，网关报告超时且未返回任务编号，"
            f"无法确认是否创建任务（request_id={request_id}）：{detail}"
        )
    raise RuntimeError(f"{label} {response.status_code}：{detail}（请求地址 {url}）")


def supports_quality(model: str) -> bool:
    """未知兼容模型默认不传 quality；只对白名单 GPT Image 家族启用。"""
    return "gpt-image" in (model or "").strip().lower()


def _quality_payload(model: str, quality: str) -> dict[str, str]:
    if not supports_quality(model) or quality == "auto":
        return {}
    selected = quality if quality in _QUALITIES else "high"
    return {"quality": selected}


def _norm_url(base_url: str) -> str:
    url = (base_url or "").rstrip("/")
    if "/images/generations" in url:
        return url
    if not url.endswith("/v1"):
        url += "/v1"
    return url + "/images/generations"


def _norm_edits_url(base_url: str) -> str:
    url = (base_url or "").rstrip("/")
    if "/images/edits" in url:
        return url
    if "/images/generations" in url:  # 用户填的是生成地址，换成编辑地址
        return url.replace("/images/generations", "/images/edits")
    if not url.endswith("/v1"):
        url += "/v1"
    return url + "/images/edits"


def _load_image_bytes(img: str) -> tuple[bytes, str, str]:
    """把 data URI / http(s) URL 图片读成 (字节, 文件名, mime)。图生图上传用。"""
    import base64
    import re
    if img.startswith("data:"):
        header, b64 = img.split(",", 1)
        data = base64.b64decode(re.sub(r"\s+", "", b64))
        mime = header.split(":", 1)[1].split(";")[0] if ":" in header else "image/png"
    else:
        _max = 30 * 1024 * 1024  # 30MB 上限，防超大直链读爆内存
        with httpx.Client(trust_env=False, timeout=120) as c:  # 规避本地代理劫持
            with c.stream("GET", img) as r:
                r.raise_for_status()
                mime = r.headers.get("content-type", "image/png").split(";")[0]
                chunks: list[bytes] = []
                total = 0
                for chunk in r.iter_bytes():
                    total += len(chunk)
                    if total > _max:
                        raise ValueError("图片超过 30MB 上限，已中止下载")
                    chunks.append(chunk)
                data = b"".join(chunks)
    ext = (mime.split("/")[1] if "/" in mime else "png") or "png"
    return data, f"image.{ext}", mime


def generate(base_url: str, api_key: str, model: str, prompt: str,
             size: str = "1024x1024", quality: str = "high") -> str:
    """纯文生图，返回可展示地址（http URL 或 data:image/...;base64,...）。

    失败抛异常，由调用方（工具/路由）捕获转成错误文本。
    """
    if not base_url or not model:
        raise ValueError("未配置生图模型（设置 → 生图模型：API URL / 模型名）")
    size = _validated_size(size)
    url = _norm_url(base_url)
    request_id = uuid.uuid4().hex
    headers = {"Authorization": f"Bearer {api_key or 'not-needed'}",
               "Content-Type": "application/json", "X-Request-ID": request_id}
    payload = {"model": model, "prompt": prompt, "n": 1, "size": size,
               "moderation": "low", **_quality_payload(model, quality)}
    started = time.monotonic()
    _LOG.info("image request start request_id=%s endpoint=generations model=%s size=%s quality=%s",
              request_id, model, size, quality)
    with httpx.Client(trust_env=False, timeout=_request_timeout(size)) as c:
        try:
            r = c.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            _LOG.warning("image request timeout request_id=%s endpoint=generations phase=%s elapsed=%.1fs",
                         request_id, type(exc).__name__, time.monotonic() - started)
            raise _timeout_error(exc, request_id, has_upload=False) from exc
        except httpx.ConnectError as exc:
            _LOG.warning("image request connect error request_id=%s endpoint=generations elapsed=%.1fs",
                         request_id, time.monotonic() - started)
            raise UpstreamRequestNotSent(
                f"连接上游生成服务失败，请求未发送（request_id={request_id}）"
            ) from exc
        _LOG.info("image response request_id=%s endpoint=generations status=%s elapsed=%.1fs",
                  request_id, r.status_code, time.monotonic() - started)
        if r.status_code >= 400:
            _raise_http_error(r, "生图接口", url, request_id)
        data = r.json()
    items = data.get("data") or []
    if not items:
        raise RuntimeError(f"生图接口未返回图片：{str(data)[:200]}")
    first = items[0] or {}
    if first.get("url"):
        return first["url"]
    b64 = first.get("b64_json")
    if b64:
        return f"data:image/png;base64,{b64}"
    raise RuntimeError(f"生图返回无 url/b64_json：{str(first)[:200]}")


def generate_with_images(base_url: str, api_key: str, model: str, prompt: str,
                         images: list[str], size: str = "1024x1024",
                         quality: str = "high", mask: str | None = None) -> str:
    """图生图：把提示词 + 一张或多张参考图交给 images/edits 接口，返回图片地址。

    走 OpenAI 官方 multipart/form-data，多图用同名 image[] 字段全部上传；
    原生局部编辑时把独立 Alpha 蒙版放在 mask 字段，透明区域表示允许修改。
    失败抛异常，由调用方（工具）捕获转成错误文本。
    """
    if not base_url or not model:
        raise ValueError("未配置生图模型（设置 → 生图模型：API URL / 模型名）")
    if not images:
        raise ValueError("图生图需要至少一张参考图")
    size = _validated_size(size)
    url = _norm_edits_url(base_url)
    request_id = uuid.uuid4().hex
    headers = {"Authorization": f"Bearer {api_key or 'not-needed'}",
               "X-Request-ID": request_id}  # multipart 不设 Content-Type
    files = []
    upload_bytes = 0
    for img in images:
        data, name, mime = _load_image_bytes(img)
        upload_bytes += len(data)
        files.append(("image[]", (name, data, mime)))
    if mask:
        mask_data, mask_name, mask_mime = _load_image_bytes(mask)
        upload_bytes += len(mask_data)
        files.append(("mask", (f"mask_{mask_name}", mask_data, mask_mime)))
    payload = {"model": model, "prompt": prompt, "n": "1", "size": size,
               "moderation": "low", **_quality_payload(model, quality)}
    started = time.monotonic()
    _LOG.info(
        "image request start request_id=%s endpoint=edits model=%s size=%s quality=%s images=%d mask=%s bytes=%d",
        request_id, model, size, quality, len(images), bool(mask), upload_bytes,
    )
    with httpx.Client(trust_env=False, timeout=_request_timeout(size)) as c:
        try:
            r = c.post(url, headers=headers, data=payload, files=files)
        except httpx.TimeoutException as exc:
            _LOG.warning("image request timeout request_id=%s endpoint=edits phase=%s elapsed=%.1fs",
                         request_id, type(exc).__name__, time.monotonic() - started)
            raise _timeout_error(exc, request_id, has_upload=True) from exc
        except httpx.ConnectError as exc:
            _LOG.warning("image request connect error request_id=%s endpoint=edits elapsed=%.1fs",
                         request_id, time.monotonic() - started)
            raise UpstreamRequestNotSent(
                f"连接上游生成服务失败，请求未发送（request_id={request_id}）"
            ) from exc
        _LOG.info("image response request_id=%s endpoint=edits status=%s elapsed=%.1fs",
                  request_id, r.status_code, time.monotonic() - started)
        if r.status_code >= 400:
            _raise_http_error(r, "图生图接口", url, request_id)
        data = r.json()
    items = data.get("data") or []
    if not items:
        raise RuntimeError(f"图生图接口未返回图片：{str(data)[:200]}")
    first = items[0] or {}
    if first.get("url"):
        return first["url"]
    b64 = first.get("b64_json")
    if b64:
        return f"data:image/png;base64,{b64}"
    raise RuntimeError(f"图生图返回无 url/b64_json：{str(first)[:200]}")
