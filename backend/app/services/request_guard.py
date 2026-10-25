"""入站请求头安全闸（单一属主）：Origin/Host 必须声称来自本机。

为什么需要：回环门禁（main._loopback_only）只看 TCP 对端地址，挡不住「浏览器中介」——
本机浏览器发起的跨站请求，对端地址同样是 127.0.0.1：
- DNS rebinding（主修复动因）：恶意域名 rebind 到 127.0.0.1 后，页面 fetch 属「同源」，
  GET 不带 Origin、CORS 不拦，但 Host 头仍是攻击者域名 ⇒ 按 Host 校验，否则
  GET /api/user-state 返回含各模型 apiKey 的 settings 可被攻击者页面读取。
- drive-by CSRF（实测面收敛，2026-10-24）：JSON Pydantic 模型端点被 FastAPI 0.140 的
  严格 Content-Type 默认挡住（无 Content-Type / form 编码体一律 422，不解析），
  跨站表单/Beacon 打不到 install-tracked 这类端点；**仍可达**的是 multipart 端点
  （/api/comfyui/upload、/api/user-state/wallpapers/upload，跨站 <form enctype=multipart>
  是简单请求必带 Origin）与无 body 的 POST。Origin 闸把整类拦死，且不依赖
  FastAPI 的版本行为——防线自有化为合同。
- 判定只看主机名、不锁端口：端口由 DEMIURGE_PORT / LAF_RUNTIME_PORT 协商
  （desktop/tauri/src/runtime_host.rs），锁端口＝制造第二份端口真源，且会挡掉
  e2e（127.0.0.1:15173）等隔离端口的合法流量。

语义（docs/security-hardening-origin-guard-and-svg-2026-10-24.md §1.2，2026-10-24）：
- Origin 缺失＝放行：非浏览器客户端与 <img>/<video> 等媒体标签不发 Origin，本闸只管浏览器中介。
- Origin host 为回环（端口不限）＝放行；字面量 "null"（sandboxed iframe）一律拒绝。
- Host 的 host 部分为回环（端口不限）或 testserver（Starlette TestClient 默认值，
  浏览器永不发送、能伪造 Host 的本机进程本就在信任面内）＝放行；空 Host 拒绝。
- allow_remote=True（LAF_ALLOW_REMOTE=1）时全部闸跳过：远端模式是显式自担风险的
  逃生门，语义保持「全放开」，不做半吊子鉴权。

残留边界（有意接受）：本机恶意进程可直接调 API——单机无鉴权设计的固有边界；
本闸只拒绝「声称来自非本机的浏览器请求」，不给 API 加身份认证。
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_TOOL_HOSTS = {"testserver"}

_CLIENT_ONLY_DETAIL = "仅允许本机访问（如需局域网访问请设 LAF_ALLOW_REMOTE=1）"
_ORIGIN_DETAIL = "请求 Origin 非本机来源，已拒绝（跨站请求防护）"
_HOST_DETAIL = "请求 Host 非本机，已拒绝（DNS rebinding 防护）"


def _host_of(value: str) -> str:
    """从 URL（Origin）或 Host 头里取主机名（小写、去 IPv6 方括号、去端口）。

    Origin 是完整 URL，交给 urlparse（hostname 已去方括号并小写，IPv6 地址内含
    冒号，绝不能再按 ':' 切端口）；Host 头是 authority 形式，裸 IPv6 必带方括号，
    按方括号取址、其余按第一个 ':' 切端口。
    """
    value = (value or "").strip()
    if "://" in value:
        try:
            return (urlparse(value).hostname or "").lower()
        except ValueError:
            return ""
    if value.startswith("["):
        end = value.find("]")
        return value[1:end].lower() if end != -1 else ""
    return value.split(":", 1)[0].lower()


def _is_loopback_host(host: str) -> bool:
    if not host:
        return False
    if host in _LOOPBACK_HOSTS | _TOOL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def origin_allowed(origin: str) -> bool:
    """Origin 头值是否放行：host 为回环（端口不限）；缺失由调用方短路，字面量 null 拒绝。"""
    value = (origin or "").strip()
    if not value or value.lower() == "null":
        return False
    return _is_loopback_host(_host_of(value))


def host_allowed(host_header: str) -> bool:
    """Host 头是否放行：host 部分为回环或测试工具默认值（端口不限），空拒绝。"""
    return _is_loopback_host(_host_of(host_header))


def inbound_verdict(
    *,
    client_host: str,
    origin: str,
    host_header: str,
    allow_remote: bool,
) -> str | None:
    """入站请求三道闸的统一裁决：放行返回 None，否则返回 403 的 detail 文案。

    client_host 门（TCP 对端）保持 main 原实现的逐字节语义，包括
    「非 IP 字符串回落 localhost/空串」的兼容分支。
    """
    if allow_remote:
        return None
    try:
        is_loopback = ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        is_loopback = client_host in ("localhost", "")
    if not is_loopback:
        return _CLIENT_ONLY_DETAIL
    if origin.strip() and not origin_allowed(origin):
        return _ORIGIN_DETAIL
    if not host_allowed(host_header):
        return _HOST_DETAIL
    return None
