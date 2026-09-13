"""更新进度：git/pip 输出 → 结构化进度（含字节数与速度）。

拆成独立模块是为了能单测解析逻辑 —— 正则跑在别人的输出格式上，
是这套东西里最容易悄悄失效的部分。

git 的传输进度走 stderr，形如：
    Receiving objects:  47% (1234/2600), 12.34 MiB | 3.21 MiB/s
pip 的下载行形如：
    Downloading numpy-2.1.0-cp313-win_amd64.whl (12.6 MB)
"""
from __future__ import annotations

import re

_GIT_RECV = re.compile(
    r"Receiving objects:\s+(\d+)%\s+\((\d+)/(\d+)\)"
    r"(?:,\s+([\d.]+)\s*([KMG]i?B))?"
    r"(?:.*?\|\s*([\d.]+)\s*([KMG]i?B)/s)?")
_GIT_RESOLVE = re.compile(r"Resolving deltas:\s+(\d+)%")
_PIP_DOWNLOAD = re.compile(r"Downloading\s+(\S+?)\s+\(([\d.]+)\s*([kKMG]i?B)\)")
_PIP_USING_CACHE = re.compile(r"Using cached\s+(\S+?)\s+\(([\d.]+)\s*([kKMG]i?B)\)")
_PIP_INSTALLING = re.compile(r"Installing collected packages:\s*(.+)")

_UNITS = {"b": 1, "kb": 1024, "kib": 1024, "mb": 1024**2, "mib": 1024**2,
          "gb": 1024**3, "gib": 1024**3}


def to_bytes(value: str, unit: str) -> int:
    """`12.34` + `MiB` → 字节数。git 用 MiB，pip 用 MB，这里一律按 1024 折算
    （pip 的 MB 实际也是 1024 进制，它显示的是 MiB 的值）。"""
    try:
        return int(float(value) * _UNITS.get(unit.lower(), 1))
    except (ValueError, TypeError):
        return 0


def human_bytes(n: int) -> str:
    if n <= 0:
        return "0 B"
    for unit, size in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} B"


def parse_git_line(line: str) -> dict | None:
    """git 传输进度行 → {phase, percent, received, total_objects, speed}。

    非进度行返回 None。git 用 \\r 刷新同一行，调用方需先按 \\r 切开。
    """
    m = _GIT_RECV.search(line)
    if m:
        pct, got, total, amount, amount_unit, speed, speed_unit = m.groups()
        return {
            "phase": "download",
            "percent": int(pct),
            "objects_done": int(got),
            "objects_total": int(total),
            "received_bytes": to_bytes(amount, amount_unit) if amount else 0,
            "speed_bps": to_bytes(speed, speed_unit) if speed else 0,
        }
    m = _GIT_RESOLVE.search(line)
    if m:
        # 解压/应用差异阶段，没有字节信息，但要让进度条继续动
        return {"phase": "resolve", "percent": int(m.group(1))}
    return None


def parse_pip_line(line: str) -> dict | None:
    """pip 输出行 → 依赖下载事件。

    区分真下载与命中缓存：命中缓存不占网络，但用户仍该看到这个包被装了。
    """
    m = _PIP_DOWNLOAD.search(line)
    if m:
        name, size, unit = m.groups()
        return {"kind": "download", "file": name, "bytes": to_bytes(size, unit)}
    m = _PIP_USING_CACHE.search(line)
    if m:
        name, size, unit = m.groups()
        return {"kind": "cached", "file": name, "bytes": to_bytes(size, unit)}
    m = _PIP_INSTALLING.search(line)
    if m:
        names = [s.strip() for s in m.group(1).split(",") if s.strip()]
        return {"kind": "installing", "packages": names}
    return None


def split_progress_stream(chunk: str) -> list[str]:
    """git 用 \\r 原地刷新进度，按 \\r 和 \\n 一起切才能拿到每次刷新。"""
    return [s for s in re.split(r"[\r\n]+", chunk) if s.strip()]
