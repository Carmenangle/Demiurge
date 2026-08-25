"""云端生视频：OpenAI 兼容 video/generations（异步任务→轮询取视频）。

与 image_gen 对齐：设置里的「视频模型」(videoModels) 透传 base_url/api_key/model。
发送参数形态参照 image_gen：
- 文生视频 generate：JSON payload（{model, prompt, size}）。
- 图生视频 generate_with_images：multipart/form-data，image[] 同名多图
  （图片用 image_gen.load_image_bytes 读字节上传，与图生图完全一致）。
视频接口多为异步：提交返回 task/job id，再轮询状态直到拿到视频 URL；
若接口同步直接返回 url（或 b64）也兼容。返回可展示的视频地址。
trust_env=False 规避本地系统代理劫持 127.0.0.1 的坑（与 image_gen 一致）。
"""
import time

import httpx

from app.services.image_gen import load_image_bytes

# 轮询上限：视频生成普遍较慢，最长约 5 分钟（60 次 * 5 秒）。
_POLL_INTERVAL = 5.0
_POLL_MAX_TRIES = 60


def _norm_url(base_url: str) -> str:
    """视频生成端点：URL 由用户决定，代码不猜版本/单复数——原样使用。

    不同 Provider 布局各异（/v1/video/generations、/v2/videos/generations、
    自定义路径、seedance 这类 /v1 根形态等），代码不做拼接猜测；
    用户填什么就是最终提交地址。空串返回空串，由 generate 统一报「未配置」。
    """
    return (base_url or "").strip().rstrip("/")


def _norm_task_url(base_url: str, task_id: str) -> str:
    """轮询任务状态地址：用户填的端点 + /<id>（OpenAI 兼容异步任务约定）。"""
    url = (base_url or "").strip().rstrip("/")
    return f"{url}/{task_id}"


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


def _submit_and_resolve(client: httpx.Client, url: str, headers: dict, **kwargs) -> str:
    """提交请求（json= 或 data=+files=），同步直返或异步轮询取视频地址。

    失败抛异常，由调用方（工具/路由）捕获转成错误文本。
    """
    r = client.post(url, headers=headers, **kwargs)
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
    task_url = _norm_task_url(url, task_id)
    for _ in range(_POLL_MAX_TRIES):
        time.sleep(_POLL_INTERVAL)
        pr = client.get(task_url, headers=headers)
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


def generate(base_url: str, api_key: str, model: str, prompt: str,
             size: str = "1024x1024", proxy: str = "") -> str:
    """文生视频：JSON payload（{model, prompt, size}）提交，返回视频地址。

    异步接口：提交拿 task_id，轮询状态直到成功取视频 URL；
    同步接口：提交直接回视频 URL。
    """
    if not base_url or not model:
        raise ValueError("未配置视频模型（设置 → 视频模型：API URL / 模型名）")
    url = _norm_url(base_url)
    headers = {"Authorization": f"Bearer {api_key or 'not-needed'}",
               "Content-Type": "application/json"}
    payload = {"model": model, "prompt": prompt, "size": size}
    client_kwargs = {"trust_env": False, "timeout": 300}
    if proxy:
        client_kwargs["proxy"] = proxy
    with httpx.Client(**client_kwargs) as c:
        return _submit_and_resolve(c, url, headers, json=payload)


def generate_with_images(base_url: str, api_key: str, model: str, prompt: str,
                         images: list[str], size: str = "1024x1024",
                         proxy: str = "") -> str:
    """图生视频：multipart/form-data 提交，image[] 同名多图（参照 image_gen.generate_with_images）。

    images 为参考图（data URI / URL / 本地路径），统一读字节上传；本地回环地址
    （127.0.0.1 的 local-view）绕过代理直读（Clash 等无法转发 localhost，会打成 502）。
    """
    if not base_url or not model:
        raise ValueError("未配置视频模型（设置 → 视频模型：API URL / 模型名）")
    if not images:
        raise ValueError("图生视频需要至少一张参考图")
    url = _norm_url(base_url)
    headers = {"Authorization": f"Bearer {api_key or 'not-needed'}"}  # multipart 不设 Content-Type
    files = []
    for img in images:
        data, name, mime = load_image_bytes(img, proxy) if proxy else load_image_bytes(img)
        files.append(("image[]", (name, data, mime)))
    payload = {"model": model, "prompt": prompt, "size": size}
    client_kwargs = {"trust_env": False, "timeout": 300}
    if proxy:
        client_kwargs["proxy"] = proxy
    with httpx.Client(**client_kwargs) as c:
        return _submit_and_resolve(c, url, headers, data=payload, files=files)
