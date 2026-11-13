"""角色当前状态卡（2026-11-12）。

这里锁的是**串人**与**编造**两类失败：真机 `堕落魔戒 SAVE02` 第 19 轮里，埃吉尔的名字出现在
下一句，而「她手里捏着那份抄本」实际是奥古斯特的动作——若按「命中句 ±1」取窗口，参考图
就会画成埃吉尔拿抄本。同样，`[在场]` 只有位置/动作时不能编出服装，性别表与正文冲突时
不能直接把表值喂给编译器。
"""

from app.services import character_state_card


def _fake_appearance(monkeypatch, *, appearance="", gender="", source="none"):
    """外貌/性别走真实接缝（`collect_appearance`），测试只换它的返回值，不碰文件系统。"""
    monkeypatch.setattr(
        character_state_card.character_portrait, "collect_appearance",
        lambda *args, **kwargs: {
            "appearance": appearance, "gender": gender, "source": source,
        },
    )


def test_state_window_keeps_only_named_sentences():
    text = (
        "埃吉尔低头看着两只叠在一起的手，没有说话。"
        "她手里捏着那份抄本，走上最后一级就停了，靠在栏杆的立柱边上。"
        "奥古斯特从梯口转上来，螺甸紫的长发在应急灯下泛着一点灰。"
    )
    # others 必须传全：漏传会让「她」句被当成单人场面的代词句收进来。
    window, evidence = character_state_card.state_window(
        text, "埃吉尔", others=["奥古斯特", "腓特烈"],
    )
    assert "抄本" not in window
    assert "螺甸紫" not in window
    assert window.startswith("埃吉尔低头看着两只叠在一起的手")
    assert evidence == ["埃吉尔低头看着两只叠在一起的手，没有说话。"]


def test_state_window_drops_dialogue_including_other_names():
    text = (
        "奥古斯特往前走了两步，声音不高，也不急。"
        "「今年配给IB031的制式戒只有两枚，一枚在腓特烈大人的中指上，一枚在你手里。」"
        "腓特烈翻了个身。"
    )
    window, _ = character_state_card.state_window(text, "腓特烈")
    assert window == "腓特烈翻了个身。"


def test_state_window_skips_pronoun_led_sentences():
    text = "她的眼睛从埃吉尔脸上移到他脸上，停了一停。"  # 主语是「她」，不是埃吉尔
    window, evidence = character_state_card.state_window(text, "埃吉尔", others=["奥古斯特"])
    assert window == ""
    assert evidence == []


def test_pronoun_sentences_rejected_when_character_never_named():
    """真机回归：腓特烈没在 turn 19 被点名，那些「她」是埃吉尔（握手/尾巴）。"""
    text = (
        "五指扣住她拿戒指的那只手，力道不轻。\n\n"
        "她的手指凉，掌心却热，整只手在他掌里停住。\n\n"
        "她那条尾巴从他腿边扫过去，末端在他裤管上轻轻磕着。"
    )
    window, evidence = character_state_card.state_window(
        text, "腓特烈", others=["埃吉尔", "奥古斯特"],
    )
    assert window == ""
    assert evidence == []


def test_pronoun_sentences_kept_in_single_actor_fragment_with_name():
    """凌霄仙母录那种单人片段仍要能收「她」句，不能被上一条修法误伤。"""
    text = "妲千沁把外衫褪到石案上。\n\n她抬起眼，眼睛还很亮。"
    window, _ = character_state_card.state_window(text, "妲千沁")
    assert "妲千沁把外衫褪到石案上" in window
    assert "她抬起眼" in window


def test_pronoun_sentences_before_target_is_named_are_not_stolen():
    """turn 18 真机：前半段「她递交接单」是别人，腓特烈到卧室段才被点名。"""
    text = (
        "她把两张交接单并在一起递过来，钢笔已经拔了帽。\n\n"
        "她皱了下眉，脚步却没停。\n\n"
        "腓特烈没睡实，项圈上的链子绕在手腕上，听见脚步就睁了眼，把被子往下推了一截。\n\n"
        "她把身子撑起来，动作里有一点费力。"
    )
    window, _ = character_state_card.state_window(
        text, "腓特烈", others=["埃吉尔", "奥古斯特"],
    )
    assert "交接单" not in window
    assert "钢笔" not in window
    assert "腓特烈没睡实" in window
    assert "她把身子撑起来" in window


def test_state_window_keeps_she_sentences_in_single_character_paragraph():
    """凌霄仙母录的实验片段通篇只有妲千沁一人（多数句子以「她」开头），必须收。"""
    text = (
        "日头爬到檐角那株狐尾槐的树冠上。"
        "妲千沁便从前殿回来了。"
        "她走得比平日急，靴底在窄道上连声响。"
        "她把剑搁在门后，走过去在石案边坐下。"
    )
    window, _ = character_state_card.state_window(text, "妲千沁", others=["凌若冰", "舞姬恋"])
    assert "她走得比平日急" in window
    assert "她把剑搁在门后" in window


def test_state_window_drops_she_sentences_when_paragraph_has_others():
    """段落里还有别的在场角色 ⇒ 「她」归属不可判，整段不收（防串人）。"""
    text = (
        "奥古斯特从梯口转上来。"
        "她手里捏着那份抄本，靠在栏杆的立柱边上。"
        "埃吉尔低头看着两只叠在一起的手。"
    )
    window, _ = character_state_card.state_window(
        text, "埃吉尔", others=["奥古斯特", "腓特烈"],
    )
    assert "抄本" not in window
    assert "埃吉尔低头看着两只叠在一起的手" in window


def test_clean_text_restores_jailbreak_markers():
    """`@嘴@(唇)@` 是防拦截拆字，还原成「嘴唇」再进画面描述。"""
    cleaned = character_state_card._clean_text("凌若冰@嘴@(唇)@从上面松开。")
    assert "@" not in cleaned
    assert "嘴唇" in cleaned


def test_clean_text_drops_roll_panel():
    """`<roll>` 里的 `[TYPE]/[SKILL] 75[基础45（…）]` 是数值面板，不是画面。"""
    text = (
        "<status>[在场] 凌若冰</status>"
        "<roll>[TYPE] 反攻夺取主导权[SKILL] 75[基础45（…）+20（…）]</roll>"
        "<content>凌若冰跪坐着，抬眼看他。</content>"
    )
    cleaned = character_state_card._clean_text(text)
    assert "[TYPE]" not in cleaned
    assert "[SKILL]" not in cleaned
    assert "凌若冰跪坐着" in cleaned


def test_wardrobe_reads_present_parentheses_not_other_dialogue(monkeypatch):
    _fake_appearance(monkeypatch)
    card = character_state_card.extract_state_card(
        base="", repo_id="", name="腓特烈",
        text="「你该去量礼服，腓特烈。」腓特烈把被子往下推了一截。",
        present="腓特烈（白色婚纱，项圈藏在头纱下）；埃吉尔（二楼走廊）",
    )
    assert card["wardrobe"] == "白色婚纱，项圈藏在头纱下"


def test_wardrobe_never_borrowed_from_other_character_lines(monkeypatch):
    _fake_appearance(monkeypatch)
    card = character_state_card.extract_state_card(
        base="", repo_id="", name="腓特烈",
        text="奥古斯特穿着黑丝女仆装站在门口。腓特烈翻了个身。",
        present="腓特烈（卧室）",
    )
    assert card["wardrobe"] == ""
    assert "wardrobe" in card["missing"]


def test_gender_conflict_clears_table_value(monkeypatch):
    _fake_appearance(monkeypatch, appearance="暗黄色长发", gender="男", source="merged")
    card = character_state_card.extract_state_card(
        base="", repo_id="", name="腓特烈",
        text="腓特烈把被子往下推了一截，她听见脚步就睁了眼。",
    )
    assert card["identity"]["gender"] == ""
    assert any(item.startswith("gender_conflict") for item in card["missing"])


def test_gender_kept_when_text_agrees(monkeypatch):
    _fake_appearance(monkeypatch, gender="女", source="merged")
    card = character_state_card.extract_state_card(
        base="", repo_id="", name="奥古斯特",
        text="奥古斯特往前走了两步，她声音不高。",
    )
    assert card["identity"]["gender"] == "女"
    assert not any(item.startswith("gender_conflict") for item in card["missing"])


def test_missing_state_is_reported_not_filled(monkeypatch):
    _fake_appearance(monkeypatch)
    card = character_state_card.extract_state_card(
        base="", repo_id="", name="腓特烈",
        text="奥古斯特从梯口转上来。",
    )
    assert card["state_text"] == ""
    assert "current_state" in card["missing"]
    assert card["evidence"] == []


def test_missing_base_image_is_reported(monkeypatch):
    _fake_appearance(monkeypatch)
    card = character_state_card.extract_state_card(
        base="", repo_id="", name="埃吉尔",
        text="埃吉尔低头看着两只叠在一起的手。",
    )
    assert "base_image" in card["missing"]


def test_empty_name_raises():
    try:
        character_state_card.extract_state_card(base="", repo_id="", name="  ", text="x")
    except ValueError:
        return
    raise AssertionError("空角色名必须抛 ValueError")


def test_state_hash_changes_with_wardrobe():
    base = {
        "name": "腓特烈",
        "identity": {"appearance": "白发", "base_image": "b.png"},
        "wardrobe": "白色婚纱",
        "state_text": "腓特烈坐着。",
        "scene_context": {"locale": "卧室", "lighting": ""},
    }
    changed = dict(base, wardrobe="黑色长裙")
    assert character_state_card.state_hash(base) != character_state_card.state_hash(changed)
    assert character_state_card.state_hash(base) == character_state_card.state_hash(dict(base))


def test_build_state_scene_locks_single_subject_and_viewpoint(monkeypatch):
    _fake_appearance(monkeypatch, appearance="白发、龙角", gender="女", source="merged")
    card = character_state_card.extract_state_card(
        base="", repo_id="", name="埃吉尔",
        text="埃吉尔低头看着两只叠在一起的手，没有说话。",
        locale="二楼走廊", lighting={"sources": ["lamplight"], "time": "early morning"},
    )
    scene = character_state_card.build_state_scene(card)
    assert scene["actors"] == ["埃吉尔"]
    assert scene["frame_subjects"] == ["埃吉尔"]
    assert scene["viewpoint"] == "third_person"
    assert scene["narrator"] == ""
    assert scene["aspect_ratio"] == "9:16"
    assert "二楼走廊" in scene["locale"]
    assert "lamplight" in scene["locale"] and "early morning" in scene["locale"]
    # 取景前缀必须在状态之前：参考图要当全身锚点，缺脚等于废图（2026-11-12 真机首图实锤）。
    assert scene["narrative"].startswith("全身竖构图")
    assert "埃吉尔低头" in scene["narrative"]


def test_build_state_scene_falls_back_to_framing_without_state():
    card = {
        "name": "腓特烈", "identity": {"appearance": ""}, "wardrobe": "",
        "state_text": "", "scene_context": {},
    }
    scene = character_state_card.build_state_scene(card)
    assert "腓特烈" in scene["narrative"]
    assert "全身入镜" in scene["narrative"]
