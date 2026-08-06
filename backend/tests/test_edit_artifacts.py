import json

import pytest

from app.services import edit_artifacts


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def test_校验合法与缺名角色卡():
    result = edit_artifacts.validate("card.json", _dump({
        "spec": "chara_card_v2", "name": "塞西莉亚", "description": "院长",
    }), "character_card")
    assert result["name"] == "塞西莉亚"

    with pytest.raises(edit_artifacts.ArtifactValidationError, match="缺少 name"):
        edit_artifacts.validate("card.json", _dump({"description": "院长"}),
                                "character_card")


def test_Demiurge角色卡拒绝ST包装和重复内嵌侧车():
    with pytest.raises(edit_artifacts.ArtifactValidationError, match="扁平格式"):
        edit_artifacts.validate("card.json", _dump({
            "spec": "chara_card_v2", "data": {"name": "塞西莉亚"},
        }), "character_card")
    with pytest.raises(edit_artifacts.ArtifactValidationError, match="worldbook.json"):
        edit_artifacts.validate("card.json", _dump({
            "name": "塞西莉亚", "character_book": {"entries": []},
        }), "character_card")


def test_Demiurge角色卡与世界书侧车格式():
    card = {
        "name": "塞西莉亚", "description": "孤儿院院长", "personality": "温和谨慎",
        "scenario": "收养仪式前夜", "first_mes": "欢迎回来。", "spec": "chara_card_v2",
        "character_book": None, "regex_scripts": [], "extensions": {},
    }
    assert edit_artifacts.validate("角色卡/塞西莉亚/card.json", _dump(card))["name"] == "塞西莉亚"

    worldbook = {"entries": [{
        "comment": "塞西莉亚", "keys": ["塞西莉亚", "院长"],
        "content": "塞西莉亚是孤儿院院长。", "constant": False, "enabled": True,
    }]}
    result = edit_artifacts.validate(
        "角色卡/塞西莉亚/worldbook.json", _dump(worldbook), "worldbook",
    )
    assert result == {"type": "worldbook", "entries": 1}

    with pytest.raises(edit_artifacts.ArtifactValidationError, match="entries"):
        edit_artifacts.validate("worldbook.json", _dump({}), "worldbook")


def test_预设拒绝重复与断裂引用():
    duplicate = {
        "prompts": [
            {"identifier": "main", "role": "system"},
            {"identifier": "main", "role": "user"},
        ],
        "prompt_order": [{"order": [{"identifier": "main", "enabled": True}]}],
    }
    with pytest.raises(edit_artifacts.ArtifactValidationError, match="identifier 重复"):
        edit_artifacts.validate("preset.json", _dump(duplicate), "preset")

    broken = {
        "prompts": [{"identifier": "main", "role": "system"}],
        "prompt_order": [{"order": [{"identifier": "missing", "enabled": True}]}],
    }
    with pytest.raises(edit_artifacts.ArtifactValidationError, match="不存在"):
        edit_artifacts.validate("preset.json", _dump(broken), "preset")


def test_Demiurge预设校验条件推理链与注入字段():
    preset = {
        "prompts": [{
            "identifier": "main", "role": "system", "content": "推进剧情",
            "injection_position": 1, "injection_depth": 4,
            "injection_trigger": ["normal", "regenerate"],
        }],
        "prompt_order": [{"order": [{"identifier": "main", "enabled": True}]}],
        "thinking_chains": [{
            "name": "冲突链", "content": "先判断目标与阻力", "position": "tail",
            "when": {"scene": "conflict", "affinity_gt": 10, "turn_mod": [3, 1]},
        }],
    }
    assert edit_artifacts.validate("preset.json", _dump(preset), "preset")["chains"] == 1

    bad_chain = {**preset, "thinking_chains": [{
        "content": "x", "position": "middle", "when": {"scene": "unknown"},
    }]}
    with pytest.raises(edit_artifacts.ArtifactValidationError, match="position"):
        edit_artifacts.validate("preset.json", _dump(bad_chain), "preset")

    bad_injection = {**preset, "prompts": [{
        "identifier": "main", "role": "system", "injection_position": 3,
    }]}
    with pytest.raises(edit_artifacts.ArtifactValidationError, match="injection_position"):
        edit_artifacts.validate("preset.json", _dump(bad_injection), "preset")


def test_正则拒绝无法编译_非法placement和三档冲突():
    base = {
        "findRegex": "/hello/gi", "replaceString": "hi", "placement": [2],
        "markdownOnly": False, "promptOnly": False,
    }
    assert edit_artifacts.validate("regex.json", _dump([base]), "regex")["scripts"] == 1

    for patch, expected in [
        ({"findRegex": "/[broken/"}, "无法编译"),
        ({"placement": [4]}, "placement 非法"),
        ({"markdownOnly": True, "promptOnly": True}, "不能同时启用"),
        ({"substituteRegex": 3}, "只能是 0/1/2"),
    ]:
        with pytest.raises(edit_artifacts.ArtifactValidationError, match=expected):
            edit_artifacts.validate("regex.json", _dump([{**base, **patch}]), "regex")


def test_python语法与auto推断():
    assert edit_artifacts.validate("tools/update.py", "print('ok')\n")["type"] == "python"
    with pytest.raises(edit_artifacts.ArtifactValidationError, match="Python 语法错误"):
        edit_artifacts.validate("tools/update.py", "def broken(:\n")


def test_javascript语法校验():
    assert edit_artifacts.validate("tools/update.js", "const ok = true;\n")["type"] == "javascript"
    with pytest.raises(edit_artifacts.ArtifactValidationError, match="JavaScript 语法错误"):
        edit_artifacts.validate("tools/update.js", "const = ;\n")
