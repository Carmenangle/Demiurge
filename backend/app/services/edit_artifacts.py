"""编辑模式产物的机械格式校验。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from app.services import character_card, regex_engine


class ArtifactValidationError(ValueError):
    pass


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ArtifactValidationError(f"JSON 非法：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}") from exc


def _validate_card(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ArtifactValidationError("角色卡根必须是 JSON 对象")
    if isinstance(data.get("data"), dict):
        raise ArtifactValidationError("Demiurge card.json 必须是扁平格式，不得包含 ST data 包装")
    if data.get("character_book"):
        raise ArtifactValidationError("Demiurge card.json 不得内嵌世界书，请写入 worldbook.json 侧车")
    if data.get("regex_scripts"):
        raise ArtifactValidationError("Demiurge card.json 不得内嵌正则，请写入 regex.json 侧车")
    try:
        card = character_card.normalize_card(data)
    except character_card.CardParseError as exc:
        raise ArtifactValidationError(str(exc)) from exc
    return {
        "type": "character_card", "name": card.name, "spec": card.spec,
        "worldbook": card.has_worldbook, "regex_count": len(card.regex_scripts),
    }


def _validate_worldbook(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or "entries" not in data:
        raise ArtifactValidationError("世界书根必须是包含 entries 的 JSON 对象")
    raw = data.get("entries")
    if isinstance(raw, dict):
        items = list(raw.values())
    elif isinstance(raw, list):
        items = raw
    else:
        raise ArtifactValidationError("世界书 entries 必须是数组或对象")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ArtifactValidationError(f"entries[{index}] 不是对象")
        if "content" in item and not isinstance(item.get("content"), str):
            raise ArtifactValidationError(f"entries[{index}].content 必须是字符串")
        keys = item.get("keys", item.get("key"))
        if keys is not None and not (
            isinstance(keys, str)
            or isinstance(keys, list) and all(isinstance(value, str) for value in keys)
        ):
            raise ArtifactValidationError(f"entries[{index}].keys 必须是字符串数组")
        for field in ("constant", "enabled", "disable"):
            if field in item and not isinstance(item[field], bool):
                raise ArtifactValidationError(f"entries[{index}].{field} 必须是布尔值")
    return {"type": "worldbook", "entries": len(items)}


def _validate_preset(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ArtifactValidationError("预设根必须是 JSON 对象")
    prompts = data.get("prompts")
    if not isinstance(prompts, list):
        raise ArtifactValidationError("预设缺少 prompts 数组")
    identifiers: list[str] = []
    for index, item in enumerate(prompts):
        if not isinstance(item, dict):
            raise ArtifactValidationError(f"prompts[{index}] 不是对象")
        identifier = str(item.get("identifier") or "").strip()
        if not identifier:
            raise ArtifactValidationError(f"prompts[{index}] 缺少 identifier")
        if item.get("role") not in (None, "system", "user", "assistant"):
            raise ArtifactValidationError(f"prompts[{index}].role 不受支持")
        injection_position = item.get("injection_position")
        if injection_position not in (None, 0, 1):
            raise ArtifactValidationError(f"prompts[{index}].injection_position 只能是 0/1")
        injection_depth = item.get("injection_depth")
        if injection_depth is not None and (
            not isinstance(injection_depth, int) or isinstance(injection_depth, bool)
            or injection_depth < 0
        ):
            raise ArtifactValidationError(f"prompts[{index}].injection_depth 必须是非负整数")
        triggers = item.get("injection_trigger")
        allowed_triggers = {"normal", "continue", "impersonate", "swipe", "regenerate", "quiet"}
        if triggers is not None and (
            not isinstance(triggers, list)
            or any(not isinstance(value, str) or value not in allowed_triggers for value in triggers)
        ):
            raise ArtifactValidationError(f"prompts[{index}].injection_trigger 非法")
        identifiers.append(identifier)
    if len(set(identifiers)) != len(identifiers):
        raise ArtifactValidationError("prompts identifier 重复")
    wraps = data.get("prompt_order")
    if not isinstance(wraps, list) or not wraps or not isinstance(wraps[0], dict):
        raise ArtifactValidationError("预设缺少 prompt_order[0]")
    order = wraps[0].get("order")
    if not isinstance(order, list):
        raise ArtifactValidationError("预设缺少 prompt_order[0].order 数组")
    unknown = [
        str(item.get("identifier") or "") for item in order if isinstance(item, dict)
        and str(item.get("identifier") or "") not in identifiers
    ]
    if unknown:
        raise ArtifactValidationError(f"prompt_order 引用了不存在的 identifier：{', '.join(unknown)}")
    chains = data.get("thinking_chains") or []
    if not isinstance(chains, list):
        raise ArtifactValidationError("thinking_chains 必须是数组")
    allowed_scenes = {"dialogue", "action", "emotion", "conflict", "nsfw", "climax"}
    for index, chain in enumerate(chains):
        if not isinstance(chain, dict):
            raise ArtifactValidationError(f"thinking_chains[{index}] 不是对象")
        if chain.get("position", "tail") not in ("head", "tail"):
            raise ArtifactValidationError(f"thinking_chains[{index}].position 只能是 head/tail")
        when = chain.get("when") or {}
        if not isinstance(when, dict):
            raise ArtifactValidationError(f"thinking_chains[{index}].when 必须是对象")
        scene = when.get("scene")
        if scene not in (None, "") and scene not in allowed_scenes:
            raise ArtifactValidationError(f"thinking_chains[{index}].when.scene 非法")
        for field in ("affinity_lt", "affinity_gt"):
            value = when.get(field)
            if value is not None and (
                not isinstance(value, (int, float)) or isinstance(value, bool)
            ):
                raise ArtifactValidationError(
                    f"thinking_chains[{index}].when.{field} 必须是数值",
                )
        turn_mod = when.get("turn_mod")
        if turn_mod is not None and (
            not isinstance(turn_mod, list) or len(turn_mod) != 2
            or not all(isinstance(value, int) and not isinstance(value, bool) for value in turn_mod)
            or turn_mod[0] <= 0 or not 0 <= turn_mod[1] < turn_mod[0]
        ):
            raise ArtifactValidationError(
                f"thinking_chains[{index}].when.turn_mod 必须是 [正整数n, 0到n-1的r]",
            )
    return {
        "type": "preset", "prompts": len(prompts),
        "enabled": sum(1 for item in order if isinstance(item, dict) and item.get("enabled")),
        "chains": len(chains), "regex_count": len(data.get("regexScripts") or []),
    }


def _regex_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("regexScripts"), list):
        items = data["regexScripts"]
    elif isinstance(data, dict) and isinstance(data.get("regex_scripts"), list):
        items = data["regex_scripts"]
    else:
        raise ArtifactValidationError("正则文件必须是脚本数组或包含 regexScripts 数组")
    if not all(isinstance(item, dict) for item in items):
        raise ArtifactValidationError("正则脚本数组含非对象元素")
    return items


def _validate_regex(data: Any) -> dict[str, Any]:
    items = _regex_items(data)
    allowed_placements = {0, 1, 2, 3, 5, 6, 7}
    for index, item in enumerate(items):
        pattern = str(item.get("findRegex") or "")
        if not pattern:
            raise ArtifactValidationError(f"正则[{index}] 缺少 findRegex")
        if regex_engine.compile_js_regex(pattern) is None:
            raise ArtifactValidationError(f"正则[{index}] findRegex 无法编译")
        placements = item.get("placement") or []
        if not isinstance(placements, list) or any(p not in allowed_placements for p in placements):
            raise ArtifactValidationError(f"正则[{index}] placement 非法")
        if item.get("markdownOnly") and item.get("promptOnly"):
            raise ArtifactValidationError(f"正则[{index}] markdownOnly 与 promptOnly 不能同时启用")
        if item.get("substituteRegex", 0) not in (0, 1, 2):
            raise ArtifactValidationError(f"正则[{index}] substituteRegex 只能是 0/1/2")
    return {"type": "regex", "scripts": len(items)}


def _infer(path: str, data: Any) -> str:
    name = Path(path).name.casefold()
    if name.endswith(".py"):
        return "python"
    if name == "card.json":
        return "character_card"
    if name == "worldbook.json":
        return "worldbook"
    if isinstance(data, dict) and "prompts" in data:
        return "preset"
    if isinstance(data, dict) and (
        "name" in data and any(field in data for field in (
            "description", "personality", "scenario", "first_mes", "character_book",
        ))
        or isinstance(data.get("data"), dict) and "name" in data["data"]
    ):
        return "character_card"
    if isinstance(data, list) or (isinstance(data, dict) and (
        "regexScripts" in data or "regex_scripts" in data
    )):
        return "regex"
    return "json"


def validate(path: str, text: str, artifact_type: str = "auto") -> dict[str, Any]:
    requested = (artifact_type or "auto").strip().casefold()
    if requested == "python" or (requested == "auto" and path.casefold().endswith(".py")):
        try:
            compile(text, path, "exec")
        except SyntaxError as exc:
            raise ArtifactValidationError(
                f"Python 语法错误：第 {exc.lineno or 0} 行，{exc.msg}",
            ) from exc
        return {"type": "python", "syntax": "ok"}
    if requested in {"javascript", "js"} or (
        requested == "auto" and path.casefold().endswith((".js", ".mjs", ".cjs"))
    ):
        try:
            checked = subprocess.run(
                ["node", "--check", "-"], input=text, text=True,
                capture_output=True, encoding="utf-8", timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ArtifactValidationError(f"JavaScript 校验器不可用：{exc}") from exc
        if checked.returncode:
            detail = (checked.stderr or checked.stdout).strip().splitlines()
            raise ArtifactValidationError(
                f"JavaScript 语法错误：{detail[-1] if detail else 'node --check 失败'}",
            )
        return {"type": "javascript", "syntax": "ok"}
    data = _json(text)
    kind = _infer(path, data) if requested == "auto" else requested
    if kind == "character_card":
        return _validate_card(data)
    if kind == "worldbook":
        return _validate_worldbook(data)
    if kind == "preset":
        result = _validate_preset(data)
        if data.get("regexScripts"):
            _validate_regex({"regexScripts": data["regexScripts"]})
        return result
    if kind == "regex":
        return _validate_regex(data)
    if kind == "json":
        return {"type": "json", "root": type(data).__name__}
    raise ArtifactValidationError(
        "artifact_type 只支持 auto/character_card/worldbook/preset/regex/python/javascript/json",
    )
