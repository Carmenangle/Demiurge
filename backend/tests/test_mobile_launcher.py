"""M4：Android 启动器（mobile_server.py）的安全契约。

启动器不在 backend 包内（Chaquopy 要求原样拷进 src/main/python），按路径加载做单测：
端口回落、令牌必填、远端开关清除、probe 不回显令牌。真实 uvicorn 启动由 PC 冒烟覆盖。
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

_LAUNCHER = (Path(__file__).resolve().parents[2] / "mobile" / "spike" / "android-spike"
             / "app" / "src" / "main" / "python" / "mobile_server.py")


@pytest.fixture(scope="module")
def launcher():
    assert _LAUNCHER.is_file(), f"启动器不在预期位置：{_LAUNCHER}"
    spec = importlib.util.spec_from_file_location("mobile_server_under_test", _LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env_snapshot():
    """_prepare 直接写 os.environ / chdir / tempfile.tempdir，monkeypatch 管不到。"""
    env = dict(os.environ)
    cwd = os.getcwd()
    tempdir = tempfile.tempdir
    yield
    os.environ.clear()
    os.environ.update(env)
    os.chdir(cwd)
    tempfile.tempdir = tempdir


# ---------------------------------------------------------------------------
# 端口
# ---------------------------------------------------------------------------


def test_8010空闲时用8010(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "_port_free", lambda port: True)
    assert launcher._choose_port(8010) == 8010


def test_8010被占用时回落到18010到18099(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "_port_free", lambda port: port != 8010)
    port = launcher._choose_port(8010)
    assert port in range(18010, 18100)


def test_全部端口被占用时启动失败(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "_port_free", lambda port: False)
    with pytest.raises(RuntimeError, match="没有可用的回环端口"):
        launcher._choose_port(8010)


# ---------------------------------------------------------------------------
# 令牌必填与环境合同
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["", "   "])
def test_空令牌直接拒绝且不落任何目录(launcher, tmp_path, token):
    with pytest.raises(ValueError, match="令牌"):
        launcher.start(str(tmp_path / "root"), str(tmp_path / "frontend"), token)
    assert not (tmp_path / "root").exists()


def test_prepare写令牌端口并清除远端开关(launcher, tmp_path, env_snapshot, monkeypatch):
    monkeypatch.setenv("LAF_ALLOW_REMOTE", "1")
    root = tmp_path / "root"
    root.mkdir()
    info = launcher._prepare(root, tmp_path / "no-frontend", 18042, "tok-abc")

    assert info["port"] == 18042
    assert os.environ["LAF_RUNTIME_PORT"] == "18042"
    assert os.environ["LAF_LOCAL_TOKEN"] == "tok-abc"
    assert "LAF_ALLOW_REMOTE" not in os.environ  # 移动壳永不放开远端
    assert os.environ["LAF_COMFY_AUTOSTART"] == "0"
    assert (root / "data").is_dir() and (root / "tmp").is_dir()


def test_probe只报鉴权形态不回显令牌(launcher, monkeypatch):
    secret = "s3cr3t-token-value-should-not-leak"
    monkeypatch.setenv("LAF_LOCAL_TOKEN", secret)
    info = launcher.probe()
    assert info["auth"]["enabled"] is True
    assert info["auth"]["header"] == "X-Demiurge-Token"
    assert info["auth"]["bootstrap"] == "/__auth"
    assert secret not in json.dumps(info, ensure_ascii=False)


def test_探针路由压在根静态挂载之前(launcher, tmp_path):
    """app.main 的 "/" StaticFiles 会吞掉后注册的路由：探针与根页必须同时可达。"""
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from fastapi.testclient import TestClient

    (tmp_path / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    app = FastAPI()
    app.mount("/", StaticFiles(directory=tmp_path, html=True), name="frontend")
    launcher._attach_probe_route(app)

    paths = [getattr(route, "path", "") for route in app.router.routes]
    assert paths[0] == "/api/mobile/selftest"
    assert paths[1] == "/api/mobile/info"

    client = TestClient(app)
    assert client.get("/api/mobile/info").status_code == 200
    assert client.get("/api/mobile/selftest").status_code == 200
    assert client.get("/").status_code == 200


def test_start只绑回环且关闭访问日志(launcher, tmp_path, env_snapshot, monkeypatch):
    """引导 URL 把令牌放在 query 里，访问日志一旦打开就会把它写进 logcat。"""
    import sys
    import types

    import uvicorn
    from fastapi import FastAPI

    captured: dict = {}
    served: dict = {}
    fake_backend = types.ModuleType("app.main")
    fake_backend.app = FastAPI()
    monkeypatch.setitem(sys.modules, "app.main", fake_backend)
    # 启动器写的是 `import app.main as backend`，而 `import a.b as c` 取的是**父包属性**
    # 不是 sys.modules：全量里只要有用例先导过真 app.main，`app.main` 属性就还挂在真模块上，
    # 只换 sys.modules 会被绕过（单跑绿、全量红，2026-10-25 实测）。两处都要换。
    import app as app_pkg

    monkeypatch.setattr(app_pkg, "main", fake_backend, raising=False)
    monkeypatch.setattr(launcher, "_port_free", lambda port: True)

    class _FakeConfig:
        def __init__(self, app, **kwargs):
            captured.update(kwargs)
            captured["app"] = app

    monkeypatch.setattr(uvicorn, "Config", _FakeConfig)
    monkeypatch.setattr(
        uvicorn, "Server",
        lambda config: types.SimpleNamespace(run=lambda: served.setdefault("run", True)),
    )
    try:
        info = launcher.start(str(tmp_path / "root"), "", "tok-access-log", port=18055)
    finally:
        launcher._started = False
        launcher._info = {}

    assert captured["app"] is fake_backend.app
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 18055
    assert captured["access_log"] is False
    assert served["run"] is True
    assert info["auth"]["enabled"] is True
