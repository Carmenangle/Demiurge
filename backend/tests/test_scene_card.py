"""场景卡抽取与空场景提示词（2026-11-12）。"""

from app.services import scene_card

WAREHOUSE = (
    "旧仓库的木门前铺着红毯，门框缠着白纱，红毯一直铺到仓库中央的木板台子。\n"
    "腓特烈从侧门走进来，白色婚纱拖在红毯上。\n"
    "她停在台前。"
)

LIT_HALL = "黄昏的光从左侧窗户照进礼堂，吊灯昏黄，红毯铺在中央。"


def _positions(entries: list[dict]) -> dict[str, str]:
    return {str(entry["item"]): str(entry["position"]) for entry in entries}


def test_extract_scene_card_reads_architecture_props_and_layout():
    card = scene_card.extract_scene_card(WAREHOUSE, scene_key="scene-0", locale="旧仓库")

    assert card["scene_key"] == "scene-0"
    assert card["locale"] == "旧仓库"
    assert card["space_type"] == "warehouse"
    architecture = _positions(card["architecture"])
    assert architecture["wooden door"] == "entrance"
    # 方位只约束所在分句；「门框缠着白纱」没写方位，不跨逗号猜成 center/entrance。
    assert architecture["door frame"] == "unspecified"
    # 门类建筑自带入口语义（正文没写方位也不标 unspecified）。
    assert architecture["side door"] == "entrance"
    props = _positions(card["fixed_props"])
    assert props["wooden platform"] == "center"
    assert props["red carpet"].startswith("from entrance to center")
    assert props["white drapery"] == "unspecified"
    assert card["layout"]["center"] == "wooden platform"
    assert card["layout"]["entrance"] == "wooden door"
    assert "foreground" not in card["layout"]
    assert {"red", "white"} <= set(card["palette"])
    assert "lighting" in card["missing"]


def test_extract_scene_card_dedupes_overlapping_prop_cues():
    """`木板台子` 同时命中 `木板台` 与 `台子`，只能产出一条陈设。"""
    card = scene_card.extract_scene_card("仓库中央是木板台子。")

    platforms = [entry["item"] for entry in card["fixed_props"] if "platform" in entry["item"]]
    assert platforms == ["wooden platform"]


def test_extract_scene_card_records_lighting_time_and_camera():
    card = scene_card.extract_scene_card(LIT_HALL, framing="大远景 仰拍")

    assert card["space_type"] == "ceremony hall"
    assert card["lighting"]["time"] == "dusk"
    assert "chandelier" in card["lighting"]["sources"]
    assert card["lighting"]["direction"] == "from the left"
    assert card["camera"]["shot_size"] == "extreme wide"
    assert card["camera"]["angle"] == "low angle"
    assert card["missing"] == []
    assert card["inferred"] == []


def test_extract_scene_card_flags_missing_facts_instead_of_guessing():
    card = scene_card.extract_scene_card("她坐在床边。")

    assert card["space_type"] == ""
    assert "space_type" in card["missing"]
    assert "architecture" in card["missing"]
    assert "camera" in card["inferred"]
    assert card["camera"]["shot_size"] == "wide"
    assert card["camera"]["angle"] == "eye level"


def test_extract_scene_card_ignores_prop_only_colors():
    """「粉色项圈」是道具色，不是环境主色；颜色只取建筑/陈设所在分句。"""
    card = scene_card.extract_scene_card(
        "卧室里窗帘拉着一半。榨乳器摆在床头柜上，一副粉色的项圈压在底下。"
    )

    assert "pink" not in card["palette"]


def test_build_scene_prompt_asks_for_room_panorama():
    """场景图要整间房的全景，不是从门口看进去的 9:16 竖构图。"""
    card = scene_card.extract_scene_card(WAREHOUSE)

    prompt = scene_card.build_scene_prompt(card)

    assert "wide-angle interior panorama" in prompt
    assert "from a corner inside the room" in prompt
    assert "entire room layout visible" in prompt
    assert "16:9 landscape composition" in prompt
    assert "foreground: wooden door" not in prompt


def test_extract_scene_card_handles_long_real_paragraph():
    """真实段落：关系跨逗号、台子带「木板」定语、末尾还拼着地标召回串。"""
    window = (
        "旧仓库的木门被拆掉了一半，剩下的门框上缠着从补给船里翻出来的白纱。"
        "红毯是从总部仓库借的，边角磨得发白，一直铺到仓库中央那块用木板垫高的台子上。"
        "；同场景地标：木门框、木板台、侧门、台前、走廊"
    )

    card = scene_card.extract_scene_card(window, scene_key="scene-0")

    props = _positions(card["fixed_props"])
    assert props["red carpet"] == "leading to center (wooden platform)"
    assert props["wooden platform"] == "center"
    assert "platform" not in props
    architecture = _positions(card["architecture"])
    assert architecture["wooden door"] == "entrance"
    # 侧门来自同场景地标（入场镜），环境图也必须带上，否则红毯起点没有门。
    assert architecture["side door"] == "entrance"
    assert card["landmarks"] == ["木门框", "木板台", "侧门", "台前", "走廊"]
    assert "木板台" in card["do_not_change"]
    prompt = scene_card.build_scene_prompt(card)
    assert "soft ambient lighting" in prompt


def test_extract_scene_card_promotes_landmark_fixtures():
    """「侧门」只出现在同场景其他镜的地标里，也要进建筑表，否则环境图没有侧门。"""
    card = scene_card.extract_scene_card(
        "旧仓库的木门前铺着红毯。红毯一直铺到仓库中央的木板台子。；同场景地标：侧门、台前"
    )

    architecture = _positions(card["architecture"])
    assert architecture["side door"] == "entrance"
    # 地标里的方位/区域词不当物件。
    assert "台前" not in architecture


def test_extract_scene_card_records_door_condition_and_platform_steps():
    """正文写「木门被拆掉了一半」「木板垫高的台子」：门要有破损状态，台要有台阶。"""
    card = scene_card.extract_scene_card(
        "旧仓库的木门被拆掉了一半，剩下的门框上缠着白纱。"
        "红毯一直铺到仓库中央的木板台子。"
    )

    architecture = {entry["item"]: entry["condition"] for entry in card["architecture"]}
    assert "half-dismantled" in architecture["wooden door"]
    assert architecture["door frame"] == "remaining"
    assert "platform_steps" in card["inferred"]

    prompt = scene_card.build_scene_prompt(card)
    assert "broken doorway with half a wooden door leaf" in prompt
    assert "the half-dismantled door leaf is broken and half missing" in prompt
    assert "jagged planks, torn hinges, a large open gap" in prompt
    assert "wooden steps at the middle of the platform front edge" in prompt
    assert "red carpet leads up those steps onto the platform" in prompt


def test_build_scene_prompt_keeps_door_off_platform_and_carpet_origin():
    """门不能长在台上；红毯进入房间的那一端必须看得见侧门。"""
    card = scene_card.extract_scene_card(
        "旧仓库的木门前铺着红毯。红毯一直铺到仓库中央的木板台子。；同场景地标：侧门"
    )

    prompt = scene_card.build_scene_prompt(card)

    assert "no door stands on the platform" in prompt
    assert "doors are set into the surrounding walls" in prompt
    assert "one single continuous straight red carpet" in prompt
    assert "side door in the foreground left wall, open and clearly visible" in prompt
    assert (
        "red carpet runs in a straight diagonal line from that side door "
        "to the middle of the platform front edge"
    ) in prompt
    assert "wooden steps at the middle of the platform front edge" in prompt
    assert "red carpet leads up those steps onto the platform" in prompt
    assert "red carpet does not split, branch, bend, or run around the platform" in prompt
    assert "the other door is closed and has no carpet" in prompt


def test_build_scene_prompt_uses_exterior_panorama_for_courtyard():
    prompt = scene_card.build_scene_prompt({"space_type": "courtyard"})

    assert prompt.startswith("unpopulated scene, no people, no characters, courtyard")
    assert "wide-angle exterior panorama" in prompt
    assert "wide-angle interior panorama" not in prompt


def test_build_scene_prompt_expands_half_withered_tree():
    card = {
        "space_type": "courtyard",
        "fixed_props": [{
            "item": "old locust tree", "position": "entrance",
            "condition": "half-withered, rough bark",
        }],
    }

    prompt = scene_card.build_scene_prompt(card)

    assert "one side of the tree is bare and dead" in prompt
    assert "sparse withered leaves" in prompt


def test_build_scene_prompt_keeps_camera_inside_room():
    prompt = scene_card.build_scene_prompt({"space_type": "bedroom"})

    assert "camera stands inside the room, no doorway frame in the foreground" in prompt
    assert "any door is on the side or back wall, not in the foreground" in prompt


def test_build_scene_prompt_includes_inferred_structural_details():
    prompt = scene_card.build_scene_prompt({
        "space_type": "factory",
        "inferred": ["concrete floor", "exposed steel roof trusses", "lighting"],
    })

    assert "inferred environment details: concrete floor, exposed steel roof trusses" in prompt


def test_build_scene_prompt_is_empty_scene_without_characters():
    card = scene_card.extract_scene_card(WAREHOUSE, locale="旧仓库")

    prompt = scene_card.build_scene_prompt(card, style="flat anime illustration")

    assert prompt.startswith("unpopulated interior, no people, no characters")
    assert "warehouse" in prompt
    assert "red carpet" in prompt
    assert "from entrance to center" in prompt
    assert "raised wooden platform at center" in prompt
    assert "flat anime illustration" in prompt
    assert "腓特烈" not in prompt


def test_build_scene_prompt_skips_blank_fields():
    prompt = scene_card.build_scene_prompt({"space_type": "bedroom"})

    assert prompt.startswith("unpopulated interior, no people, no characters, bedroom")
    assert ", ," not in prompt
