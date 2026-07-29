"""技能扩展（Skills）持久化：读写 data/skills.json。

技能 = 可开关的提示词注入片段，启用后拼进智能体 system_prompt，让用户自定义 AI 行为
（如"生图时always加高质量负面词""回答用专业术语"）。比 MCP 更轻量，纯提示词层，无副作用。
结构：[{id, name, enabled, prompt_fragment}]
落盘模式仿 mcp_store / user_state。
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from app.config import DATA_DIR


def _path() -> Path:
    return DATA_DIR / "skills.json"


def load_skills() -> list[dict]:
    p = _path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return []


def save_skills(skills: list[dict]) -> list[dict]:
    """整体覆盖写，为缺 id 的项补 id。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    normalized = []
    for s in skills or []:
        if not isinstance(s, dict):
            continue
        s = dict(s)
        if not s.get("id"):
            s["id"] = uuid4().hex
        normalized.append(s)
    _path().write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return normalized


def enabled_prompt_fragments() -> list[str]:
    """返回所有已启用技能的提示词片段（供 agent 拼进 system_prompt）。"""
    out = []
    for s in load_skills():
        if s.get("enabled") and (s.get("prompt_fragment") or "").strip():
            out.append(s["prompt_fragment"].strip())
    return out


def fragments_by_ids(skill_ids: list[str]) -> list[str]:
    """按技能 id 列表返回提示词片段（供多 Agent 选择性启用；仍要求该技能本身 enabled）。"""
    if not skill_ids:
        return []
    idset = set(skill_ids)
    out = []
    for s in load_skills():
        if s.get("id") in idset and s.get("enabled") and (s.get("prompt_fragment") or "").strip():
            out.append(s["prompt_fragment"].strip())
    return out
