"""M4 运行时（任务包 2）：mobile_server 的暂停/恢复/自愈 + 日志轮转 + /api/health 计数。

不真起 uvicorn（PC 单测不跑 Android 运行态）：server 句柄用假对象注入状态机；
健康端点直接调函数；comfy_pending 计数用 monkeypatch 假源。真机行为由
verify-device.sh + logcat 判读覆盖。
"""
from __future__ import annotations

import importlib.util
import logging
import types

from pathlib import Path

import pytest

_LAUNCHER = (Path(__file__).resolve().parents[2] / "mobile" / "spike" / "android-spike"
             / "app" / "src" /"main" / "python" / "mobile_server.py")


@pytest.fixture()
def launcher():
    spec = importlib.util.spec_from_file_location("mobile_server_runtime_test", _LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeServer:
    def __init__(self) -> None:
        self.should_exit = False


def _install_fake_server(launcher, alive_thread: bool = True) -> _FakeServer:
    server = _FakeServer()
    thread = types.SimpleNamespace(is_alive=lambda: alive_thread)
    launcher._server = server
    launcher._server_thread = thread
    return server


# ── running_generation_count ──

def test_运行计数读comfy_pending快照(launcher, monkeypatch: pytest.MonkeyPatch):
    from app.services import comfy_pending

    monkeypatch.setattr(comfy_pending, "snapshot",
                        lambda: [{"prompt_id": "a"}, {"prompt_id": "b"}])
    assert launcher.running_generation_count() == 2


def test_后端未就绪时计数保守为0(launcher, monkeypatch: pytest.MonkeyPatch):
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if "comfy_pending" in name:
            raise ImportError("not ready")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    assert launcher.running_generation_count() == 0


# ── pause_if_idle / resume 状态机 ──

def test_未起服时暂停是空操作(launcher):
    launcher._server = None
    assert launcher.pause_if_idle() == {"paused": False, "reason": "not-serving"}


def test_有在途生成时拒绝暂停(launcher, monkeypatch: pytest.MonkeyPatch):
    server = _install_fake_server(launcher)
    monkeypatch.setattr(launcher, "running_generation_count", lambda: 3)
    result = launcher.pause_if_idle()
    assert result == {"paused": False, "running": 3}
    assert server.should_exit is False


def test_空闲时暂停优雅停服务线程(launcher, monkeypatch: pytest.MonkeyPatch):
    server = _install_fake_server(launcher)
    monkeypatch.setattr(launcher, "running_generation_count", lambda: 0)
    assert launcher.pause_if_idle() == {"paused": True, "running": 0}
    assert server.should_exit is True
    assert launcher._info["phase"] == "paused"


def test_resume在服务还活着时是空操作(launcher):
    _install_fake_server(launcher, alive_thread=True)
    assert launcher.resume() == {"resumed": False, "reason": "already-serving"}


# ── /api/health 的 running_jobs ──

def test_health带运行计数(monkeypatch: pytest.MonkeyPatch):
    from app.main import health
    from app.services import comfy_pending

    monkeypatch.setattr(comfy_pending, "snapshot", lambda: [{"p": 1}])
    assert health() == {"status": "ok", "running_jobs": 1}


def test_health空任务时计数为零(monkeypatch: pytest.MonkeyPatch):
    from app.main import health
    from app.services import comfy_pending

    monkeypatch.setattr(comfy_pending, "snapshot", lambda: [])
    assert health() == {"status": "ok", "running_jobs": 0}


# ── 日志轮转 ──

def test_日志轮转挂载2x2MB(launcher, tmp_path: Path):
    for logger_name in ("", "uvicorn", "uvicorn.error"):
        logging.getLogger(logger_name).handlers.clear()
    path = launcher._setup_logging(tmp_path)
    assert path.endswith("backend.log")
    assert (tmp_path / "logs" / "backend.log").exists()
    handler = next(h for h in logging.getLogger().handlers
                   if getattr(h, "maxBytes", 0) == 2 * 1024 * 1024)
    assert handler.backupCount == 2
    # 清理：不把测试 handler 泄漏给同进程的其他用例
    logging.getLogger().handlers.remove(handler)


def test_uvicorn启动不清空日志handler且不双写(launcher, tmp_path: Path):
    """真机回归：uvicorn 默认 log_config 会 dictConfig 清空 uvicorn.error 的 handler，
    而 app 内模块全走该 logger ⇒ backend.log 恒 0 字节。改用 log_config=None 并
    只挂 root 后，uvicorn.error 的日志必须落盘且只写一次。"""
    import uvicorn

    root = logging.getLogger()
    before = list(root.handlers)
    uvicorn_error = logging.getLogger("uvicorn.error")
    old_handlers = list(uvicorn_error.handlers)
    old_propagate = uvicorn_error.propagate
    uvicorn_error.propagate = True
    try:
        launcher._setup_logging(tmp_path)
        uvicorn.Config(
            object(), log_level="info", access_log=False, log_config=None,
        ).configure_logging()
        uvicorn_error.info("after-config-marker")
        for handler in list(root.handlers):
            handler.flush()
        text = (tmp_path / "logs" / "backend.log").read_text(encoding="utf-8")
        assert text.count("after-config-marker") == 1
        assert not [h for h in uvicorn_error.handlers if isinstance(h, logging.FileHandler)]
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
        uvicorn_error.handlers[:] = old_handlers
        uvicorn_error.propagate = old_propagate
