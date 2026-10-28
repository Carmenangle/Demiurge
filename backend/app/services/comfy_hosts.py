"""ComfyUI 主机白名单的「登记 + 落盘 + 开机播种」属主（2026-10-27）。

背景：`url_guard.validate_comfyui_url` 只认回环 + 白名单，而 PC 侧此前唯一的非回环入口
是**导入期读一次**的 `COMFYUI_ALLOWED_HOSTS` 环境变量 ⇒ 多加一台云端/局域网 ComfyUI 就要
改环境并重启。手机端早有「保存即登记」（壳层 `POST /api/mobile/comfy-host`），但那套逻辑
在 Chaquopy 启动器里，PC 用不到。本模块把它升成后端能力：设置页保存 ComfyUI 地址
（`POST /api/comfyui/config`）顺带登记，落盘后重启仍生效。

两份状态，单向同步：
- `DATA_DIR/comfy_hosts.json`＝**落盘真源**（开机播种的依据），schema `{"hosts": [...]}`
  与手机壳写的那份逐字相同 ⇒ 两端共用同一个文件，不产生第二份数据；
- `url_guard._COMFYUI_EXTRA`＝**运行期判定面**。

登记＝进内存 + 写文件（仅成功才写）；播种＝读文件进内存。手机壳本轮保留它自己的薄封装
未改成委托本模块（「先共存」的定案）：同一文件、同一 schema、登记幂等，故不冲突。

上限 `url_guard.MAX_EXTRA_HOSTS` 是环境变量与运行时登记共用的一个池：登记先过内存判定，
超限即抛 ⇒ 落盘条数天然不会超上限。文件只记「设置页保存过的主机」，环境变量那份不在里面。
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from app import config
from app.services import url_guard

FILE_NAME = "comfy_hosts.json"

# 与 url_guard.validate_comfyui_url 的协议面一致（判定真源在那边）；这里只是不把
# 明显用不了的形态（ftp/file/乱码）存进主机簿、白占 8 个槽之一。
_SCHEMES = ("", "http", "https", "ws", "wss")


def _hosts_path() -> Path:
    # 调用时读 config.DATA_DIR（不是导入期常量）：测试与隔离实例都要能重定向。
    return config.DATA_DIR / FILE_NAME


def load_hosts() -> list[str]:
    """落盘的主机清单。文件缺失/损坏/形态不对 ⇒ 空表，绝不抛。"""
    path = _hosts_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return []
    raw = data.get("hosts") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    return [str(h) for h in raw if isinstance(h, str)]


def _persist(hosts: list[str]) -> None:
    path = _hosts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"hosts": hosts}, ensure_ascii=False, indent=2), encoding="utf-8")


def host_of(url: str) -> str:
    """从地址里取出要登记的主机名（小写、去尾点）。

    兼容裸 `host:port` 填法（与手机壳同口径）；内嵌凭据的地址取其主机——提交时
    `validate_comfyui_url` 仍会因凭据拒绝，这里不额外开口子。
    """
    value = (url or "").strip()
    if not value:
        raise ValueError("地址不能为空")
    if "://" not in value:
        value = "http://" + value  # 裸 host:port
    parsed = urlparse(value)
    if parsed.scheme not in _SCHEMES:
        raise ValueError(f"不支持的协议：{parsed.scheme}")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError(f"地址无法解析出主机名：{url!r}")
    return host


def register_from_url(url: str) -> str:
    """登记地址里的主机（内存 + 落盘），返回效果：`"loopback"` 或主机名。

    失败一律抛 ValueError（形态不合法 / 白名单已达上限）——由调用方决定怎么显示，
    本模块不吞。回环不写文件（恒放行，登记它是噪音）。
    """
    effect = url_guard.register_comfyui_host(host_of(url))
    if effect == "loopback":
        return effect
    hosts = load_hosts()
    if effect not in hosts:
        hosts.append(effect)
        _persist(hosts)
    return effect


def remove_from_url(url: str) -> str:
    """注销地址里的主机（内存 + 落盘），返回被判定的主机名。

    两层各删一次、各自幂等：内存面可能因播种/环境变量与本文件不一致（用户手改过文件、
    `COMFYUI_ALLOWED_HOSTS` 也声明了同一台），只删一层就会留下「删了还在」的假象。
    回环返回 `"loopback"` 且不碰文件；环境变量声明的主机由 url_guard 抛 ValueError。
    """
    host = host_of(url)
    effect = url_guard.unregister_comfyui_host(host)
    if effect != "loopback":
        hosts = load_hosts()
        kept = [h for h in hosts if h != host]
        if kept != hosts:
            _persist(kept)
    return host


def seed_runtime() -> list[str]:
    """把落盘的主机播种进运行期白名单（开机调用；重启后已保存地址仍可用）。

    单条失败跳过（脏条目不该拖垮整本簿子），返回本次真正登记进去的主机。
    """
    seeded: list[str] = []
    for host in load_hosts():
        try:
            effect = url_guard.register_comfyui_host(host)
        except ValueError:
            continue
        if effect != "loopback":
            seeded.append(effect)
    return seeded


def snapshot() -> dict[str, object]:
    """主机簿现状：运行期判定面 + 落盘真源 + 上限（设置页/诊断用）。"""
    return {
        "hosts": list(url_guard.comfy_extra_hosts()),
        "saved": load_hosts(),
        "cap": url_guard.MAX_EXTRA_HOSTS,
    }
