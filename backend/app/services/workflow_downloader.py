"""工作流模板下载：把 Civitai 等站点的工作流文件下到「默认工作流文件夹」，之后即可当骨架底座。

复用 model_downloader 的任务表(_TASKS)与代理规则(trust_env=False + 显式 proxy)，
但落盘目标是工作流目录、接受 .json / .zip：
- .json 直接落盘；
- .zip 解出其中所有 .json（Civitai 工作流常打包成 zip），其余忽略。
下载文件名去重不覆盖已有（保护用户/骨架文件），撞名自动加序号。
"""
from __future__ import annotations

import io
import re
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from app.services import model_downloader as _md
from app.services.pathnames import safe_seg

ALLOWED_HOSTS = {"huggingface.co", "civitai.com"}


def _safe_json_name(name: str) -> str:
    """清洗成安全 .json 文件名。"""
    name = unquote(name or "").replace("\\", "/").split("/")[-1]
    stem = safe_seg(name.rsplit(".", 1)[0], f"workflow_{uuid.uuid4().hex[:8]}")
    return f"{stem}.json"


def _unique_path(target_dir: Path, fname: str) -> Path:
    """撞名不覆盖：a.json 存在则 a_1.json、a_2.json…（保护现有骨架/用户文件）。"""
    dest = target_dir / fname
    if not dest.exists():
        return dest
    stem, ext = fname.rsplit(".", 1)
    i = 1
    while (target_dir / f"{stem}_{i}.{ext}").exists():
        i += 1
    return target_dir / f"{stem}_{i}.{ext}"


def _save_json_bytes(target_dir: Path, fname: str, data: bytes) -> str:
    """校验是合法 JSON 再落盘（去半截/非工作流文件）。返回落盘文件名。"""
    import json
    json.loads(data.decode("utf-8"))  # 非法 JSON 抛错，不落盘
    dest = _unique_path(target_dir, _safe_json_name(fname))
    dest.write_bytes(data)
    return dest.name


def _extract_from_zip(target_dir: Path, raw: bytes) -> list[str]:
    """从 zip 里解出所有 .json 落盘，返回落盘文件名列表。"""
    saved: list[str] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for info in z.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".json"):
                continue
            try:
                saved.append(_save_json_bytes(target_dir, info.filename, z.read(info)))
            except (ValueError, UnicodeDecodeError):
                continue  # 非工作流 json（配置等）跳过
    return saved


def _filename_from_headers(resp: httpx.Response, fallback: str) -> str:
    cd = resp.headers.get("content-disposition", "")
    m = re.search(r'filename="?([^"\r\n;]+)"?', cd)
    if m:
        return unquote(m.group(1)).split("/")[-1]
    return fallback or urlparse(str(resp.url)).path.split("/")[-1] or "workflow"


def _download(task_id: str, url: str, workflow_dir: str, civitai_token: str, proxy: str) -> None:
    """后台下载工作流文件到 workflow_dir。.json 直落，.zip 抽 json。"""
    try:
        target = Path(workflow_dir)
        target.mkdir(parents=True, exist_ok=True)
        u = url
        if civitai_token and "civitai.com" in u and "token=" not in u:
            u += ("&" if "?" in u else "?") + f"token={civitai_token}"
        _md._set(task_id, status="downloading", downloaded=0, total=0, error="")
        cli_kw: dict = {"trust_env": False, "follow_redirects": True, "timeout": None}
        if proxy and proxy.strip():
            cli_kw["proxy"] = proxy.strip()
        with httpx.Client(**cli_kw) as c:
            r = c.get(u)
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}（可能需要 token 或链接失效）")
            raw = r.content
            fname = _filename_from_headers(r, "")
            _md._set(task_id, filename=fname, total=len(raw), downloaded=len(raw))
            lower = fname.lower()
            ct = r.headers.get("content-type", "").lower()
            if lower.endswith(".zip") or "zip" in ct:
                saved = _extract_from_zip(target, raw)
            else:
                saved = [_save_json_bytes(target, fname or "workflow.json", raw)]
        if not saved:
            raise RuntimeError("下载内容里没有可用的工作流 .json")
        _md._set(task_id, status="done", filename="、".join(saved))
    except Exception as e:  # noqa: BLE001
        _md._set(task_id, status="error", error=str(e))


def start_download(url: str, workflow_dir: str, name: str = "",
                   civitai_token: str = "", proxy: str = "") -> str:
    """启动后台工作流下载，返回 task_id（进度走 model_downloader 的共享任务面板）。"""
    if not workflow_dir:
        raise ValueError("未配置默认工作流文件夹（设置 → 路径）")
    host = (urlparse(url).hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"仅支持 huggingface.co / civitai.com，收到：{host or '无效URL'}")
    import threading
    task_id = uuid.uuid4().hex
    disp = (name or "").strip() or (urlparse(url).path.split("/")[-1] or url)
    _md._set(task_id, status="pending", downloaded=0, total=0, filename="", error="",
             name=disp, model_type="工作流模板", created=time.time())
    threading.Thread(target=_download, args=(task_id, url, workflow_dir, civitai_token, proxy),
                     daemon=True).start()
    return task_id
