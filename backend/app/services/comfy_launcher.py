"""ComfyUI 进程生命周期深模块：配置持久化 + 解释器发现 + 拉起子进程 + 状态查询。

与 comfyui_client 的分工：client 管「与运行中 ComfyUI 的 HTTP 对话」，launcher 管
「本地 ComfyUI 进程的起停与配置」——两个不同接缝，别合并。

进程句柄由本模块持有（此前散在路由的模块级 _proc 全局），让启动决策可测。
"""
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from app.config import COMFY_EXT_DIR, COMFYUI_BASE_URL, DATA_DIR
from app.services import comfyui_client

# 本进程拉起的 ComfyUI 子进程（None = 未由本工具启动）
_proc: "subprocess.Popen | None" = None
# ComfyUI 子进程的输出日志句柄：必须与子进程同生命周期持有，句柄关闭即失效
_log_fp = None


def _stdout_log_path() -> Path:
    return DATA_DIR / "logs" / "comfyui-stdout.log"


def _config_path() -> Path:
    return DATA_DIR / "comfy_config.json"


def load_config() -> dict:
    """读 ComfyUI 路径/地址配置。缺失/损坏返回默认。"""
    p = _config_path()
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return {
                "path": data.get("path", ""),
                "url": data.get("url", COMFYUI_BASE_URL),
                "python_path": data.get("python_path", ""),
            }
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            pass
    return {"path": "", "url": COMFYUI_BASE_URL, "python_path": ""}


def save_config(path: str, url: str, python_path: str = "") -> dict:
    """保存配置到 data/comfy_config.json（start-dev 脚本据此启动）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = {"path": path, "url": url, "python_path": python_path}
    _config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def find_python(base: Path, configured: str = "") -> str | None:
    """只选择属于 ComfyUI 的解释器；禁止回退到本应用 Runtime。"""
    if configured.strip():
        explicit = Path(configured).expanduser()
        return str(explicit) if explicit.is_file() else None
    for cand in [
        base / ".venv" / "Scripts" / "python.exe",
        base / "venv" / "Scripts" / "python.exe",
        base / ".venv" / "bin" / "python",
        base / "venv" / "bin" / "python",
        base.parent / "python_embeded" / "python.exe",
        base.parent / "python" / "python.exe",
        base.parent / "python312" / "python.exe",
        base / "python" / "python.exe",
    ]:
        if cand.is_file():
            return str(cand)
    return None


def write_ext_yaml() -> str:
    """生成把 laf_lock 扩展目录注册为 custom_nodes 的 yaml，返回其路径。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    yaml_path = DATA_DIR / "comfy_extra_paths.yaml"
    content = f"laf_ext:\n  custom_nodes: {COMFY_EXT_DIR.as_posix()}\n"
    yaml_path.write_text(content, encoding="utf-8")
    return str(yaml_path)


def is_managed() -> bool:
    """当前是否有本工具拉起且仍存活的 ComfyUI 子进程。"""
    return _proc is not None and _proc.poll() is None


class LaunchError(Exception):
    """启动失败，携带 HTTP 状态码供路由映射。"""
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(detail)


def start(path: str, url: str, python_path: str = "") -> dict:
    """拉起 ComfyUI 子进程。已在运行则不重复启动。失败抛 LaunchError。"""
    global _proc
    if comfyui_client.is_up(url):
        return {"running": True, "managed": False, "message": "ComfyUI 已在运行"}

    base = Path(path)
    if not (base / "main.py").is_file():
        raise LaunchError(400, f"未找到 main.py：{base / 'main.py'}")

    py = find_python(base, python_path)
    if py is None:
        detail = (
            f"配置的 ComfyUI Python 不存在：{python_path}"
            if python_path.strip()
            else "未找到 ComfyUI 独立 Python；请在设置中填写 ComfyUI Python 路径"
        )
        raise LaunchError(400, detail)
    try:
        # 输出必须重定向到文件（2026-08-30 实锤）：子进程继承后端控制台句柄，而后端以
        # 隐藏窗口/reload 子进程方式运行时该句柄可能失效——任何节点往控制台打印一行
        # （如 rgthree Seed 节点输出种子值）都会 OSError [Errno 22] 直接炸掉整个生图任务。
        # 重定向到日志文件后句柄恒有效，顺带留下 ComfyUI 运行日志可查。
        global _log_fp
        log_path = _stdout_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _log_fp = open(log_path, "ab")
        _proc = subprocess.Popen(
            [py, "main.py", "--extra-model-paths-config", write_ext_yaml(), "--enable-cors-header", "*"],
            cwd=str(base),
            stdout=_log_fp,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except Exception as e:
        raise LaunchError(500, f"启动失败：{e}")

    return {"running": False, "managed": True, "message": "已启动，正在初始化（首次较慢）", "python": py}


def autostart() -> dict:
    """后端启动时按已保存配置自动拉起 ComfyUI。

    仅当 comfy_config.json 已填写 ComfyUI 目录时才启动；未配置或启动失败都不抛异常，
    以免阻断后端。start() 自身幂等（已在运行则跳过），可安全重复调用。
    """
    cfg = load_config()
    path = (cfg.get("path") or "").strip()
    if not path:
        return {"started": False, "reason": "未配置 ComfyUI 路径"}
    try:
        result = start(path, cfg.get("url", COMFYUI_BASE_URL), cfg.get("python_path", ""))
        return {"started": True, **result}
    except LaunchError as e:
        return {"started": False, "reason": e.detail}


def _kill_by_port(port: int = 8188) -> int:
    """按监听端口找进程并杀（含子进程树）。返回杀掉的进程数。
    用于停止非本工具拉起的 ComfyUI（整合包/外部启动，_proc 为空时）。
    Windows 用 taskkill /T 杀进程树，规避子 worker 残留(见记忆 uvicorn 幽灵进程教训)。"""
    import re
    killed = 0
    try:
        proc = subprocess.Popen(
            ["netstat", "-ano"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out, _ = proc.communicate(timeout=10)
    except Exception:
        return 0
    pids = set()
    for line in (out or "").splitlines():
        if f":{port} " in line and "LISTENING" in line:
            m = re.search(r"(\d+)\s*$", line.strip())
            if m:
                pids.add(m.group(1))
    for pid in pids:
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", pid],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                killed += 1
        except Exception:
            pass
    return killed


def stop(url: str = COMFYUI_BASE_URL) -> dict:
    """关闭 ComfyUI。本工具拉起的先 terminate 子进程树；否则按端口杀。"""
    global _proc, _log_fp
    port = urlparse(url).port or 8188
    if _proc is not None and _proc.poll() is None:
        pid = _proc.pid
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=10)
        except Exception:
            _proc.terminate()
        _proc = None
        if _log_fp is not None:
            try:
                _log_fp.close()
            except Exception:
                pass
            _log_fp = None
        # 兜底：端口可能仍被子进程占，再按端口清一次
        _kill_by_port(port)
        return {"stopped": True, "message": "已关闭 ComfyUI（本工具启动的进程）"}
    n = _kill_by_port(port)
    _proc = None
    if n > 0:
        return {"stopped": True, "message": f"已关闭 ComfyUI（{n} 个监听 {port} 的进程）"}
    return {"stopped": False, "message": f"未发现监听 {port} 的 ComfyUI 进程（可能已关闭）"}


def restart(path: str, url: str, python_path: str = "", wait_seconds: float = 1.5) -> dict:
    """关闭后等待端口释放，再按相同配置启动。"""
    stop(url)
    time.sleep(wait_seconds)
    return start(path, url, python_path)
