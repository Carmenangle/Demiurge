"""外网素材链路的代理探活降级（2026-10-27 真机实锤：PC Clash 地址进手机 ⇒ 7897
无人监听 ⇒ Connection refused）。合同：配置地址探活不存活 ⇒ 降级直连，让 VPN(tun)
模式代理 App 在网络层接管；显式传入的代理同样探活。"""
from __future__ import annotations

import pytest

from app.routers.comfyui import _state_proxy
from app.services import system_proxy


@pytest.fixture()
def fake_state(monkeypatch: pytest.MonkeyPatch):
    def _set(address: str) -> None:
        monkeypatch.setattr(
            "app.services.repo_meta.setting_str_from_state",
            lambda key: address if key in ("systemProxyUrl", "proxyUrl") else "",
        )
    return _set


def test_配置地址不监听时降级直连(fake_state, monkeypatch: pytest.MonkeyPatch):
    fake_state("http://127.0.0.1:7897")
    monkeypatch.setattr(system_proxy, "_tcp_probe", lambda a, timeout=1.0: False)
    assert _state_proxy() == ""


def test_配置地址在监听时照用(fake_state, monkeypatch: pytest.MonkeyPatch):
    fake_state("http://127.0.0.1:7897")
    monkeypatch.setattr(system_proxy, "_tcp_probe", lambda a, timeout=1.0: True)
    assert _state_proxy() == "http://127.0.0.1:7897"


def test_显式传入优先且同样探活(fake_state, monkeypatch: pytest.MonkeyPatch):
    fake_state("")  # 配置为空
    monkeypatch.setattr(system_proxy, "_tcp_probe", lambda a, timeout=1.0: True)
    assert _state_proxy("http://10.0.0.2:7890") == "http://10.0.0.2:7890"
    monkeypatch.setattr(system_proxy, "_tcp_probe", lambda a, timeout=1.0: False)
    assert _state_proxy("http://10.0.0.2:7890") == ""


def test_alive_空串恒假():
    assert system_proxy.alive("") is False
    assert system_proxy.alive("   ") is False
