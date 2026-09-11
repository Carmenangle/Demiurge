"""全局正则脚本持久化：读写 data/regex_scripts.json。

全局正则 = 跨作品生效的一组 ST 格式正则脚本（camelCase 原样存，喂 regex_engine 前归一）。
区别于卡内嵌 regex.json（随卡、只该卡生效）。两者都由 agent_graph/前端合并后喂引擎。

落盘模式仿 agent_store。纯读写，不解析/不跑正则（那是 regex_engine 的事）。
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from app.config import DATA_DIR


def _path() -> Path:
    return DATA_DIR / "regex_scripts.json"


def load_scripts() -> list[dict]:
    p = _path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return []


def save_scripts(scripts: list[dict]) -> list[dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    normalized = []
    for s in scripts or []:
        if not isinstance(s, dict):
            continue
        s = dict(s)
        if not s.get("id"):
            s["id"] = uuid4().hex
        normalized.append(s)
    _path().write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized
