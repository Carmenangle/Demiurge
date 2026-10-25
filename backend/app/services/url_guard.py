"""URL 安全校验：防止后端主动 fetch 用户传入的任意 URL（SSRF）。

两类接入：
  validate_comfyui_url(url)  —— 本机 ComfyUI 地址，只允许 localhost/127.x/::1，
                                 以及用户在 COMFYUI_ALLOWED_HOSTS 环境变量里明确配置的局域网地址。
  validate_media_url(url)    —— 公网媒体 URL（外部图片/视频保存），允许 http/https，
                                 但阻止指向私网/metadata/localhost 的地址，防 SSRF。

两个校验失败都抛 ValueError，调用方按需转 HTTPException。

出站建连 IP 钉扎（2026-10-24 审计建议 3，TOCTOU 修复）：
  validate 校验时解析一次 DNS，httpx 建连时会再解析一次——两次解析之间 DNS rebinding
  可把已验证的公网域名换到私网/metadata IP，校验即被绕过。pinned_media_url /
  pinned_host 把「校验时解析到的 IP」钉给 with 块内的建连：校验与建连共用同一批 IP，
  Host/SNI 仍是原域名（TLS 校验不受影响）。所有「validate 后再 fetch」的出站链
  （image_proxy / image_store 保存 / visual_ci 取图 / model_downloader /
  material_sources httpx 兜底 / web_search 正文抓取）都必须包进对应上下文。
"""
from __future__ import annotations

import contextlib
import ipaddress
import os
import re
import socket
import threading
from typing import Iterator
from urllib.parse import urlparse

# ---- 可配置白名单 ---- #
_ENV_ALLOWED = os.environ.get("COMFYUI_ALLOWED_HOSTS", "")
_COMFYUI_EXTRA: list[str] = [h.strip().lower() for h in _ENV_ALLOWED.split(",") if h.strip()]

_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}

# AWS/GCP/Azure 实例 metadata IP
_METADATA_IPS = {"169.254.169.254", "fd00:ec2::254"}

# 需要封锁的私网 CIDR
_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local
    ipaddress.ip_network("100.64.0.0/10"),     # shared address space
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
]


def _is_private_ip(host: str) -> bool:
    """尝试把 host 解析成 IP，检测是否属于私网/loopback。解析失败当公网处理（不阻止）。"""
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_loopback or addr.is_link_local or addr.is_private:
            return True
        if str(addr) in _METADATA_IPS:
            return True
        for net in _PRIVATE_NETS:
            if addr in net:
                return True
        return False
    except ValueError:
        return False


def _resolve_ips(hostname: str) -> list[str]:
    """解析主机名为去重 IP 列表（全模块唯一解析点，校验与钉扎共用）。

    失败返回空表——沿用「解析失败不拒绝合法请求」的保守语义，调用方按空表=不钉扎处理。
    """
    try:
        infos = _orig_getaddrinfo(hostname, None)
    except OSError:
        return []
    ips: list[str] = []
    for *_, addr in infos:
        ip = addr[0]
        if ip not in ips:
            ips.append(ip)
    return ips


def _resolve_and_check(hostname: str) -> list[str]:
    """DNS 解析后逐 IP 检查（防 rebinding），返回解析到的 IP 供钉扎复用——
    校验与建连必须同源，二次解析会重开 TOCTOU 窗口。"""
    ips = _resolve_ips(hostname)
    for ip in ips:
        if _is_private_ip(ip) or ip in _METADATA_IPS:
            raise ValueError(f"URL 解析后指向私网地址 {ip}，已拒绝")
    return ips


def validate_comfyui_url(url: str) -> str:
    """校验 ComfyUI 地址：只允许本机 localhost/127.x/::1 及环境变量白名单里的主机。"""
    if not url or not url.strip():
        raise ValueError("ComfyUI URL 不能为空")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "ws"):
        raise ValueError("ComfyUI URL 只支持 http/ws 协议")
    if parsed.username or parsed.password:
        raise ValueError("ComfyUI URL 不允许内嵌凭据")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("ComfyUI URL 缺少主机名")
    if hostname in _LOOPBACK_NAMES or hostname in _COMFYUI_EXTRA:
        return url.rstrip("/")
    # 也允许 127.0.0.0/8 整段
    try:
        if ipaddress.ip_address(hostname).is_loopback:
            return url.rstrip("/")
    except ValueError:
        pass
    raise ValueError(
        f"ComfyUI URL 主机名 {hostname!r} 不在允许范围内（仅限 localhost/127.x/::1，"
        "或在环境变量 COMFYUI_ALLOWED_HOSTS 里配置额外主机）"
    )


def _validate_media_url_core(url: str) -> tuple[str, str, list[str]]:
    """媒体 URL 校验本体，返回 (url, hostname, 校验解析到的 IP)。

    校验与解析必须同源：返回的 IP 是「通过检查的那一批」，钉扎只允许钉这批。
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"媒体 URL 只支持 http/https，当前协议：{parsed.scheme!r}")
    if parsed.username or parsed.password:
        raise ValueError("媒体 URL 不允许内嵌凭据")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("媒体 URL 缺少主机名")
    if hostname in _LOOPBACK_NAMES or _is_private_ip(hostname):
        raise ValueError(f"媒体 URL 指向本机或私网地址 {hostname!r}，已拒绝")
    # DNS 解析后再检查，防 rebinding；IP 交给钉扎复用
    return url, hostname, _resolve_and_check(hostname)


def validate_media_url(url: str) -> str:
    """校验公网媒体 URL：允许 http/https 公网地址，阻止私网/metadata/file 等协议。"""
    if not url or not url.strip():
        raise ValueError("媒体 URL 不能为空")
    if url.strip().startswith("data:"):
        return url  # data URI 在调用方本地解码，不走网络，直接放行
    _validate_media_url_core(url)
    return url


# ---- 出站建连 IP 钉扎 ---- #
# 线程局部表：hostname → IP 栈（栈顶生效；同线程嵌套钉扎同一主机时内层退出恢复外层）。
# getaddrinfo 包装是模块级的：httpcore 建连走 socket.create_connection，其内部按模块属性
# 动态查找 getaddrinfo ⇒ 替换 socket.getaddrinfo 即可让建连只拿到被钉 IP。未钉主机零
# 开销（一次 dict 查找后原样转发）；经代理建连时本地不解析目标主机，钉扎天然惰性。
_pin_local = threading.local()
if getattr(socket.getaddrinfo, "_is_pin_wrapper", False):
    _orig_getaddrinfo = socket.getaddrinfo._pin_orig  # type: ignore[attr-defined]  # 防重复包装
else:
    _orig_getaddrinfo = socket.getaddrinfo


def _patched_getaddrinfo(host, port, *args, **kwargs):
    mapping = getattr(_pin_local, "hosts", None)
    stack = mapping.get(host.lower()) if (mapping and isinstance(host, str)) else None
    ips = stack[-1] if stack else None
    if not ips:
        return _orig_getaddrinfo(host, port, *args, **kwargs)
    family = kwargs.get("family", 0)
    sock_type = kwargs.get("type", 0)
    if not family and args:
        family = args[0]
    if not sock_type and len(args) > 1:
        sock_type = args[1]
    results = []
    for ip in ips:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        af = socket.AF_INET if addr.version == 4 else socket.AF_INET6
        if family not in (0, af):
            continue  # 钉扎 IP 不满足请求族 ⇒ 空结果拒连，绝不回落系统解析重开窗口
        sockaddr = (ip, port or 0) if addr.version == 4 else (ip, port or 0, 0, 0)
        proto = socket.IPPROTO_TCP if sock_type in (0, socket.SOCK_STREAM) else 0
        results.append((af, sock_type or socket.SOCK_STREAM, proto, "", sockaddr))
    return results


_patched_getaddrinfo._is_pin_wrapper = True  # type: ignore[attr-defined]
_patched_getaddrinfo._pin_orig = _orig_getaddrinfo  # type: ignore[attr-defined]
socket.getaddrinfo = _patched_getaddrinfo


def _pin_host_ips(hostname: str, ips: list[str]) -> None:
    stack_map = getattr(_pin_local, "hosts", None)
    if stack_map is None:
        stack_map = {}
        _pin_local.hosts = stack_map
    stack_map.setdefault(hostname, []).append(list(ips))


def _unpin_host(hostname: str) -> None:
    stack_map = getattr(_pin_local, "hosts", None)
    if stack_map:
        entries = stack_map.get(hostname)
        if entries:
            entries.pop()
            if not entries:
                stack_map.pop(hostname, None)


@contextlib.contextmanager
def pinned_media_url(url: str) -> Iterator[str]:
    """校验公网媒体 URL 并把校验解析到的 IP 钉给 with 块内的建连，yield 校验后 URL。

    所有「validate_media_url 后再 fetch」的链路（image_proxy / image_store 保存 /
    visual_ci 取图）都必须改走本上下文——校验与建连共用同一批 IP，rebinding TOCTOU
    窗口关闭。重定向逐跳各自进入本上下文（每跳校验+钉扎一次）。
    """
    url, hostname, ips = _validate_media_url_core(url)
    if ips:
        _pin_host_ips(hostname, ips)
    try:
        yield url
    finally:
        _unpin_host(hostname)


@contextlib.contextmanager
def pinned_host(hostname: str) -> Iterator[None]:
    """白名单固定域名的出站下载用（model_downloader / material_sources httpx 兜底）：
    解析一次并钉给 with 块内的建连，含私网/metadata 拒绝（固定公网域名解析到私网只能是攻击）。

    与 pinned_media_url 的区别：不做 URL 协议/凭据校验（调用方已有白名单）；解析失败
    （OSError → 空表）不钉扎直接放行，沿用「不因网络故障拒绝合法请求」的保守语义。
    """
    host = (hostname or "").strip().lower()
    ips = _resolve_ips(host)
    if ips:
        for ip in ips:
            if _is_private_ip(ip) or ip in _METADATA_IPS:
                raise ValueError(f"{host} 解析后指向私网地址 {ip}，已拒绝")
        _pin_host_ips(host, ips)
    try:
        yield
    finally:
        _unpin_host(host)


def is_local_view_url(url: str) -> bool:
    """判断是否是本应用的 local-view 代理地址（内部落盘图片，不走 validate_media_url）。

    两种形态都算：
    - 根相对 `/api/comfyui/local-view?...`（2026-10-24 起的 wire 形态，见 services/view_urls）：
      没有主机部分，只能解析到「发出这个响应的同一个后端」，与回环形态同信任级。
      但必须拒 `//` 开头的协议相对形态——那种写法的 host 由字符串本身给出，等于放行外部主机。
    - 绝对回环 + 路径是 /comfyui/local-view（历史落盘与内部自取地址）。
    只匹配路径子串会被 http://evil.com/comfyui/local-view 绕过 SSRF 校验，故须校验主机。
    """
    value = (url or "").strip()
    if value.startswith("/"):
        if value.startswith("//"):
            return False
        return bool(re.match(r"/api/comfyui/local-view\?", value))
    try:
        parsed = urlparse(value)
    except (ValueError, AttributeError):
        return False
    host = (parsed.hostname or "").lower()
    is_loopback = host in _LOOPBACK_NAMES
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False
    # 路径可能带 /api 前缀（前端经 API_BASE 访问），故 search 不锚定开头；主机已限 loopback。
    return is_loopback and bool(re.search(r"/comfyui/local-view\b", parsed.path or ""))


# local-view 只服务媒体文件（图片/视频/音频）。用扩展名白名单作边界：
# 该端点会读任意本地路径（对话背景图允许用户填任意图片完整路径，故不能按目录 jail），
# 但严格限制只回媒体文件——挡住读 .env/.db/.py/密钥/源码等敏感文件的 LFI 攻击面。
_MEDIA_EXTS = {
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico", "avif", "tiff",
    "mp4", "webm", "mov", "mkv", "m4v", "avi", "ogv",
    "wav", "mp3", "flac", "ogg", "oga", "opus", "m4a", "aac", "wma",
}


def is_media_file(path: str) -> bool:
    """路径是否指向允许的媒体扩展名（大小写不敏感）。无扩展名/非媒体一律 False。"""
    from pathlib import Path
    ext = Path(path).suffix.lower().lstrip(".")
    return ext in _MEDIA_EXTS
