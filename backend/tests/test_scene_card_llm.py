"""LLM 场景卡补全（2026-11-12）。"""

import json

from app.services import scene_card, scene_card_llm

WINDOW = (
    "旧仓库的木门被拆掉了一半，剩下的门框上缠着白纱。"
    "红毯一直铺到仓库中央那块用木板垫高的台子上，台上摆着两个铁桶。"
    "；同场景地标：侧门"
)


def _baseline() -> dict:
    return scene_card.extract_scene_card(WINDOW, scene_key="scene-0")


def _payload(**overrides) -> dict:
    data = {
        "space_type": "warehouse",
        "architecture": [
            {"item": "wooden door", "position": "entrance",
             "condition": "half-dismantled with missing planks",
             "evidence": "旧仓库的木门被拆掉了一半"},
        ],
        "fixed_props": [
            {"item": "raised wooden platform", "position": "center", "condition": "raised",
             "evidence": "用木板垫高的台子"},
            {"item": "iron buckets", "position": "center", "evidence": "台上摆着两个铁桶"},
        ],
        "layout": {"entrance": "wooden door", "center": "raised wooden platform"},
        "relations": [
            {"text": "red carpet runs from the side door to the wooden steps at the platform",
             "evidence": "红毯一直铺到仓库中央那块用木板垫高的台子上"},
        ],
        "lighting": {"sources": [], "direction": "", "quality": "", "time": "",
                     "weather": ""},
        "palette": ["red", "white"],
        "camera": {"shot_size": "wide", "height": "eye level", "angle": "eye level",
                   "depth": "deep"},
        "missing": [],
        "inferred": ["platform_steps"],
    }
    data.update(overrides)
    return data


def _fake(data: dict):
    def generate_text(system: str, user: str) -> str:
        assert "空场景地理勘察" in system
        return json.dumps(data, ensure_ascii=False)

    return generate_text


def test_enrich_scene_card_merges_llm_facts_and_keeps_rule_landmarks():
    card = scene_card_llm.enrich_scene_card(_baseline(), generate_text=_fake(_payload()))

    assert card["extractor"] == "llm"
    items = {entry["item"] for entry in [*card["architecture"], *card["fixed_props"]]}
    assert "iron buckets" in items
    # 规则卡里的同场景地标「侧门」不能被 LLM 漏项挤掉。
    assert "side door" in items
    assert card["relations"][0]["text"].startswith("red carpet runs from the side door")
    assert "platform_steps" in card["inferred"]
    assert "iron buckets" in card["do_not_change"]

    prompt = scene_card.build_scene_prompt(card)
    assert "iron buckets" in prompt
    assert "red carpet runs from the side door" in prompt
    assert "central area: raised wooden platform" in prompt
    assert "wooden steps at the middle of the platform front edge" in prompt
    assert "red carpet leads up those steps onto the platform" in prompt
    assert "one single continuous straight red carpet" in prompt
    assert card["architecture"][0]["condition"].count("half-dismantled") == 1
    assert "palette" not in card["missing"]


def test_enrich_scene_card_dedupes_item_across_architecture_and_props():
    window = "仓库里有一架梯子靠在横梁下。中央是木板台子。"
    card = scene_card.extract_scene_card(window, scene_key="scene-0")
    data = _payload(
        architecture=[
            {"item": "ladder", "position": "unspecified",
             "evidence": "仓库里有一架梯子靠在横梁下"},
        ],
        fixed_props=[
            {"item": "ladder", "position": "unspecified",
             "evidence": "仓库里有一架梯子靠在横梁下"},
            {"item": "wooden platform", "position": "center",
             "evidence": "中央是木板台子"},
        ],
        layout={"center": "wooden platform"},
        relations=[],
    )

    enriched = scene_card_llm.enrich_scene_card(card, generate_text=_fake(data))

    names = [
        entry["item"]
        for entry in [*enriched["architecture"], *enriched["fixed_props"]]
    ]
    assert names.count("ladder") == 1
    assert names.count("wooden platform") == 1


def test_enrich_scene_card_merges_synonym_fixtures():
    """LLM 的 white gauze 与规则卡的 white drapery 是同一件，不能画两遍。"""
    data = _payload(
        fixed_props=[
            {"item": "white gauze", "position": "entrance",
             "evidence": "剩下的门框上缠着白纱"},
        ],
        relations=[],
    )

    card = scene_card_llm.enrich_scene_card(_baseline(), generate_text=_fake(data))

    drapery = [
        entry["item"] for entry in card["fixed_props"]
        if "gauze" in entry["item"].lower() or "drapery" in entry["item"].lower()
    ]
    assert len(drapery) == 1


def test_enrich_scene_card_drops_entries_without_text_evidence():
    data = _payload()
    data["architecture"].append({
        "item": "crystal chandelier",
        "position": "ceiling",
        "evidence": "天花板上挂着一盏巨大的水晶吊灯",
    })
    data["relations"].append({
        "text": "chandelier hangs above the platform",
        "evidence": "天花板上挂着一盏巨大的水晶吊灯",
    })

    card = scene_card_llm.enrich_scene_card(_baseline(), generate_text=_fake(data))

    items = {entry["item"] for entry in [*card["architecture"], *card["fixed_props"]]}
    assert "crystal chandelier" not in items
    assert all("chandelier" not in relation["text"] for relation in card["relations"])


def test_enrich_scene_card_falls_back_on_invalid_json():
    def generate_text(system: str, user: str) -> str:
        return "这不是 JSON"

    card = scene_card_llm.enrich_scene_card(_baseline(), generate_text=generate_text)

    assert card["extractor"] == "rules"
    assert card["space_type"] == "warehouse"


def test_enrich_scene_card_falls_back_when_model_raises():
    def generate_text(system: str, user: str) -> str:
        raise RuntimeError("model down")

    card = scene_card_llm.enrich_scene_card(_baseline(), generate_text=generate_text)

    assert card["extractor"] == "rules"


def test_enrich_scene_cards_keeps_one_scene_failure_isolated():
    good = _payload()
    calls = {"count": 0}

    def generate_text(system: str, user: str) -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            return json.dumps(good, ensure_ascii=False)
        raise RuntimeError("second scene failed")

    cards = {"scene-0": _baseline(), "scene-1": _baseline()}
    enriched = scene_card_llm.enrich_scene_cards(cards, generate_text=generate_text)

    assert enriched["scene-0"]["extractor"] == "llm"
    assert enriched["scene-1"]["extractor"] == "rules"
