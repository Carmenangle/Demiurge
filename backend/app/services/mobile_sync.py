"""手机端联机同库：在线同步（从 PC 拉一份资源包并导入本机）。

形态：手机本机后端请求 PC 的 `GET /api/config/pull`（带 PC 令牌、恒不含密钥），
流式落盘后交给 `config_bundle.import_bundle` 合进本机——与「离线资源同步」的导入
是同一条服务，区别只在包从哪来。同步完成后断开联机即回到本机数据，资源留在手机上。

安全边界：
- 只走 `/api/config/pull`（remote_gate 在 `/api/config` DENY 前缀里精确放行的唯一
  子路径），包内绝不含密钥；本机已有密钥不被抹。
- 出站 `trust_env=False`：不走系统代理，避免把 PC 令牌交给代理进程。
- 目标地址只来自本机 `remote_backend.json`（用户填的电脑地址），不接受前端传入。

只在 `LAF_MOBILE_REMOTE=1` 的手机壳里被路由调用；PC 后端不会走到这里。
"""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from app import config
from app.services import config_bundle, mobile_remote

_PULL_PATH = "/api/config/pull"
_CHUNK = 1 << 20
_ACTIVE_STATES = ("downloading", "importing")
_SPACE_MARGIN = 64 << 20  # 导入期要同时容纳 zip 与解包副本，留 64MB 余量

_lock = threading.Lock()
_job: dict[str, Any] = {"state": "idle"}


class SyncError(ValueError):
    """用户可见的同步错误（路由层转 400）。"""


class SyncBusy(SyncError):
    """已有任务在跑（路由层转 409）。"""


def status() -> dict[str, Any]:
    with _lock:
        return dict(_job)


def start(*, overwrite: bool = False) -> dict[str, Any]:
    with _lock:
        if _job.get("state") in _ACTIVE_STATES:
            raise SyncBusy("已有同步任务在进行中")
        cfg = mobile_remote.load_config()
        if not mobile_remote.is_active(cfg):
            raise SyncError("尚未启用联机同库：先在面板里保存并连接电脑")
        _job.clear()
        _job.update({
            "state": "downloading",
            "stage": "正在从电脑打包并下载…",
            "bytes": 0,
            "total": 0,
            "overwrite": bool(overwrite),
            "result": None,
            "error": "",
            "started_at": time.time(),
            "finished_at": None,
        })
    threading.Thread(
        target=_worker, args=(dict(cfg), bool(overwrite)),
        name="mobile-online-sync", daemon=True).start()
    return status()


def _update(**fields: Any) -> None:
    with _lock:
        _job.update(fields)


def _worker(cfg: dict, overwrite: bool) -> None:
    dest = Path(config.DATA_DIR) / "transfer" / f"online-sync-{uuid4().hex}.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        _download(cfg, dest)
        _update(state="importing", stage="正在导入到本机…")
        result = config_bundle.import_bundle(dest, overwrite=overwrite)
        _update(state="done", stage="同步完成", result=result,
                finished_at=time.time())
    except Exception as exc:  # noqa: BLE001 — 任何失败都要落成用户可见终态
        _update(state="error", stage="同步失败", error=_error_text(exc),
                finished_at=time.time())
    finally:
        dest.unlink(missing_ok=True)


def _download(cfg: dict, dest: Path) -> None:
    url = cfg["base_url"].rstrip("/") + _PULL_PATH
    headers = {mobile_remote.PC_TOKEN_HEADER: cfg["token"]}
    received = 0
    with httpx.Client(
        timeout=httpx.Timeout(120.0, connect=8.0), trust_env=False,
        follow_redirects=False,
    ) as client:
        with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code != 200:
                raise SyncError(_detail_of(resp) or f"电脑返回 HTTP {resp.status_code}")
            total = int(resp.headers.get("content-length") or 0)
            _ensure_space(dest.parent, total)
            _update(total=total)
            with dest.open("wb") as out:
                for chunk in resp.iter_bytes(_CHUNK):
                    out.write(chunk)
                    received += len(chunk)
                    _update(bytes=received)


def _ensure_space(parent: Path, total: int) -> None:
    if total <= 0:
        return
    free = shutil.disk_usage(parent).free
    if free < total * 2 + _SPACE_MARGIN:
        raise SyncError(
            f"手机存储不足：资源包约 {total // (1 << 20)}MB，"
            f"导入还需等量临时空间，当前可用 {free // (1 << 20)}MB")


def _detail_of(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return ""
    return str(data.get("detail") or "") if isinstance(data, dict) else ""


def _error_text(exc: Exception) -> str:
    if isinstance(exc, SyncError):
        return str(exc)
    if isinstance(exc, config_bundle.ConfigBundleError):
        return f"导入失败：{exc}"
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ConnectError)):
        return "连不上电脑：确认同一 Wi‑Fi、电脑端已用 start-lan 脚本启动并放行防火墙"
    if isinstance(exc, httpx.ReadTimeout):
        return "下载超时：电脑侧打包或网络过慢，稍后重试"
    return f"同步失败（{exc.__class__.__name__}）：{exc}"
