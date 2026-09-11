"""每个小仓库的「当前色彩约束」。

落 data/palette_pref.json 而不进 SQLite：这是一小块按仓库存的用户偏好，
没有关系查询需求，对齐 user_state.json 的做法。

颜色规范化在这里收口 —— 注入提示词时必须是干净的 #rrggbb，
不能把用户随手粘的 `rgb(1,2,3)` 或带空格的串原样塞进提示词。
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any

from app.config import DATA_DIR

_LOCK = threading.Lock()

# 一次最多带多少个颜色进提示词。太多会淹掉提示词本身的语义，
# 实测 8 个以内模型还能照顾到，再多就基本被忽略了。
MAX_COLORS = 12

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _path():
    return DATA_DIR / "palette_pref.json"


def normalize_colors(colors: list[str]) -> list[str]:
    """任意输入 → 去重后的 #rrggbb 列表。非法值直接丢弃。"""
    out: list[str] = []
    seen: set[str] = set()
    for raw in colors or []:
        m = _HEX.match(str(raw).strip())
        if not m:
            continue
        h = m.group(1).lower()
        if len(h) == 3:                      # #abc -> #aabbcc
            h = "".join(c * 2 for c in h)
        val = f"#{h}"
        if val not in seen:
            seen.add(val)
            out.append(val)
        if len(out) >= MAX_COLORS:
            break
    return out


def _read_all() -> dict[str, Any]:
    p = _path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}


def save(repo_id: str, colors: list[str], name: str = "") -> dict[str, Any]:
    """写入一个仓库的色彩约束。colors 为空即清除。"""
    clean = normalize_colors(colors)
    with _LOCK:
        allp = _read_all()
        if clean:
            allp[repo_id] = {"colors": clean, "name": name}
        else:
            allp.pop(repo_id, None)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps(allp, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return {"colors": clean, "name": name}


def load(repo_id: str) -> dict[str, Any]:
    """读一个仓库的色彩约束。没有则返回空列表。"""
    with _LOCK:
        entry = _read_all().get(repo_id) or {}
    colors = entry.get("colors") if isinstance(entry, dict) else None
    return {
        "colors": normalize_colors(colors or []),
        "name": (entry.get("name") or "") if isinstance(entry, dict) else "",
    }
