import json

from app.services import edit_artifacts, edit_import_adapter


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def test_ST角色卡转换为Demiurge扁平卡和侧车():
    source = {
        "spec": "chara_card_v2", "spec_version": "2.0",
        "data": {
            "name": "塞西莉亚", "description": "孤儿院院长", "first_mes": "欢迎回来。",
            "character_book": {"entries": [{
                "keys": ["塞西莉亚"], "content": "她是孤儿院院长。", "enabled": True,
            }]},
            "extensions": {"regex_scripts": [{
                "scriptName": "隐藏思考", "findRegex": "/<think>[\\s\\S]*?<\\/think>/g",
                "replaceString": "", "placement": [2],
            }]},
        },
    }

    converted = edit_import_adapter.convert(
        "cecilia.json", _dump(source), "character_card", "导入",
    )

    assert set(converted.files) == {
        "导入/角色卡/塞西莉亚/card.json",
        "导入/角色卡/塞西莉亚/worldbook.json",
        "导入/角色卡/塞西莉亚/regex.json",
    }
    card = json.loads(converted.files["导入/角色卡/塞西莉亚/card.json"])
    assert card["name"] == "塞西莉亚"
    assert "data" not in card
    assert card["character_book"] is None
    assert card["regex_scripts"] == []
    assert edit_artifacts.validate("card.json", _dump(card))["type"] == "character_card"


def test_纯角色卡没有附属内容时只生成card文件():
    source = {"name": "无名剑客", "description": "独行剑客", "first_mes": "何事？"}

    converted = edit_import_adapter.convert(
        "swordsman.json", _dump(source), "character_card", "导入",
    )

    assert set(converted.files) == {"导入/角色卡/无名剑客/card.json"}


def test_ST预设转换时清理密钥并补项目扩展():
    source = {
        "prompts": [{"identifier": "main", "role": "system", "content": "推进剧情"}],
        "prompt_order": [{"order": [{"identifier": "main", "enabled": True}]}],
        "api_key": "secret", "reverse_proxy": "https://example.invalid/v1",
        "temperature": 0.8,
    }

    converted = edit_import_adapter.convert("Gray.json", _dump(source), "preset", "预设")
    preset = json.loads(converted.files["预设/Gray.json"])

    assert "api_key" not in preset and "reverse_proxy" not in preset
    assert preset["thinking_chains"] == []
    assert preset["temperature"] == 0.8
    assert edit_artifacts.validate("Gray.json", _dump(preset), "preset")["type"] == "preset"


def test_ST正则转换时补齐Demiurge字段和id():
    source = [{"scriptName": "清理", "findRegex": "/foo/g", "replaceString": "bar"}]

    converted = edit_import_adapter.convert("regex.json", _dump(source), "regex", "迁移")
    scripts = json.loads(converted.files["迁移/regex.json"])

    assert scripts[0]["id"]
    assert scripts[0]["placement"] == []
    assert scripts[0]["runOnEdit"] is True
    assert scripts[0]["substituteRegex"] == 0
    assert edit_artifacts.validate("regex.json", _dump(scripts), "regex")["type"] == "regex"


def test_纯世界书转换为当前作品worldbook侧车():
    source = {"entries": {
        "0": {"comment": "王都", "key": ["王都", "圣城"], "content": "王都是帝国首都。"},
        "1": {"comment": "废弃", "key": "旧设定", "content": "已废弃。", "disable": True},
    }}

    converted = edit_import_adapter.convert("帝国设定.json", _dump(source), "worldbook", "导入")
    book = json.loads(converted.files["导入/worldbook.json"])

    assert len(book["entries"]) == 2
    assert book["entries"][0]["keys"] == ["王都", "圣城"]
    assert book["entries"][0]["enabled"] is True
    assert book["entries"][1]["enabled"] is False
    assert edit_artifacts.validate("worldbook.json", _dump(book), "worldbook")["entries"] == 2
