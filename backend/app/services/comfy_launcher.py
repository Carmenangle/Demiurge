"""ComfyUI 进程生命周期深模块：配置持久化 + 解释器发现 + 拉起子进程 + 状态查询。

与 comfyui_client 的分工：client 管「与运行中 ComfyUI 的 HTTP 对话」，launcher 管
「本地 ComfyUI 进程的起停与配置」——两个不同接缝，别合并。

进程句柄由本模块持有（此前散在路由的模块级 _proc 全局），让启动决策可测。
"""
import json
import os
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


def _runtime_tmp_dir() -> Path:
    """ComfyUI 子进程专用的临时目录（2026-10-21 真机实锤）。

    ComfyUI 的 torch → dill 在 import 期调用 `tempfile.gettempdir()`；若子进程继承的
    TEMP/TMP 指向不可写目录（宿主 Temp 被清理/ACL 变化/8.3 短名失效），ComfyUI 会直接
    `FileNotFoundError: No usable temporary directory found` 崩在 import 阶段，表现为
    「start-dev 拉不起 ComfyUI / 生图全部失败」。这里显式给子进程一个本工具保证可写的
    目录，并在 Popen 前创建，不再依赖宿主 TEMP。
    """
    tmp = DATA_DIR / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def _numba_cache_dir() -> Path:
    """ComfyUI 子进程的 numba 缓存目录（2026-10-21 真机实锤）。

    后端运行进程可能处于**低强制性完整性级别**（用户环境沙箱化：token 带
    `Mandatory Label\\Low Mandatory Level`，子进程经 Popen 继承）。Windows MIC 的
    NO_WRITE_DOWN 规则下，低完整性进程**无法写入无标签（=中等）的 ComfyUI 安装树**：
    `comfy_extras/mesh3d/uv_unwrap/pack.py` 的 `@njit(cache=True)` 在 import 期经
    numba `ensure_cache_path` 在 ComfyUI 树 `__pycache__` 建临时文件 ⇒
    PermissionError ⇒ `tempfile._mkstemp_inner` 把 PermissionError 当可重试
    （`isdir && W_OK` 即 continue），而本机 `os.TMP_MAX = 2**31-1` ⇒ 无限自旋，
    ComfyUI 永远卡在自定义节点 import 阶段（现象：8188 无监听、进程 ~1 核空转）。
    把 numba 缓存指到本工具保证可写的数据目录，numba 即走
    UserProvidedCacheLocator，不再碰 ComfyUI 树。
    """
    d = DATA_DIR / "numba_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _node_cache_dir() -> Path:
    """ComfyUI 自定义节点的缓存目录（2026-10-21 真机实锤，低完整性环境）。

    与 _numba_cache_dir 同一根因：ComfyUI-Lora-Manager 等节点把 sqlite 缓存/
    FTS 索引放在用户 profile 或 ComfyUI 安装树（均=中等完整性），低完整性子进程
    写入被 MIC 拒绝。该节点在 on_startup 里初始化 update schema，失败会**直接
    抛穿 aiohttp 启动流程把整个服务器带崩**（runner.setup → app.startup →
    lora_routes.initialize_services → sqlite3.OperationalError）。节点官方提供了
    环境变量覆盖入口，这里统一指到本工具可写的数据目录。
    """
    d = DATA_DIR / "comfyui_node_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


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


# ── 同装进程识别（2026-10-23 真机实锤）──────────────────────────────────────
# 冷启动期端口未监听，`is_managed()` 只能看见「本进程 Popen 出来的」那个子进程：
# uvicorn --reload 换 worker 后模块级 `_proc` 归零，start-dev/外部启动器又会各起一个。
# 多实例共用一个安装树会互抢 `ComfyUI\user\comfyui.db`，并在自定义节点的
# `.install.lock`（AlekPet，timeout=600）上互相等死——实测单次冷启动被拖到
# 600.1 秒才 IMPORT FAILED，端口全程不监听。故 start/stop 都按「命令行里同时出现
# main.py 与本安装解释器/目录」做全机识别，而不是只看 `_proc`。
_PROC_QUERY = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
    "ForEach-Object { if ($_.CommandLine) { '{0}`t{1}' -f $_.ProcessId, $_.CommandLine } }"
)


def _parse_process_rows(text: str) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    for line in (text or "").splitlines():
        pid_s, sep, cmd = line.partition("\t")
        if not sep:
            continue
        try:
            rows.append((int(pid_s.strip()), cmd.strip()))
        except ValueError:
            continue
    return rows


def list_python_processes() -> list[tuple[int, str]]:
    """全机 python 进程 (pid, 命令行)。非 Windows 或查询失败返回空（不阻断启动）。"""
    if os.name != "nt":
        return []
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PROC_QUERY],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
    except Exception:  # noqa: BLE001 查询失败退化为旧行为（只看 _proc/端口），绝不因探测挡启动
        return []
    return _parse_process_rows(done.stdout or "")


def _norm_cmd(text: str) -> str:
    return os.path.normcase(text).replace("/", "\\")


def _path_mentioned(hay: str, needle: str) -> bool:
    """按路径边界匹配：`...\\ComfyUI` 不得命中 `...\\ComfyUI_TE`。"""
    if not needle:
        return False
    start = hay.find(needle)
    while start != -1:
        end = start + len(needle)
        if end == len(hay) or hay[end] in '\\"\' ':
            return True
        start = hay.find(needle, start + 1)
    return False


def _match_install_pids(
    rows: list[tuple[int, str]], base: str, python_path: str = ""
) -> list[int]:
    """挑出「跑本安装 main.py」的 pid。

    解释器路径这条是必需的：launcher 用 `cwd=<base>` + 相对 `main.py` 启动，
    子进程命令行里**不含**安装目录（实测 `"…\\python\\python.exe" main.py --extra…`）。
    """
    # normpath：配置里 `D:\tool\ComfyUI\` 这类尾随分隔符否则永远匹配不上（边界字符是 `\`）
    base_n = os.path.normpath(base.strip()) if base.strip() else ""
    py_n = os.path.normpath(python_path.strip()) if python_path.strip() else ""
    needles = [n for n in (_norm_cmd(base_n), _norm_cmd(py_n)) if n]
    if not needles:
        return []
    pids: list[int] = []
    for pid, cmd in rows:
        norm = _norm_cmd(cmd)
        if "main.py" not in norm:
            continue
        if any(_path_mentioned(norm, n) for n in needles):
            pids.append(pid)
    return pids


def find_install_processes(path: str, python_path: str = "") -> list[int]:
    """本安装正在运行的 ComfyUI 进程（含「启动中、还没监听端口」的）。"""
    base = (path or "").strip()
    if not base:
        return []
    return _match_install_pids(list_python_processes(), base, python_path)


def install_tree_writable(base: Path) -> bool:
    """当前进程能不能写 ComfyUI 安装树（决定 Popen 出的子进程会不会被拦死）。

    只探「目录能建文件」不够：低完整性进程能*新建*文件（新文件继承低标签），却打不开
    已存在的中等标签文件——AlekPet 的 `.install.lock` 正是这种已存在文件，filelock
    把 EACCES 当「锁被占用」，静默重试到 600 秒超时（2026-10-22 实测 22 次）。
    故同时探：① 目录内新建/删除；② 以写方式打开已存在的 main.py（不写入）。
    """
    try:
        probe = base / f".laf-write-probe-{os.getpid()}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return False
    try:
        os.close(os.open(base / "main.py", os.O_WRONLY))
    except OSError:
        return False
    return True


def _restricted_message(base: Path) -> str:
    return (
        f"当前进程处于受限上下文，写不了 ComfyUI 安装树：{base}。"
        "此时拉起的 ComfyUI 会卡在自定义节点（AlekPet 文件锁 600 秒）后才开端口。"
        "请用 start-dev.bat 启动（它会用 ComfyUI 自带解释器在工作区外拉起）；"
        "不要从沙箱/受限 shell 直接拉起后端或 ComfyUI。"
    )


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


def _resolve_targets(path: str, python_path: str) -> tuple[Path, str]:
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
    return base, py


def _build_spec(base: Path, py: str) -> dict:
    """唯一的 spawn 规格：后端托管（start）与脚本上下文（launch_detached）共用。"""
    # 输出必须重定向到文件（2026-08-30 实锤）：子进程继承后端控制台句柄，而后端以
    # 隐藏窗口/reload 子进程方式运行时该句柄可能失效——任何节点往控制台打印一行
    # （如 rgthree Seed 节点输出种子值）都会 OSError [Errno 22] 直接炸掉整个生图任务。
    # 重定向到日志文件后句柄恒有效，顺带留下 ComfyUI 运行日志可查。
    log_path = _stdout_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # 2026-10-21：显式 TEMP/TMP —— 不能继承宿主（见 _runtime_tmp_dir docstring）。
    env = dict(os.environ)
    tmp_dir = _runtime_tmp_dir()
    env["TEMP"] = str(tmp_dir)
    env["TMP"] = str(tmp_dir)
    # 2026-10-21：numba 缓存重定向 —— 低完整性子进程写不了 ComfyUI 安装树
    # （见 _numba_cache_dir docstring：PermissionError → tempfile 无限重试）。
    env["NUMBA_CACHE_DIR"] = str(_numba_cache_dir())
    # 2026-10-21：自定义节点缓存重定向 —— 同一低完整性根因；Lora-Manager 的
    # on_startup 建库失败会抛穿 aiohttp 启动流程（见 _node_cache_dir docstring）。
    node_cache = _node_cache_dir()
    env["LORA_MANAGER_MODEL_UPDATE_DB"] = str(node_cache / "lora_manager_model_update.sqlite")
    env["LORA_MANAGER_CACHE_DB"] = str(node_cache / "lora_manager_cache.sqlite")
    env["LORA_MANAGER_RECIPE_CACHE_DB"] = str(node_cache / "lora_manager_recipe.sqlite")
    env["LORA_MANAGER_RECIPE_FTS_DB"] = str(node_cache / "lora_manager_recipe_fts.sqlite")
    env["LORA_MANAGER_TAG_FTS_DB"] = str(node_cache / "lora_manager_tag_fts.sqlite")
    argv = [
        py, "main.py", "--extra-model-paths-config", write_ext_yaml(),
        "--enable-cors-header", "*",
        # 2026-10-21 21:20：ComfyUI 运行期临时目录重定向——`folder_paths.temp_directory`
        # 默认是 `<ComfyUI根>/temp`（无标签=中等），低完整性子进程写不了 ⇒ PreviewImage
        # 存预览图直接 `[Errno 13] Permission denied` 炸掉整个生图任务（用户实锤）。
        # 本版本 main.py 支持 `--temp_directory`（内部 join "temp" 后 set_temp_directory），
        # 指到 DATA_DIR（低进程可写）⇒ ComfyUI temp 落 `DATA_DIR/temp`。
        "--temp-directory", str(DATA_DIR),
    ]
    return {"argv": argv, "env": env, "cwd": str(base), "log_path": log_path}


# 规划 JSON 只带必须改写的环境变量，不带整个宿主环境（几十项、含宿主机密）。
_PLAN_ENV_KEYS = (
    "TEMP",
    "TMP",
    "NUMBA_CACHE_DIR",
    "LORA_MANAGER_MODEL_UPDATE_DB",
    "LORA_MANAGER_CACHE_DB",
    "LORA_MANAGER_RECIPE_CACHE_DB",
    "LORA_MANAGER_RECIPE_FTS_DB",
    "LORA_MANAGER_TAG_FTS_DB",
)


def build_plan(path: str, python_path: str = "") -> dict:
    """给「由外部进程实际 spawn」的调用方返回 JSON 友好的 spawn 规格；自身不 spawn。

    **也不判断当前进程能否写安装树**：本机沙箱按映像路径降级——工作区内的可执行
    文件一律 Low，工作区外（ComfyUI 自带 python）保持 Medium。后端与 venv python
    都在工作区内，它们 spawn 出的 ComfyUI 必然 Low、写不动安装树；而 start-dev 的
    PowerShell（System32，工作区外）能直接 spawn 工作区外的解释器。故规划（可用
    低完整性 python 做）与执行（PowerShell 做）必须拆开，可写性由执行者在自己的
    上下文里探（scripts/comfy_probe_write.py 用**将实际启动的那个解释器**探）。
    """
    base, py = _resolve_targets(path, python_path)
    spec = _build_spec(base, py)
    env = {k: spec["env"][k] for k in _PLAN_ENV_KEYS if k in spec["env"]}
    return {
        "python": py,
        "argv": spec["argv"],
        "env": env,
        "cwd": spec["cwd"],
        "log_path": str(spec["log_path"]),
    }


def _spawn_flags() -> int:
    return (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )


def _starting_result(pids: list[int]) -> dict:
    """同安装已有实例（含启动中还没监听端口的）⇒「别叠加」结果。

    只查端口不够：ComfyUI 冷启动期（AlekPet 等节点 import）根本不开监听，
    uvicorn --reload 换 worker 后 `_proc` 归零，TE 启动器/start-dev 又可能各起一个。
    """
    return {
        "running": False,
        "managed": False,
        "pids": pids,
        "message": f"ComfyUI 正在启动中（pid={pids[0]}，非本工具拉起），请等待端口就绪",
    }


def start(path: str, url: str, python_path: str = "") -> dict:
    """拉起 ComfyUI 子进程。已在运行则不重复启动。失败抛 LaunchError。"""
    global _proc, _log_fp
    if comfyui_client.is_up(url):
        return {"running": True, "managed": False, "message": "ComfyUI 已在运行"}
    if is_managed():
        return {
            "running": False,
            "managed": True,
            "message": "ComfyUI 正在启动中（本工具已拉起，请等待端口就绪）",
        }

    base, py = _resolve_targets(path, python_path)

    pids = find_install_processes(path, py)
    if pids:
        return _starting_result(pids)

    # 2026-10-23 真机实锤：冷启动被拖到 600 秒的根因是 AlekPet 节点
    # `FileLock(.install.lock, timeout=600)`——filelock 把受限上下文的 EACCES 当
    # 「锁被占用」静默轮询到超时。拉起的子进程继承本进程完整性级别，本进程写不动
    # 安装树时子进程必然也写不动 ⇒ 直接 503 快速失败，不再让人等 9 分钟。
    if not install_tree_writable(base):
        raise LaunchError(503, _restricted_message(base))

    try:
        spec = _build_spec(base, py)
        _log_fp = open(spec["log_path"], "ab")
        _proc = subprocess.Popen(
            spec["argv"],
            cwd=spec["cwd"],
            stdout=_log_fp,
            stderr=subprocess.STDOUT,
            env=spec["env"],
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except Exception as e:
        raise LaunchError(500, f"启动失败：{e}")

    return {"running": False, "managed": True, "message": "已启动，正在初始化（首次较慢）", "python": py}


def launch_detached(path: str, url: str, python_path: str = "") -> dict:
    """在当前进程的上下文拉起 ComfyUI，但**不接管**其生命周期（`_proc` 保持 None）。

    用于「后端跑在受限上下文、只有用户启动的脚本写得上安装树」的环境：
    start-dev 用本函数在用户身份下把 ComfyUI 拉起，后端只负责探测与状态呈现。
    子进程句柄关闭由文件系统/OS 负责（CREATE_NO_WINDOW，不随脚本退出被回收）。
    """
    if comfyui_client.is_up(url):
        return {"running": True, "managed": False, "message": "ComfyUI 已在运行"}
    if is_managed():
        return {
            "running": False,
            "managed": True,
            "message": "ComfyUI 正在启动中（已由后端拉起），请等待端口就绪",
        }

    base, py = _resolve_targets(path, python_path)

    pids = find_install_processes(path, py)
    if pids:
        return _starting_result(pids)

    if not install_tree_writable(base):
        raise LaunchError(503, _restricted_message(base))

    try:
        spec = _build_spec(base, py)
        with open(spec["log_path"], "ab") as log:
            proc = subprocess.Popen(
                spec["argv"],
                cwd=spec["cwd"],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=spec["env"],
                creationflags=_spawn_flags(),
            )
    except Exception as e:
        raise LaunchError(500, f"启动失败：{e}")

    return {
        "running": False,
        "managed": False,
        "pid": proc.pid,
        "message": f"已在当前上下文启动 ComfyUI（pid={proc.pid}），正在初始化（1~3 分钟）",
    }


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
        except Exception:  # noqa: BLE001 [EXC-保留] taskkill 清理 best-effort：失败不阻断停服（随后按端口再清一次）
            pass
    return killed


def stop(url: str = COMFYUI_BASE_URL, path: str = "", python_path: str = "") -> dict:
    """关闭 ComfyUI：按命令行找「同安装」进程杀树，再按端口兜底。

    必须按命令行清：冷启动期进程还没监听端口，只按端口杀不掉（用户点了「停止」，
    进程继续占着 `.install.lock`/`comfyui.db`，下一次启动又被拖 600 秒）。
    """
    global _proc, _log_fp
    port = urlparse(url).port or 8188
    cfg = load_config()
    target_path = (path or "").strip() or (cfg.get("path") or "").strip()
    target_py = python_path or (cfg.get("python_path") or "")
    if not target_py.strip() and target_path:
        # 默认配置 python_path 为空：launcher 子进程命令行只含解释器路径、不含安装目录
        # （cwd=<base> + 相对 main.py），不解析出解释器就永远匹配不到冷启动实例。
        target_py = find_python(Path(target_path)) or ""

    managed_pid = _proc.pid if (_proc is not None and _proc.poll() is None) else None
    pids: list[int] = [managed_pid] if managed_pid is not None else []
    for pid in find_install_processes(target_path, target_py):
        if pid not in pids:
            pids.append(pid)

    killed = 0
    for pid in pids:
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=10,
            )
            if result.returncode == 0:
                killed += 1
        except Exception:  # noqa: BLE001 [EXC-保留] taskkill 清理 best-effort：失败不阻断停服（随后按端口再清一次）
            pass
    if managed_pid is not None and _proc is not None and _proc.poll() is None:
        try:
            _proc.terminate()
        except Exception:  # noqa: BLE001 [EXC-保留] 托管句柄 terminate 兜底：失败不阻断停服
            pass
    _proc = None
    if _log_fp is not None:
        try:
            _log_fp.close()
        except Exception:  # noqa: BLE001 [EXC-保留] 日志句柄关闭 best-effort：失败不阻断停服
            pass
        _log_fp = None

    n = _kill_by_port(port)
    total = killed + n
    if total > 0:
        return {"stopped": True, "message": f"已关闭 ComfyUI（{total} 个进程）"}
    return {"stopped": False, "message": f"未发现监听 {port} 的 ComfyUI 进程（可能已关闭）"}


def restart(path: str, url: str, python_path: str = "", wait_seconds: float = 1.5) -> dict:
    """关闭后等待端口释放，再按相同配置启动。"""
    stop(url, path, python_path)
    time.sleep(wait_seconds)
    return start(path, url, python_path)
