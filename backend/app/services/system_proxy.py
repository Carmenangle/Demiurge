"""系统代理解析：读「电脑本身」的系统代理设置并验证可用性。

单一属主：所有「继承全局代理」的判定都走这里，不要再各自 `getproxies` / 读注册表。

三层语义（自上而下短路）：
1. **系统代理**：Windows 走 WinINET（`HKCU\\...\\Internet Settings` 的
   `ProxyEnable`/`ProxyServer`，与浏览器/Clash「系统代理」开关同一处真源），
   非 Windows 走环境变量。这是「电脑有没有开代理」的唯一权威来源。
2. **候选存活**：TCP 能连上 `host:port`（真的有进程在听）才算可用；
   只要端口没在听，一律判不可用——避免把「代理关了但系统设置还留着」当成有代理。
3. **联网搜索可用**（可选、按需触发）：真跑一次最小搜索。端口在听 ≠ 能出网
   （Clash 直连模式/规则未命中时端口照样在听），某站可达也 ≠ 能搜到东西——
   只有这一步能回答「现在这套代理/直连能不能真的用」。

返回值由 `/user-state/proxy-status` 直接透出（含旧字段 `listening`/`address` 保持兼容）。
"""
from __future__ import annotations

import sys
import urllib.request
from urllib.parse import urlparse

# TCP 探活超时：本机端口连不上会立刻 ECONNREFUSED，挂了代理才会等满。
_TCP_TIMEOUT = 1.5

# 可用于 HTTP 代理的 scheme；socks 需要额外依赖（httpx[socks]），只记录不使用。
_HTTP_SCHEMES = ("http", "https")


def normalize(address: str) -> str:
    """归一成带 scheme 的代理地址：`127.0.0.1:7897` → `http://127.0.0.1:7897`。"""
    text = (address or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    return text


def _pick_from(mapping: dict) -> str:
    """从 {scheme: url} 里挑一个可用的 http(s) 代理地址（https 优先）。"""
    if not isinstance(mapping, dict):
        return ""
    for scheme in ("https", "http", "all"):
        value = mapping.get(scheme)
        if not isinstance(value, str):
            continue
        text = normalize(value)
        parsed = urlparse(text)
        if parsed.scheme.lower() in _HTTP_SCHEMES and parsed.hostname:
            return text
    return ""


def _read_registry_proxy() -> str:
    """Windows WinINET 系统代理（ProxyEnable 为真才返回值）。非 Windows / 读取失败返回空。"""
    if sys.platform != "win32":
        return ""
    getter = getattr(urllib.request, "getproxies_registry", None)
    if getter is None:
        return ""
    try:
        return _pick_from(getter())
    except Exception:  # noqa: BLE001 - 注册表不可读（权限/异常策略）按「没有系统代理」处理
        return ""


def read_system_proxy() -> tuple[str, str]:
    """返回 (address, source)。source ∈ {system, env, none}。

    系统代理（注册表）优先，其次环境变量——两者都没有才是 really「没开代理」。
    """
    registry = _read_registry_proxy()
    if registry:
        return registry, "system"
    try:
        env = _pick_from(urllib.request.getproxies_environment())
    except Exception:  # noqa: BLE001
        env = ""
    if env:
        return env, "env"
    return "", "none"


def _tcp_probe(address: str, timeout: float = _TCP_TIMEOUT) -> bool:
    """TCP 能否连上该代理的 host:port（有进程在听即真）。"""
    import socket

    text = normalize(address)
    if not text:
        return False
    try:
        parsed = urlparse(text)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 0
    except ValueError:
        return False
    if port <= 0:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_search(proxy: str = "") -> tuple[bool, str]:
    """真跑一次最小联网搜索，回答「现在能不能搜到东西」（空 proxy=直连）。

    判据与灵感搜索完全同源——同一 Adapter、同一代理、同一套解析（`web_search.probe`）。
    换来的代价是一次真实搜索请求，所以只在用户手动点「重新检测」时才做。
    """
    from app.services import web_search

    return web_search.probe(proxy)


def resolve(manual: str = "", *, search: bool = False) -> dict:
    """解析生效代理，返回给前端的完整判定结果。

    候选顺序 = 系统代理 → 应用内手填地址（去重）。取**首个 TCP 存活**者生效；
    都不存活则 `listening=False`、`address` 仍给出系统代理（有则）以便提示用户
    「系统代理指向 X 但没在听」。`source ∈ {system, env, manual, none}`。

    search=True 时额外真跑一次联网搜索（经生效代理；无生效代理则直连），
    结果放 `search_reachable` / `search_detail`——这是唯一能回答
    「端口在听但实际搜不到」这种情形的手段。
    """
    system_address, system_source = read_system_proxy()
    manual_address = normalize(manual)

    candidates: list[dict] = []
    seen: set[str] = set()
    for address, source in ((system_address, system_source), (manual_address, "manual")):
        if not address or address in seen or source == "none":
            continue
        seen.add(address)
        candidates.append({
            "address": address,
            "source": source,
            "listening": _tcp_probe(address),
        })

    chosen = next((c for c in candidates if c["listening"]), None)
    result = {
        # 兼容旧字段：listening=是否选中了可用代理；address=生效地址（空=直连）
        "listening": chosen is not None,
        "address": chosen["address"] if chosen else "",
        "source": chosen["source"] if chosen else "none",
        "system_address": system_address,
        "manual_address": manual_address,
        "candidates": candidates,
    }
    # 没有可用代理时也把「指向哪个地址」透出，便于前端提示「系统代理 X 未在监听」
    if chosen is None:
        result["address"] = system_address or manual_address
    if search:
        ok, detail = check_search(chosen["address"] if chosen else "")
        result["search_reachable"] = ok
        result["search_detail"] = detail
    return result
