"""把已校验的小仓库产物受控发布到后端配置的角色卡或预设源库。"""
from __future__ import annotations

import base64
import binascii
import json
import shutil
from pathlib import Path
from typing import Any

from app.services import character_card, character_store, edit_artifacts, preset_store, project_files, repo_meta
from app.services.pathnames import safe_dir


class EditPublicationError(ValueError):
    pass


def attachment_png(data_uri: str) -> bytes:
    prefix = "data:image/png;base64,"
    if not isinstance(data_uri, str) or not data_uri.casefold().startswith(prefix):
        raise EditPublicationError("附件必须是 PNG data URI")
    try:
        data = base64.b64decode(data_uri[len(prefix):], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise EditPublicationError("PNG 附件 base64 非法") from exc
    if not data.startswith(project_files.PNG_SIGNATURE):
        raise EditPublicationError("附件内容不是有效的 PNG")
    if len(data) > project_files.MAX_PNG_BYTES:
        raise EditPublicationError(f"PNG 超过 {project_files.MAX_PNG_BYTES} 字节限制")
    return data


def _load_json(root: Path, path: str, kind: str) -> Any:
    content = project_files.read_text(root, path)
    edit_artifacts.validate(path, content, kind)
    return json.loads(content)


def publish_character(root: Path, name: str, *, overwrite: bool = False) -> dict[str, Any]:
    clean_name = safe_dir(name)
    base_path = f"角色卡/{clean_name}"
    card_path = f"{base_path}/card.json"
    raw_card = _load_json(root, card_path, "character_card")
    card = character_card.normalize_card(raw_card)
    if card.name != name:
        raise EditPublicationError("card.json 的 name 与发布名称不一致")
    worldbook_path = f"{base_path}/worldbook.json"
    regex_path = f"{base_path}/regex.json"
    if project_files.file_exists(root, worldbook_path):
        card.character_book = _load_json(root, worldbook_path, "worldbook")
    if project_files.file_exists(root, regex_path):
        scripts = _load_json(root, regex_path, "regex")
        card.regex_scripts = scripts if isinstance(scripts, list) else scripts.get("regexScripts", [])
    avatar_path = f"{base_path}/avatar.png"
    avatar = project_files.read_png(root, avatar_path) if project_files.file_exists(root, avatar_path) else None
    target = repo_meta.setting_dir_from_state("characterDir")
    if not target:
        raise EditPublicationError("请先在设置中配置角色卡文件夹")
    try:
        summary = character_store.save_card(target, card, avatar=avatar, overwrite=overwrite)
    except FileExistsError as exc:
        raise EditPublicationError("角色卡源库已存在同名卡；确认后使用 overwrite=true") from exc
    source_expressions = root / "角色卡" / clean_name / character_store.EXPRESSIONS_DIR
    target_expressions = character_store.card_dir(target, name) / character_store.EXPRESSIONS_DIR
    if source_expressions.is_dir():
        target_expressions.mkdir(parents=True, exist_ok=True)
        for image in source_expressions.glob("*.png"):
            project_files.read_png(root, image.relative_to(root).as_posix())
            shutil.copy2(image, target_expressions / image.name)
    return summary.__dict__


def publish_preset(root: Path, path: str, name: str, *, overwrite: bool = False) -> dict[str, Any]:
    preset = _load_json(root, path, "preset")
    target = repo_meta.setting_dir_from_state("presetDir")
    if not target:
        raise EditPublicationError("请先在设置中配置预设文件夹")
    try:
        return preset_store.save(target, name, preset, overwrite=overwrite).__dict__
    except FileExistsError as exc:
        raise EditPublicationError("预设源库已存在同名预设；确认后使用 overwrite=true") from exc
