"""模型下载：从 HuggingFace / Civitai 下载到 ComfyUI models 目录。

ComfyUI 原生扫描 models/<type>/，下载到对应子目录即可被识别，无需额外配置。
安全：仅允许 huggingface.co / civitai.com；文件名清洗防路径穿越；token 仅后端使用。
"""
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse, unquote

import httpx

from app.services.pathnames import safe_seg

# model_type -> ComfyUI models 子目录（对齐 ComfyUI 原生 models 目录结构）
TYPE_DIRS = {
    "checkpoint": "checkpoints",
    "lora": "loras",
    "vae": "vae",
    "controlnet": "controlnet",
    "embedding": "embeddings",
    "upscale": "upscale_models",
    "clip": "clip",
    "clip_vision": "clip_vision",
    "text_encoder": "text_encoders",
    "diffusion_model": "diffusion_models",
    "unet": "diffusion_models",
    "style_model": "style_models",
    "hypernetwork": "hypernetworks",
    "ipadapter": "ipadapter",
    "gligen": "gligen",
    "ultralytics": "ultralytics",
    "sam": "sams",
    "vae_approx": "vae_approx",
    "photomaker": "photomaker",
    "diffuser": "diffusers",
    "audio_encoder": "audio_encoders",
    "other": "checkpoints",
}

ALLOWED_HOSTS = {"huggingface.co", "civitai.com"}

# 下载任务进度表：task_id -> {status, downloaded, total, filename, error}
_TASKS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _set(task_id: str, **kw) -> None:
    with _LOCK:
        _TASKS.setdefault(task_id, {}).update(kw)


def get_status(task_id: str) -> dict:
    with _LOCK:
        return dict(_TASKS.get(task_id, {"status": "unknown"}))


def list_tasks() -> list[dict]:
    """全部下载任务（供跨 tab 共享的下载面板轮询）。按创建时间倒序，最近在前。"""
    with _LOCK:
        items = [dict(id=tid, **t) for tid, t in _TASKS.items()]
    items.sort(key=lambda x: x.get("created", 0), reverse=True)
    return items


def _safe_name(name: str) -> str:
    """清洗文件名：去路径分隔与 ..，只留基名，防穿越。"""
    name = unquote(name or "").replace("\\", "/").split("/")[-1]
    return safe_seg(name, f"model_{uuid.uuid4().hex[:8]}")


def parse_url(url: str, civitai_token: str = "") -> tuple[str, str]:
    """归一成直链 + 推断文件名。仅放行白名单域名。

    返回 (direct_url, filename_hint)。filename_hint 可能为空（下载时再从响应头取）。
    """
    host = (urlparse(url).hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"仅支持 huggingface.co / civitai.com，收到：{host or '无效URL'}")

    if host == "huggingface.co":
        # blob 链转 resolve 直链；resolve 链原样
        u = url.replace("/blob/", "/resolve/")
        fname = _safe_name(urlparse(u).path)
        return u, fname

    # civitai：下载 API 原样；模型页则交给下载时跟随重定向，文件名从响应头取
    u = url
    if civitai_token and "token=" not in u:
        sep = "&" if "?" in u else "?"
        u = f"{u}{sep}token={civitai_token}"
    return u, ""


def _resolve_dir(comfy_models_dir: str, model_type: str) -> Path:
    sub = TYPE_DIRS.get(model_type)
    if sub is None:
        raise ValueError(f"未知模型类型：{model_type}")
    d = Path(comfy_models_dir) / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def _filename_from_headers(resp: httpx.Response, fallback: str) -> str:
    cd = resp.headers.get("content-disposition", "")
    m = re.search(r'filename="?([^"\r\n;]+)"?', cd)
    if m:
        return _safe_name(m.group(1))
    return _safe_name(fallback or urlparse(str(resp.url)).path)


def _download(task_id: str, url: str, comfy_models_dir: str, model_type: str,
              hf_token: str, civitai_token: str, proxy: str = "") -> None:
    """实际下载（在后台线程跑）。流式写 .part，完成原子重命名。"""
    try:
        direct, fname_hint = parse_url(url, civitai_token)
        target_dir = _resolve_dir(comfy_models_dir, model_type)
        headers = {}
        if "huggingface.co" in direct and hf_token:
            headers["Authorization"] = f"Bearer {hf_token}"
        _set(task_id, status="downloading", downloaded=0, total=0, filename="", error="")
        # 外网下载必须走代理（huggingface.co/civitai.com 常被墙）；trust_env=False 不读系统环境，
        # 只用显式 proxy——与访问 127.0.0.1 本地服务的 trust_env=False 规则一致但目的相反。
        cli_kw: dict = {"trust_env": False, "follow_redirects": True, "timeout": None}
        if proxy and proxy.strip():
            cli_kw["proxy"] = proxy.strip()
        with httpx.Client(**cli_kw) as c:
            with c.stream("GET", direct, headers=headers) as r:
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}（可能需要 token 或链接失效）")
                fname = _filename_from_headers(r, fname_hint)
                total = int(r.headers.get("content-length", 0) or 0)
                _set(task_id, filename=fname, total=total)
                part = target_dir / (fname + ".part")
                done = 0
                with open(part, "wb") as f:
                    for chunk in r.iter_bytes(1024 * 256):
                        f.write(chunk)
                        done += len(chunk)
                        _set(task_id, downloaded=done)
                part.replace(target_dir / fname)  # 原子重命名，避免半截文件被 ComfyUI 读到
        _set(task_id, status="done")
    except Exception as e:
        _set(task_id, status="error", error=str(e))


def start_download(url: str, comfy_models_dir: str, model_type: str,
                   hf_token: str = "", civitai_token: str = "", name: str = "",
                   proxy: str = "") -> str:
    """启动后台下载，立即返回 task_id。先做同步校验，校验失败直接抛。
    name 为展示名（各 tab 传模型名），供下载面板显示；缺省用 URL 尾段兜底。
    proxy 为外网代理（前端从设置透传，空=直连）。"""
    if not comfy_models_dir:
        raise ValueError("未配置模型目录")
    parse_url(url, civitai_token)  # 同步校验域名/类型，失败早返回
    if model_type not in TYPE_DIRS:
        raise ValueError(f"未知模型类型：{model_type}")
    task_id = uuid.uuid4().hex
    disp = (name or "").strip() or _safe_name(urlparse(url).path) or url
    _set(task_id, status="pending", downloaded=0, total=0, filename="", error="",
         name=disp, model_type=model_type, created=time.time())
    t = threading.Thread(
        target=_download,
        args=(task_id, url, comfy_models_dir, model_type, hf_token, civitai_token, proxy),
        daemon=True,
    )
    t.start()
    return task_id


def fetch_info(url: str, hf_token: str = "", civitai_token: str = "", proxy: str = "") -> dict:
    """输入链接，拉取模型预览图 + 介绍（下载前预览）。

    返回 {name, description, images:[url...], download_url}。仅白名单域名。
    proxy 为外网代理（前端从设置透传，空=直连）。
    """
    host = (urlparse(url).hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"仅支持 huggingface.co / civitai.com，收到：{host or '无效URL'}")
    cli_kw: dict = {"trust_env": False, "follow_redirects": True, "timeout": 30}
    if proxy and proxy.strip():
        cli_kw["proxy"] = proxy.strip()
    with httpx.Client(**cli_kw) as c:
        if host == "civitai.com":
            return _civitai_info(c, url, civitai_token)
        return _hf_info(c, url, hf_token)


def _civitai_info(c: httpx.Client, url: str, token: str) -> dict:
    # 从链接里抽 modelId / modelVersionId
    m = re.search(r"/models/(\d+)", url)
    vm = re.search(r"modelVersionId=(\d+)", url)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if vm:
        r = c.get(f"https://civitai.com/api/v1/model-versions/{vm.group(1)}", headers=headers)
        r.raise_for_status()
        v = r.json()
        imgs = [i.get("url", "") for i in v.get("images", []) if i.get("url")]
        files = v.get("files", [])
        dl = files[0].get("downloadUrl", "") if files else v.get("downloadUrl", "")
        return {"name": v.get("name", ""), "description": v.get("description", "") or "",
                "images": imgs, "download_url": dl}
    if m:
        r = c.get(f"https://civitai.com/api/v1/models/{m.group(1)}", headers=headers)
        r.raise_for_status()
        d = r.json()
        versions = d.get("modelVersions", [])
        v0 = versions[0] if versions else {}
        imgs = [i.get("url", "") for i in v0.get("images", []) if i.get("url")]
        files = v0.get("files", [])
        dl = files[0].get("downloadUrl", "") if files else ""
        return {"name": d.get("name", ""), "description": d.get("description", "") or "",
                "images": imgs, "download_url": dl}
    raise ValueError("无法从链接解析 Civitai 模型 ID")


def _hf_info(c: httpx.Client, url: str, token: str) -> dict:
    # 从链接抽 repo（org/name）
    m = re.search(r"huggingface\.co/([^/]+/[^/]+)", url)
    if not m:
        raise ValueError("无法从链接解析 HuggingFace 仓库")
    repo = m.group(1)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = c.get(f"https://huggingface.co/api/models/{repo}", headers=headers)
    r.raise_for_status()
    d = r.json()
    desc = d.get("cardData", {}).get("model-index") and "" or ""
    # HF 无统一描述字段，取 README 首段
    try:
        rd = c.get(f"https://huggingface.co/{repo}/raw/main/README.md", headers=headers)
        if rd.status_code == 200:
            desc = re.sub(r"^---[\s\S]*?---\s*", "", rd.text).strip()[:2000]
    except Exception:
        pass
    return {"name": repo, "description": desc, "images": [], "download_url": url}
