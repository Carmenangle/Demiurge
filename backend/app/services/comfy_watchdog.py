"""ComfyUI 运行态看门狗：autostart 失败登记 + 托管进程死亡自动重启 + 活动面板可见性。

背景（2026-10-23 架构评审：运行时稳健性 7.5/10 的两个静默失败场景）：
① 托管 ComfyUI 在会话中途死亡（OOM / 驱动复位）时无人恢复——用户要连败几次后
   自己去「节点管理」页点重启；② autostart 失败时返回值被启动线程丢弃，用户只看到
   「生图没反应」却查不到原因。

设计约束：
- **只救自己的进程**：曾托管过（is_managed 观测到 / note_owned）才重启；外部实例
  （整合包/手动拉起）的死活不是本工具的责任，杀了夺端反而越权。
- **重启幂等**：走 comfy_launcher.start()——端口已被占用时它自己返回不重复拉起。
- **冷启动阻塞可达数百秒**（AlekPet install.lock 互等）⇒ 尝试放 daemon 线程，
  `_restarting` 防并发重复尝试。
- **防重启风暴**：窗口内连续重启达上限即停手并留痕，直到观测到端口恢复（用户手动
  修好）才复位——坏配置（路径错/缺依赖）下无脑重启只会刷屏。
- **可见性**：状态经 activity_feed 投影进「后台活动」面板（只读投影，不碰 /jobs
  注册表——那两个真源不得合并）。

依赖方向：本模块被 main.py（启动钩子）与 routers/comfyui.py（手动 start 成功后标记）
调用；对 comfy_launcher/comfyui_client 一律**函数内 late import**（它们不依赖本模块，
但顶层 import 会让 runtime_entry 自检链多背一层）。
"""
from __future__ import annotations

import logging
import threading
import time

_LOG = logging.getLogger("uvicorn.error")

_CHECK_INTERVAL_S = 15.0        # 看门狗 tick 间隔
_MAX_CONSECUTIVE = 3            # 窗口内连续重启上限（超过即停手留痕）
_RESTART_WINDOW_S = 600.0       # 连续重启计数窗口
_KEEP_RECOVERED_S = 300.0       # 「已自动重启恢复」条目驻留时长

_lock = threading.Lock()
_thread: threading.Thread | None = None

_owned = False              # 我们曾持有托管进程句柄（autostart/手动 start 成功或观测到）
_restarting = False         # 一次重启尝试在进行中（冷启动阻塞，勿并发）
_restart_times: list[float] = []   # 窗口内重启尝试时刻
_gave_up = False            # 连续重启超限，停手
_gave_up_reason = ""
_last_error = ""            # 最近一次重启/启动失败原因
_autostart_error = ""       # autostart 失败原因（面板常驻直到端口恢复）
_recovered_at = 0.0         # 最近一次「重启成功后观测到端口就绪」的时刻（面板 done 条目）
_restart_ok_at = 0.0        # 最近一次重启尝试无异常返回的时刻（0=从未成功过）


def note_owned() -> None:
    """登记「我们持有托管进程」（autostart / 用户手动 start/restart 成功后调）。"""
    global _owned
    with _lock:
        _owned = True


def note_autostart_failed(reason: str) -> None:
    """登记 autostart 失败原因（启动钩子调）；端口恢复后自动清除。"""
    global _autostart_error
    with _lock:
        _autostart_error = reason.strip() or "未知原因"


def start_watchdog() -> None:
    """启动看门狗 daemon 线程（幂等；由 main.py startup 钩子调用）。"""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, name="comfy-watchdog", daemon=True)
        _thread.start()


def watchdog_snapshot() -> list[dict]:
    """后台活动条目（activity_feed 只读投影）。healthy 且无 recent 事件时返回空。"""
    now = time.time()
    with _lock:
        if _restarting:
            return [_item(
                status="running",
                label="ComfyUI 自动重启",
                progress=f"第 {len(_restart_times)} 次重启中（冷启动较慢，请稍候）",
                started_at=_restart_times[-1] if _restart_times else now,
            )]
        if _gave_up:
            detail = _gave_up_reason or f"连续重启超过 {_MAX_CONSECUTIVE} 次"
            if _last_error:
                detail = f"{detail}；最后一次错误：{_last_error}"
            return [_item(status="failed", label="ComfyUI 守护已停手",
                          error=f"{detail}。请到节点管理手动启动", target="node-manager")]
        if _autostart_error:
            return [_item(status="failed", label="ComfyUI 自动启动失败",
                          error=f"{_autostart_error}。请到节点管理检查路径并手动启动",
                          target="node-manager")]
        if _recovered_at and now - _recovered_at < _KEEP_RECOVERED_S:
            return [_item(status="done", label="ComfyUI 已自动重启恢复",
                          progress="托管进程曾死亡，看门狗已重启并观测到端口就绪",
                          started_at=_recovered_at)]
    return []


def _item(*, status: str, label: str, progress: str = "", error: str = "",
          target: str = "", started_at: float | None = None) -> dict:
    return {
        "id": "comfy-watchdog",
        "kind": "comfy_watchdog",
        "label": label,
        "status": status,
        "progress": progress,
        "detail": "",
        "error": error,
        "target": target,
        "repo_id": "",
        "started_at": float(started_at if started_at is not None else time.time()),
        "updated_at": time.time(),
    }


def _loop() -> None:
    while True:
        try:
            _tick()
        except Exception as exc:  # noqa: BLE001 — 看门狗自身永不退出（否则静默失效）
            _LOG.warning("ComfyUI 看门狗 tick 失败（继续运行）: %s", exc)
        time.sleep(_CHECK_INTERVAL_S)


def _tick() -> None:
    from app.config import COMFYUI_BASE_URL
    from app.services import comfy_launcher, comfyui_client

    cfg = comfy_launcher.load_config()
    path = (cfg.get("path") or "").strip()
    if not path:
        return  # 未配置 ComfyUI：不属本看门狗职责
    url = (cfg.get("url") or COMFYUI_BASE_URL).strip() or COMFYUI_BASE_URL
    if comfy_launcher.is_managed():
        note_owned()
        if comfyui_client.is_up(url):
            _mark_recovered()
        return  # 进程活着（端口可能仍在冷启动中）——不重启
    if comfyui_client.is_up(url):
        _mark_recovered()  # 外部实例在服务：不夺端，顺带清除停手/失败态
        return
    with _lock:
        owned = _owned
    if not owned:
        return  # 从未托管过：外部实例的死活不管
    _attempt_restart(path, url, str(cfg.get("python_path") or ""))


def _mark_recovered() -> None:
    """观测到端口就绪：清零连续重启计数与停手/失败态。"""
    global _gave_up, _gave_up_reason, _autostart_error, _restart_times, _recovered_at
    with _lock:
        _gave_up = False
        _gave_up_reason = ""
        _autostart_error = ""
        _restart_times = []
        # 只有「我们的重启尝试成功过」才挂恢复条目——用户手动修好的不冒领功劳
        if _restart_ok_at:
            _recovered_at = _restart_ok_at


def _attempt_restart(path: str, url: str, python_path: str) -> None:
    global _restarting, _gave_up, _gave_up_reason, _restart_times
    with _lock:
        if _restarting or _gave_up:
            return
        now = time.time()
        recent = [t for t in _restart_times if now - t < _RESTART_WINDOW_S]
        if len(recent) >= _MAX_CONSECUTIVE:
            _gave_up = True
            _gave_up_reason = f"窗口内连续重启 {len(recent)} 次仍未就绪"
            _LOG.warning("ComfyUI 看门狗停手：%s", _gave_up_reason)
            return
        _restart_times = recent + [now]
        _restarting = True
        attempt_no = len(_restart_times)

    def _run() -> None:
        global _restarting, _last_error, _restart_ok_at
        try:
            from app.services import comfy_launcher

            comfy_launcher.start(path, url, python_path)
            with _lock:
                _restart_ok_at = time.time()
            _LOG.warning("ComfyUI 看门狗第 %d 次重启已执行", attempt_no)
        except Exception as exc:  # noqa: BLE001 — 启动失败原因要留痕，且不得杀看门狗
            with _lock:
                _last_error = str(exc)
            _LOG.warning("ComfyUI 看门狗第 %d 次重启失败：%s", attempt_no, exc)
        finally:
            with _lock:
                _restarting = False

    threading.Thread(target=_run, name="comfy-watchdog-restart", daemon=True).start()
