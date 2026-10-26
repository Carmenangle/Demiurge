"""M7 联机：ComfyUI 主机运行时白名单登记（url_guard.register_comfyui_host）
与 mobile_server 的 /api/mobile/comfy-host 路由（登记 + 落盘 + 开机播种）。

真源合同：前端设置页 comfyuiUrl 是唯一 PC 地址来源，保存即登记；壳层/Java 零配置。
"""
from __future__ import annotations

import importlib.util
import json

from pathlib import Path

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import url_guard


@pytest.fixture(autouse=True)
def _restore_extra_hosts():
    """白名单是模块级可变列表：每个用例后恢复原状，防跨用例泄漏。"""
    saved = list(url_guard._COMFYUI_EXTRA)
    yield
    url_guard._COMFYUI_EXTRA[:] = saved


# ── url_guard.register_comfyui_host ──

def test_登记局域网IP后validate放行():
    host = url_guard.register_comfyui_host("192.168.1.9")
    assert host == "192.168.1.9"
    assert url_guard.validate_comfyui_url("http://192.168.1.9:8188/") == "http://192.168.1.9:8188"


def test_登记Tailscale域名与大小写归一():
    assert url_guard.register_comfyui_host("PC-Tail.tail5cfc8.ts.net.") == "pc-tail.tail5cfc8.ts.net"
    assert url_guard.validate_comfyui_url("http://pc-tail.tail5cfc8.ts.net:8188")


def test_回环无需登记():
    assert url_guard.register_comfyui_host("127.0.0.1") == "loopback"
    assert "127.0.0.1" not in url_guard.comfy_extra_hosts()


@pytest.mark.parametrize("bad", ["", "   ", "bad host!", "http://x", "a" * 254, "h@st"])
def test_形态不合法一律拒绝(bad: str):
    with pytest.raises(ValueError):
        url_guard.register_comfyui_host(bad)


def test_重复登记幂等且上限拦截():
    url_guard.register_comfyui_host("10.0.0.5")
    url_guard.register_comfyui_host("10.0.0.5")
    assert url_guard.comfy_extra_hosts().count("10.0.0.5") == 1
    for i in range(url_guard.MAX_EXTRA_HOSTS - 1):
        url_guard.register_comfyui_host(f"10.0.1.{i}")
    with pytest.raises(ValueError):
        url_guard.register_comfyui_host("10.0.9.9")


# ── mobile_server /api/mobile/comfy-host ──

_LAUNCHER = (Path(__file__).resolve().parents[2] / "mobile" / "spike" / "android-spike"
             / "app" / "src" / "main" / "python" / "mobile_server.py")


@pytest.fixture(scope="module")
def launcher():
    assert _LAUNCHER.is_file(), f"启动器不在预期位置：{_LAUNCHER}"
    spec = importlib.util.spec_from_file_location("mobile_server_comfy_host_test", _LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def route_client(launcher, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("LAF_DATA_DIR", str(tmp_path))
    url_guard._COMFYUI_EXTRA.clear()
    app = FastAPI()
    launcher._attach_comfy_host_routes(app)
    return TestClient(app)


def test_路由登记主机_落盘_列表可见(route_client: TestClient, tmp_path: Path):
    res = route_client.post("/api/mobile/comfy-host", json={"url": "http://192.168.1.9:8188"})
    assert res.json() == {"ok": True, "host": "192.168.1.9"}
    # 落盘（开机播种的真源）
    saved = json.loads((tmp_path / "comfy_hosts.json").read_text(encoding="utf-8"))
    assert saved == {"hosts": ["192.168.1.9"]}
    listed = route_client.get("/api/mobile/comfy-host").json()
    assert listed["hosts"] == ["192.168.1.9"]
    assert listed["seeded"] == ["192.168.1.9"]
    # 登记后真正的提交地址过白名单
    assert url_guard.validate_comfyui_url("http://192.168.1.9:8188")


def test_路由兼容裸host端口与坏输入(route_client: TestClient, tmp_path: Path):
    assert route_client.post("/api/mobile/comfy-host", json={"url": "192.168.1.9:8188"}).json() == {
        "ok": True, "host": "192.168.1.9",
    }
    assert route_client.post("/api/mobile/comfy-host", json={"url": "!!not a url"}).json()["ok"] is False
    assert route_client.post("/api/mobile/comfy-host", json={"url": "127.0.0.1"}).json() == {
        "ok": True, "host": "loopback",
    }
    # 回环不进落盘内容（合法登记过的主机仍在）
    saved_hosts = json.loads((tmp_path / "comfy_hosts.json").read_text(encoding="utf-8"))["hosts"]
    assert "127.0.0.1" not in saved_hosts


def test_开机播种读落盘文件(route_client: TestClient, launcher, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    route_client.post("/api/mobile/comfy-host", json={"url": "http://10.1.2.3:8188"})
    url_guard._COMFYUI_EXTRA.clear()  # 模拟重启后的干净白名单
    monkeypatch.setenv("LAF_DATA_DIR", str(tmp_path))
    assert launcher._seed_comfy_hosts() == ["10.1.2.3"]
    assert url_guard.validate_comfyui_url("http://10.1.2.3:8188")
    # 坏条目被跳过、不挡启动
    (tmp_path / "comfy_hosts.json").write_text(
        json.dumps({"hosts": ["bad host!", "10.1.2.4"]}), encoding="utf-8")
    url_guard._COMFYUI_EXTRA.clear()
    assert launcher._seed_comfy_hosts() == ["10.1.2.4"]
