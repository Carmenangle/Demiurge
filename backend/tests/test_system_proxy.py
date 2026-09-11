"""系统代理解析（2026-09-10「继承全局」升级）回归。

契约：`system_proxy.resolve()` 是「本机有没有开代理」的唯一判定口——
先读系统代理（WinINET 注册表 / 环境变量），再回退应用内手填地址，
TCP 探活通过才算可用；`internet=True` 时才做出网验证。

本文件把注册表读取、环境变量、TCP 探活、出网探测全部 patch 掉，
不产生任何真实网络/注册表副作用。
"""
import pytest

from app.services import system_proxy


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch):
    """隔离真实环境变量代理，避免跑测试的机器上 HTTP_PROXY 影响断言。"""
    monkeypatch.setattr(system_proxy.urllib.request, "getproxies_environment", lambda: {})
    monkeypatch.setattr(system_proxy, "_read_registry_proxy", lambda: "")


def _fake_probe(listening: set[str]):
    """造一个「只有 listening 里列出的地址连得上」的探活函数。"""
    return lambda address, timeout=0: system_proxy.normalize(address) in {
        system_proxy.normalize(a) for a in listening
    }


# ── normalize ────────────────────────────────────────────────

def test_normalize补全scheme():
    assert system_proxy.normalize("127.0.0.1:7897") == "http://127.0.0.1:7897"
    assert system_proxy.normalize(" http://127.0.0.1:7897 ") == "http://127.0.0.1:7897"
    assert system_proxy.normalize("") == ""


def test_pick_from只认http系列代理():
    """socks 代理需要额外依赖，不纳入可用候选；https 优先于 http。"""
    assert system_proxy._pick_from({"http": "127.0.0.1:7897"}) == "http://127.0.0.1:7897"
    assert system_proxy._pick_from(
        {"http": "127.0.0.1:7890", "https": "127.0.0.1:7897"}) == "http://127.0.0.1:7897"
    assert system_proxy._pick_from({"socks": "socks://127.0.0.1:1080"}) == ""
    assert system_proxy._pick_from({}) == ""
    assert system_proxy._pick_from(None) == ""


# ── read_system_proxy：注册表优先于环境变量 ─────────────────

def test_系统代理优先于环境变量(monkeypatch):
    monkeypatch.setattr(system_proxy, "_read_registry_proxy", lambda: "http://127.0.0.1:7890")
    monkeypatch.setattr(system_proxy.urllib.request, "getproxies_environment",
                        lambda: {"http": "http://127.0.0.1:10809"})
    assert system_proxy.read_system_proxy() == ("http://127.0.0.1:7890", "system")


def test_注册表为空时光靠环境变量也算有代理(monkeypatch):
    monkeypatch.setattr(system_proxy.urllib.request, "getproxies_environment",
                        lambda: {"http": "127.0.0.1:10809"})
    assert system_proxy.read_system_proxy() == ("http://127.0.0.1:10809", "env")


def test_两者都没有才算没开代理():
    assert system_proxy.read_system_proxy() == ("", "none")


# ── resolve：候选优先级与回退 ────────────────────────────────

def test_系统代理可用时走系统代理而非手填(monkeypatch):
    """核心回归：手填地址写错端口，但系统代理是好的 → 必须用系统代理。"""
    monkeypatch.setattr(system_proxy, "_read_registry_proxy", lambda: "http://127.0.0.1:7890")
    monkeypatch.setattr(system_proxy, "_tcp_probe", _fake_probe({"http://127.0.0.1:7890"}))
    out = system_proxy.resolve("http://127.0.0.1:7897")
    assert out["listening"] is True
    assert out["source"] == "system"
    assert out["address"] == "http://127.0.0.1:7890"
    assert out["system_address"] == "http://127.0.0.1:7890"
    assert out["manual_address"] == "http://127.0.0.1:7897"
    assert [c["source"] for c in out["candidates"]] == ["system", "manual"]


def test_系统代理没在听时回退手填地址(monkeypatch):
    monkeypatch.setattr(system_proxy, "_read_registry_proxy", lambda: "http://127.0.0.1:7890")
    monkeypatch.setattr(system_proxy, "_tcp_probe", _fake_probe({"http://127.0.0.1:7897"}))
    out = system_proxy.resolve("http://127.0.0.1:7897")
    assert out["listening"] is True
    assert out["source"] == "manual"
    assert out["address"] == "http://127.0.0.1:7897"


def test_都不在听时判未启用但仍透出指向地址(monkeypatch):
    """「系统设置还留着代理、进程已经关了」是最常见的误判来源，必须回 listening=False。"""
    monkeypatch.setattr(system_proxy, "_read_registry_proxy", lambda: "http://127.0.0.1:7890")
    monkeypatch.setattr(system_proxy, "_tcp_probe", _fake_probe(set()))
    out = system_proxy.resolve("http://127.0.0.1:7897")
    assert out["listening"] is False
    assert out["source"] == "none"
    assert out["address"] == "http://127.0.0.1:7890"  # 供界面提示「系统代理 X 没在听」
    assert [c["listening"] for c in out["candidates"]] == [False, False]


def test_系统与手填同址时候选去重(monkeypatch):
    monkeypatch.setattr(system_proxy, "_read_registry_proxy", lambda: "http://127.0.0.1:7897")
    monkeypatch.setattr(system_proxy, "_tcp_probe", _fake_probe({"http://127.0.0.1:7897"}))
    out = system_proxy.resolve("127.0.0.1:7897")
    assert len(out["candidates"]) == 1
    assert out["source"] == "system"


def test_没有系统代理时只用手填(monkeypatch):
    monkeypatch.setattr(system_proxy, "_tcp_probe", _fake_probe({"http://127.0.0.1:10809"}))
    out = system_proxy.resolve("http://127.0.0.1:10809")
    assert out["listening"] is True
    assert out["source"] == "manual"
    assert out["system_address"] == ""


def test_手填地址为空且无系统代理时直连(monkeypatch):
    monkeypatch.setattr(system_proxy, "_tcp_probe", _fake_probe(set()))
    out = system_proxy.resolve("")
    assert out["listening"] is False
    assert out["address"] == ""
    assert out["source"] == "none"
    assert out["candidates"] == []


# ── 联网搜索探测：端口在听 ≠ 能搜到 ─────────────────────────

def test_默认不做搜索探测(monkeypatch):
    monkeypatch.setattr(system_proxy, "_read_registry_proxy", lambda: "http://127.0.0.1:7897")
    monkeypatch.setattr(system_proxy, "_tcp_probe", _fake_probe({"http://127.0.0.1:7897"}))
    called: list[str] = []
    monkeypatch.setattr(system_proxy, "check_search",
                        lambda proxy="": (called.append(proxy), (True, ""))[1])
    out = system_proxy.resolve("")
    assert called == []
    assert "search_reachable" not in out


def test_search为真时经生效代理探测并回填(monkeypatch):
    monkeypatch.setattr(system_proxy, "_read_registry_proxy", lambda: "http://127.0.0.1:7897")
    monkeypatch.setattr(system_proxy, "_tcp_probe", _fake_probe({"http://127.0.0.1:7897"}))
    seen: list[str] = []

    def fake_search(proxy=""):
        seen.append(proxy)
        return False, "搜索无结果（网络或搜索源不可用）"

    monkeypatch.setattr(system_proxy, "check_search", fake_search)
    out = system_proxy.resolve("", search=True)
    assert seen == ["http://127.0.0.1:7897"]  # 端口在听但搜不到 → 用生效代理去验，不是直连
    assert out["search_reachable"] is False
    assert "搜索无结果" in out["search_detail"]


def test_无可用代理时搜索探测走直连(monkeypatch):
    monkeypatch.setattr(system_proxy, "_tcp_probe", _fake_probe(set()))
    seen: list[str] = []
    monkeypatch.setattr(system_proxy, "check_search",
                        lambda proxy="": (seen.append(proxy), (True, "搜索正常（返回 2 条）"))[1])
    out = system_proxy.resolve("", search=True)
    assert seen == [""]
    assert out["search_reachable"] is True
    assert out["search_detail"] == "搜索正常（返回 2 条）"


# ── 兼容性：旧字段不能被删 ───────────────────────────────────

def test_保留旧字段listening与address(monkeypatch):
    """前端旧版本只读 listening/address，升级后必须仍然拿得到。"""
    monkeypatch.setattr(system_proxy, "_tcp_probe", _fake_probe({"http://127.0.0.1:7897"}))
    out = system_proxy.resolve("http://127.0.0.1:7897")
    assert set(["listening", "address"]).issubset(out)
    assert isinstance(out["listening"], bool)
    assert isinstance(out["address"], str)
