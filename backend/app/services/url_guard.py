"""URL 安全校验：防止后端主动 fetch 用户传入的任意 URL（SSRF）。

两类接入：
  validate_comfyui_url(url)  —— 本机 ComfyUI 地址，只允许 localhost/127.x/::1，
                                 以及用户在 COMFYUI_ALLOWED_HOSTS 环境变量里明确配置的局域网地址。
  validate_media_url(url)    —— 公网媒体 URL（外部图片/视频保存），允许 http/https，
                                 但阻止指向私网/metadata/localhost 的地址，防 SSRF。

两个校验失败都抛 ValueError，调用方按需转 HTTPException。
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
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


def _resolve_and_check(hostname: str) -> None:
    """DNS 解析后再检查，防 DNS rebinding。解析失败视为可通过（保守策略：不因网络故障拒绝合法请求）。"""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return
    for _, _, _, _, addr in infos:
        ip = addr[0]
        if _is_private_ip(ip) or ip in _METADATA_IPS:
            raise ValueError(f"URL 解析后指向私网地址 {ip}，已拒绝")


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


def validate_media_url(url: str) -> str:
    """校验公网媒体 URL：允许 http/https 公网地址，阻止私网/metadata/file 等协议。"""
    if not url or not url.strip():
        raise ValueError("媒体 URL 不能为空")
    if url.strip().startswith("data:"):
        return url  # data URI 在调用方本地解码，不走网络，直接放行
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
    # DNS 解析后再检查，防 rebinding
    _resolve_and_check(hostname)
    return url


def is_local_view_url(url: str) -> bool:
    """判断是否是本应用的 local-view 代理地址（内部落盘图片，不走 validate_media_url）。

    必须同时满足：主机是本机 loopback + 路径是 /comfyui/local-view。
    只匹配路径子串会被 http://evil.com/comfyui/local-view 绕过 SSRF 校验，故须校验主机。
    """
    try:
        parsed = urlparse(url.strip())
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


# local-view 只服务图片/视频文件。用扩展名白名单作边界：
# 该端点会读任意本地路径（对话背景图允许用户填任意图片完整路径，故不能按目录 jail），
# 但严格限制只回媒体文件——挡住读 .env/.db/.py/密钥/源码等敏感文件的 LFI 攻击面。
_MEDIA_EXTS = {
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico", "avif", "tiff",
    "mp4", "webm", "mov", "mkv", "m4v", "avi",
}


def is_media_file(path: str) -> bool:
    """路径是否指向允许的媒体扩展名（大小写不敏感）。无扩展名/非媒体一律 False。"""
    from pathlib import Path
    ext = Path(path).suffix.lower().lstrip(".")
    return ext in _MEDIA_EXTS
