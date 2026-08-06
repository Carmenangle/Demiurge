"""把外部 JSON 资源确定性转换为 Demiurge 当前作品格式。"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from app.services import character_card, edit_artifacts, preset_store
from app.services.pathnames import safe_dir


class ImportConversionError(ValueError):
    pass


@dataclass(frozen=True)
class ConvertedArtifact:
    artifact_type: str
    files: dict[str, str]


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImportConversionError(
            f"输入 JSON 非法：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}",
        ) from exc


def _target_dir(value: str) -> str:
    raw = (value or "").strip().replace("\\", "/").strip("/")
    if not raw:
        return ""
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ImportConversionError("目标目录必须是当前作品内的相对路径")
    return path.as_posix()


def _join(base: str, *parts: str) -> str:
    return PurePosixPath(base, *parts).as_posix() if base else PurePosixPath(*parts).as_posix()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _regex_scripts(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("regexScripts"), list):
        items = data["regexScripts"]
    elif isinstance(data, dict) and isinstance(data.get("regex_scripts"), list):
        items = data["regex_scripts"]
    else:
        raise ImportConversionError("正则输入必须是脚本数组或包含 regexScripts 的对象")
    converted: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise ImportConversionError(f"正则[{index}] 不是对象")
        item = dict(raw)
        stable = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        item.setdefault("id", uuid.uuid5(uuid.NAMESPACE_URL, f"demiurge-regex:{index}:{stable}").hex)
        item.setdefault("scriptName", f"迁移正则 {index + 1}")
        item.setdefault("replaceString", "")
        item.setdefault("trimStrings", [])
        item.setdefault("placement", [])
        item.setdefault("disabled", False)
        item.setdefault("markdownOnly", False)
        item.setdefault("promptOnly", False)
        item.setdefault("runOnEdit", True)
        item.setdefault("substituteRegex", 0)
        item.setdefault("minDepth", None)
        item.setdefault("maxDepth", None)
        converted.append(item)
    edit_artifacts.validate("regex.json", _dump(converted), "regex")
    return converted


def _character(data: Any, target: str) -> ConvertedArtifact:
    if not isinstance(data, dict):
        raise ImportConversionError("角色卡输入根必须是对象")
    try:
        normalized = character_card.normalize_card(data)
    except character_card.CardParseError as exc:
        raise ImportConversionError(str(exc)) from exc
    card = normalized.to_dict()
    card["character_book"] = None
    card["regex_scripts"] = []
    base = _join(target, "角色卡", safe_dir(normalized.name))
    files = {_join(base, "card.json"): _dump(card)}
    if normalized.character_book and normalized.character_book.get("entries") is not None:
        files[_join(base, "worldbook.json")] = _dump(normalized.character_book)
    if normalized.regex_scripts:
        files[_join(base, "regex.json")] = _dump(_regex_scripts(normalized.regex_scripts))
    for path, content in files.items():
        edit_artifacts.validate(path, content)
    return ConvertedArtifact("character_card", files)


def _preset(data: Any, source_path: str, target: str) -> ConvertedArtifact:
    if not isinstance(data, dict):
        raise ImportConversionError("预设输入根必须是对象")
    converted = preset_store.sanitize(data)
    converted.setdefault("thinking_chains", [])
    if isinstance(converted.get("regexScripts"), list):
        converted["regexScripts"] = _regex_scripts(converted["regexScripts"])
    name = safe_dir(Path(source_path).stem or "preset")
    path = _join(target, f"{name}.json")
    content = _dump(converted)
    edit_artifacts.validate(path, content, "preset")
    return ConvertedArtifact("preset", {path: content})


def _regex(data: Any, target: str) -> ConvertedArtifact:
    scripts = _regex_scripts(data)
    path = _join(target, "regex.json")
    return ConvertedArtifact("regex", {path: _dump(scripts)})


def _worldbook(data: Any, target: str) -> ConvertedArtifact:
    if not isinstance(data, dict) or "entries" not in data:
        raise ImportConversionError("世界书输入必须是包含 entries 的对象")
    raw = data.get("entries")
    if isinstance(raw, dict):
        items = list(raw.values())
    elif isinstance(raw, list):
        items = raw
    else:
        raise ImportConversionError("世界书 entries 必须是数组或对象")
    entries: list[dict[str, Any]] = []
    for index, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            raise ImportConversionError(f"世界书 entries[{index}] 不是对象")
        item = dict(raw_item)
        keys = item.get("keys", item.get("key", []))
        if isinstance(keys, str):
            keys = [keys] if keys.strip() else []
        item["keys"] = keys
        item.setdefault("comment", "")
        item.setdefault("constant", False)
        item.setdefault("enabled", not bool(item.get("disable")))
        entries.append(item)
    book = {**data, "entries": entries}
    path = _join(target, "worldbook.json")
    content = _dump(book)
    edit_artifacts.validate(path, content, "worldbook")
    return ConvertedArtifact("worldbook", {path: content})


def _infer(data: Any) -> str:
    if isinstance(data, dict) and "prompts" in data:
        return "preset"
    if isinstance(data, dict) and "entries" in data:
        return "worldbook"
    if isinstance(data, dict) and (
        isinstance(data.get("data"), dict) and data["data"].get("name")
        or data.get("name") and any(key in data for key in (
            "description", "personality", "scenario", "first_mes", "spec",
        ))
    ):
        return "character_card"
    if isinstance(data, list) or isinstance(data, dict) and (
        "regexScripts" in data or "regex_scripts" in data
    ):
        return "regex"
    raise ImportConversionError("无法自动判断输入是角色卡、预设还是正则")


def convert(
    source_path: str, text: str, artifact_type: str = "auto", target_dir: str = "",
) -> ConvertedArtifact:
    data = _json(text)
    requested = (artifact_type or "auto").strip().casefold()
    kind = _infer(data) if requested == "auto" else requested
    target = _target_dir(target_dir)
    try:
        if kind == "character_card":
            return _character(data, target)
        if kind == "preset":
            return _preset(data, source_path, target)
        if kind == "regex":
            return _regex(data, target)
        if kind == "worldbook":
            return _worldbook(data, target)
        raise ImportConversionError(
            "artifact_type 只支持 auto/character_card/worldbook/preset/regex",
        )
    except edit_artifacts.ArtifactValidationError as exc:
        raise ImportConversionError(f"转换结果不符合 Demiurge 格式：{exc}") from exc
