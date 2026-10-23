"""comfy_launcher 纯逻辑测试：解释器发现 + 配置读写（不真正拉起进程）。"""

from pathlib import Path

import pytest

from app.services import comfy_launcher


@pytest.fixture(autouse=True)
def _no_real_process_scan(monkeypatch):
    """绝不扫全机进程：单测里命中真实 ComfyUI 会被当成「同装实例」甚至被杀掉。"""
    monkeypatch.setattr(comfy_launcher, "list_python_processes", lambda: [])


def test_find_python_无独立解释器不回退应用Runtime(tmp_path):
    assert comfy_launcher.find_python(tmp_path) is None


def test_find_python_命中内置解释器(tmp_path):
    # base/python/python.exe 存在 → 优先用它
    inner = tmp_path / "python"
    inner.mkdir()
    (inner / "python.exe").write_text("", encoding="utf-8")
    assert comfy_launcher.find_python(tmp_path) == str(inner / "python.exe")


def test_find_python_命中同级整合包(tmp_path):
    # base.parent/python312/python.exe 存在（整合包常见布局）
    base = tmp_path / "ComfyUI"
    base.mkdir()
    sib = tmp_path / "python312"
    sib.mkdir()
    (sib / "python.exe").write_text("", encoding="utf-8")
    assert comfy_launcher.find_python(base) == str(sib / "python.exe")


def test_find_python_命中ComfyUI虚拟环境和显式路径(tmp_path):
    base = tmp_path / "ComfyUI"
    venv = base / ".venv" / "Scripts"
    venv.mkdir(parents=True)
    python = venv / "python.exe"
    python.write_text("", encoding="utf-8")
    assert comfy_launcher.find_python(base) == str(python)

    custom = tmp_path / "custom-python.exe"
    custom.write_text("", encoding="utf-8")
    assert comfy_launcher.find_python(base, str(custom)) == str(custom)


def test_find_python_命中macOS虚拟环境(tmp_path):
    base = tmp_path / "ComfyUI"
    venv = base / "venv" / "bin"
    venv.mkdir(parents=True)
    python = venv / "python"
    python.write_text("", encoding="utf-8")
    assert comfy_launcher.find_python(base) == str(python)


def test_config_读写往返(tmp_path, monkeypatch):
    cfg_file = tmp_path / "comfy_config.json"
    monkeypatch.setattr(comfy_launcher, "_config_path", lambda: cfg_file)
    comfy_launcher.save_config(
        r"D:\ComfyUI", "http://127.0.0.1:9999", r"D:\ComfyUI\.venv\Scripts\python.exe",
    )
    got = comfy_launcher.load_config()
    assert got == {
        "path": r"D:\ComfyUI",
        "url": "http://127.0.0.1:9999",
        "python_path": r"D:\ComfyUI\.venv\Scripts\python.exe",
    }


def test_config_缺失返回默认(tmp_path, monkeypatch):
    monkeypatch.setattr(comfy_launcher, "_config_path", lambda: tmp_path / "nope.json")
    assert comfy_launcher.load_config() == {
        "path": "", "url": "http://127.0.0.1:8188", "python_path": "",
    }


def test_config_损坏返回默认(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(comfy_launcher, "_config_path", lambda: bad)
    assert comfy_launcher.load_config() == {
        "path": "", "url": "http://127.0.0.1:8188", "python_path": "",
    }


def test_start_已运行则不重复启动(monkeypatch):
    # is_up 返回 True → 直接返回「已在运行」，不 Popen
    monkeypatch.setattr(comfy_launcher.comfyui_client, "is_up", lambda url: True)
    res = comfy_launcher.start(r"D:\whatever", "http://127.0.0.1:8188")
    assert res["running"] is True and res["managed"] is False


def test_start_启动中不重复拉起(monkeypatch):
    """2026-10-21 真机实锤：ComfyUI 冷启动 8~10 分钟（Manager 拉 ComfyRegistry 就
    ~5 分钟），期间 uvicorn --reload 的 autostart 钩子与用户反复点「启动」会各自再
    spawn 一个 —— 多进程互抢 ComfyUI\\user\\comfyui.db 锁（Database is locked）与
    8188 端口，谁也起不来。端口未起但本工具已拉起且子进程还活着 ⇒ 直接返回。"""
    monkeypatch.setattr(comfy_launcher.comfyui_client, "is_up", lambda url: False)

    class BootingProc:
        def poll(self):
            return None  # 子进程还活着（启动中）

    monkeypatch.setattr(comfy_launcher, "_proc", BootingProc())
    res = comfy_launcher.start(r"D:\whatever", "http://127.0.0.1:8188")
    assert res["running"] is False and res["managed"] is True
    assert "启动中" in res["message"]


def test_start_托管进程已死则允许重新拉起(tmp_path, monkeypatch):
    """is_managed 只挡「还活着」的子进程；上次拉起的已退出 ⇒ 允许重新 start。"""
    monkeypatch.setattr(comfy_launcher.comfyui_client, "is_up", lambda url: False)

    class DeadProc:
        def poll(self):
            return 1  # 已退出

    monkeypatch.setattr(comfy_launcher, "_proc", DeadProc())
    spawned = {}

    class FakeProc:
        pid = 1

        def poll(self):
            return None

    monkeypatch.setattr(
        comfy_launcher.subprocess, "Popen",
        lambda *a, **k: spawned.update(args=a, kwargs=k) or FakeProc(),
    )
    base = tmp_path / "comfy"
    (base / "main.py").parent.mkdir(parents=True, exist_ok=True)
    (base / "main.py").write_text("#", encoding="utf-8")
    (base / "python.exe").write_text("#", encoding="utf-8")
    res = comfy_launcher.start(str(base), "http://127.0.0.1:8188", str(base / "python.exe"))
    assert res["managed"] is True
    assert spawned, "已退出的旧进程不应阻止重新拉起"


def test_start_缺main_py抛LaunchError(tmp_path, monkeypatch):
    monkeypatch.setattr(comfy_launcher.comfyui_client, "is_up", lambda url: False)
    try:
        comfy_launcher.start(str(tmp_path), "http://127.0.0.1:8188")
        assert False, "应抛 LaunchError"
    except comfy_launcher.LaunchError as e:
        assert e.status == 400


def test_start_找不到ComfyUI解释器时拒绝使用应用Runtime(tmp_path, monkeypatch):
    tmp_path.joinpath("main.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(comfy_launcher.comfyui_client, "is_up", lambda url: False)
    with __import__("pytest").raises(comfy_launcher.LaunchError) as exc_info:
        comfy_launcher.start(str(tmp_path), "http://127.0.0.1:8188")
    assert "Python" in exc_info.value.detail


def test_autostart_未配置路径则跳过(monkeypatch):
    monkeypatch.setattr(comfy_launcher, "load_config",
                        lambda: {"path": "", "url": "http://127.0.0.1:8188", "python_path": ""})
    called = []
    monkeypatch.setattr(comfy_launcher, "start", lambda *a, **k: called.append(a))
    res = comfy_launcher.autostart()
    assert res["started"] is False
    assert called == []


def test_autostart_已配路径则按配置启动(monkeypatch):
    monkeypatch.setattr(comfy_launcher, "load_config", lambda: {
        "path": r"D:\ComfyUI", "url": "http://127.0.0.1:9999",
        "python_path": r"D:\ComfyUI\python.exe",
    })
    calls = []
    monkeypatch.setattr(
        comfy_launcher, "start",
        lambda path, url, python_path="": (
            calls.append((path, url, python_path)) or {"running": False, "managed": True}
        ),
    )
    res = comfy_launcher.autostart()
    assert res["started"] is True and res["managed"] is True
    assert calls == [(r"D:\ComfyUI", "http://127.0.0.1:9999", r"D:\ComfyUI\python.exe")]


def test_autostart_启动失败不抛异常(monkeypatch):
    monkeypatch.setattr(comfy_launcher, "load_config",
                        lambda: {"path": r"D:\ComfyUI", "url": "http://127.0.0.1:8188", "python_path": ""})

    def _boom(*a, **k):
        raise comfy_launcher.LaunchError(400, "找不到解释器")

    monkeypatch.setattr(comfy_launcher, "start", _boom)
    res = comfy_launcher.autostart()
    assert res["started"] is False
    assert "找不到解释器" in res["reason"]


def test_restart_按停止等待启动顺序执行(monkeypatch):
    calls = []
    monkeypatch.setattr(
        comfy_launcher, "stop",
        lambda url, path="", python_path="": calls.append(("stop", url)),
    )
    monkeypatch.setattr(comfy_launcher.time, "sleep", lambda seconds: calls.append(("sleep", seconds)))
    monkeypatch.setattr(
        comfy_launcher,
        "start",
        lambda path, url, python_path="": (
            calls.append(("start", path, url, python_path)) or {"running": False}
        ),
    )

    result = comfy_launcher.restart("D:/ComfyUI", "http://127.0.0.1:8188")

    assert result == {"running": False}
    assert calls == [
        ("stop", "http://127.0.0.1:8188"),
        ("sleep", 1.5),
        ("start", "D:/ComfyUI", "http://127.0.0.1:8188", ""),
    ]


def test_kill_by_port_用管道读取监听进程且不依赖run_stdout(monkeypatch):
    class NetstatProcess:
        def communicate(self, timeout):
            assert timeout == 10
            return (
                "  TCP    127.0.0.1:8188    0.0.0.0:0    LISTENING    4321\n",
                "",
            )

    commands = []
    monkeypatch.setattr(
        comfy_launcher.subprocess,
        "Popen",
        lambda *args, **kwargs: NetstatProcess(),
    )

    class Result:
        returncode = 0
        stdout = None

    def fake_run(command, **_kwargs):
        commands.append(command)
        return Result()

    monkeypatch.setattr(comfy_launcher.subprocess, "run", fake_run)

    assert comfy_launcher._kill_by_port(8188) == 1
    assert commands == [["taskkill", "/F", "/T", "/PID", "4321"]]


def test_启动输出重定向到日志文件(tmp_path, monkeypatch):
    """2026-08-30 实锤：子进程继承后端失效控制台句柄 → 节点打印即 OSError [Errno 22]。
    Popen 必须把 stdout/stderr 重定向到日志文件，句柄与子进程同生命周期。"""
    opened = []
    spawned = {}

    class FakeProc:
        pid = 12345

        def poll(self):
            return None

    import builtins
    real_open = builtins.open

    def fake_open(path, mode="r", *a, **k):
        if "comfyui-stdout" in str(path):
            handle = real_open(tmp_path / "captured.log", "wb")
            opened.append((str(path), mode, handle))
            return handle
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(
        comfy_launcher.comfyui_client, "is_up", lambda url: False)
    monkeypatch.setattr(
        comfy_launcher.subprocess, "Popen",
        lambda *a, **k: spawned.update(args=a, kwargs=k) or FakeProc(),
    )
    base = tmp_path / "comfy"
    (base / "main.py").parent.mkdir(parents=True, exist_ok=True)
    (base / "main.py").write_text("#", encoding="utf-8")
    py = base / "python.exe"
    py.write_text("#", encoding="utf-8")

    result = comfy_launcher.start(str(base), "http://127.0.0.1:8188", str(py))

    assert result["managed"] is True
    assert spawned["kwargs"].get("stdout") is not None, "stdout 必须重定向"
    assert spawned["kwargs"].get("stderr") == comfy_launcher.subprocess.STDOUT
    assert opened and "comfyui-stdout" in opened[0][0]
    opened[0][2].close()
    # 本测试走了真实 start() 会把 _proc 置为 FakeProc；清理掉，避免 is_managed
    # 守卫挡住后续测试的 Popen（2026-10-21）。
    comfy_launcher._proc = None


def test_start_显式注入可写环境变量(tmp_path, monkeypatch):
    """2026-10-21 真机实锤：后端进程运行在低强制性完整性级别（用户环境沙箱化），
    Windows MIC NO_WRITE_DOWN 使其无法写入无标签（=中等）的 ComfyUI 安装树与用户
    profile：numba 缓存（@njit(cache=True) import 期建临时文件）、Lora-Manager 的
    sqlite 库（on_startup 建库失败会抛穿 aiohttp 启动）全被拒。Popen 的 env 必须
    显式把这些位置指到本工具保证可写的数据目录。"""
    spawned = {}

    class FakeProc:
        pid = 12345

        def poll(self):
            return None

    monkeypatch.setattr(comfy_launcher.comfyui_client, "is_up", lambda url: False)
    monkeypatch.setattr(
        comfy_launcher.subprocess, "Popen",
        lambda *a, **k: spawned.update(args=a, kwargs=k) or FakeProc(),
    )
    # 清掉可能由前序测试（真实 start 路径）遗留的托管句柄，否则 is_managed 守卫
    # 会挡住本次 Popen（2026-10-21 新增守卫后的测试间污染）。
    monkeypatch.setattr(comfy_launcher, "_proc", None)
    monkeypatch.setattr(comfy_launcher, "DATA_DIR", tmp_path)
    base = tmp_path / "comfy"
    (base / "main.py").parent.mkdir(parents=True, exist_ok=True)
    (base / "main.py").write_text("#", encoding="utf-8")
    (base / "python.exe").write_text("#", encoding="utf-8")

    result = comfy_launcher.start(str(base), "http://127.0.0.1:8188", str(base / "python.exe"))

    assert result["managed"] is True
    env = spawned["kwargs"]["env"]
    # 宿主 TEMP 可能是坏的（被清理/ACL/8.3 失效）⇒ 显式覆盖
    assert env["TEMP"] == str(tmp_path / "tmp")
    assert env["TMP"] == str(tmp_path / "tmp")
    assert (tmp_path / "tmp").is_dir()
    # numba 缓存重定向（否则 PermissionError → tempfile 2**31 次重试挂死）
    assert env["NUMBA_CACHE_DIR"] == str(tmp_path / "numba_cache")
    assert (tmp_path / "numba_cache").is_dir()
    # Lora-Manager 五个库/FTS 索引重定向（否则 on_startup 建库失败崩掉服务器）
    node_cache = tmp_path / "comfyui_node_cache"
    assert env["LORA_MANAGER_MODEL_UPDATE_DB"] == str(node_cache / "lora_manager_model_update.sqlite")
    assert env["LORA_MANAGER_CACHE_DB"] == str(node_cache / "lora_manager_cache.sqlite")
    assert env["LORA_MANAGER_RECIPE_CACHE_DB"] == str(node_cache / "lora_manager_recipe.sqlite")
    assert env["LORA_MANAGER_RECIPE_FTS_DB"] == str(node_cache / "lora_manager_recipe_fts.sqlite")
    assert env["LORA_MANAGER_TAG_FTS_DB"] == str(node_cache / "lora_manager_tag_fts.sqlite")
    assert node_cache.is_dir()


# ── 2026-10-23：同装单实例 / 受限上下文快速失败 / 用户上下文拉起 ──────────────

def test_match_install_pids_解释器路径与安装目录都算且不误伤相邻目录():
    """launcher 以 cwd=安装目录 + 相对 main.py 启动，子进程命令行里**不含**安装目录
    （实测 `"…\\python\\python.exe" main.py --extra…`）⇒ 必须同时认解释器路径。"""
    base = r"D:\tool\ComfyUI\ComfyUI_aaaki\ComfyUI"
    py = r"D:\tool\ComfyUI\ComfyUI_aaaki\python\python.exe"
    rows = [
        (11, f'"{py}" main.py --extra-model-paths-config x'),
        (12, r'"D:\tool\ComfyUI\ComfyUI_TE\python\python.exe" main.py'),
        (13, r'"D:\tool\Demiurge\backend\.venv\Scripts\python.exe" -m uvicorn app.main:app'),
    ]
    assert comfy_launcher._match_install_pids(rows, base, py) == [11]
    # 仅凭安装目录（绝对 main.py 启动的实例）也能命中
    rows_abs = [(14, f'"{py}" "{base}\\main.py"')]
    assert comfy_launcher._match_install_pids(rows_abs, base) == [14]
    # 配置里带尾随分隔符也要命中（normpath）
    assert comfy_launcher._match_install_pids(rows_abs, base + "\\") == [14]


def test_start_同装实例启动中不叠加(tmp_path, monkeypatch):
    """冷启动期端口未监听：同安装已有进程（哪怕非本工具拉起）⇒ 不再 spawn。"""
    monkeypatch.setattr(comfy_launcher.comfyui_client, "is_up", lambda url: False)
    monkeypatch.setattr(comfy_launcher, "_proc", None)
    base = tmp_path / "comfy"
    base.mkdir()
    (base / "main.py").write_text("#", encoding="utf-8")
    py = base / "python.exe"
    py.write_text("#", encoding="utf-8")
    monkeypatch.setattr(comfy_launcher, "list_python_processes", lambda: [
        (4321, f'"{py}" main.py --enable-cors-header *'),
    ])
    monkeypatch.setattr(
        comfy_launcher.subprocess, "Popen",
        lambda *a, **k: pytest.fail("同装实例启动中时不得再 Popen"),
    )

    res = comfy_launcher.start(str(base), "http://127.0.0.1:8188", str(py))

    assert res["running"] is False and res["managed"] is False
    assert res["pids"] == [4321]
    assert "启动中" in res["message"]


def test_start_安装树不可写快速失败503(tmp_path, monkeypatch):
    """受限上下文拉起的 ComfyUI 会卡 AlekPet 锁 600 秒 ⇒ 必须立刻 503 说清原因。"""
    monkeypatch.setattr(comfy_launcher.comfyui_client, "is_up", lambda url: False)
    monkeypatch.setattr(comfy_launcher, "_proc", None)
    monkeypatch.setattr(comfy_launcher, "install_tree_writable", lambda base: False)
    monkeypatch.setattr(
        comfy_launcher.subprocess, "Popen",
        lambda *a, **k: pytest.fail("不可写时不得 Popen"),
    )
    base = tmp_path / "comfy"
    base.mkdir()
    (base / "main.py").write_text("#", encoding="utf-8")
    (base / "python.exe").write_text("#", encoding="utf-8")

    with pytest.raises(comfy_launcher.LaunchError) as exc:
        comfy_launcher.start(str(base), "http://127.0.0.1:8188", str(base / "python.exe"))

    assert exc.value.status == 503
    assert "受限上下文" in exc.value.detail and "start-dev" in exc.value.detail


def test_build_plan_不做可写性判断且只带覆盖环境变量(tmp_path, monkeypatch):
    """规划与执行必须拆开（2026-10-23 真机实锤）：本机沙箱按**映像路径**降级——
    工作区内的可执行文件一律 Low，工作区外（ComfyUI 自带 python）保持 Medium。
    后端与 venv python 都在工作区内，实际 spawn 必须由 PowerShell 用 ComfyUI 的
    解释器完成；而规划者可能正是那个 Low 进程 ⇒ build_plan 不得代判可写性、
    不得 spawn，也不能把整个宿主环境塞进 JSON。"""
    import json

    monkeypatch.setattr(comfy_launcher, "DATA_DIR", tmp_path)
    monkeypatch.setattr(comfy_launcher, "install_tree_writable", lambda base: False)
    monkeypatch.setattr(
        comfy_launcher.subprocess, "Popen",
        lambda *a, **k: pytest.fail("build_plan 只出规格，不得 spawn"),
    )
    base = tmp_path / "comfy"
    base.mkdir()
    (base / "main.py").write_text("#", encoding="utf-8")
    py = base / "python.exe"
    py.write_text("#", encoding="utf-8")

    plan = comfy_launcher.build_plan(str(base), str(py))

    assert plan["python"] == str(py)
    assert plan["argv"][0] == str(py)
    assert "main.py" in plan["argv"]
    assert plan["cwd"] == str(base)
    assert plan["log_path"].endswith("comfyui-stdout.log")
    assert set(plan["env"]) == {
        "TEMP",
        "TMP",
        "NUMBA_CACHE_DIR",
        "LORA_MANAGER_MODEL_UPDATE_DB",
        "LORA_MANAGER_CACHE_DB",
        "LORA_MANAGER_RECIPE_CACHE_DB",
        "LORA_MANAGER_RECIPE_FTS_DB",
        "LORA_MANAGER_TAG_FTS_DB",
    }
    assert plan["env"]["TEMP"] == str(tmp_path / "tmp")
    assert "PATH" not in plan["env"]
    json.dumps(plan)  # 必须 JSON 可序列化（PowerShell ConvertFrom-Json 消费）


def test_install_tree_writable_必须探已存在文件的写权限(tmp_path, monkeypatch):
    """只探「能新建」不够：低完整性进程能新建低标签文件，却打不开已存在的
    `.install.lock`（中等标签）——filelock 把 EACCES 当「锁被占用」静默等 600 秒。"""
    import os

    base = tmp_path / "comfy"
    base.mkdir()
    (base / "main.py").write_text("#", encoding="utf-8")
    assert comfy_launcher.install_tree_writable(base) is True

    real_open = os.open
    calls = []

    def deny_existing(path, flags, *a, **k):
        if str(path).endswith("main.py"):
            calls.append(flags)
            raise PermissionError(13, "Permission denied")
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(os, "open", deny_existing)
    assert comfy_launcher.install_tree_writable(base) is False
    assert calls, "必须探已存在文件的写权限"


def test_stop_按命令行清掉启动中未监听实例(tmp_path, monkeypatch):
    """冷启动期没监听端口，只按端口杀不掉；不杀会占着 .install.lock/comfyui.db。"""
    base = tmp_path / "comfy"
    base.mkdir()
    (base / "main.py").write_text("#", encoding="utf-8")
    py = base / "python.exe"
    py.write_text("#", encoding="utf-8")
    monkeypatch.setattr(comfy_launcher, "_proc", None)
    monkeypatch.setattr(comfy_launcher, "load_config", lambda: {
        "path": str(base), "url": "http://127.0.0.1:8188", "python_path": str(py),
    })
    monkeypatch.setattr(comfy_launcher, "list_python_processes", lambda: [
        (777, f'"{py}" main.py --enable-cors-header *'),
    ])
    monkeypatch.setattr(comfy_launcher, "_kill_by_port", lambda port=8188: 0)
    commands = []

    class Result:
        returncode = 0

    def fake_run(command, **kwargs):
        commands.append(command)
        return Result()

    monkeypatch.setattr(comfy_launcher.subprocess, "run", fake_run)

    res = comfy_launcher.stop("http://127.0.0.1:8188", str(base), str(py))

    assert res["stopped"] is True
    assert ["taskkill", "/F", "/T", "/PID", "777"] in commands


def test_stop_未配置解释器时用同装解释器找冷启动实例(tmp_path, monkeypatch):
    """默认配置 python_path 为空：子进程命令行只有解释器路径、不含安装目录，
    stop 必须像 start 一样解析出同装解释器，否则冷启动实例永远杀不到。"""
    parent = tmp_path / "ComfyUI_aaaki"
    base = parent / "ComfyUI"
    base.mkdir(parents=True)
    (base / "main.py").write_text("#", encoding="utf-8")
    py = parent / "python" / "python.exe"
    py.parent.mkdir()
    py.write_text("#", encoding="utf-8")
    monkeypatch.setattr(comfy_launcher, "_proc", None)
    monkeypatch.setattr(comfy_launcher, "load_config", lambda: {
        "path": str(base), "url": "http://127.0.0.1:8188", "python_path": "",
    })
    monkeypatch.setattr(comfy_launcher, "list_python_processes", lambda: [
        (888, f'"{py}" main.py --enable-cors-header *'),
    ])
    monkeypatch.setattr(comfy_launcher, "_kill_by_port", lambda port=8188: 0)
    commands = []

    class Result:
        returncode = 0

    def fake_run(command, **kwargs):
        commands.append(command)
        return Result()

    monkeypatch.setattr(comfy_launcher.subprocess, "run", fake_run)

    res = comfy_launcher.stop("http://127.0.0.1:8188")

    assert res["stopped"] is True
    assert ["taskkill", "/F", "/T", "/PID", "888"] in commands


def test_launch_detached_不接管进程句柄(tmp_path, monkeypatch):
    """start-dev 在用户上下文拉起 ComfyUI：子进程归脚本侧，后端 `_proc` 必须保持 None。"""
    spawned = {}

    class FakeProc:
        pid = 2468

    monkeypatch.setattr(comfy_launcher.comfyui_client, "is_up", lambda url: False)
    monkeypatch.setattr(comfy_launcher, "_proc", None)
    monkeypatch.setattr(comfy_launcher, "DATA_DIR", tmp_path / "data")
    base = tmp_path / "comfy"
    base.mkdir()
    (base / "main.py").write_text("#", encoding="utf-8")
    py = base / "python.exe"
    py.write_text("#", encoding="utf-8")
    monkeypatch.setattr(
        comfy_launcher.subprocess, "Popen",
        lambda *a, **k: spawned.update(args=a, kwargs=k) or FakeProc(),
    )

    res = comfy_launcher.launch_detached(str(base), "http://127.0.0.1:8188", str(py))

    assert res["pid"] == 2468 and res["managed"] is False
    assert comfy_launcher._proc is None, "脚本上下文拉起的进程不得由后端接管"
    no_window = getattr(comfy_launcher.subprocess, "CREATE_NO_WINDOW", 0)
    assert no_window and spawned["kwargs"]["creationflags"] & no_window


def test_tmp_guard_PermissionError不进入重试(tmp_path, monkeypatch):
    """comfyui-ext/laf_tmp_guard/prestartup_script.py 的合同：低完整性进程写中等
    目录拿到 PermissionError 时，CPython 原版 _mkstemp_inner 会按
    `isdir && access(W_OK)` 判为可重试并 continue，而本机 os.TMP_MAX = 2**31-1
    ⇒ 实质无限自旋（ComfyUI 卡死在自定义节点 import）。守卫必须首次失败即抛出，
    让调用方库走各自的降级路径（numba 跳过缓存/节点记 Cannot import 后继续启动）。"""
    import importlib.util
    import os
    import tempfile

    script = (
        Path(__file__).resolve().parents[2]
        / "comfyui-ext" / "laf_tmp_guard" / "prestartup_script.py"
    )
    assert script.is_file(), f"守卫脚本缺失：{script}"

    original = tempfile._mkstemp_inner
    try:
        spec = importlib.util.spec_from_file_location("laf_tmp_guard_probe", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # 就地替换 tempfile._mkstemp_inner

        calls = {"n": 0}

        def _deny(*a, **k):
            calls["n"] += 1
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(os, "open", _deny)
        with __import__("pytest").raises(PermissionError):
            tempfile._mkstemp_inner(
                str(tmp_path), "tmp", "", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0,
            )
        assert calls["n"] == 1, "PermissionError 必须首次即抛出，不得重试"

        # 正常路径不受影响：FileExistsError（真名碰撞）仍按原语义重试
        seq = iter([FileExistsError(17, "exists"), FileExistsError(17, "exists"), None])
        made = {"n": 0}

        def _exists_then_ok(path, flags, mode):
            made["n"] += 1
            exc = next(seq)
            if exc is not None:
                raise exc
            return 42

        monkeypatch.setattr(os, "open", _exists_then_ok)
        fd, name = tempfile._mkstemp_inner(
            str(tmp_path), "tmp", "", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0,
        )
        assert fd == 42 and made["n"] == 3
    finally:
        tempfile._mkstemp_inner = original
