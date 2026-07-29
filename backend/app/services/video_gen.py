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
    url = (base_url or "").rstrip("/")
    if "/video/generations" in url or "/videos/generations" in url:
        return url
    if not url.endswith("/v1"):
        url += "/v1"
    return url + "/video/generations"


def _norm_task_url(base_url: str, task_id: str) -> str:
    """轮询任务状态地址：<base>/v1/video/generations/<id>。"""
    url = (base_url or "").rstrip("/")
    # 若填的是提交地址，取其目录作为任务基址
    for tail in ("/video/generations", "/videos/generations"):
        if url.endswith(tail):
            return f"{url}/{task_id}"
    if not url.endswith("/v1"):
        url += "/v1"
    return f"{url}/video/generations/{task_id}"


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


def generate(base_url: str, api_key: str, model: str, prompt: str,
             size: str = "1024x1024") -> str:
    """文生视频，返回可展示地址（http URL 或 data:video/...;base64,...）。

    异步接口：提交拿 task_id，轮询状态直到成功取视频 URL；
    同步接口：提交直接回视频 URL。失败抛异常，由调用方（工具/路由）捕获转错误文本。
    """
    if not base_url or not model:
        raise ValueError("未配置视频模型（设置 → 视频模型：API URL / 模型名）")
    url = _norm_url(base_url)
    headers = {"Authorization": f"Bearer {api_key or 'not-needed'}",
               "Content-Type": "application/json"}
    payload = {"model": model, "prompt": prompt, "size": size}
    with httpx.Client(trust_env=False, timeout=300) as c:
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
