"""云端生视频：OpenAI 兼容 video/generations（异步任务→轮询取视频）。

与 image_gen 对齐：设置里的「视频模型」(videoModels) 透传 base_url/api_key/model。
视频接口多为异步：提交返回 task/job id，再轮询状态直到拿到视频 URL；
若接口同步直接返回 url（或 b64）也兼容。返回可展示的视频地址。
trust_env=False 规避本地系统代理劫持 127.0.0.1 的坑（与 image_gen 一致）。
"""
import time

import httpx

# 轮询上限：视频生成普遍较慢，最长约 5 分钟（60 次 * 5 秒）。
_POLL_INTERVAL = 5.0
_POLL_MAX_TRIES = 60


def _norm_url(base_url: str) -> str:
    """归一视频生成端点。适用于所有 OpenAI 兼容形态：
    填完整端点（含 /v1/video/generations、/v2/videos/generations 等）→ 原样使用；
    填带版本前缀（…/v1、…/v2）→ 拼对应端点；
    填纯站点根 → 默认 v1 布局（向后兼容），报错时提示填完整端点最稳。"""
    url = (base_url or "").strip().rstrip("/")
    low = url.lower()
    if "/video/generations" in low or "/videos/generations" in low:
        return url
    if low.endswith("/v1"):
        return url + "/video/generations"
    if low.endswith("/v2"):
        return url + "/videos/generations"
    return url + "/v1/video/generations"


def _norm_task_url(base_url: str, task_id: str) -> str:
    """轮询任务状态地址：<完整端点>/<id>；填根/版本前缀时按 _norm_url 同规则拼。"""
    url = (base_url or "").strip().rstrip("/")
    low = url.lower()
    for tail in ("/video/generations", "/videos/generations"):
        if low.endswith(tail):
            return f"{url}/{task_id}"
    if low.endswith("/v1"):
        return url + "/video/generations/" + task_id
    if low.endswith("/v2"):
        return url + "/videos/generations/" + task_id
    return url + "/v1/video/generations/" + task_id


def _pick_video_url(payload: dict) -> str:
    """从各种返回形态里抽取视频 URL。兼容常见字段名。"""
    if not isinstance(payload, dict):
        return ""
    # 直链常见位置
    for key in ("video_url", "url", "output_url"):
        v = payload.get(key)
        if isinstance(v, str) and v.startswith(("http://", "https://", "data:")):
            return v
    # data: [ {url|video_url|b64_json} ]
    items = payload.get("data") or payload.get("output") or []
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, str) and first.startswith(("http", "data:")):
            return first
        if isinstance(first, dict):
            for key in ("video_url", "url", "b64_json"):
                v = first.get(key)
                if isinstance(v, str) and v:
                    return v if v.startswith(("http", "data:")) else f"data:video/mp4;base64,{v}"
    # 顶层 b64
    b64 = payload.get("b64_json")
    if isinstance(b64, str) and b64:
        return f"data:video/mp4;base64,{b64}"
    return ""


def _status_of(payload: dict) -> str:
    """归一任务状态：succeeded / failed / running。字段名各家不同，尽量兼容。"""
    if not isinstance(payload, dict):
        return "running"
    raw = str(payload.get("status") or payload.get("state") or payload.get("task_status") or "").lower()
    if raw in ("succeeded", "success", "completed", "complete", "done", "finished"):
        return "succeeded"
    if raw in ("failed", "error", "cancelled", "canceled"):
        return "failed"
    # 无状态字段但已能取到视频 URL → 视为完成（同步接口）
    if not raw and _pick_video_url(payload):
        return "succeeded"
    return "running"


def _to_data_uri(image: str, proxy: str = "") -> str:
    """把参考图（data URI / http(s) URL / 本地文件路径）归一为 data URI，供 JSON payload 提交。

    参考图走 JSON 提交（非 multipart），远端服务访问不到本机 127.0.0.1 的 local-view 地址，
    必须内联成 base64。本地回环地址直读不走代理（Clash 等无法转发 localhost，会打成 502）。
    """
    import base64
    import mimetypes
    import os
    import re
    if image.startswith("data:"):
        return image
    if re.match(r"^https?://", image):
        from app.services.url_guard import is_local_view_url
        use_proxy = proxy if not is_local_view_url(image) else ""
        client_kwargs = {"trust_env": False, "timeout": 120}
        if use_proxy:
            client_kwargs["proxy"] = use_proxy
        with httpx.Client(**client_kwargs) as c:
            r = c.get(image)
            r.raise_for_status()
            mime = r.headers.get("content-type", "image/png").split(";")[0]
            return f"data:{mime};base64,{base64.b64encode(r.content).decode()}"
    if os.path.isfile(image):
        mime = mimetypes.guess_type(image)[0] or "image/png"
        with open(image, "rb") as f:
            return f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"
    raise RuntimeError("参考图无法读取（须为 data URI / http(s) URL / 本地文件路径）")


def generate(base_url: str, api_key: str, model: str, prompt: str,
             size: str = "1024x1024", proxy: str = "", image: str | None = None) -> str:
    """生视频，返回可展示地址（http URL 或 data:video/...;base64,...）。

    image 传首帧参考图（data URI / URL / 本地路径）→ 图生视频；不传 → 文生视频。
    参考图字段名按 OpenAI 兼容最常见形态用 `image`；若 Provider 字段名不同，
    改本函数 payload 里的一处键名即可（不同中转站字段差异通常仅此一处）。
    异步接口：提交拿 task_id，轮询状态直到成功取视频 URL；
    同步接口：提交直接回视频 URL。失败抛异常，由调用方（工具/路由）捕获转错误文本。
    """
    if not base_url or not model:
        raise ValueError("未配置视频模型（设置 → 视频模型：API URL / 模型名）")
    url = _norm_url(base_url)
    headers = {"Authorization": f"Bearer {api_key or 'not-needed'}",
               "Content-Type": "application/json"}
    payload: dict = {"model": model, "prompt": prompt, "size": size}
    if image:
        payload["image"] = _to_data_uri(image, proxy)
    client_kwargs = {"trust_env": False, "timeout": 300}
    if proxy:
        client_kwargs["proxy"] = proxy
    with httpx.Client(**client_kwargs) as c:
        r = c.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"生视频接口 {r.status_code}：{r.text[:300]}（请求地址 {url}）")
        data = r.json()

        # 同步直返视频 URL
        direct = _pick_video_url(data)
        if direct and _status_of(data) != "failed":
            return direct

        # 异步：拿 task_id 轮询
        task_id = str(data.get("id") or data.get("task_id") or data.get("request_id") or "")
        if not task_id:
            raise RuntimeError(f"生视频接口未返回视频或任务号：{str(data)[:200]}")
        task_url = _norm_task_url(base_url, task_id)
        for _ in range(_POLL_MAX_TRIES):
            time.sleep(_POLL_INTERVAL)
            pr = c.get(task_url, headers=headers)
            if pr.status_code >= 400:
                raise RuntimeError(f"查询视频任务 {pr.status_code}：{pr.text[:300]}")
            pd = pr.json()
            status = _status_of(pd)
            if status == "succeeded":
                out = _pick_video_url(pd)
                if out:
                    return out
                raise RuntimeError(f"视频任务完成但无视频地址：{str(pd)[:200]}")
            if status == "failed":
                raise RuntimeError(f"视频任务失败：{str(pd)[:200]}")
        raise RuntimeError("视频生成超时（轮询约 5 分钟仍未完成）")
