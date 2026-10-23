"""ComfyUI 看门狗特征测试（2026-10-23 架构评审运行时稳健性短板 ①②）。

覆盖判据：
- 只救自己的进程：未托管过（外部实例）时端口再空也不重启；
- 冷启动中（is_managed 但端口未就绪）不重复重启；
- 重启尝试放线程、不并发；
- 窗口内连续重启达上限即停手留痕，端口恢复（用户手动修好）后复位；
- autostart 失败登记进面板，端口恢复后自动清除；
- activity_feed 只读投影（kind/label/status/error/target）。
"""
import threading
import time

import pytest

from app.services import activity_feed, comfy_watchdog as cw


@pytest.fixture(autouse=True)
def _reset_state():
    """看门狗模块级状态逐例隔离（与生产线程无涉：_thread 不重置也行，_loop 里
    load_config 未配置时直接 return，不会干扰断言）。"""
    for name, value in {
        "_owned": False, "_restarting": False, "_restart_times": [],
        "_gave_up": False, "_gave_up_reason": "", "_last_error": "",
        "_autostart_error": "", "_recovered_at": 0.0, "_restart_ok_at": 0.0,
    }.items():
        setattr(cw, name, value)
    yield


def _fake_env(monkeypatch, *, path="C:/ComfyUI", url="http://127.0.0.1:8188",
              managed=False, up=False, start_result=None, start_error=None):
    """假造 ComfyUI 环境；返回 start 调用记录列表。"""
    calls: list[tuple[str, str, str]] = []

    def fake_start(p, u, py):
        calls.append((p, u, py))
        if start_error:
            raise RuntimeError(start_error)
        return start_result or {"started": True}

    monkeypatch.setattr("app.services.comfy_launcher.load_config",
                        lambda: {"path": path, "url": url, "python_path": "py"})
    monkeypatch.setattr("app.services.comfy_launcher.is_managed", lambda: managed)
    monkeypatch.setattr("app.services.comfyui_client.is_up", lambda u, timeout=5.0: up)
    monkeypatch.setattr("app.services.comfy_launcher.start", fake_start)
    return calls


# ---------------------------------------------------------------------------
# 不管的情形
# ---------------------------------------------------------------------------

def test_未配置路径时看门狗完全静默(monkeypatch):
    _fake_env(monkeypatch, path="")
    cw._tick()
    assert cw.watchdog_snapshot() == []


def test_从未托管过时外部实例死亡不重启(monkeypatch):
    """外部整合包/手动拉起的实例不是本工具的责任——杀了夺端反而越权。"""
    calls = _fake_env(monkeypatch, managed=False, up=False)
    cw._tick()
    assert calls == []
    assert cw.watchdog_snapshot() == []


def test_托管进程冷启动中不重复重启(monkeypatch):
    """is_managed=True 但端口未就绪（冷启动数百秒）⇒ 等，不重启。"""
    calls = _fake_env(monkeypatch, managed=True, up=False)
    cw._tick()
    assert calls == []
    assert cw._owned is True  # tick 顺手把托管标记补上


# ---------------------------------------------------------------------------
# 自动重启
# ---------------------------------------------------------------------------

def test_托管进程死亡后按配置重启一次(monkeypatch):
    started = threading.Event()

    def blocking_start(p, u, py):
        started.set()
        time.sleep(0.05)
        return {"started": True}

    monkeypatch.setattr("app.services.comfy_launcher.load_config",
                        lambda: {"path": "C:/ComfyUI", "url": "http://127.0.0.1:8188",
                                 "python_path": "py"})
    monkeypatch.setattr("app.services.comfy_launcher.is_managed", lambda: False)
    monkeypatch.setattr("app.services.comfyui_client.is_up", lambda u, timeout=5.0: False)
    monkeypatch.setattr("app.services.comfy_launcher.start", blocking_start)

    cw.note_owned()
    cw._tick()
    assert started.wait(2), "看门狗未在 daemon 线程里执行重启"

    items = cw.watchdog_snapshot()
    assert len(items) == 1
    assert items[0]["status"] == "running"
    assert "第 1 次重启中" in items[0]["progress"]


def test_重启进行中不并发尝试(monkeypatch):
    """冷启动阻塞期间连续 tick 不得叠加重启尝试（_restarting 闸）。"""
    release = threading.Event()
    entered = threading.Event()

    def blocking_start(p, u, py):
        entered.set()
        release.wait(2)
        return {"started": True}

    monkeypatch.setattr("app.services.comfy_launcher.load_config",
                        lambda: {"path": "C:/ComfyUI", "url": "", "python_path": ""})
    monkeypatch.setattr("app.services.comfy_launcher.is_managed", lambda: False)
    monkeypatch.setattr("app.services.comfyui_client.is_up", lambda u, timeout=5.0: False)
    monkeypatch.setattr("app.services.comfy_launcher.start", blocking_start)

    cw.note_owned()
    cw._tick()
    assert entered.wait(2)
    for _ in range(3):
        cw._tick()  # 重启仍在阻塞：这些 tick 必须被 _restarting 挡掉
    assert cw._restart_times and len(cw._restart_times) == 1
    release.set()
    time.sleep(0.1)


def test_连续重启超限即停手并留痕(monkeypatch):
    calls = _fake_env(monkeypatch, managed=False, up=False, start_error="端口被占用")
    cw.note_owned()
    for _ in range(cw._MAX_CONSECUTIVE):
        cw._tick()
        while cw._restarting:  # 等本次尝试落地（假 start 立即返回）
            time.sleep(0.01)
    assert len(calls) == cw._MAX_CONSECUTIVE

    cw._tick()  # 第 N+1 次：超限 ⇒ 停手
    assert cw._gave_up is True
    assert len(calls) == cw._MAX_CONSECUTIVE, "停手后不得再尝试"

    items = cw.watchdog_snapshot()
    assert len(items) == 1 and items[0]["status"] == "failed"
    assert "停手" in items[0]["label"] and "节点管理" in items[0]["error"]
    assert "端口被占用" in items[0]["error"]  # 最后一次错误要带出来


def test_端口恢复后复位可再次守护(monkeypatch):
    """停手后用户手动修好（端口就绪）⇒ 清零计数；再死亡可重新自动重启。"""
    _fake_env(monkeypatch, managed=False, up=False, start_error="坏配置")
    cw.note_owned()
    for _ in range(cw._MAX_CONSECUTIVE + 1):
        cw._tick()
        while cw._restarting:
            time.sleep(0.01)
    assert cw._gave_up is True

    # 端口恢复（外部或手动启动）⇒ 停手态与失败态一起清
    monkeypatch.setattr("app.services.comfyui_client.is_up", lambda u, timeout=5.0: True)
    cw._tick()
    assert cw._gave_up is False
    assert cw.watchdog_snapshot() == []

    # 再次死亡 ⇒ 重新纳入自动重启
    monkeypatch.setattr("app.services.comfyui_client.is_up", lambda u, timeout=5.0: False)
    calls = _fake_env(monkeypatch, managed=False, up=False)
    cw._tick()
    while cw._restarting:
        time.sleep(0.01)
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# autostart 失败登记
# ---------------------------------------------------------------------------

def test_autostart失败登记面板条目且端口恢复后清除(monkeypatch):
    _fake_env(monkeypatch, managed=False, up=False)
    cw.note_autostart_failed("ComfyUI 目录不可写")
    items = cw.watchdog_snapshot()
    assert len(items) == 1
    assert items[0]["status"] == "failed"
    assert "自动启动失败" in items[0]["label"]
    assert "目录不可写" in items[0]["error"]
    assert items[0]["target"] == "node-manager"

    # 用户后来手动把 ComfyUI 起来了 ⇒ 失败条目自动消失（不翻旧账）
    monkeypatch.setattr("app.services.comfyui_client.is_up", lambda u, timeout=5.0: True)
    cw._tick()
    assert cw.watchdog_snapshot() == []


# ---------------------------------------------------------------------------
# activity_feed 投影
# ---------------------------------------------------------------------------

def test_activity_feed投影看门狗条目():
    cw.note_autostart_failed("x")
    items = [i for i in activity_feed.snapshot() if i.get("kind") == "comfy_watchdog"]
    assert len(items) == 1
    assert items[0]["title"] == activity_feed.KIND_LABEL["comfy_watchdog"]
    assert items[0]["status"] == "failed"
    assert items[0]["target"] == "node-manager"


def test_healthy时活动面板无看门狗条目():
    assert [i for i in activity_feed.snapshot() if i.get("kind") == "comfy_watchdog"] == []
