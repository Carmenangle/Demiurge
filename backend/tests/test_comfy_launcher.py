"""comfy_launcher 纯逻辑测试：解释器发现 + 配置读写（不真正拉起进程）。"""

from app.services import comfy_launcher


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
    monkeypatch.setattr(comfy_launcher, "stop", lambda url: calls.append(("stop", url)))
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
