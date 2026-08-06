import base64
import json

import pytest

from app.services import edit_publication, project_files


def _dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def test_PNG附件只接受有效data_uri():
    raw = project_files.PNG_SIGNATURE + b"test"
    uri = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    assert edit_publication.attachment_png(uri) == raw
    with pytest.raises(edit_publication.EditPublicationError, match="PNG data URI"):
        edit_publication.attachment_png("https://example.invalid/avatar.png")


def test_发布角色卡到后端设置源库并带头像表情(monkeypatch, tmp_path):
    root = tmp_path / "work"
    library = tmp_path / "characters"
    root.mkdir()
    card = {
        "name": "塞西莉亚", "description": "孤儿院院长", "spec": "chara_card_v2",
        "character_book": None, "regex_scripts": [], "extensions": {},
    }
    project_files.write_text(root, "角色卡/塞西莉亚/card.json", _dump(card))
    project_files.write_text(root, "角色卡/塞西莉亚/worldbook.json", _dump({
        "entries": [{"keys": ["孤儿院"], "content": "她经营的孤儿院。"}],
    }))
    png = project_files.PNG_SIGNATURE + b"avatar"
    project_files.write_png(root, "角色卡/塞西莉亚/avatar.png", png)
    project_files.write_png(root, "角色卡/塞西莉亚/expressions/微笑.png", png)
    monkeypatch.setattr(
        edit_publication.repo_meta, "setting_dir_from_state",
        lambda key: str(library) if key == "characterDir" else "",
    )

    result = edit_publication.publish_character(root, "塞西莉亚")

    assert result["name"] == "塞西莉亚"
    saved = json.loads((library / "塞西莉亚" / "card.json").read_text(encoding="utf-8"))
    assert saved["character_book"]["entries"][0]["keys"] == ["孤儿院"]
    assert (library / "塞西莉亚" / "avatar.png").read_bytes() == png
    assert (library / "塞西莉亚" / "expressions" / "微笑.png").read_bytes() == png


def test_发布预设到后端设置源库且默认拒绝覆盖(monkeypatch, tmp_path):
    root = tmp_path / "work"
    library = tmp_path / "presets"
    root.mkdir()
    preset = {
        "prompts": [{"identifier": "main", "role": "system"}],
        "prompt_order": [{"order": [{"identifier": "main", "enabled": True}]}],
    }
    project_files.write_text(root, "预设/主预设.json", _dump(preset))
    monkeypatch.setattr(
        edit_publication.repo_meta, "setting_dir_from_state",
        lambda key: str(library) if key == "presetDir" else "",
    )

    result = edit_publication.publish_preset(root, "预设/主预设.json", "主预设")
    assert result["name"] == "主预设"
    with pytest.raises(edit_publication.EditPublicationError, match="overwrite=true"):
        edit_publication.publish_preset(root, "预设/主预设.json", "主预设")
