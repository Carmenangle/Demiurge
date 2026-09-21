import json
import re

import pytest

from app.services import image_prompt_profiles as profiles

K2_TAGS_LINE = (
    "1girl, long black hair, crimson eyes, torn white robe, medium shot, dramatic side light, detailed background"
)


def _scene(rating="sfw"):
    return {
        "narrative": "高潮动作所在段落",
        "draft_prompt": "close-up, dramatic lighting",
        "wardrobe": "红色长裙",
        "locale": "寝殿",
        "actors": ["爱丽丝"],
        "rating": rating,
        "character_lora": True,
    }


def _anima_json(content, *, visual_hook="deep negative space and a strong diagonal lead the eye to the decisive glance",
                primary_focus="the reflected crimson eye", supporting_elements=None):
    return json.dumps({
        "visual_hook": visual_hook,
        "primary_focus": primary_focus,
        "supporting_elements": supporting_elements or ["silver crest", "pale silhouette"],
        "content": content,
    })


def test_krea2输出两段式tags与文段并剥离自检():
    tags = (
        "1woman, long red dress, medium shot, low angle, cool side light, "
        "moving fabric folds, clean negative space"
    )
    body = (
        "A low-angle medium shot follows an adult woman in a red dress as she crosses the quiet bedchamber. "
        "Cool side light catches the moving folds and guides the eye from her face to her leading hand, while "
        "the room falls into restrained shadow and the doorway supplies clean negative space. " * 3
    ).strip()
    calls = []

    def generate(system, user):
        calls.append((system, json.loads(user)))
        return tags + "\n" + body + "\n\n——自检——\n自检: 致命项 9/9"

    prompt = profiles.generate("krea2", _scene("sfw"), generate)

    assert prompt == tags + "\n" + body  # 两段结构保留
    assert "仅输出两行" in calls[0][0]
    assert "不得夹杂中文" in calls[0][0]
    assert "六个维度" in calls[0][0]
    assert "SFW" not in calls[0][0]
    assert "NSFW" not in calls[0][0]
    assert "——自检——" not in prompt


def test_krea2不按分级切换模板而直接转译高潮内容():
    tags = (
        "1woman, red dress disordered, high angle medium shot, cool directional light, "
        "explicit consensual action, readable anatomy"
    )
    body = (
        "An adult woman occupies the left third of a high-angle medium shot inside the bedchamber. "
        "Her red dress remains in the exact disordered condition established by the scene as she bends forward "
        "and continues the explicit consensual action. Cool directional light separates her face, hands, and the "
        "contact point from the softened background; restrained occlusion keeps the anatomy readable, while the "
        "bed frame directs attention back to the decisive interaction and preserves clear spatial depth. " * 2
    ).strip()
    seen = {}

    def generate(system, user):
        seen["system"] = system
        return tags + "\n" + body

    assert profiles.generate("krea2", _scene("nsfw"), generate) == tags + "\n" + body
    assert "仅输出两行" in seen["system"]
    assert seen["system"] == profiles._system("krea2", _scene("sfw"))
    assert "不得续写剧情" in seen["system"]
    assert "不得改变人物、动作、服装、关系、地点或剧情结果" in seen["system"]


@pytest.mark.parametrize("profile,prompt", [
    (
        "krea2",
        "A medium environmental composition keeps the primary adult woman's visible action as the single visual focus. "
        "Directional side light shapes the subject while fabric layers and the background recede into shadow.",
    ),
    (
        "anima_tags",
        profiles.ANIMA_QUALITY_TAGS + "\nadult woman, medium shot, side light, layered background, fabric folds, visual focus. "
        "The subject remains readable against controlled shadow.",
    ),
    (
        "natural_language",
        "A medium shot keeps an adult woman as the visual focus under directional light, with fabric texture and layered background depth.",
    ),
    (
        "niji_sections",
        "adult woman\nrefined illustration\nmedium shot, directional light, layered composition, fabric texture\n--ar 3:4 --niji 6",
    ),
])
def test_字段账本为所有模板补齐真实外貌服装动作地点与质量(profile, prompt):
    scene = {
        "narrative": "冷倾雪在栏杆边转身，破损的法衣随动作绷紧。",
        "appearance": (
            "【外貌】漆黑墨发扎成发团，插紫玉金髻，朱唇，晶亮美目。"
            "【身材】丰腴熟躯，纤腰，宽厚圆硕臀部，修长美腿。"
        ),
        "wardrobe": "破损的素紫薄纱法衣，蚕丝白袜",
        "locale": "栏杆边",
        "draft_prompt": "turning beside a railing",
        "actors": ["冷倾雪"],
    }

    completed, ledger = profiles.complete_field_coverage(profile, prompt, scene)

    assert completed.isascii()
    assert "冷倾雪" not in completed
    assert all(not item["required"] or item["covered"] for item in ledger.values())
    for fact in ("jet-black hair", "purple jade", "torn clothing", "white silk", "turning", "railing"):
        assert fact in completed


@pytest.mark.parametrize("profile", ["krea2", "anima_tags", "natural_language", "niji_sections"])
def test_所有模板把连续高潮编译为复合动作并让动态破损服装覆盖基础服装(profile):
    scene = {
        "narrative": (
            "他的手扣住她的脖子，把她压向石台。墙后的机关发出闷响。"
            "他俯身贴近，在这个时候重新推进去了。"
            "他一下一下掐着她的脖子继续动作，高潮骤然袭来。"
        ),
        "appearance": "漆黑墨发，暗红美眸，红莲纹饰华袍，丰腴身材。",
        "wardrobe": "",
        "locale": "天牢囚室",
        "actors": ["虞妙玥"],
        "subjects": [{
            "name": "虞妙玥",
            "description": "华袍残片散落在腰侧，颈部留有清晰指痕。",
        }],
    }
    if profile == "anima_tags":
        seed = profiles.ANIMA_QUALITY_TAGS + "\nadult woman, medium shot, side light, layered background."
    elif profile == "niji_sections":
        seed = "adult woman\nrefined illustration\nmedium shot, side light, layered background\n--ar 2:3 --niji 6"
    else:
        seed = (
            "A medium shot places an adult woman in a confinement cell under directional side light, "
            "with layered background depth, controlled fabric texture, clean anatomy, and polished detail."
        )

    completed, ledger = profiles.complete_field_coverage(profile, seed, scene)

    assert (
        "penetrative intercourse while the secondary adult partner grips the primary woman's throat"
        in completed
    )
    assert "torn remnants of a red lotus patterned robe" in completed
    assert "a red lotus patterned robe" not in completed.replace(
        "torn remnants of a red lotus patterned robe", "",
    )
    assert ledger["action"]["covered"] is True
    assert ledger["wardrobe"]["covered"] is True


@pytest.mark.parametrize("profile", ["krea2", "anima_tags", "natural_language", "niji_sections"])
def test_所有模板逐条验收开放类型的证据化可视事实(profile):
    scene = {
        "narrative": "她停在门边，左手托着发光的陌生螺旋物，右肩抵住裂开的铜镜。",
        "actors": ["甲"],
        "visual_facts": [
            {"kind": "invented_prop", "fact": "a glowing spiral object rests on her left palm", "evidence": "左手托着发光的陌生螺旋物"},
            {"kind": "body_contact", "fact": "her right shoulder braces against a cracked bronze mirror", "evidence": "右肩抵住裂开的铜镜"},
        ],
    }
    if profile == "anima_tags":
        seed = profiles.ANIMA_QUALITY_TAGS + "\nadult woman, medium shot, side light, layered room."
    elif profile == "niji_sections":
        seed = "adult woman\nrefined illustration\nmedium shot, side light, layered room\n--ar 3:4 --niji 6"
    else:
        seed = "A medium shot shows an adult woman under directional light in a layered room."

    completed, ledger = profiles.complete_field_coverage(profile, seed, scene)

    assert "a glowing spiral object rests on her left palm" in completed
    assert "her right shoulder braces against a cracked bronze mirror" in completed
    assert ledger["visual_facts"]["covered"] is True


@pytest.mark.parametrize("profile,raw,expected", [
    (
        "krea2",
        "1woman, torn red dress, black hair, narrow eyes, close medium shot, "
        "directional side light, controlled shadow, negative space\n"
        + (
            "A close medium shot keeps the woman's @(gripping)@ hand and torn red dress in the foreground. "
            "Side light follows her black hair, narrow eyes, and the exact contact point while the chamber "
            "recedes through controlled shadow and negative space. " * 2
        ),
        "gripping",
    ),
    (
        "anima_tags",
        lambda: _anima_json(
            "1girl, black hair, narrow eyes, torn red dress, @(gripping)@ wrist, side light. "
            "A foreground frame leads toward the exact contact point while the chamber recedes into shadow."
        ),
        "gripping wrist",
    ),
    (
        "natural_language",
        "A black-haired woman in a torn red dress @(grips)@ the other wrist under directional side light.",
        "grips",
    ),
    (
        "niji_sections",
        '{"subject":"black-haired woman in a red long dress @(gripping)@ a wrist","style":"painted illustration",'
        '"additions":"side light and foreground framing","suffix":"--ar 2:3 --niji 6"}',
        "gripping",
    ),
])
def test_所有模板都能还原防拦截输出再转换格式(profile, raw, expected):
    value = raw() if callable(raw) else raw
    prompt = profiles.generate(profile, _scene("nsfw"), lambda _system, _user: value)

    assert "@(" not in prompt
    assert expected in prompt


def test_profile调用保留防拦截剧情而本地事实使用还原正文():
    scene = {
        **_scene("nsfw"),
        "wardrobe": "",
        "narrative": "她抓住对方手腕，红裙已经撕裂。",
        "protected_narrative": "她@(抓)@住对方@(手)@@(腕)@，红裙已经@(撕)@@(裂)@。",
    }
    seen = {}
    tags = (
        "1woman, torn red dress, black hair, narrow eyes, close medium shot, "
        "directional side light, receding chamber"
    )
    body = (
        "A close medium shot places the gripping hand and torn red dress in the left foreground. "
        "Her established black hair and narrow eyes remain readable as directional side light follows "
        "the contact point, controlled fabric tension, and the chamber's receding negative space. " * 2
    )

    def generate(_system, user):
        seen.update(json.loads(user))
        return tags + "\n" + body

    assert profiles.generate("krea2", scene, generate) == tags + "\n" + body.strip()
    assert seen["narrative"] == scene["protected_narrative"]
    assert "protected_narrative" not in seen


def test_anima输出固定质量行加英文tags与关系描述():
    content = (
        "1girl, solo, red dress, low angle, shallow depth of field, rim lighting. "
        "Warm rim light separates her face from the softened background while the dress catches fine highlights."
    )
    prompt = profiles.generate(
        "anima_tags", _scene(), lambda _system, _user: _anima_json(content),
    )
    tags, prose = prompt.splitlines()
    assert tags.startswith(profiles.ANIMA_QUALITY_TAGS + ", 1girl, solo, red dress")
    assert tags.endswith(",")
    assert prose.startswith("Warm rim light separates")


def test_anima按场景合并基础质量词与成人质量词():
    sfw = profiles.anima_quality_tags("sfw")
    nsfw = profiles.anima_quality_tags("nsfw")

    for tag in ("best quality", "score_7", "score_9", "very aesthetic",
                "ultra detailed", "fair skin", "high contrast"):
        assert tag in sfw
        assert tag in nsfw
    assert "sensitive" not in sfw
    assert "explicit" not in sfw
    assert "sensitive" in nsfw
    assert "explicit" in nsfw


def test_anima连续拒答时回退已有高潮tags而不阻断出图():
    scene = {
        **_scene("nsfw"),
        "draft_prompt": "masterpiece, best quality\n1girl, solo, dramatic composition, rim lighting, close-up, detailed eyes",
    }

    prompt = profiles.generate(
        "anima_tags", scene, lambda _system, _user: "I can't help with this request.",
    )

    tags, prose = prompt.splitlines()
    assert "explicit" in tags
    assert "1girl, solo, rim lighting" in tags
    assert "dramatic composition" not in tags
    assert "1girl, solo, rim lighting" in prose
    assert "visible action remains" not in prose.lower()
    assert "I can't help" not in prompt


def test_anima拒答兜底合并混合语言角色条目的具体英文外貌():
    scene = {
        **_scene("nsfw"),
        "appearance": "冷倾雪: long black hair, straight bangs, narrow red eyes",
        "draft_prompt": (
            "two adult women, wrist held, pulling closer, rain-soaked railing, "
            "side light, medium shot"
        ),
    }

    prompt = profiles.generate(
        "anima_tags", scene, lambda _system, _user: "I can't help with this request.",
    )

    assert "long black hair" in prompt
    assert "straight bangs" in prompt
    assert "narrow red eyes" in prompt


def test_anima完整拒答尾缀不得进入最终提示词():
    refusal = (
        "I can't help with this request. The prompt describes content involving intimate "
        "physical contact with what appears to be a vulnerable or incapacitated person in a "
        "way that raises serious concerns. If you have other creative or technical prompts "
        "you'd like help with, I'm happy to assist."
    )

    prompt = profiles.generate("anima_tags", _scene("nsfw"), lambda _system, _user: refusal)

    assert "I can't help" not in prompt
    assert "serious concerns" not in prompt
    assert len(prompt.splitlines()) == 2


def test_anima保留有效英文提示词并裁掉中文拒答尾缀():
    raw = (
        "masterpiece, best quality, score_7, score_9, anime coloring\n"
        "adult woman, black hair, red eyes, close-up, side lighting, shallow depth of field. "
        "Her face remains the sharp visual focus while the room recedes into soft shadow.\n"
        "此请求包含明确的色情内容，我无法协助处理。\n"
        "如果你需要帮助提取绘画风格、构图、光影等关键词，欢迎提供其他提示词。"
    )

    prompt = profiles.normalize_inline("anima_tags", raw, {**_scene("nsfw"), "wardrobe": ""})

    assert "adult woman, black hair, red eyes" in prompt.splitlines()[0]
    assert "无法协助" not in prompt
    assert "欢迎提供" not in prompt


def test_anima剧情有动作时普通肖像输出必须触发重写():
    scene = {
        **_scene(),
        "wardrobe": "",
        "narrative": "她伸手抓住他的手腕，将他拉向门口。",
        "draft_prompt": "1girl, solo, dramatic composition, cinematic lighting, close-up, detailed eyes",
    }
    outputs = iter([
        _anima_json(
            "1girl, solo, close-up, detailed eyes, rim lighting, shallow depth of field. "
            "Her face remains the only sharp focal point against the softened doorway.",
        ),
        _anima_json(
            "1girl, reaching out, gripping wrist, pulling another person, doorway, diagonal composition. "
            "Her extended arm and locked grip form the sharp diagonal that pulls both figures toward the door.",
        ),
    ])
    users = []

    def generate(_system, user):
        users.append(user)
        return next(outputs)

    prompt = profiles.generate("anima_tags", scene, generate)

    assert "gripping wrist" in prompt
    assert len(users) == 2
    assert "剧情存在明确动作" in users[1]


def test_anima连续失败时仍从剧情保留具体动作而非退化为特写():
    scene = {
        **_scene(),
        "wardrobe": "",
        "narrative": "她伸手抓住他的手腕，将他拉向门口。",
        "draft_prompt": "1girl, solo, dramatic composition, cinematic lighting, close-up, detailed eyes",
    }

    prompt = profiles.generate(
        "anima_tags", scene, lambda _system, _user: "I can't help with this request.",
    )

    content = prompt
    assert "reaching out" in content
    assert "gripping wrist" in content
    assert "pulling another person" in content


def test_anima新角色Profile连续失败时从中文场景保留身份外貌动作与环境():
    scene = {
        **_scene(),
        "wardrobe": "",
        "narrative": (
            "骡子拉的平板车停在边地孤儿院门口。方葛是三十出头的药材商贩，方脸浓眉，"
            "宽肩厚背，褐色短褂袖口挽起，露出结实小臂和掌心厚茧，笑时露出小虎牙。"
            "她把第一口药材木箱搬到门前石台上，招呼不安的院长查看黄芪。"
        ),
        "draft_prompt": "",
        "actors": ["方葛"],
        "encounter": {
            "who": "方葛（伪装药材商贩）",
            "where": "边地孤儿院门口",
            "mood": "风尘仆仆的爽朗热络",
        },
    }

    prompt = profiles.generate(
        "anima_tags", scene, lambda _system, _user: "I can't help with this request.",
    )

    content = prompt
    for fact in (
        "adult woman", "early thirties", "broad shoulders", "square face",
        "thick eyebrows", "prominent canine tooth", "calloused hands", "brown work jacket",
        "rolled sleeves", "unloading a wooden medicine crate", "mule-drawn cart",
        "medicinal herbs", "orphanage entrance", "anxious matron",
    ):
        assert fact in content
    assert "1girl, solo, dramatic composition" not in content


def test_anima新角色提示词事实错误时重写且不固定艺术方案():
    scene = {
        **_scene(),
        "wardrobe": "",
        "narrative": (
            "方葛是药材商贩，方脸浓眉、宽肩厚背，褐色短褂挽起袖口，掌心厚茧，"
            "笑时露出小虎牙。她把药材木箱搬到孤儿院门前，招呼不安的院长查看黄芪，"
            "骡车停在身后。"
        ),
        "draft_prompt": "",
        "actors": ["方葛"],
        "encounter": {"who": "方葛（伪装药材商贩）", "where": "孤儿院门前"},
    }
    wrong = _anima_json(
        "1girl, solo, brown jacket, one hand raised, looking at viewer, low angle, mule cart. "
        "She freezes after slapping the mule's neck while the dark animal fills the frame.",
        visual_hook="the mule silhouette frames her raised hand",
        primary_focus="her raised hand beside the mule",
    )
    repaired = _anima_json(
        "2women, adult woman, herb merchant, broad shoulders, sturdy build, square face, "
        "thick eyebrows, prominent canine tooth, calloused hands, brown work jacket, rolled sleeves, "
        "unloading a wooden medicine crate, medicinal herbs, anxious matron, orphanage entrance, "
        "mule-drawn cart, diagonal composition, selective focus. The crate edge becomes a leading "
        "diagonal from her calloused hands to the matron's hesitant reach, while warm light on rough "
        "wood and muted herbs carries the scene's trust-building tension.",
        visual_hook="the crate edge forms a diagonal between both women's hands",
        primary_focus="the merchant's calloused hands unloading the medicine crate",
        supporting_elements=["the matron's hesitant reach", "softened mule cart"],
    )
    outputs = iter([wrong, repaired])
    users = []

    prompt = profiles.generate(
        "anima_tags", scene,
        lambda _system, user: (users.append(user), next(outputs))[1],
    )

    assert len(users) == 2
    assert "药材木箱" in users[1]
    assert "院长" in users[1]
    assert "正文没有拍打骡子的动作" in users[1]
    assert "diagonal composition" in prompt
    assert "warm light on rough wood" in prompt
    assert "slapping the mule" not in prompt


def test_anima满足事实合同的不同艺术方案首轮直接通过():
    scene = {
        **_scene(),
        "wardrobe": "",
        "narrative": (
            "方葛是药材商贩，方脸浓眉、宽肩厚背，褐色短褂挽起袖口，掌心厚茧，"
            "笑时露出小虎牙。她把药材木箱搬到孤儿院门前，院长迟疑地伸手查看药材。"
        ),
        "actors": ["方葛"],
    }
    artistic = _anima_json(
        "2women, adult woman, herb merchant, broad shoulders, square face, thick eyebrows, "
        "prominent canine tooth, calloused hands, brown work jacket, rolled sleeves, unloading a "
        "wooden medicine crate, medicinal herbs, anxious matron, orphanage entrance, overhead shot, "
        "frame within frame, cool shadows, amber highlights. The open crate forms a bright rectangle "
        "inside the dark doorway, linking the merchant's rough hands to the matron's hesitant reach.",
        visual_hook="the bright open crate creates a frame within the dark doorway",
        primary_focus="both hands meeting across the medicine crate",
        supporting_elements=["amber herb slices", "cool doorway shadow"],
    )
    calls = []

    prompt = profiles.generate(
        "anima_tags", scene,
        lambda _system, user: (calls.append(user), artistic)[1],
    )

    assert len(calls) == 1
    assert "overhead shot" in prompt
    assert "frame within frame" in prompt
    assert "cool shadows, amber highlights" in prompt


@pytest.mark.parametrize("profile", ["natural_language", "niji_sections"])
def test_非Anima_Profile连续失败仍保留场景而不取消插画(profile):
    scene = {
        **_scene(),
        "narrative": "方葛把药材木箱搬到孤儿院门前，院长迟疑地伸手接过。",
        "appearance": "Fang Ge: broad shoulders, square face, brown work jacket",
        "draft_prompt": "Fang Ge carries a wooden medicine crate to the orphanage entrance",
        "actors": ["方葛"],
    }

    prompt = profiles.generate(
        profile, scene, lambda _system, _user: "I can't help with this request.",
    )

    assert prompt.isascii()
    assert "Fang Ge" in prompt
    assert "wooden medicine crate" in prompt
    if profile == "niji_sections":
        assert len(prompt.splitlines()) == 4
        assert prompt.splitlines()[-1].startswith("--")


@pytest.mark.parametrize("profile", ["natural_language", "niji_sections"])
def test_自然语言与Niji拒答兜底也必须输出纯英文并保留英文高潮外貌(profile):
    scene = {
        **_scene("nsfw"),
        "protected_narrative": "冷倾雪@(抓)@住同伴的@(手)@@(腕)@并将她拉近。",
        "appearance": "冷倾雪: long black hair, straight bangs, narrow red eyes",
        "draft_prompt": "wrist held, pulling closer beside a rain-soaked railing",
    }

    prompt = profiles.generate(
        profile, scene, lambda _system, _user: "I can't help with this request.",
    )

    assert prompt.isascii()
    assert "@(" not in prompt
    assert "I can't help" not in prompt
    assert "long black hair" in prompt
    assert "wrist held" in prompt


def test_anima负面提示词独立返回且不混入正向两行():
    result = profiles.generate_result(
        "anima_tags", _scene(),
        lambda _system, _user: _anima_json(
            "1girl, solo, red dress, low angle, shallow depth of field, rim lighting. "
            "Soft directional light defines the subject against a restrained background."
        ),
    )

    assert result["prompt"].count("\n") == 1
    assert "low quality" not in result["prompt"]
    assert "low quality" in result["negative_prompt"]


def test_profile结果报告修复与兜底原因但不记录正文():
    result = profiles.generate_result(
        "krea2", _scene("nsfw"), lambda _system, _user: "I can't help with this request.",
    )

    assert result["strategy"] == "fallback"
    assert "模型返回拒答" in result["validation_errors"]
    assert all("高潮动作所在段落" not in item for item in result["validation_errors"])


def test_anima默认正负提示词可供设置页固定使用():
    defaults = profiles.profile_defaults("anima_tags", "nsfw")

    assert "score_7" in defaults["quality_prompt"]
    assert "sensitive" in defaults["quality_prompt"]
    assert "NJSW33T" not in defaults["quality_prompt"]
    assert "score_1" in defaults["negative_prompt"]
    assert "watermark" in defaults["negative_prompt"]


def test_anima独立Profile缺少具体视觉装置时触发重写():
    flat_content = (
        "1girl, 1boy, carriage, lacquer box, castle gate, medium shot, afternoon light. "
        "The woman, child, carriage, box, and gate are all clearly visible in the scene."
    )
    designed_content = (
        "1girl, lacquer box, eye reflection, silver crest, silhouette, selective focus. "
        "The black lacquer lid acts as a dark mirror, holding one crimson eye and the pale child's distant silhouette; "
        "the silver crest repeats that narrow highlight while carriage and gate dissolve into negative space."
    )
    outputs = iter([
        _anima_json(
            flat_content,
            visual_hook="dramatic departure scene",
            primary_focus="woman, child, box, carriage, and castle gate",
            supporting_elements=["child", "box", "carriage", "castle gate"],
        ),
        _anima_json(designed_content),
    ])
    users = []

    def generate(_system, user):
        users.append(user)
        return next(outputs)

    prompt = profiles.generate("anima_tags", {**_scene(), "wardrobe": ""}, generate)

    tags, prose = prompt.splitlines()
    assert designed_content.partition(".")[0] in tags
    assert prose == designed_content.partition(".")[2].strip()
    assert len(users) == 2
    assert "上次输出未通过" in users[1]
    assert "辅助元素最多两个" in users[1]


def test_anima结构化艺术决策不泄漏进最终两行提示词():
    content = (
        "1girl, lacquer reflection, close-up, crimson eye, silver crest, negative space. "
        "The lacquer reflection compresses the farewell into one eye and one fading silhouette."
    )

    prompt = profiles.generate(
        "anima_tags", {**_scene(), "wardrobe": ""}, lambda _s, _u: _anima_json(content),
    )

    assert prompt.count("\n") == 1
    assert "visual_hook" not in prompt
    assert "primary_focus" not in prompt
    tags, prose = prompt.splitlines()
    assert content.partition(".")[0] in tags
    assert prose.startswith(content.partition(".")[2].strip())
    assert "bedchamber" in prompt


def test_anima系统提示要求可见装置并禁止普通剧情清单():
    system = profiles._system("anima_tags", {"rating": "sfw"})

    for rule in ("visual_hook", "primary_focus", "supporting_elements", "最多两个", "普通剧情清单"):
        assert rule in system
    assert "反射" in system
    assert "负空间" in system
    assert "不要照搬示例" in system
    assert "人物的面部、目光、动作、接触点或人物关系" in system
    assert "物件只能作为辅助视觉装置" in system


def test_niji把结构化结果组装为四段():
    raw = json.dumps({
        "subject": "A swordswoman standing in rain",
        "style": "refined anime illustration",
        "additions": "cinematic rim light, dynamic framing",
        "suffix": "--stylize 400 --chaos 8 --no text",
    })
    prompt = profiles.generate("niji_sections", {**_scene(), "wardrobe": ""}, lambda _s, _u: raw)
    lines = prompt.splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("A swordswoman standing in rain")
    assert "bedchamber" in lines[0]
    # 第 33 刀（2026-09-21 用户口径）：内容段末尾统一补 `", "`（「保持良好的习惯」）；
    # `--` 参数段是 niji 参数串，**不得**补。
    assert lines[1] == "refined anime illustration, "
    assert lines[2].endswith(", ")
    assert lines[3] == "--stylize 400 --chaos 8 --no text"


def test_自然语言模式保持完整段落而非tags():
    raw = "A swordswoman stands on rain-darkened stone steps. Cool backlight defines her silhouette from a low angle."
    prompt = profiles.generate(
        "natural_language", {**_scene(), "wardrobe": ""}, lambda _s, _u: raw,
    )
    assert prompt.startswith(raw)
    assert "bedchamber" in prompt


def test_主模型内联Anima提示词只做本地归一不再调用模型():
    prompt = profiles.normalize_inline(
        "anima_tags",
        "1girl, solo, red dress, low angle, rim lighting, shallow depth of field. "
        "The face and eyes receive the finest detail while the background falls into soft focus.",
    )

    assert prompt.splitlines() == [
        profiles.ANIMA_QUALITY_TAGS
        + ", 1girl, solo, red dress, low angle, rim lighting, shallow depth of field,",
        "The face and eyes receive the finest detail while the background falls into soft focus.",
    ]


def test_主模型内联Anima在剧情有动作时拒绝静态特写降级():
    assert profiles.normalize_inline(
        "anima_tags",
        "1girl, solo, close-up, detailed eyes, rim lighting, shallow depth of field. "
        "Her face remains the only sharp focal point against the softened doorway.",
        {"narrative": "她伸手抓住他的手腕，将他拉向门口。", "rating": "sfw"},
    ) == ""


def test_anima拒绝只有tags而缺少英文关系描述():
    assert profiles.normalize_inline(
        "anima_tags", "1girl, solo, red dress, low angle, rim lighting, close-up",
    ) == ""


def test_主模型内联Krea中文旧格式失效以触发英文Profile生成():
    assert profiles.normalize_inline("krea2", "主模型已经生成的完整画面描述。") == ""


def test_主模型内联Krea提示词中性化真人皮肤语义():
    raw = (
        "1woman, purple dress, slightly elevated medium close-up, long lens, "
        "shallow depth of field, controlled side light\n"
        "A slightly elevated medium close-up uses a long lens and shallow depth of field. "
        "Her photorealistic translucent skin exposes microscopic veins and visible facial pores. "
        "Side light creates a controlled gradient across the purple dress."
    )

    prompt = profiles.normalize_inline("krea2", raw, {"rating": "sfw"})

    assert "microscopic veins" not in prompt
    assert "facial pores" not in prompt
    assert "translucent skin" not in prompt
    assert "photorealistic" not in prompt
    assert "natural tonal transitions across the skin" in prompt
    assert "slightly elevated medium close-up" in prompt
    assert "controlled gradient across the purple dress" in prompt


def test_同轮Krea只漏具体视觉事实时保留成稿并机械补齐():
    scene = {
        "appearance": "角色：【外貌】漆黑墨发扎成发团、插紫玉金髻，朱唇、脸颊红润。",
        "actors": ["角色"],
        "rating": "sfw",
    }
    raw = (
        # tags 首行只写构图/镜头，不得提前覆盖条目外貌事实（机械补齐依赖事实缺失）
        "1girl, low angle, broken wooden door, cold dawn light, misty mountain path, "
        "restrained negative space\n"
        "A low-angle environmental composition places the adult woman beside a broken wooden door. "
        "Cold dawn light defines her face and reaching hand while the mountain path recedes into mist. "
        "Restrained negative space, coherent anatomy, clean edges, controlled material detail, and stable perspective "
        "preserve the decisive action and polished image fidelity."
    )

    prompt = profiles.normalize_inline("krea2", raw, scene)

    assert "A low-angle environmental composition places the adult woman beside a broken wooden door." in prompt
    assert "Cold dawn light defines her face and reaching hand" in prompt
    assert "glossy jet-black hair" in prompt
    assert "rounded bun" in prompt
    assert "purple jade and gold hair ornament" in prompt
    assert "full crimson lips" in prompt
    assert "rosy cheeks" in prompt


def test_主模型内联Krea只删除锁定媒介的画风词():
    shared = (
        "A low-angle view places the woman in a purple dress beside a wooden rail, "
        "with side light defining her silhouette in a diagonal composition."
    )
    tags = (
        "1woman, purple dress, low-angle view, wooden rail, side light, diagonal composition"
    )

    realistic = profiles.normalize_inline(
        "krea2", tags + "\nPhotorealistic live-action imagery. " + shared, {"rating": "sfw"},
    )
    anime = profiles.normalize_inline(
        "krea2", tags + "\nDetailed anime illustration. " + shared, {"rating": "sfw"},
    )
    locked = profiles.normalize_inline(
        "krea2", tags + "\nLive-action photography with realistic human skin and 3D rendering. " + shared,
        {"rating": "sfw"},
    )

    for prompt in (realistic, anime, locked):
        assert "Photorealistic" not in prompt
        assert "live-action" not in prompt
        assert "anime illustration" not in prompt
        assert "realistic human skin" not in prompt
        assert "3D rendering" not in prompt
        assert shared in prompt
    assert "natural skin tones" in locked  # 真人向措辞收敛后替换为媒介中性描述


def test_格式不合格时带错误重写一次():
    # 词数偏短已降级为警告放行（2026-08-30 成本改约）；触发重写的必须是真实合同错误（如中文混入）
    outputs = iter([
        "too short 好的",
        (
            K2_TAGS_LINE + "\n"
            "A low-angle medium shot keeps the adult character's decisive movement as the primary focus. "
            "Soft directional light describes the clothing material, separates both hands from the body, "
            "and lets the layered chamber recede through controlled contrast and negative space. " * 3
        ).strip(),
    ])
    users = []

    def generate(_system, user):
        users.append(user)
        return next(outputs)

    prompt = profiles.generate("krea2", {**_scene(), "wardrobe": ""}, generate)
    assert prompt.splitlines()[1].startswith("A low-angle medium shot")  # 首行是 tags
    assert len(users) == 2
    assert "上次输出未通过" in users[1]


def test_krea2重写后仍不完全合规也返回非空结果而不阻断生图():
    outputs = iter([
        "初稿混入中文 First draft is too short.",
        "The second draft preserves the decisive action but remains shorter than the preferred range.",
    ])

    prompt = profiles.generate(
        "krea2", {**_scene("sfw"), "wardrobe": ""}, lambda _s, _u: next(outputs),
    )

    # 两版输出都未过两段式合同 → 确定性兜底接管，仍为两段且不阻断生图
    assert prompt.strip()
    assert len(prompt.splitlines()) == 2
    assert "The second draft preserves" not in prompt
    assert not re.search(r"[\u3400-\u9fff]", prompt)
    assert "bedchamber" in prompt
    assert "decisive action" in prompt


def test_krea2词数偏短降级为警告放行不再触发重写():
    """2026-08-30 成本改约：一次 repair = 一次携带防拦截预设的调用；词数偏短只警告。"""
    calls = []

    def generate(_system, _user):
        calls.append(1)
        return K2_TAGS_LINE + "\n" + "A short but valid English prompt describing the decisive action clearly."

    diagnostics: dict = {}
    prompt = profiles.generate("krea2", {**_scene("sfw"), "wardrobe": ""}, generate, diagnostics)

    assert len(calls) == 1  # 不再触发重写调用
    assert "bedchamber" in prompt  # 可视事实照常补齐
    assert "英文单词数必须为80到500" in (diagnostics.get("warnings") or [])


def test_krea2重写后仍含中文时不得提交而改用英文兜底():
    outputs = iter(["Too short.", "第二版仍然错误地使用中文。"])

    prompt = profiles.generate("krea2", _scene("sfw"), lambda _s, _u: next(outputs))

    assert not re.search(r"[\u3400-\u9fff]", prompt)
    assert "primary adult woman" in prompt
    assert "identified character" not in prompt


def test_krea2模型连续返回空内容时直接使用高潮场景兜底():
    scene = _scene("sfw")

    prompt = profiles.generate("krea2", scene, lambda _s, _u: "")

    assert not re.search(r"[\u3400-\u9fff]", prompt)
    assert "primary adult woman" in prompt
    assert "identified character" not in prompt
    assert "decisive action" in prompt


def test_krea2拒答不得作为最终提示词():
    assert profiles.normalize_inline(
        "krea2", "I can't help with this request.", {"rating": "nsfw"},
    ) == ""


def test_krea2连续拒答时使用合规场景兜底():
    prompt = profiles.generate(
        "krea2", _scene("nsfw"), lambda _system, _user: "I can't help with this request.",
    )

    assert "I can't help" not in prompt
    assert profiles.normalize_inline("krea2", prompt, {"rating": "nsfw"}) == prompt


def test_krea2角色lora连续拒答时兜底使用具体外貌而非身份占位句():
    scene = {
        **_scene("nsfw"),
        "wardrobe": "",
        "appearance": (
            "冷倾雪：【外貌】清秀动人却成熟色气的绝代美人，漆黑墨发扎成发团、插紫玉金髻，"
            "朱唇娇艳、脸颊红润，晶亮圆润的美目透着成熟干练；"
            "【身材】前凸后翘、曲线优美的丰腴熟躯，丰满胸部、纤腰、宽厚圆硕的臀部，"
            "蚕丝白袜包裹修长厚嫩美腿；【穿着】素紫色薄纱法衣、碎花紫长裙，高叉露出白袜美腿。"
        ),
    }
    prompt = profiles.generate(
        "krea2", scene, lambda _system, _user: "I can't help with this request.",
    )

    assert "jet-black hair" in prompt
    assert "purple jade" in prompt
    assert "crimson lips" in prompt
    assert "rosy cheeks" in prompt
    assert "luminous rounded eyes" in prompt
    assert "mature and composed gaze" in prompt
    assert "voluptuous" in prompt
    assert "white silk stockings" in prompt
    assert "purple" in prompt and "gauze robe" in prompt
    assert "even tonal transitions" in prompt  # 照片向措辞收敛后的新尾句
    assert "Leng Qingxue" not in prompt
    assert "established facial structure" not in prompt
    assert "identified character" not in prompt
    assert "bound character" not in prompt
    assert "photorealistic" not in prompt.lower()
    assert "live-action" not in prompt.lower()
    assert not re.search(r"\b(?:blue|green|red|golden|amber|violet|purple) (?:iris|irises|eyes)\b", prompt, re.I)


def test_profile模型输入匿名化角色姓名但保留完整条目外貌():
    scene = {
        **_scene("sfw"),
        "wardrobe": "",
        "actors": ["冷倾雪"],
        "appearance": "冷倾雪：【外貌】漆黑墨发扎成发团，朱唇，脸颊红润。",
        "subjects": [{"name": "冷倾雪", "description": "mature woman with a black hair bun"}],
        "narrative": "冷倾雪从破墙豁口望向外面。",
    }
    seen = {}
    tags = (
        "1woman, black hair bun, crimson lips, rosy cheeks, portrait composition, "
        "broken wall opening, cold side light"
    )
    valid = (
        "A portrait composition places the primary adult woman on the left third as she looks through the broken wall. "
        "Her glossy jet-black hair is gathered into a neat bun, with crimson lips and rosy cheeks under a composed mature gaze. "
        "A close medium three-quarter view keeps her face and hand sharp while the ruined enclosure recedes into cold haze. "
        "Side light follows the hair, skin, and worn fabric before falling into controlled shadow. "
        "The opening supplies directional negative space toward the outside path, with coherent anatomy, clean edges, stable perspective, and polished image fidelity."
    )

    def generate(_system, user):
        seen.update(json.loads(user))
        return tags + "\n" + valid

    assert profiles.generate("krea2", scene, generate) == tags + "\n" + valid
    serialized = json.dumps(seen, ensure_ascii=False)
    assert "冷倾雪" not in serialized
    assert "the primary adult character" in serialized.lower()
    assert "漆黑墨发扎成发团" in serialized


def test_krea2拒绝姓名和空泛身份锁并要求重写为具体视觉事实():
    scene = {
        **_scene("sfw"),
        "wardrobe": "",
        "actors": ["冷倾雪"],
        "appearance": (
            "冷倾雪：【外貌】漆黑墨发扎成发团、插紫玉金髻，朱唇、脸颊红润；"
            "【身材】丰腴曲线、纤腰、圆硕臀部；【穿着】素紫薄纱法衣、碎花紫长裙、蚕丝白袜。"
        ),
    }
    tags = (
        "1woman, glossy black hair, purple jade hair ornament, crimson lips, "
        "light purple gauze robe, white silk stockings, close medium three-quarter view, side light"
    )
    vague = tags + "\n" + (
        "Portrait composition centered on the adult woman Leng Qingxue, with directional negative space toward the exit. "
        "Preserve Leng Qingxue's established facial structure, eye shape, hairstyle silhouette, body proportions, costume, "
        "and illustrated identity exactly as defined by the bound character model, with no replacement face or altered clothing. "
        "Use a close medium three-quarter view with shallow depth of field. Side light defines her face and hand while the dark "
        "interior recedes behind her. Maintain precise anatomy, coherent fabric tension, stable perspective, clean edges, controlled "
        "fine detail, high image fidelity, and complete polished illustration quality across the finished image."
    )
    concrete = tags + "\n" + (
        "A portrait composition places the adult woman across the left third, with open space leading toward the exit. "
        "Her glossy jet-black hair is gathered into a rounded bun beneath a purple jade and gold hair ornament; crimson lips, rosy "
        "cheeks, luminous mature eyes, and a voluptuous figure with a narrow waist and rounded hips define her visible appearance. "
        "She wears a light purple gauze robe over a floral purple long skirt with a high slit and white silk stockings. "
        "A close medium three-quarter view keeps her face and reaching hand sharp. Cold side light traces the gauze, hair ornament, "
        "and silk stockings while the enclosure recedes through softened contrast, coherent anatomy, clean edges, and polished fidelity."
    )
    outputs = iter((vague, concrete))
    calls: list[str] = []

    def generate(_system, user):
        calls.append(user)
        return next(outputs)

    assert profiles.generate("krea2", scene, generate) == concrete  # concrete 已含 tags 首行
    assert len(calls) == 2
    assert "空泛" in calls[1]


@pytest.mark.parametrize("profile,raw", [
    (
        "krea2",
        "1woman, close medium portrait, broken wall opening, dawn directional light, "
        "fabric folds, softened background\n"
        "A close medium portrait places Alice beside a broken wall opening under directional dawn light, "
        "with stable perspective, clean fabric folds, coherent anatomy, restrained negative space, and a softened background.",
    ),
    (
        "anima_tags",
        "1girl, Alice, black hair, red lips, purple robe, turning, side light. "
        "Alice turns toward the broken opening while dawn light separates her from the softened background.",
    ),
    (
        "natural_language",
        "Alice turns toward a broken wall opening while dawn side light follows her black hair and purple robe.",
    ),
    (
        "niji_sections",
        '{"subject":"Alice turning toward a broken wall opening","style":"painted illustration",'
        '"additions":"dawn side light, restrained negative space","suffix":"--ar 3:4 --niji 6"}',
    ),
])
def test_所有profile最终文本移除角色姓名(profile, raw):
    prompt = profiles.normalize_inline(profile, raw, {"actors": ["Alice"], "rating": "sfw"})

    assert prompt
    assert "Alice" not in prompt
    assert "primary adult character" in prompt


def test_krea2当前剧情服装覆盖角色条目的基础穿着():
    scene = {
        **_scene("sfw"),
        "appearance": "角色：【外貌】黑发红唇；【穿着】素紫色薄纱法衣、碎花紫长裙。",
        "wardrobe": "红色长裙已经撕裂，布料沾有灰尘",
    }

    prompt = profiles.deterministic_fallback("krea2", scene)

    assert "red long dress" in prompt
    assert "torn clothing" in prompt
    assert "purple gauze robe" not in prompt
    assert "floral purple long skirt" not in prompt


def test_虞妙玥条目在本地兜底中生成具体外貌而非通用女人():
    scene = {
        **_scene("sfw"),
        "actors": ["虞妙玥"],
        "appearance": (
            "虞妙玥：【外貌】丰腴肥熟的美艳熟妇，一头华贵墨发，狭长暗红美眸，"
            "雪嫩脸蛋、熟厚双唇；【身材】丰腴肥熟的极致熟躯，宽厚丰臀，"
            "一身红莲纹饰华袍。"
        ),
        "narrative": "虞妙玥躺在机关天牢的冰冷石地上，偏过脸去。",
        "locale": "机关天牢",
    }

    prompt = profiles.deterministic_fallback("krea2", scene)
    ledger = profiles.prompt_field_ledger(prompt, scene)

    for fact in ("jet-black hair", "dark-red eyes", "full mature lips", "voluptuous", "red lotus patterned robe"):
        assert fact in prompt
    assert ledger["appearance"]["covered"] is True


def test_字段账本不把无可验证期望的通用外貌词判为覆盖():
    scene = {
        **_scene("sfw"),
        "appearance": "【外貌】无法由当前视觉词典识别的独特容貌。",
    }

    ledger = profiles.prompt_field_ledger(
        "An adult woman with hair and eyes stands in a composed medium shot.", scene,
    )

    assert ledger["appearance"] == {
        "required": True,
        "covered": False,
        "expected": [],
    }


@pytest.mark.parametrize("profile", profiles.PROFILE_IDS)
@pytest.mark.parametrize(("narrative", "expected"), [
    ("她仰卧在床上，双腿压上对方肩膀，面对面保持传教士体位。", "legs raised over the partner's shoulders"),
    ("她被抱离地面，以火车便当式悬空贴住对方。", "standing suspended carry position"),
    ("两名成年角色以69式上下交叠。", "mutual oral 69 position"),
])
def test_所有profile从高潮正文保留具体体位(profile, narrative, expected):
    scene = {
        **_scene("nsfw"),
        "narrative": narrative,
        "protected_narrative": narrative,
        "appearance": "【外貌】漆黑墨发，暗红美眸，丰腴熟躯。",
        "wardrobe": "红莲纹饰华袍已经撕开，仅剩残片挂在腰侧。",
        "locale": "机关天牢的冰冷石板上",
        "actors": ["虞妙玥"],
    }

    raw = profiles.deterministic_fallback(profile, scene)
    prompt, ledger = profiles.complete_field_coverage(profile, raw, scene)

    assert expected in prompt
    assert "torn remnants of a red lotus patterned robe" in prompt
    assert "confinement cell" in prompt
    assert ledger["action"]["covered"] is True
    assert ledger["wardrobe"]["covered"] is True
    assert ledger["location"]["covered"] is True


def _cold_qingxue_scene():
    return {
        **_scene("sfw"),
        "actors": ["冷倾雪"],
        "narrative": "冷倾雪在破屋中猛然转身，伸手推开通往山路的破门。",
        "draft_prompt": "turning sharply and pushing open the broken door toward a dawn mountain path",
        "wardrobe": "",
        "locale": "一座漏雨破屋，门外是黎明山路",
        "appearance": (
            "冷倾雪：【外貌】清秀动人却成熟色气，漆黑墨发扎成发团、插紫玉金髻，"
            "朱唇娇艳、脸颊红润，晶亮圆润的美目透着成熟干练；"
            "【身材】前凸后翘、曲线优美的丰腴熟躯，丰满胸部、纤腰、宽厚圆硕的臀部，"
            "蚕丝白袜包裹修长美腿；【穿着】素紫色薄纱法衣、碎花紫长裙、高叉。"
        ),
    }


def _other_profile_output(profile, *, concrete):
    if concrete:
        description = (
            "glossy jet-black hair, rounded hair bun, purple jade and gold hair ornament, crimson lips, "
            "rosy cheeks, luminous mature eyes, voluptuous curvy figure, full bust, narrow waist, broad rounded hips, "
            "long legs, white silk stockings, light purple gauze robe, floral purple long skirt, high side slit"
        )
    else:
        description = "stable appearance, established identity, current clothing condition"
    action = "turning, pushing, broken door, dawn side light, mountain path, foreground frame"
    sentence = (
        f"The primary adult character is {description} while turning sharply and pushing open the broken door; "
        "dawn side light and a foreground frame lead toward the mountain path."
    )
    if profile == "anima_tags":
        return _anima_json(
            f"1girl, adult woman, {description}, {action}. {sentence}",
            visual_hook="the broken door forms a foreground frame toward the dawn path",
            primary_focus="the woman's face and hand pushing the broken door",
            supporting_elements=["broken door", "dawn mountain path"],
        )
    if profile == "natural_language":
        return sentence
    return json.dumps({
        "subject": f"An adult woman with {description}, turning and pushing open a broken door",
        "style": "refined two-dimensional narrative illustration with controlled painted color",
        "additions": "dawn side light, foreground door frame, mountain path, restrained negative space",
        "suffix": "--ar 2:3 --niji 6",
    })


@pytest.mark.parametrize("profile", ["anima_tags", "natural_language", "niji_sections"])
def test_其他Profile也必须拒绝空泛身份锁并修复为具体角色外貌(profile):
    scene = _cold_qingxue_scene()
    outputs = iter((
        _other_profile_output(profile, concrete=False),
        _other_profile_output(profile, concrete=True),
    ))
    calls = []

    def generate(_system, user):
        calls.append(user)
        return next(outputs)

    prompt = profiles.generate(profile, scene, generate)

    assert len(calls) == 2
    for fact in (
        "jet-black hair", "hair bun", "purple jade", "crimson lips", "rosy cheeks",
        "mature eyes", "voluptuous", "full bust", "narrow waist", "rounded hips",
        "long legs", "white silk stockings", "purple gauze robe", "floral purple long skirt",
        "high side slit", "turning", "pushing", "broken door", "mountain path",
    ):
        assert fact in prompt
    assert "冷倾雪" not in prompt
    assert "established identity" not in prompt
    assert "current clothing condition" not in prompt


@pytest.mark.parametrize("profile", ["anima_tags", "natural_language", "niji_sections"])
def test_其他Profile拒答兜底仍保留具体外貌与高潮事实(profile):
    prompt = profiles.generate(
        profile, _cold_qingxue_scene(),
        lambda _system, _user: "I can't help with this request.",
    )

    for fact in (
        "jet-black hair", "purple jade", "crimson lips", "rosy cheeks", "voluptuous",
        "narrow waist", "rounded hips", "white silk stockings", "purple gauze robe",
        "floral purple long skirt", "turning", "pushing", "broken door", "mountain path",
    ):
        assert fact in prompt
    assert "冷倾雪" not in prompt
    assert "identified character" not in prompt
    assert "established identity" not in prompt
    assert profiles._errors(profile, prompt, _cold_qingxue_scene()) == []


def test_anima兜底不得用固定时段色板覆盖当前黎明场景():
    prompt = profiles.generate(
        "anima_tags", _cold_qingxue_scene(),
        lambda _system, _user: "I can't help with this request.",
    )

    assert "dawn" in prompt and "mountain path" in prompt
    assert "late afternoon" not in prompt
    assert "warm ochre" not in prompt
    assert "dust backlighting" not in prompt


@pytest.mark.parametrize("profile", ["anima_tags", "natural_language", "niji_sections"])
def test_其他Profile当前服装也覆盖角色条目基础穿着(profile):
    scene = {
        **_cold_qingxue_scene(),
        "wardrobe": "红色长裙已经撕裂，布料沾有灰尘",
    }

    prompt = profiles.deterministic_fallback(profile, scene)

    assert "red long dress" in prompt
    assert "torn clothing" in prompt
    assert "purple gauze robe" not in prompt
    assert "floral purple long skirt" not in prompt


@pytest.mark.parametrize("profile", ["anima_tags", "natural_language", "niji_sections"])
def test_Profile兜底不得混入已不在当前actors中的旧主体描述(profile):
    scene = {
        **_cold_qingxue_scene(),
        "subjects": [
            {"name": "冷倾雪", "description": "adult woman with black hair and a purple robe"},
            {"name": "我", "description": "adult man walking away along the mountain path"},
        ],
    }

    prompt = profiles.deterministic_fallback(profile, scene)

    assert "adult woman with black hair and a purple robe" in prompt
    assert "adult man walking away" not in prompt


def test_krea2拒答兜底仍保留高潮动作与角色条目英文外貌():
    scene = {
        **_scene("sfw"),
        "actors": ["冷倾雪"],
        "draft_prompt": "turning sharply beside the broken wooden rail",
        "subjects": [{
            "name": "冷倾雪",
            "description": "a black-haired swordswoman with her established narrow eyes and facial structure",
        }],
    }

    prompt = profiles.generate(
        "krea2", scene, lambda _system, _user: "I can't help with this request.",
    )

    assert "turning sharply beside the broken wooden rail" in prompt
    assert "black-haired swordswoman" in prompt
    assert "established narrow eyes and facial structure" in prompt


def test_krea2兜底从混合语言appearance保留具体英文身份特征():
    scene = {
        **_scene("nsfw"),
        "appearance": (
            "冷倾雪: long black hair, straight bangs, narrow red eyes, pale skin, "
            "slender figure, red dress"
        ),
        "draft_prompt": "wrist held, pulling closer, kissing beside a rain-soaked railing",
    }

    prompt = profiles.generate(
        "krea2", scene, lambda _system, _user: "I can't help with this request.",
    )

    assert "long black hair" in prompt
    assert "straight bangs" in prompt
    assert "narrow red eyes" in prompt
    assert "wrist held" in prompt


def test_krea2拒绝中文混入最终英文提示词():
    invalid = "A medium shot uses golden-hour light while 冷倾雪 stands beside the wooden rail."
    assert profiles.normalize_inline("krea2", invalid, {"rating": "nsfw"}) == ""


def test_krea2接受完整英文自然语言且不要求成年分级词():
    tags = (
        "1woman, torn purple gauze robe, low medium shot, shallow depth of field, "
        "cool dawn backlight, wooden rail foreground"
    )
    valid = (
        "A low medium shot uses shallow depth of field and cool dawn backlight. "
        "The identified woman wears a torn purple gauze robe and rests one arm on the wooden rail. "
        "The rail forms a foreground frame while the abandoned village dissolves into morning mist."
    )
    assert profiles.normalize_inline(
        "krea2", tags + "\n" + valid, {"rating": "nsfw"},
    ) == tags + "\n" + valid


def test_krea2系统按六维顺序转译高潮并把写实改为画质质量():
    system = profiles._system("krea2", {"rating": "sfw"})

    dimensions = (
        "第一，构图与留白占比",
        "第二，角色外貌与服装",
        "第三，摄影风格、镜头视角与透视表现",
        "第四，有机材质与画面质感",
        "第五，光影、层次与色彩设定",
        "第六，画质质量与完成度",
    )
    positions = [system.index(dimension) for dimension in dimensions]
    assert positions == sorted(positions)
    assert "写实画质" not in system
    assert "禁止照片写实向描述" in system  # 媒介交给 LoRA，禁真人摄影词汇（2026-08-30 实测真人化倾向）
    assert "skin texture" in system  # 禁词清单点名
    assert "细节克制" in system
    assert "SFW" not in system and "NSFW" not in system
    assert "角色设定图" not in system and "超巨型" not in system


@pytest.mark.parametrize("profile", ["anima_tags", "natural_language", "niji_sections"])
def test_其他模式复用事实底座与艺术决策而非逐项填满(profile):
    system = profiles._system(profile, {"rating": "sfw"})

    for detail in (
        "在场人物", "世界书稳定外貌", "当前外观变化", "服装状态", "实际动作", "地点",
        "唯一视觉命题", "主体层级", "色彩与材质母题", "光影因果", "镜头", "景深", "构图",
    ):
        assert detail in system
    assert "无关项简写或省略" in system
    assert "开放视觉槽" in system


@pytest.mark.parametrize("profile", profiles.PROFILE_IDS)
def test_所有模式先做统一艺术决策再格式化(profile):
    system = profiles._system(profile, {"rating": "nsfw"})

    for decision in ("唯一视觉命题", "主体层级", "色彩与材质母题", "光影因果", "第一视觉中心"):
        assert decision in system
    assert "禁止全画面等密度" in system
    assert "不得因艺术化而改写事实" in system
    assert "人物互动高潮" in system
    assert "发现、开启、争夺或取得物件" in system
    assert "色彩材质母题、光影因果、镜头构图" in system


@pytest.mark.parametrize("profile", profiles.PROFILE_IDS)
def test_四种profile区分硬事实锁与允许联想的开放视觉槽(profile):
    system = profiles._system(profile, {"rating": "sfw"})

    assert "硬事实锁" in system
    assert "开放视觉槽" in system
    assert "微动作" in system
    assert "时间、地点、天气、情绪" in system
    assert "重要新人物" in system
    assert "关键道具" in system
    assert "新事件" in system


@pytest.mark.parametrize("profile", profiles.PROFILE_IDS)
def test_四种profile把缺失画面硬事实的具体补全设为必执行(profile):
    system = profiles._system(profile, {"rating": "sfw"})

    assert "缺失硬事实补全" in system
    assert "必须补出一个具体答案" in system
    assert "不得留空" in system
    assert "不得使用占位语" in system
    for field in ("当前服装", "具体动作或姿态", "地点环境", "镜头", "构图", "光影"):
        assert field in system


def test_krea2不再禁止为空缺视觉槽合理补全():
    system = profiles._system("krea2", {"rating": "sfw"})

    assert "不得加入输入中没有的画面事实" not in system
    assert "允许为开放视觉槽补足" in system


def test_anima同轮成稿格式错误时从完整动作窗口紧凑重建而非拼接整份草稿():
    scene = {
        "narrative": (
            "她仰卧在冰冷石板上，双手被锁链束缚。他把她的膝盖慢慢分开，"
            "取出玉制器具放在一旁，用指腹在接触点反复摩擦；每当她接近高潮就撤开。"
            "她的腰追着撤开的手往上顶，呼吸急促，最后哑声开口。"
        ),
        "draft_prompt": (
            "The moment after she speaks is the visual thesis; Primary: her face; Secondary: damp fabric; "
            "single low torch source; (voluptuous mature woman lying on stone:1.8), "
            "post-confession moment, averted gaze"
        ),
        "appearance": (
            "虞妙玥：【外貌】华贵墨发，狭长暗红美眸，熟厚双唇，丰腴肥熟的极致熟躯；"
            "【穿着】红莲纹饰华袍。"
        ),
        "wardrobe": "囚衣已经湿透",
        "locale": "机关天牢内层牢房",
        "actors": ["虞妙玥"],
        "subjects": [{
            "name": "虞妙玥",
            "description": (
                "voluptuous mature woman with long black hair, narrow dark-red eyes, "
                "a damp pale prisoner's robe, lying supine on cold stone"
            ),
        }],
        "rating": "nsfw",
        "aspect_ratio": "2:3",
    }

    prompt = profiles.deterministic_fallback("anima_tags", scene)
    prompt = profiles.normalize_inline("anima_tags", prompt, scene)
    ledger = profiles.prompt_field_ledger(prompt, scene)

    assert len(prompt.splitlines()) == 2
    assert ledger["action"]["required"] is True
    assert ledger["action"]["covered"] is True
    assert "rubbing the contact point" in prompt
    assert "withdrawing just before completion" in prompt
    assert "her body following the withdrawn contact" in prompt
    tags, prose = prompt.splitlines()
    assert "rubbing the contact point" in tags
    assert "rubbing the contact point" in prose
    assert "visible action remains" not in prose.lower()
    assert "stated action" not in prose.lower()
    assert "current clothing" not in prose.lower()
    assert "one continuous moment" not in prose.lower()
    assert "visual thesis" not in prompt.lower()
    assert "Primary:" not in prompt
    assert ":1.8" not in prompt
    assert len(prompt) < 2500


@pytest.mark.parametrize("profile", profiles.PROFILE_IDS)
def test_双角色Profile分别编译两人的外貌动作再描述整体关系(profile):
    scene = {
        "narrative": "冷倾雪抬头看向门外。虞妙玥跪在石阶前。",
        "draft_prompt": "two-shot, stone chamber, layered background, directional light",
        "appearance": (
            "冷倾雪：【外貌】漆黑墨发扎成发团，插紫玉金髻，朱唇娇艳\n"
            "虞妙玥：【外貌】乌黑长发，脸颊红润；【穿着】红色长裙"
        ),
        "wardrobe": "",
        "locale": "stone chamber",
        "actors": ["冷倾雪", "虞妙玥"],
        "subjects": [
            {"name": "冷倾雪", "description": "black-haired adult woman raising her head"},
            {"name": "虞妙玥", "description": "long-haired adult woman kneeling on stone steps"},
        ],
        "rating": "sfw",
        "aspect_ratio": "4:3",
    }

    raw = profiles.deterministic_fallback(profile, scene)
    prompt = profiles.normalize_inline(profile, raw, scene)

    assert prompt
    assert "primary adult character" in prompt.lower()
    assert "second adult character" in prompt.lower()
    assert "冷倾雪" not in prompt and "虞妙玥" not in prompt
    assert prompt.lower().index("primary adult character") < prompt.lower().index(
        "second adult character",
    )
    if profile == "anima_tags":
        assert len(prompt.splitlines()) == 2
        assert "2girls" in prompt.splitlines()[0]
    if profile == "krea2":
        assert "masterpiece" not in prompt.lower()
        assert "\n\n" not in prompt


def _unknown_object_scene():
    evidence = "她把玄枢按上门锁，青铜圆盘沿放射槽旋转，铁木门随即开启"
    return {
        "narrative": evidence + "。",
        "appearance": "voluptuous mature beauty, glossy ink-black hair, narrow dark-red eyes",
        "wardrobe": "torn pale prisoner's robe with red lotus embroidery",
        "locale": "confinement cell and corridor",
        "camera": "medium shot",
        "visual_facts": [{
            "kind": "prop",
            "fact": "a palm-sized bronze disk with radial slots, rotating against the ironwood door lock as a mechanical key",
            "evidence": evidence,
        }],
        "actors": ["角色甲"],
        "rating": "nsfw",
    }


def test_krea2把角色动作未知道具地点作为主体内容而非只剩质量套话():
    scene = _unknown_object_scene()

    prompt = profiles.deterministic_fallback("krea2", scene)

    lowered = prompt.lower()
    for fact in ("glossy ink-black hair", "narrow dark-red eyes", "torn pale prisoner's robe",
                 "palm-sized bronze disk", "radial slots", "mechanical key"):
        assert fact in lowered
    assert "visible subjects and scene are concretely defined" in lowered


def test_anima剔除通用艺术词不得把相邻tags粘连():
    r"""2026-09-20 真机实锤（L3·anima 成稿）：剔除 `cinematic lighting` 的正则
    `(?:^|,\s*)(...)(?=,|\.|$)\s*,?\s*` 同时吃掉了**前后两个**分隔符，
    把 `volumetric steam, cinematic lighting, depth of field` 粘成
    `volumetric steamdepth of field`。"""
    scene = {"rating": "sfw", "actors": ["角色甲"]}
    raw = _anima_json(
        "solo, 1girl, volumetric steam, cinematic lighting, depth of field. She smiles."
    )

    tags = profiles._normalize("anima_tags", raw, scene).splitlines()[0]

    assert "volumetric steam" in tags
    assert "depth of field" in tags
    assert "steamdepth" not in tags
    assert "cinematic lighting" not in tags.lower()


def test_anima画幅比例不得重复aspect_ratio():
    """模型已写 `3:4 aspect ratio` 时，`数字:数字` → `N by M aspect ratio` 的替换会追加成
    `3 by 4 aspect ratio aspect ratio`（L2/L3·anima 成稿实锤），且替换后紧跟的前一个 tag
    会被吃掉分隔符。"""
    scene = {"rating": "sfw", "actors": ["角色甲"]}
    raw = _anima_json(
        "solo, 1girl, negative space, cinematic lighting, 3:4 aspect ratio. She smiles."
    )

    tags = profiles._normalize("anima_tags", raw, scene).splitlines()[0]

    assert tags.lower().count("aspect ratio") == 1
    assert "3 by 4 aspect ratio" in tags.lower()
    assert "negative space" in tags


def test_anima严格两行且内容行先写具体事实只用逗号分隔tags():
    scene = _unknown_object_scene()

    prompt = profiles.deterministic_fallback("anima_tags", scene)
    tags, prose = prompt.splitlines()

    assert tags.startswith(profiles.anima_quality_tags("nsfw") + ", voluptuous mature beauty")
    assert ";" not in tags
    assert tags.endswith(",")
    assert "palm-sized bronze disk with radial slots" in tags
    assert prose
    for generic in ("dramatic scene", "climactic moment", "dynamic composition",
                    "action pose", "cinematic lighting"):
        assert generic not in tags.lower()


def test_anima把第二行标签式小段归一为连续英文句子且禁止冒号():
    scene = {"rating": "nsfw", "actors": ["角色甲"]}
    raw = (
        profiles.anima_quality_tags("nsfw")
        + ", adult woman, glossy jet-black hair, confinement cell,\n"
        "Her body: lush mature figure, large exposed breasts heaving with labored breath. "
        "Bound: iron chains slack at her wrists above her head. "
        "Position: collapsed on the dungeon floor, limbs slack, head tilted back."
    )

    prompt = profiles.normalize_inline("anima_tags", raw, scene)
    tags, prose = prompt.splitlines()

    assert tags.endswith(",")
    assert ":" not in tags
    assert ":" not in prose
    assert "Her body is shown with lush mature figure" in prose
    assert "She is bound with iron chains slack at her wrists above her head" in prose
    assert "She is positioned collapsed on the dungeon floor" in prose


# ── 2026-09-19：anima JSON 分支的 tags/prose 切分口径（真机 L1/L3 三连判死根因）─────
# `_normalize` 的 JSON 分支原先把 content 的换行**先压平**再 `partition(".")`；模型若用换行
# （而非句点）分隔 tags 与 prose，整段会被塞进 tag_head、prose 落空 ⇒ 输出退化 1 行 ⇒
# 「必须正好两行」连带另外两条三连判死。真源：`docs/illustration-prompt-delivery-2026-09-19.md` §2.6。

_ANIMA_SPLIT_TAGS = (
    "1girl, long black hair, amber eyes, white shirt, black pencil skirt, "
    "legs together, medium shot, dramatic side light, notice board background"
)
_ANIMA_SPLIT_PROSE = "She leans back against the notice board with her legs trembling."


def test_animaJSON内容含换行时按首行tags其余并入prose():
    content = f"{_ANIMA_SPLIT_TAGS}\n{_ANIMA_SPLIT_PROSE}"
    scene = {"rating": "nsfw", "actors": ["宥夏"]}

    prompt = profiles._normalize("anima_tags", _anima_json(content), scene)
    tags, prose = prompt.splitlines()

    assert tags.endswith(",")
    assert "leans back" not in tags
    assert prose == _ANIMA_SPLIT_PROSE
    assert profiles._errors("anima_tags", prompt, scene) == []
    assert profiles.normalize_inline("anima_tags", _anima_json(content), scene)


def test_animaJSON内容换行且prose多句时整段prose不被首句句点截断():
    content = (
        f"{_ANIMA_SPLIT_TAGS}\n"
        "She leans back against the notice board. "
        "A thin strand of semen drips down her inner thigh."
    )
    scene = {"rating": "nsfw", "actors": ["宥夏"]}

    tags, prose = profiles._normalize("anima_tags", _anima_json(content), scene).splitlines()

    assert "leans back" not in tags
    assert prose == (
        "She leans back against the notice board. "
        "A thin strand of semen drips down her inner thigh."
    )


def test_animaJSON内容单行时仍按首个句点切分tags与prose():
    content = f"{_ANIMA_SPLIT_TAGS}. {_ANIMA_SPLIT_PROSE}"
    scene = {"rating": "nsfw", "actors": ["宥夏"]}

    tags, prose = profiles._normalize("anima_tags", _anima_json(content), scene).splitlines()

    assert "leans back" not in tags
    assert prose == _ANIMA_SPLIT_PROSE


def test_animaJSON换行内容与非JSON质量前缀形态切分口径一致():
    """两条分支必须同口径（首行 tags、其余并入 prose）—— 原缺陷正是两处不一致。"""
    plain = (
        f"{profiles.anima_quality_tags('nsfw')}, {_ANIMA_SPLIT_TAGS}\n{_ANIMA_SPLIT_PROSE}"
    )
    scene = {"rating": "nsfw", "actors": ["宥夏"]}

    from_json = profiles._normalize(
        "anima_tags", _anima_json(f"{_ANIMA_SPLIT_TAGS}\n{_ANIMA_SPLIT_PROSE}"), scene,
    )
    from_plain = profiles._normalize("anima_tags", plain, scene)

    for prompt in (from_json, from_plain):
        tags, prose = prompt.splitlines()
        assert "leans back" not in tags
        assert "leans back" in prose


_ANIMA_NOTE = (
    "The scene captures the delicate aftermath of a raw encounter, focusing on the "
    "glistening trail of semen on her inner thigh while her face lingers in the "
    "soft-focus background."
)


def test_animaJSON把prose写进非规格字段时从该字段取回第二行():
    """真机 2026-09-19 实锤：模型把 tags 写进 `content`、画面句写进**非规格字段** `content_note`
    （`content` 里既无换行也无句点）⇒ prose 落空 ⇒ 输出退化 1 行 ⇒ 三连判死。"""
    raw = json.dumps({
        "visual_hook": "a low-angle close-up of her hips against a scratched metal board",
        "primary_focus": "the glistening trail of semen on her inner thigh",
        "supporting_elements": ["her trembling legs"],
        "content": _ANIMA_SPLIT_TAGS,
        "content_note": _ANIMA_NOTE,
    })
    scene = {"rating": "nsfw", "actors": ["宥夏"]}

    prompt = profiles._normalize("anima_tags", raw, scene)
    tags, prose = prompt.splitlines()

    assert tags.endswith(",")
    assert "aftermath" not in tags
    assert "glistening trail of semen" in prose
    assert profiles._errors("anima_tags", prompt, scene) == []
    assert profiles.normalize_inline("anima_tags", raw, scene)


def test_animaJSONcontent自带prose时不得采用非规格字段():
    """零回归：`content` 里已有画面句 ⇒ 额外字段一律忽略，不得顶掉 `content` 的 prose。"""
    raw = json.dumps({
        "visual_hook": "v", "primary_focus": "p", "supporting_elements": [],
        "content": f"{_ANIMA_SPLIT_TAGS}. {_ANIMA_SPLIT_PROSE}",
        "content_note": "An unrelated leftover note with one very long sentence inside it.",
    })
    scene = {"rating": "nsfw", "actors": ["宥夏"]}

    _tags, prose = profiles._normalize("anima_tags", raw, scene).splitlines()

    assert prose == _ANIMA_SPLIT_PROSE


def test_animaJSONcontent只有tags且无额外句子字段时仍判死():
    """边界锁：修复只覆盖「别处真有英文句」这一形态，纯 tags 输出必须照旧判死（不许放宽成通过）。"""
    raw = json.dumps({
        "visual_hook": "v", "primary_focus": "p", "supporting_elements": [],
        "content": _ANIMA_SPLIT_TAGS,
    })
    scene = {"rating": "nsfw", "actors": ["宥夏"]}

    prompt = profiles._normalize("anima_tags", raw, scene)

    assert len(prompt.splitlines()) == 1
    assert "必须正好两行" in profiles._errors("anima_tags", prompt, scene)


def test_animaJSON附带说明字段过短且无逗号时不得当成画面句():
    """准入判据必须排除「顺便附带的说明」——否则会拿说明冒充画面句蒙混通过。"""
    raw = json.dumps({
        "visual_hook": "v", "primary_focus": "p", "supporting_elements": [],
        "content": _ANIMA_SPLIT_TAGS,
        "comment": "Here is the JSON you requested.",
    })
    scene = {"rating": "nsfw", "actors": ["宥夏"]}

    prompt = profiles._normalize("anima_tags", raw, scene)

    assert len(prompt.splitlines()) == 1
    assert "Here is the JSON" not in prompt
    assert "必须正好两行" in profiles._errors("anima_tags", prompt, scene)


def test_anima修复Agent环境tags加分号人物文段且把具体动作复制到首行():
    raw = (
        "2girls, ancient stone chamber, low amber side light, cold air atmosphere\n"
        "primary adult character: silver-haired swordswoman lying on stone, both wrists bound, "
        "one hand gripping a broken sword; second adult character: dark-robed opponent kneeling; "
        "the visual center: her hand pulling the sword free"
    )
    scene = {
        "narrative": "她躺在石地上，双腕被缚，伸手拔出断剑。",
        "actors": ["甲", "乙"],
        "visual_facts": [{
            "kind": "action",
            "fact": "her hand pulling the broken sword free",
            "evidence": "伸手拔出断剑",
        }],
        "rating": "sfw",
    }

    prompt = profiles.normalize_inline("anima_tags", raw, scene)
    tags, prose = prompt.splitlines()

    assert "silver-haired swordswoman lying on stone" in tags
    assert "her hand pulling the sword free" in tags
    assert "The primary adult character is" in prose
    assert ";" not in tags


def test_anima同义外貌不丢弃Agent动作成稿而由字段账本精确补齐():
    scene = {
        "narrative": "她躺在石地上，双腕被锁链束缚，伸手拔出断剑。",
        "appearance": "narrow dark-red eyes, full mature lips",
        "wardrobe": "a pale prisoner's robe",
        "actors": ["甲"],
        "rating": "sfw",
    }
    raw = (
        "stone chamber, narrow dark-crimson eyes, full lips, torn prisoner robe, lying down, "
        "both wrists bound, pulling a broken sword free\n"
        "The adult woman lies on stone with both wrists bound while pulling a broken sword free."
    )

    normalized = profiles.normalize_inline("anima_tags", raw, scene)
    completed, ledger = profiles.complete_field_coverage("anima_tags", normalized, scene)

    assert "pulling a broken sword free" in completed
    assert "narrow dark-red eyes" in completed.splitlines()[0]
    assert "narrow dark-red eyes" in completed.splitlines()[1]
    assert ledger["appearance"]["covered"] is True


@pytest.mark.parametrize("profile", profiles.PROFILE_IDS)
def test_四种profile都保留未知专名物件的材质形态运动功能释义(profile):
    prompt = profiles.deterministic_fallback(profile, _unknown_object_scene()).lower()

    for component in ("bronze disk", "radial slots", "rotating", "mechanical key"):
        assert component in prompt


def test_未知profile拒绝():
    with pytest.raises(ValueError, match="未知提示词模式"):
        profiles.generate("unknown", _scene(), lambda _s, _u: "x")


_FRAME_OK = _anima_json(
    "1girl, reaching out, gripping wrist, pulling another person, doorway, diagonal composition. "
    "Her extended arm and locked grip form the sharp diagonal that pulls both figures toward the door.",
)


def _frame_scene(rating="nsfw"):
    """wardrobe 清空（对齐 342 行先例）：帧成品不含服饰 tags，避免服饰事实校验干扰。"""
    return {**_scene(rating), "wardrobe": ""}


def test_首尾帧同构编译_提取成功后走同一编译器():
    """时点提取成功 → 两帧各自走同一编译器，帧 scene 携带提取事实与防拦截标记。"""
    calls: list[tuple[str, str]] = []

    def generate(system: str, user: str) -> str:
        calls.append((system, user))
        if "首尾帧画面事实提取器" in system:
            return json.dumps({
                "first": {
                    "action": "rising from the cold stone floor",
                    "visual_facts": ["one hand braced against the wall", "chains slack at her wrists"],
                },
                "last": {
                    "action": "kneeling with head bowed",
                    "visual_facts": ["torn garment pooled around her knees"],
                },
            })
        return _FRAME_OK

    diagnostics: dict = {}
    results = profiles.generate_frame_prompts(
        "anima_tags", _frame_scene("nsfw"),
        {"first": "她从冰冷的石地上撑起身。", "last": "她跪下低头，衣衫散落膝边。"},
        generate, diagnostics=diagnostics,
    )

    assert set(results) == {"first", "last"}
    assert diagnostics["frame_extract_ok"] is True
    assert len(calls) == 3  # 1 次提取 + 2 次编译
    assert all("首尾帧画面事实提取器" not in system for system, _ in calls[1:])
    for frame, (system, user) in zip(("first", "last"), calls[1:]):
        payload = json.loads(user)
        # 模型输入侧：protected_narrative 转移为带防拦截标记的 narrative（_scene_for_model 语义）
        assert payload["narrative"].startswith("@(")
        assert payload["action"]
        assert payload["visual_facts"]
        assert frame in ("first", "last")
    assert results["first"]["prompt"]
    assert results["last"]["prompt"]
    assert results["first"]["negative_prompt"]


def test_首尾帧提取失败重试一次后走narrative降级():
    """提取两次都不可解析 → 不携带提取事实仍编译出成品（narrative 降级），不抛错。"""
    calls: list[str] = []

    def generate(system: str, _user: str) -> str:
        calls.append(system)
        if "首尾帧画面事实提取器" in system:
            return "not json at all"
        return _FRAME_OK

    diagnostics: dict = {}
    results = profiles.generate_frame_prompts(
        "anima_tags", _frame_scene(),
        {"last": "她跪下低头。"},
        generate, diagnostics=diagnostics,
    )

    assert diagnostics["frame_extract_ok"] is False
    assert len([s for s in calls if "首尾帧画面事实提取器" in s]) == 2  # 带因重试一次
    assert set(results) == {"last"}
    assert results["last"]["prompt"]


def test_首尾帧单帧只编译该帧():
    results = profiles.generate_frame_prompts(
        "anima_tags", _frame_scene(), {"last": "她跪下低头。"},
        lambda system, _user: _FRAME_OK if "首尾帧画面事实提取器" not in system else "{}",
    )
    assert set(results) == {"last"}


def test_编译输出剥离think段():
    """渲染模型 CoT 混进产物必须剥离（2026-08-29 新仓库 trace：整段 think 提交 ComfyUI）。"""
    result = profiles.generate(
        "anima_tags", _frame_scene("nsfw"),
        lambda _system, _user: "<think> The user wants me to write a prompt. </think>\n" + _FRAME_OK,
    )
    assert result
    assert "<think>" not in result
    assert "The user wants" not in result


def test_帧提取解析容忍think与代码围栏():
    """提取输出带 think/围栏仍可解析——否则两次重试全废、帧编译失去提取事实。"""
    raw = (
        "<think> let me structure the scene. </think>\n```json\n"
        + json.dumps({
            "first": {"action": "rising from the floor", "visual_facts": ["chains slack"]},
        })
        + "\n```"
    )
    parsed = profiles._parse_frame_extract(raw)
    assert parsed is not None
    assert parsed["first"]["action"] == "rising from the floor"


def test_首尾帧提取输出带think仍提取成功():
    def generate(system: str, _user: str) -> str:
        if "首尾帧画面事实提取器" in system:
            return "<think> reasoning about the frame </think>\n" + json.dumps({
                "first": {"action": "rising", "visual_facts": ["chains slack"]},
                "last": {"action": "kneeling", "visual_facts": []},
            })
        return _FRAME_OK

    diagnostics: dict = {}
    results = profiles.generate_frame_prompts(
        "anima_tags", _frame_scene("nsfw"),
        {"first": "她撑起身。", "last": "她跪下。"}, generate, diagnostics=diagnostics,
    )
    assert diagnostics["frame_extract_ok"] is True
    assert set(results) == {"first", "last"}


def test_anima重写后仅余焦点违例保留模型content而非词表通用动作():
    """A1（2026-09-04 事故回归）：repair 后只剩 primary_focus 中间契约违例时，LLM 的
    content 主体必须保留（真实高潮动作），不得整稿作废退 deterministic 词表
    （removing the visible object 通用动作事故）。"""
    content = (
        "1girl, black hair, full bust, narrow waist, flushed face, parted lips, "
        "glistening entrance, close-up, dramatic side light. "
        "The primary adult woman kneels on the stone with her spine arched, the visible "
        "intercourse rendered in close-up while the cavern pool recedes behind her."
    )
    # focus 复合且超长（NSFW 高潮典型写法）；content 本身合格 → 应保留 content
    long_focus = (
        "the flushed face and parted lips of the kneeling woman receiving the decisive "
        "thrust at the moment of climax, with her narrow waist arched and thighs trembling "
        "as the pool water laps around her knees"
    ) * 2
    outputs = iter([
        _anima_json(content, primary_focus=long_focus),  # 首版：content 好，focus 复合超长
        _anima_json(content, primary_focus=long_focus),  # repair 仍不改 focus
    ])
    calls = []

    def generate(_system, _user):
        calls.append(1)
        return next(outputs)

    diagnostics: dict = {}
    prompt = profiles.generate(
        "anima_tags", {**_scene("nsfw"), "wardrobe": ""}, generate, diagnostics,
    )

    assert len(calls) == 2
    assert diagnostics["strategy"] == "kept_content"
    assert "glistening entrance" in prompt             # 模型 content 保住
    assert "cavern pool recedes" in prompt
    assert "In this instant, the concrete action is" not in prompt  # 非词表句式
    assert "removing the visible object" not in prompt
    assert any("primary_focus" in item for item in diagnostics.get("repair_errors") or [])


def test_anima复合焦点描述不再按160字判死触发重写():
    """A2（2026-09-04）：primary_focus 复合描述超旧判据 160 字不再触发 repair——
    中间思考字段宽容化，content 合格时首轮直接通过（省一次画像 LLM 调用）。"""
    content = (
        "1girl, crimson eye, black hair, parted lips, close-up, dramatic side light, "
        "shallow depth of field. The single close-up keeps her flushed expression as the "
        "visual anchor while everything else dissolves into soft shadow."
    )
    medium_focus = (
        "her flushed expression at the decisive instant as the single readable center, "
        "with narrowed eyes and parted lips catching the side light while the hairline "
        "and collarbone fall out of focus to preserve one composition"
    )
    assert 160 < len(medium_focus) < 300  # 超过旧判据但未达新失控上限
    calls = []

    def generate(_system, _user):
        calls.append(1)
        return _anima_json(content, primary_focus=medium_focus)

    diagnostics: dict = {}
    prompt = profiles.generate(
        "anima_tags", {**_scene(), "wardrobe": ""}, generate, diagnostics,
    )

    assert len(calls) == 1                # 不再触发 repair
    assert diagnostics["strategy"] == "direct"
    assert "flushed expression" in prompt


def test_anima重写后content仍不合格继续词表兜底():
    """A1 兜底不失效：content 本身不合格（tags 不足/无英文文段）时仍走 deterministic
    词表，不把烂 content 当宝放行。"""
    bad_content = "1girl, close-up"  # tags<6、无英文文段
    outputs = iter([
        _anima_json(bad_content, primary_focus="a single focus phrase kept short"),
        _anima_json(bad_content, primary_focus="a single focus phrase kept short"),
    ])

    def generate(_system, _user):
        return next(outputs)

    diagnostics: dict = {}
    prompt = profiles.generate(
        "anima_tags", {**_scene(), "wardrobe": ""}, generate, diagnostics,
    )

    assert diagnostics["strategy"] == "fallback"
    assert len(prompt.splitlines()) == 2
    assert prompt.strip()


def test_诊断字段区分上游空返回与模型拒答():
    """插桩回归（2026-09-18）：generate 必须把「原始返回长度 / 剥离后是否为空 /
    是否命中拒答句式」写进 diagnostics，供 trace 判定 NSFW 拦截发生在哪一层。

    - 上游返回空串 → raw_chars 0、empty True、refusal False（静默拦空）；
    - 模型返回拒答句 → raw_chars >0、empty True、refusal True（明确拒答，清洗剥空）。
    """
    empty_diagnostics: dict = {}
    profiles.generate(
        "krea2", {**_scene(), "wardrobe": ""}, lambda _s, _u: "", empty_diagnostics,
    )
    assert empty_diagnostics["first_raw_chars"] == 0
    assert empty_diagnostics["first_empty"] is True
    assert empty_diagnostics["first_refusal"] is False

    refusal_diagnostics: dict = {}
    profiles.generate(
        "krea2", {**_scene(), "wardrobe": ""},
        lambda _s, _u: "抱歉，我无法协助完成这个请求。", refusal_diagnostics,
    )
    assert refusal_diagnostics["first_raw_chars"] > 0
    assert refusal_diagnostics["first_empty"] is True
    assert refusal_diagnostics["first_refusal"] is True


def test_动作提取逐段判定不因中文叙事丢弃英文草稿动作():
    """贴合剧情回归（2026-09-18）：scene 的 narrative 是中文正文，draft_prompt 是英文
    动作底座（同轮 <illustration> 或 build_fallback_content_tags 产出）。旧实现用整段
    source 判 isascii，中文叙事一在场就把 _ANIMA_ACTION_RE 分支整体跳过——同一条英文
    动作事实在「无叙事」时被采纳、「有中文叙事」时被丢弃；连带 PromptFieldLedger 的
    action 塌成 required=False，动作字段退出验收（模型写不写动作都不判错）。
    修法：按字段分别判定，英文动作底座逐段提取。
    """
    en_actions = "kneeling, reaching out, packing a medicine bottle"
    mixed = profiles._scene_action_tags({
        "narrative": "她蹲下身子，把药瓶塞进包袱里。",
        "draft_prompt": en_actions,
    })
    assert "kneeling" in mixed
    assert "reaching" in mixed
    # 「无叙事」时的结果是不变量基准：同一份英文底座两处必须一致
    assert mixed == profiles._scene_action_tags({"narrative": "", "draft_prompt": en_actions})


def test_动作提取保持中文词表命中且不误伤纯中文场景():
    """护航：修 ASCII 判定不得改变纯中文路径行为。"""
    assert profiles._scene_action_tags({"narrative": "她伸手拂过他的脸颊。"}) == ["reaching out"]
    assert profiles._scene_action_tags({"narrative": "她蹲下身子，把药瓶塞进包袱里。"}) == []


# ---------------------------------------------------------------------------
# 多人契约按「画面主体数」判（2026-09-19 优化项③）
#
# 用户口径：一律按**画面主体数**判，不按角色表人数、也不按在场人数。
# 契约原先只看 `scene["actors"]`，而 actors 由 `_resolve_illustration_request_actors`
# 从**取材窗口**推出 ⇒ 窗口里被提及的任何人都会算成画面在场 ⇒ 单人定格帧被误判成多人帧，
# 被契约判死（多烧一次 repair 或整稿退词表兜底）。
# 正解：契约看「本帧主体名集合」——优先同轮计划 `subjects`，其次判据档给出的
# `frame_subjects`；**两者都缺 ⇒ 视为无正向证据，保持旧行为（退回 actors）**，这是零回归前提。
# ---------------------------------------------------------------------------

_单人两行文案 = (
    "masterpiece, best quality, 1girl, solo, aqua eyes, silver hair, white robe, "
    "standing, soft daylight, shallow depth\n"
    "The adult woman stands in the corridor with silver hair and aqua eyes, calm expression."
)


def _契约场景(*, actors, frame_subjects=None, subjects=None):
    scene = {"narrative": "她站在走廊里。", "actors": list(actors), "rating": "nsfw"}
    if frame_subjects is not None:
        scene["frame_subjects"] = list(frame_subjects)
    if subjects is not None:
        scene["subjects"] = list(subjects)
    return scene


def _契约错误(prompt, scene):
    """只取多人契约那几条错误，避免被同场景其它校验（动作/长度）干扰。"""
    return [
        error for error in profiles._errors("anima_tags", prompt, scene)
        if "第一角色" in error or "第二角色" in error or "2girls" in error
    ]


def test_本帧主体只一人时多人契约不得按在场角色表触发():
    scene = _契约场景(actors=["甲", "乙"], frame_subjects=["甲"])
    assert _契约错误(_单人两行文案, scene) == []


def test_无本帧主体信号时多人契约仍按在场角色表触发():
    """零回归：没有正向证据就必须保持旧行为。"""
    scene = _契约场景(actors=["甲", "乙"])
    assert _契约错误(_单人两行文案, scene)


def test_本帧主体为空表时视为无信号():
    scene = _契约场景(actors=["甲", "乙"], frame_subjects=[])
    assert _契约错误(_单人两行文案, scene)


def test_本帧主体两人时多人契约照常触发():
    scene = _契约场景(actors=["甲", "乙"], frame_subjects=["甲", "乙"])
    assert _契约错误(_单人两行文案, scene)


def test_同轮计划主体优先于本帧主体():
    scene = _契约场景(
        actors=["甲", "乙"], frame_subjects=["甲"],
        subjects=[{"name": "甲"}, {"name": "乙"}],
    )
    assert _契约错误(_单人两行文案, scene)


# ---------------------------------------------------------------------------
# 多人契约「句式放宽」：语义分述（左右 / 序数 / 一个另一个…）也满足契约
# （2026-09-19 优化项⑨）
#
# 真源：`docs/illustration-prompt-delivery-2026-09-19.md` §2.19。
# 旧口径只认模板字样 `primary/second adult character|woman`；真机 L3·natural 成稿用
# 「the woman on the left / the woman on the right」完整分述了两人，仍被判红 2 条
# ⇒ 契约验收的是**措辞**而不是**分述**。
# 新口径：只要命中一对**互斥指称**（左右 / 第一第二 / 一个另一个 / 前后…）且各自
# **绑定角色名词**，即视为已单独描述每个画面主体。只放宽句式，不放松人数与绑定。
# ---------------------------------------------------------------------------


def _分述契约错误(prompt, scene):
    """只取「第一/第二角色」两条，与 `2girls`（Anima 硬标签要求）分开看。"""
    return [
        error for error in profiles._multi_actor_contract_errors(
            "natural_language", prompt, scene,
        )
        if "角色" in error
    ]


def test_左右分述满足多人契约():
    scene = _契约场景(actors=["甲", "乙"], frame_subjects=["甲", "乙"])
    prompt = (
        "Two women kneel side by side while a man watches them. "
        "The woman on the left has long black hair; "
        "the woman on the right wears a loose ponytail."
    )

    assert _分述契约错误(prompt, scene) == []


def test_序数分述满足多人契约():
    scene = _契约场景(actors=["甲", "乙"], frame_subjects=["甲", "乙"])
    prompt = "The first woman kneels on the bed while the second woman looks back at her."

    assert _分述契约错误(prompt, scene) == []


def test_一个另一个分述满足多人契约():
    scene = _契约场景(actors=["甲", "乙"], frame_subjects=["甲", "乙"])
    prompt = "One woman holds the railing while the other woman reaches toward her."

    assert _分述契约错误(prompt, scene) == []


def test_只出现单侧方位不算分述():
    """保护侧：只写「左边」没有「右边」⇒ 没有单独描述两个主体，契约照旧判红。"""
    scene = _契约场景(actors=["甲", "乙"], frame_subjects=["甲", "乙"])
    prompt = "The woman on the left lowers her gaze."

    assert _分述契约错误(prompt, scene)


def test_方位词未绑定角色名词不算分述():
    """保护侧：场景方位（台灯在左、门在右）不是角色分述。"""
    scene = _契约场景(actors=["甲", "乙"], frame_subjects=["甲", "乙"])
    prompt = (
        "A warm lamp sits on the left of the room, a wooden door on the right, "
        "and two figures stand together in the middle."
    )

    assert _分述契约错误(prompt, scene)


def test_方位构图词不得被当成角色分述():
    """保护侧：`first-person perspective` / `light from the left` 这类构图词不是角色指称。"""
    scene = _契约场景(actors=["甲", "乙"], frame_subjects=["甲", "乙"])
    prompt = (
        "first-person perspective, soft key light from the left, shallow depth; "
        "the woman stands in the corridor while a man watches her."
    )

    assert _分述契约错误(prompt, scene)


def test_原模板句式不受句式放宽影响():
    scene = _契约场景(actors=["甲", "乙"], frame_subjects=["甲", "乙"])
    prompt = "The primary adult character kneels; the second adult character looks back."

    assert _分述契约错误(prompt, scene) == []


def test_单人帧的方位描述不触发多人契约():
    """人数口径不变：画面主体只一人时，任何分述句式都不该把契约打开。"""
    scene = _契约场景(actors=["甲"], frame_subjects=["甲"])
    prompt = "The woman on the left raises her hand; the man on the right watches."

    assert _分述契约错误(prompt, scene) == []


def test_anima的2girls标签要求不因句式放宽而消失():
    """只放宽「第一/第二角色」句式；Anima 的 `2girls` 是硬标签要求，不随之放宽。"""
    scene = _契约场景(actors=["甲", "乙"], frame_subjects=["甲", "乙"])
    prompt = "The woman on the left smiles; the woman on the right looks away."

    errors = profiles._multi_actor_contract_errors("anima_tags", prompt, scene)

    assert [error for error in errors if "角色" in error] == []
    assert "Anima 双人提示词必须包含2girls" in errors


def test_本帧主体只一人时与角色表人数无关():
    scene = _契约场景(actors=["甲", "乙", "丙"], frame_subjects=["丙"])
    assert _契约错误(_单人两行文案, scene) == []


# ---------------------------------------------------------------------------
# 兜底模板按「画面主体数」判（2026-09-19 优化项⑥）
#
# 优化项③ 把**契约**改成按画面主体数判，但**兜底模板**仍只看 `actors` ⇒
# 出现自相矛盾的双口径：契约放行单人定格成稿，兜底却硬写
# `two adult women` / `2girls, two-shot` / `The two adult characters`。
# 正解：兜底复用同一条收口 `_contract_subject_names`；
# **无正向证据时逐字节等于旧的 actors 口径**（零回归前提）。
# ---------------------------------------------------------------------------


def _兜底场景(*, actors, frame_subjects=None, subjects=None):
    """两个角色都必须有可拆的外貌/动作来源。

    否则 `_multi_actor_sentences` 本就为空（它只在 actors ≥ 2 且有内容时才产出句子），
    测不出「该不该走多人模板」这件事本身。
    """
    scene = {
        "narrative": "甲按住乙的手腕，把她拉近，两人贴在一起。",
        "appearance": "甲: long black hair, red eyes\n乙: short silver hair, blue eyes",
        "draft_prompt": "wrist held, pulling closer, rain-soaked railing, side light, medium shot",
        "locale": "雨夜回廊",
        "actors": list(actors),
        "rating": "nsfw",
        "character_lora": True,
    }
    if frame_subjects is not None:
        scene["frame_subjects"] = list(frame_subjects)
    if subjects is not None:
        scene["subjects"] = list(subjects)
    return scene


_多人措辞 = ("two adult women", "two women", "2girls", "two adult characters", "two-character")


@pytest.mark.parametrize(
    "profile", ["krea2", "anima_tags", "natural_language", "niji_sections"],
)
def test_本帧主体只一人时兜底模板不得写多人措辞(profile):
    scene = _兜底场景(actors=["甲", "乙"], frame_subjects=["甲"])

    prompt = profiles.deterministic_fallback(profile, scene).lower()

    for wording in _多人措辞:
        assert wording not in prompt


@pytest.mark.parametrize(
    "profile", ["krea2", "anima_tags", "natural_language", "niji_sections"],
)
def test_无本帧主体信号时兜底模板与旧actors口径逐字节一致(profile):
    """零回归：没有正向证据时，兜底多人分支必须与 `_multi_actor_sentences` 完全相同。"""
    scene = _兜底场景(actors=["甲", "乙"])

    assert (
        profiles._fallback_multi_actor_sentences(scene)
        == profiles._multi_actor_sentences(scene)
    )
    assert profiles._fallback_multi_actor_sentences(scene), "无信号时不得把多人分支关掉"


def test_空本帧主体表视为无信号_兜底照旧走多人分支():
    scene = _兜底场景(actors=["甲", "乙"], frame_subjects=[])

    assert (
        profiles._fallback_multi_actor_sentences(scene)
        == profiles._multi_actor_sentences(scene)
    )


def test_本帧主体两人时兜底照旧走多人分支():
    """受保护用例：真实两人帧（判据也说两人）不得被这条门槛误伤。"""
    scene = _兜底场景(actors=["甲", "乙"], frame_subjects=["甲", "乙"])

    assert (
        profiles._fallback_multi_actor_sentences(scene)
        == profiles._multi_actor_sentences(scene)
    )


def test_同轮计划主体只一人时兜底也按一人成稿():
    """与契约同优先级：同轮计划 `subjects` 是身份真源，压过 `frame_subjects`。"""
    scene = _兜底场景(
        actors=["甲", "乙"], frame_subjects=["甲", "乙"],
        subjects=[{"name": "甲", "description": "adult woman with long black hair"}],
    )

    assert profiles._fallback_multi_actor_sentences(scene) == []


def test_契约与兜底共用同一条画面主体数口径():
    """优化项③ 暴露的双口径残余：契约按主体数放行，兜底必须跟着放行。"""
    scene = _兜底场景(actors=["甲", "乙"], frame_subjects=["甲"])

    assert profiles._contract_subject_names(scene) == ["甲"]
    prompt = profiles.deterministic_fallback("anima_tags", scene)
    assert "2girls" not in prompt.lower()
    assert profiles._multi_actor_contract_errors("anima_tags", prompt, scene) == []


def test_anima兜底描述主语也按画面主体数():
    solo = _兜底场景(actors=["甲", "乙"], frame_subjects=["甲"])
    duo = _兜底场景(actors=["甲", "乙"])

    solo_description = profiles._anima_content_description(solo, ["adult woman"])
    assert "The primary adult woman" in solo_description
    assert "two adult characters" not in solo_description.lower()

    assert "The two adult characters" in profiles._anima_content_description(
        duo, ["adult woman"],
    )


# ---------------------------------------------------------------------------
# 第 24 刀：兜底必须**咬住剧情里的具体动作**，多人帧必须**逐人位置 + 各自动作**
#
# 用户实锤（2026-09-20）：之前 L3 的 `anima_tags` / `natural_language` 走兜底，成稿里
# **没有**剧情中「两名女主角翘起屁股、掰开臀瓣」的动作，也没有「镜头左侧那位 / 右侧那位」
# 的逐人分述。用户口径（原话）：「就算她们的动作都一样，你也要说这名角色在镜头左侧翘起
# 臀部掰开屁股，另一名角色在镜头右侧翘起臀部掰开屁股」。
#
# 零 LLM 探针（`_tmp/item23/fallback_l3.py`）量到的两层根因：
#   ① `_actor_visual_details` 对三名角色**全部**返回 facts=[] actions=[] ⇒ 逐人句退化成
#      `is present in frame`（只有位置、没有动作）；
#   ② 场景级动作表咬不住「掰开臀肉 / 晃动屁股」这类体位动作 ⇒ 兜底只剩
#      `looking back, looking at, withdrawing the fingers`。
# 窗口原文里其实**有**事实：「她把双手贴到臀肉上，小心翼翼地掰开屁股。」（世熙·代词句）
#                        「宥夏也掰开臀肉，连滴答淌水的小穴和后庭都露了出来。」（宥夏·具名句）
# ---------------------------------------------------------------------------

# 取材窗口里真实存在的那几句（`_tmp/item23/window_l3.txt`）
_L3_代词句窗口 = "\n\n".join([
    "先把手伸向屁股的是世熙。",
    "她把双手贴到臀肉上，小心翼翼地掰开屁股。",
    "接着，旁边一直踌躇的宥夏也鼓起勇气，把手伸向身后。",
    "宥夏也掰开臀肉，连滴答淌水的小穴和后庭都露了出来。",
])

_L3_叙事者句窗口 = "\n\n".join([
    "先把手伸向屁股的是世熙。",
    "她把双手贴到臀肉上，小心翼翼地掰开屁股。",
    "我悄悄抽出了正在抚弄阴阜的手指。",
    "宥夏也掰开臀肉，连滴答淌水的小穴和后庭都露了出来。",
])


def _多人动作场景(narrative: str, actors: list[str], *, frame_subjects=None) -> dict:
    """只给叙事、不给外貌/草稿：把「事实必须来自正文出处」这条前提钉死。"""
    scene = {
        "narrative": narrative,
        "appearance": "",
        "draft_prompt": "",
        "locale": "",
        "actors": list(actors),
        "rating": "nsfw",
    }
    if frame_subjects is not None:
        scene["frame_subjects"] = list(frame_subjects)
    return scene


def test_兜底动作表咬住掰开臀肉与晃动屁股():
    """① 修：`_SCENE_ACTION_RULES` 必须覆盖本作高频体位动作（窗口原文有出处才触发）。

    第 31 刀（用户实锤）：「把双手贴在臀肉上掰开屁股」原本**一条源句同时命中两条规则**
    ⇒ 首行出现 `spreading her own buttocks apart` + `both palms pressed against her own
    buttocks` 两条**语义重叠**的串（用户原话：「说的词多还不够详细」）。定案（用户选定
    「合并成一条」）：本支改写为 `both hands spreading her buttocks apart`，原「双掌贴臀」
    支**删除** ⇒ 同一动作永不出两条。反向锁同时钉住新旧两串。
    """
    scene = _多人动作场景(
        "\n\n".join([
            "她把双手贴到臀肉上，小心翼翼地掰开屁股。",
            "宥夏也掰开臀肉，连滴答淌水的小穴和后庭都露了出来。",
            "两侧同时献上小穴和后庭，争着要他插进来，屁股轻轻晃着。",
        ]),
        ["世熙", "宥夏"],
    )

    tags = profiles._scene_action_tags(scene)

    assert "both hands spreading her buttocks apart" in tags
    assert "swaying her hips" in tags
    assert "exposing her pussy and anus" in tags
    assert "both palms pressed against her own buttocks" not in tags  # 第 31 刀：该支已删


def test_兜底动作表不凭空触发():
    """零编造：正文没写这些动作时，一条都不许出现。"""
    scene = _多人动作场景("她安静地站在窗边，看着外面下雨。", ["世熙", "宥夏"])

    tags = profiles._scene_action_tags(scene)

    assert "both hands spreading her buttocks apart" not in tags
    assert "swaying her hips" not in tags


def test_多人分述按就近代词归属各自动作():
    """② 修：具名句与**就近前指代词句**都要落到本人名下（左右各自带自己的动作）。"""
    scene = _多人动作场景(_L3_代词句窗口, ["世熙", "宥夏"])

    details = {name: (facts, actions) for name, facts, actions in profiles._actor_visual_details(scene)}
    sentences = profiles._multi_actor_sentences(scene)

    assert "both hands spreading her buttocks apart" in details["世熙"][1], "代词句「她…掰开屁股」应归世熙"
    assert "both hands spreading her buttocks apart" in details["宥夏"][1], "具名句「宥夏也掰开臀肉」应归宥夏"
    assert sentences[0].startswith("The primary adult character on the left side of the frame")
    assert sentences[1].startswith("The second adult character on the right side of the frame")
    for sentence in sentences:
        assert "both hands spreading her buttocks apart" in sentence
        assert "is present in frame" not in sentence


def test_多人分述不得把叙事者动作广播给角色():
    """⚠ 关键防线（第 21 刀「跨句串味」同族）：含有「我」的叙事者句**不得**归给任何角色。"""
    scene = _多人动作场景(_L3_叙事者句窗口, ["世熙", "宥夏"])

    sentences = " ".join(profiles._multi_actor_sentences(scene))

    assert "withdrawing the fingers" not in sentences
    assert "both hands spreading her buttocks apart" in sentences


def test_多数主体无独立证据时用集体句动作补齐():
    """③ 修：全员都取不到独立证据时，用**集体句**（两侧/两人/双方…）的动作逐人补齐。

    这正是用户口径的机械化：动作一样，也要在各自位置各写一遍。
    """
    scene = _多人动作场景(
        "两侧同时献上小穴和后庭，争着要他插进来，屁股轻轻晃着。", ["甲", "乙"],
    )

    sentences = profiles._multi_actor_sentences(scene)

    assert len(sentences) == 2
    assert all("swaying her hips" in sentence for sentence in sentences)
    assert all("is present in frame" not in sentence for sentence in sentences)
    assert sentences[0] != sentences[1]  # 位置锚必须不同


def test_少数主体缺证据时不广播集体动作():
    """⚠ 反向锁：一位缺证据 ≠ 把集体动作塞给他/她（避免把两人的动作误配给第三人）。"""
    scene = _多人动作场景(
        "\n\n".join([_L3_代词句窗口, "泰宇老师坐在两人中间看着她们。"]),
        ["世熙", "宥夏", "泰宇老师"],
    )

    sentences = profiles._multi_actor_sentences(scene)

    assert "both hands spreading her buttocks apart" in sentences[0]
    assert "both hands spreading her buttocks apart" in sentences[1]
    assert "both hands spreading her buttocks apart" not in sentences[2]


def test_三人帧位置锚互不相同():
    """三人帧不得出现两人共用同一位置锚（探针实锤：第 3 人曾与第 2 人同为右侧）。"""
    scene = _多人动作场景(_L3_代词句窗口, ["世熙", "宥夏", "泰宇老师"])
    scene["appearance"] = "泰宇老师: short black hair"

    sentences = profiles._multi_actor_sentences(scene)
    positions = [profiles._MULTI_ACTOR_POSITIONS[
        index if index < len(profiles._MULTI_ACTOR_POSITIONS) else -1
    ] for index in range(len(sentences))]

    assert len(set(positions)) == len(positions)


def test_三人帧兜底模板不得写两人措辞():
    """三人帧仍写 `two adult women` / `two-character` 是双口径残留（真机 L3 三主体）。"""
    scene = _多人动作场景(_L3_代词句窗口, ["世熙", "宥夏", "泰宇老师"])

    for profile in ("anima_tags", "natural_language", "niji_sections", "krea2"):
        lowered = profiles.deterministic_fallback(profile, scene).lower()
        for wording in ("two adult women", "two women", "two adult characters", "two-character"):
            assert wording not in lowered, f"{profile} 仍写两人措辞：{wording}"
        # ⚠ **不得把 `2girls` 自动升格成 `3girls`**：多人契约的硬要求就是 `2girls`
        # （用户 2026-09-20 定案「只需 2girls、不要求补 1boy」），且兜底层拿不到角色性别
        # ——真机三人帧是「2 女 + 1 男」。
        assert "3girls" not in lowered, f"{profile} 不得把硬标签升格成 3girls"


def test_两人帧兜底措辞逐字节不变():
    """零回归：两人帧的既有措辞受保护，不得被上面的计数口径改动波及。"""
    scene = _多人动作场景(_L3_代词句窗口, ["世熙", "宥夏"])

    assert "places two adult women in clearly separated positions" in (
        profiles.deterministic_fallback("natural_language", scene)
    )
    assert "2girls, two-shot" in profiles.deterministic_fallback("anima_tags", scene)


# ---------------------------------------------------------------------------
# 第 26 刀：环境句要落**地点事实**，不是永远写「分层背景」
#
# 用户原话（2026-09-20 22:28）：「natural_language 根本没有获取背景，只说明了是分层环境……
# 要么文段剧情能提供地点获取环境，要么没获取就按照默认的状态表来」。
# 口径：地点来源优先级＝本轮正文/状态 > 数据表的上一轮旧值；两处都没有 → 才落回分层背景。
# ---------------------------------------------------------------------------

def test_多人兜底环境句采用地点事实():
    scene = _多人动作场景(_L3_代词句窗口, ["世熙", "宥夏"])
    scene["locale"] = "寝殿"

    natural = profiles.deterministic_fallback("natural_language", scene)
    krea = profiles.deterministic_fallback("krea2", scene)

    assert "a layered bedchamber environment" in natural
    assert "layered environmental background" not in natural
    assert "a layered bedchamber environment" in krea
    assert "against a layered background." not in krea


def test_多人兜底环境句_无地点时逐字节落回旧措辞():
    """零回归：没有地点事实时，句面必须与改动前逐字节一致（受保护）。"""
    scene = _多人动作场景(_L3_代词句窗口, ["世熙", "宥夏"])
    assert scene["locale"] == ""

    assert "against a layered environmental background. " in (
        profiles.deterministic_fallback("natural_language", scene)
    )
    assert "against a layered background." in profiles.deterministic_fallback("krea2", scene)


def test_环境短语_映射不到的地名与超长串不硬塞():
    """映射不到的中文地名 / 超长串 ⇒ 退回默认短语（硬塞会读成病句）。"""
    scene = _多人动作场景(_L3_代词句窗口, ["世熙", "宥夏"])

    scene["locale"] = "某个从未见过的地名甲"
    assert "layered environmental background" in profiles.deterministic_fallback("natural_language", scene)

    scene["locale"] = "x" * 60
    assert "layered environmental background" in profiles.deterministic_fallback("natural_language", scene)


# ---------------------------------------------------------------------------
# 第 25 刀：主角（第一人称）**也有动作**，要判不要一律丢
#
# 用户原话（2026-09-20 19:5x）：「并不是主人公不吃体位词，而是要判断主角有没有所谓的动作，
# 如紧捏 xxx 角色的臀部，然后你就可以说『（一只手）放在哪个角色的臀部上』；如果没有，
# 那也不需要强调第一或第三人称」。
#
# 第 24 刀把「含第一人称的句子」整类跳过 ⇒ 主角的动作**一条都不进画面**。本刀的判定是：
#   · 句里**点了名**的角色 ⇒ 该角色拿到「主角的手放在她身上」这条可见事实（`has …` 措辞）；
#   · 句里**没点名** ⇒ 答不出「哪个角色」⇒ 不归属、不编造（宁缺勿猜）；
#   · 「**她**把双手贴到自己臀肉上」这类**非第一人称**的自摸不产接触 tag（自己的手≠主角的手）。
# ---------------------------------------------------------------------------


def test_主角对某角色的接触动作归到被触角色名下():
    scene = _多人动作场景("我紧捏着世熙的臀部。", ["世熙", "宥夏"])

    details = {name: (facts, actions) for name, facts, actions in profiles._actor_visual_details(scene)}
    sentences = profiles._multi_actor_sentences(scene)

    assert "a hand resting on her buttocks" in details["世熙"][1], "被点名的世熙应拿到主角的手"
    assert "a hand resting on her buttocks" not in details["宥夏"][1], "没被点名的宥夏不得被分到"
    assert "has a hand resting on her buttocks" in sentences[0]
    assert "a hand resting on her buttocks" not in sentences[1]


def test_主角接触动作无点名对象时不归属也不编造():
    """⚠ 反向锁：只有部位、没有角色 ⇒ 答不出「哪个角色」就不写。"""
    scene = _多人动作场景("我悄悄抽出了正在抚弄阴阜的手指。", ["世熙", "宥夏"])

    sentences = " ".join(profiles._multi_actor_sentences(scene))

    assert "a hand touching her crotch" not in sentences
    assert "a hand resting on her buttocks" not in sentences


def test_非第一人称的自摸不算主角接触():
    """「她把双手贴到自己的臀肉上」＝她自己的手，不是主角的手 ⇒ 不得产出接触事实。"""
    scene = _多人动作场景("世熙把双手贴到臀肉上，小心翼翼地掰开屁股。", ["世熙", "宥夏"])

    details = {name: (facts, actions) for name, facts, actions in profiles._actor_visual_details(scene)}

    assert "a hand resting on her buttocks" not in details["世熙"][1]
    # 但**她自己**的可见动作必须还在（第 24 刀口径不受影响；第 31 刀把该串合并为
    # `both hands spreading her buttocks apart`）
    assert "both hands spreading her buttocks apart" in details["世熙"][1]


def test_主角接触动作不依赖人称信号():
    """用户口径：「如果没有（主角动作），那也不需要强调第一或第三人称」⇒ 人称不参与这条产出。"""
    base = _多人动作场景("我紧捏着世熙的臀部。", ["世熙", "宥夏"])
    first = dict(base, viewpoint="first_person")
    third = dict(base, viewpoint="third_person")

    assert profiles._multi_actor_sentences(first) == profiles._multi_actor_sentences(third)


def test_主角接触事实进得了兜底成稿():
    scene = _多人动作场景("我紧捏着世熙的臀部。", ["世熙", "宥夏"])

    prompt = profiles.deterministic_fallback("natural_language", scene)

    assert "has a hand resting on her buttocks" in prompt


# ---------------------------------------------------------------------------
# 校验失败改「补丁降级」（2026-09-19 优化项⑤）
#
# 真源：`docs/illustration-prompt-delivery-2026-09-19.md` §2.6 / §3 第 5 项。
# 旧行为：模型稿 repair 后仍红 ⇒ **整稿作废** ⇒ 掉 `deterministic_fallback` 词表模板
# （真机实测：2237 字成稿被换成 777 字模板 + `removing the visible object` 事故串）。
# 新行为：稿子**本体**（结构 + 语言）可用时保留模型稿，只用本地机械补缺口
# （`complete_field_coverage` 补客观字段），不再整稿作废。
# 单向放宽：补丁只在**原本就掉兜底**的分支上接手 ⇒ 对已合格稿零影响。
# ---------------------------------------------------------------------------

_补丁外貌条目 = "冷倾雪：【外貌】漆黑墨发，朱唇娇艳。"


def _补丁场景():
    """两个可验证外貌事实（hair / lips）；模型稿只写 hair ⇒ 必红一条「可视事实未落实」。"""
    return {
        "narrative": "窗外落着小雨。",
        "draft_prompt": "",
        "appearance": _补丁外貌条目,
        "wardrobe": "",
        "locale": "",
        "actors": ["冷倾雪"],
        "rating": "sfw",
    }


_补丁两行稿 = (
    "1girl, solo, glossy jet-black hair, white linen robe, medium shot, side light, "
    "quiet interior, plain background\n"
    "The adult woman stands in a quiet interior with her gaze lowered and her hands still."
)
_补丁合格稿 = (
    "1girl, solo, glossy jet-black hair, full crimson lips, white linen robe, medium shot, "
    "side light, quiet interior, plain background\n"
    "The adult woman stands in a quiet interior with her gaze lowered and her hands still."
)


def _补丁生成(profile, raw, scene=None, *, allow_repair=True):
    """同一份 raw 连给两次（首轮 + 重写），把 prompt / diagnostics 一起交回。"""
    scene = _补丁场景() if scene is None else scene
    outputs = iter([raw, raw])

    def _next(_system, _user):
        return next(outputs)

    diagnostics: dict = {}
    prompt = profiles.generate(
        profile, scene, _next, diagnostics, allow_repair=allow_repair,
    )
    return prompt, diagnostics


def test_anima补丁降级保留模型两行稿并补客观事实():
    prompt, diagnostics = _补丁生成("anima_tags", _anima_json(_补丁两行稿))

    assert diagnostics["strategy"] == "patched"
    assert any("可视事实未落实" in item for item in diagnostics["repair_errors"])
    assert "The adult woman stands in a quiet interior" in prompt   # 画面句逐字保留
    assert "glossy jet-black hair" in prompt                        # 模型 tags 保留
    assert "full crimson lips" in prompt                            # 补进的客观事实
    # 必须是「模型稿 + 补丁」，不是词表模板
    assert "detailed anime illustration" not in prompt
    assert "removing the visible object" not in prompt


def test_补丁降级只补缺字段不重复已覆盖事实():
    prompt, _ = _补丁生成("anima_tags", _anima_json(_补丁两行稿))

    assert prompt.count("glossy jet-black hair") == 1


_长句默认值 = (
    "a scene-appropriate medium shot with coherent perspective and readable depth of field",
    "one coherent directional light source with consistent highlights and cast shadows",
    "consistent hair, fabric and surface rendering with believable light response",
    "consistent anatomy, stable contours, clean edges, and restrained detail",
)


def _补丁缺字段场景():
    """外貌有可验证期望、镜头/构图/光影/材质/质量全缺 ⇒ 必然走到 `defaults` 追加那一步。"""
    return {
        "narrative": "她站在廊下。",
        "appearance": "narrow dark-red eyes",
        "wardrobe": "",
        "locale": "",
        "actors": ["甲"],
        "rating": "sfw",
    }


_补丁缺字段稿 = chr(10).join((
        "1girl, solo, narrow dark-crimson eyes, pale robe,",
        "The adult woman stands under the eaves.",
    ))


def test_anima补丁首行不得出现长句默认值():
    """2026-09-20 真机 L1/L3·anima 实锤：⑤补丁把 `defaults`（长句）当 tag 追加进首行 ⇒
    `..., dramatic tension., withdrawing what was sheathed, a scene-appropriate medium shot with
    coherent perspective and readable depth of field,`。首行是逗号 tag 序列，长句默认值必须换 tag 形状。"""
    completed, _ledger = profiles.complete_field_coverage(
        "anima_tags", _补丁缺字段稿, _补丁缺字段场景(),
    )
    head = completed.splitlines()[0]

    for long_clause in _长句默认值:
        assert long_clause not in completed, long_clause
    assert "medium shot, coherent perspective, readable depth of field" in head
    assert "cast shadows" in head
    assert "restrained detail" in head


def test_anima补丁文段不复述通用模板长句():
    """文段是模型写的自然语言，只允许追加**真事实**的复述句（既有锁要求事实进第二行），
    不得把 `defaults` 长句也复述一遍——真机实锤 `The two adult characters is depicted with
    lowering head, a clear primary visual focus, … one coherent directional light source …`。"""
    completed, _ledger = profiles.complete_field_coverage(
        "anima_tags", _补丁缺字段稿, _补丁缺字段场景(),
    )
    prose = completed.splitlines()[1]

    assert prose.startswith("The adult woman stands under the eaves.")
    assert "narrow dark-red eyes" in prose          # 真事实照旧复述（既有锁）
    for long_clause in _长句默认值:
        assert long_clause not in prose, long_clause


def test_niji补丁分段不得出现长句默认值():
    """同病：L2·niji 的 subject 段被追加 `looking back, withdrawing what was sheathed, consistent
    anatomy, stable contours, clean edges, and restrained detail`。"""
    prompt = chr(10).join((
        "1girl, solo, narrow dark-crimson eyes, pale robe",
        "anime style, clean lineart, muted palette",
        "quiet interior, calm atmosphere",
        "--ar 3:4 --niji 6",
    ))
    completed, _ledger = profiles.complete_field_coverage(
        "niji_sections", prompt, _补丁缺字段场景(),
    )

    for long_clause in _长句默认值:
        assert long_clause not in completed, long_clause
    assert "medium shot" in completed
    assert "restrained detail" in completed
    assert "narrow dark-red eyes" in completed


def test_anima兜底文段复数主语配are():
    """`The two adult characters is depicted with …` 是语法破损（真机 L1/L3 成稿实锤）。"""
    solo = profiles._anima_content_description(
        _兜底场景(actors=["甲", "乙"], frame_subjects=["甲"]), ["adult woman"],
    )
    duo = profiles._anima_content_description(_兜底场景(actors=["甲", "乙"]), ["adult woman"])

    assert "The primary adult woman is depicted with" in solo
    assert "The two adult characters are depicted with" in duo
    assert "characters is depicted" not in duo


def test_补丁门槛只看本体不吃事实():
    """门槛是「结构与语言」；事实缺口照旧判红，但不阻断保留。"""
    scene = _补丁场景()
    prompt = profiles._normalize("anima_tags", _anima_json(_补丁两行稿), scene)

    assert profiles._errors("anima_tags", prompt, scene)
    assert profiles._body_errors("anima_tags", prompt) == []


def test_补丁门槛认得出结构不合格():
    """单行纯 tags（真机 §2.6 的 L3 形态）本体不合格 ⇒ 补丁不接手。"""
    assert "必须正好两行" in profiles._body_errors("anima_tags", _ANIMA_SPLIT_TAGS)


def test_krea2补丁降级保留两段稿并补可视事实():
    prompt, diagnostics = _补丁生成("krea2", _补丁两行稿)

    assert diagnostics["strategy"] == "patched"
    assert "The adult woman stands in a quiet interior" in prompt
    assert "full crimson lips" in prompt
    assert "A balanced two-character" not in prompt


def test_天然语言补丁降级保留英文段落并补客观事实():
    paragraph = (
        "An adult woman with glossy jet-black hair stands in a quiet interior, her gaze "
        "lowered and her hands resting still, while a soft side light keeps one calm visual "
        "center and the background recedes into restrained shadow."
    )
    prompt, diagnostics = _补丁生成("natural_language", paragraph)

    assert diagnostics["strategy"] == "patched"
    assert "her gaze lowered" in prompt           # 模型段落逐字保留
    assert "full crimson lips" in prompt          # 补进的客观事实
    assert "The image preserves" not in prompt    # 不是词表兜底段落


def test_niji补丁降级保留四段稿并补可视事实():
    raw = json.dumps({
        "subject": "1girl, solo, glossy jet-black hair, white linen robe, standing still",
        "style": "refined two-dimensional narrative illustration, cohesive linework",
        "additions": "quiet interior, soft side light, plain background",
        "suffix": "--ar 2:3 --niji 6",
    })
    prompt, diagnostics = _补丁生成("niji_sections", raw)

    assert diagnostics["strategy"] == "patched"
    assert "quiet interior" in prompt             # 模型四段保留
    assert "full crimson lips" in prompt
    assert len(prompt.splitlines()) == 4


def test_天然语言含中文稿不因补丁而降级保留():
    """§2.8 的在役档硬失败：中文正文属**本体**不合格 ⇒ 补丁不接手，照旧落兜底。"""
    prompt, diagnostics = _补丁生成("natural_language", "她站在安静的室内，目光低垂，双手停着不动。")

    assert diagnostics["strategy"] == "fallback"
    assert "The image preserves" in prompt


def test_补丁降级不救单行纯tags稿():
    """诚实边界（§2.6 残余）：模型只给 tags、画面句根本不存在时，仍照旧词表兜底。"""
    raw = json.dumps({
        "visual_hook": "v", "primary_focus": "p", "supporting_elements": [],
        "content": _ANIMA_SPLIT_TAGS,
    })
    prompt, diagnostics = _补丁生成("anima_tags", raw)

    assert diagnostics["strategy"] == "fallback"
    assert len(prompt.splitlines()) == 2          # 兜底产物本身仍合规


def test_补丁降级对空返回与拒答无效():
    empty_prompt, empty_diagnostics = _补丁生成("anima_tags", "")
    assert empty_diagnostics["strategy"] == "fallback"
    assert empty_prompt.strip()

    refusal_prompt, refusal_diagnostics = _补丁生成(
        "anima_tags", "抱歉，我无法协助完成这个请求。",
    )
    assert refusal_diagnostics["strategy"] == "fallback"
    assert refusal_diagnostics["first_refusal"] is True
    assert refusal_prompt.strip()


def test_补丁降级不影响已合格稿():
    """零回归：稿子本来就合格 ⇒ 照旧 direct，补丁不介入。"""
    prompt, diagnostics = _补丁生成("anima_tags", _anima_json(_补丁合格稿))

    assert diagnostics["strategy"] == "direct"
    assert prompt == profiles.complete_field_coverage(
        "anima_tags",
        profiles._normalize("anima_tags", _anima_json(_补丁合格稿), _补丁场景()),
        _补丁场景(),
    )[0]
    assert "Required visible facts" not in prompt


# ---------------------------------------------------------------------------
# 在役档语言覆盖声明 + 语言违例的观测事实（2026-09-19 优化项②）
#
# 真源：`docs/illustration-prompt-delivery-2026-09-19.md` §2.8 / §2.17；技术手册红线 27。
# 真机取证：`system_with_preset` 把防拦截预设**整段拼在模板之前**（拼接后 10,699 字里
# 预设段占 8,737 字），而预设正文含「正文简体中文」「推演当然使用中文啦」等明文语言要求
# ⇒ 对「输出语言」这个话题，上游越权指令在篇幅与位置上双压模板末尾那句
# 「最终结果必须全部使用英文。」⇒ 约 1/10 次模型整段写中文叙事 ⇒ `_errors` 纯英文硬判据
# 判死掉兜底（形态：282/311 字 CJK＝90.7%，整段中文文学正文）。
#
# 定案（观测后）：修法＝**覆盖式前置声明**，且**放在 Profile 文本里**而不是拼接函数里
# ——语言约束是模板自己的合同，同一条约束两处写就是两处漂移；模板段离生成位置更近。
# 反向边界同样锁死：拼接器**不得**再插一层语言指令（否则将来必与模板段漂移）。
# ---------------------------------------------------------------------------

_语言声明片段 = "【最高优先级·语言】"
# `_COMMON` 是四档共用的**通用**段（不含任何档位语言要求），用于把语言声明锚定在**档内**：
# `_NATURAL_SYSTEM = _COMMON + (…)`，故偏移 `_COMMON_LEN` 之后才是本档自己的正文。
_COMMON_LEN = len(profiles._COMMON)

# 自然语言档的合格稿（两行都合格：长度 >20、纯英文、含句末标点、不以 ``` 开头）
_自然语言合格稿 = (
    "A lone adult woman leans back against a notice board in a dim school corridor. "
    "Low evening light rakes across the paper-covered surface and defines her silhouette."
)


def _自然语言场景():
    return {
        "narrative": "她靠在公告栏上。", "draft_prompt": "", "appearance": "",
        "wardrobe": "", "locale": "", "actors": ["宥夏"], "rating": "nsfw",
    }


def test_自然语言档系统提示含覆盖式语言声明():
    """在役档的语言要求必须是「压过上游设定」的独立声明，且不能只在结尾顺带一句。"""
    system = profiles._NATURAL_SYSTEM

    assert _语言声明片段 in system
    assert "纯英文" in system
    # 声明必须属于**本档正文**（而非 _COMMON 的通用段），且位于档内主体描述之前：
    # 档内正文起点 = _COMMON_LEN，而「你在为 …」正是档内第一句。
    assert _语言声明片段 in system[_COMMON_LEN:]
    assert system.index(_语言声明片段) > system.index("你在为 GPT Image", _COMMON_LEN)
    assert system.index(_语言声明片段) < system.index("不要使用标签堆砌")
    assert system.endswith("不要使用标签堆砌、质量咒语、权重语法、参数后缀、标题、解释或Markdown。")


def test_niji档系统提示含覆盖式语言声明():
    system = profiles._NIJI_SYSTEM

    assert _语言声明片段 in system
    assert "纯英文" in system
    assert _语言声明片段 in system[_COMMON_LEN:]


def test_anima档系统提示含覆盖式语言声明():
    """2026-09-20 真机实锤：L3·anima raw#1 是一次 **265 字纯中文叙事**（221 CJK / 1 行，
    连 JSON 都没输出）⇒ 本档原先**没有**这条声明，而防拦截预设是整段前置的。"""
    system = profiles._ANIMA_SYSTEM

    assert _语言声明片段 in system
    assert "纯英文" in system
    # 必须属本档正文（`_COMMON` 之后），且不在块末顺带一句：
    assert _语言声明片段 in system[_COMMON_LEN:]
    assert system.index(_语言声明片段) > system.index("你在为 Anima", _COMMON_LEN)
    assert system.index(_语言声明片段) < system.index("content 中不要输出质量词")


def test_视角缺键时三档系统提示词逐字节不变():
    """零回归锁（2026-09-20 用户定案）：`viewpoint` 键缺失 ⇒ 视角段为空串 ⇒
    三档系统提示词**逐字节**等于基础文本。"""
    for profile in ("anima_tags", "natural_language", "niji_sections", "krea2"):
        base = profiles._KREA_SYSTEM if profile == "krea2" else profiles._SYSTEMS[profile]

        assert profiles._system(profile, {"rating": "sfw"}) == base, profile
        assert profiles._system(profile, {"rating": "sfw", "viewpoint": ""}) == base, profile
        assert profiles._system(profile, {"rating": "sfw", "viewpoint": "bird_eye"}) == base, profile


def test_第一人称视角口径要求身体局部入画():
    """用户口径（2026-09-20）：默认第一人称；**第一人称下叙事者的身体局部要入画**
    （NSFW 桥段必然有身体部位）——禁止的是把叙事者画成一个人，不是不许画他的手。"""
    for profile in ("anima_tags", "natural_language", "niji_sections"):
        system = profiles._system(
            profile, {"rating": "nsfw", "viewpoint": "first_person", "narrator": "甲"},
        )

        assert "【视角·第一人称】" in system, profile
        assert "身体局部" in system, profile
        assert "只在输入画面事实写明时才入画" in system, profile
        assert "不入画" in system, profile
        assert "第二个人" in system, profile
        assert "甲" in system, profile


def test_第三人称视角口径要求叙事者算一个主体():
    for profile in ("anima_tags", "natural_language", "niji_sections"):
        system = profiles._system(
            profile, {"rating": "sfw", "viewpoint": "third_person", "narrator": "甲"},
        )

        assert "【视角·第三人称】" in system, profile
        assert "正常参与画面主体数" in system, profile
        assert "【视角·第一人称】" not in system, profile


def test_第一人称契约回落actors时剔除叙事者():
    """③ 回落路径原本只看 actors ⇒ 叙事者在角色表里会被算回来，收紧只在一半路径生效。"""
    scene = {"actors": ["甲", "宥夏"], "viewpoint": "first_person", "narrator": "甲"}

    assert profiles._contract_subject_names(scene) == ["宥夏"]
    assert profiles._contract_subject_names(
        {"actors": ["甲", "宥夏"], "viewpoint": "third_person", "narrator": "甲"},
    ) == ["甲", "宥夏"]
    # 零回归：没有 narrator 键 ⇒ 逐字节等于旧行为
    assert profiles._contract_subject_names(
        {"actors": ["甲", "宥夏"], "viewpoint": "first_person"},
    ) == ["甲", "宥夏"]
    assert profiles._contract_subject_names({"actors": ["甲", "宥夏"]}) == ["甲", "宥夏"]


def test_匿名化第三名角色不再输出裸编号():
    """第 17 刀（2026-09-20 用户实锤）：第 3 名起原为 `adult character 3`，**裸编号进最终提示词**。"""
    scene = {"actors": ["甲", "乙", "丙", "丁"]}
    prompt = profiles._anonymize_prompt_names("甲、乙、丙、丁四个人", scene)

    lowered = prompt.lower()
    assert "the primary adult character" in lowered
    assert "the second adult character" in lowered
    assert "the third adult character" in lowered
    assert "the fourth adult character" in lowered
    assert "adult character 3" not in prompt
    assert "adult character 4" not in prompt
    assert "甲" not in prompt and "丙" not in prompt

    # 契约仍认得出两个分开指称的主体（`_ACTOR_NOUN_PATTERN` 只认 character 词根）
    assert profiles._character_labels(4) == [
        "the primary adult character", "the second adult character",
        "the third adult character", "the fourth adult character",
    ]


def test_抽出拔出按对象分档不再产出鞘中之物():
    """第 17 刀：原串 `withdrawing what was sheathed` 在真机语料里全是「抽出手指」。"""
    fingers = profiles._scene_action_tags(
        {"narrative": "我悄悄抽出了正在抚弄阴阜的手指。"},
    )
    generic = profiles._scene_action_tags({"narrative": "要拔出来了。"})

    assert "withdrawing the fingers" in fingers
    assert "withdrawing what was sheathed" not in fingers
    assert "pulling out" not in fingers        # 粗串不得与细串同时进 tags
    assert "pulling out" in generic
    assert "withdrawing what was sheathed" not in generic


def test_三档多人分述条款措辞一致():
    """第 19 刀（2026-09-20 用户实锤）：L3·anima「tag 没把两名角色的动作表示出来、没区分人物」，
    L3·niji「构图动作混乱、出现自己反驳自己动作的现象」⇒ 三档统一加【多人分述】。"""
    def _clause(system):
        start = system.index("【多人分述】")
        end = system.index("不得自创与原文冲突的机位。", start) + len("不得自创与原文冲突的机位。")
        return system[start:end]

    systems = [profiles._system(p, {"rating": "nsfw"}) for p in
               ("anima_tags", "natural_language", "niji_sections")]
    first = _clause(systems[0])
    for system in systems[1:]:
        assert _clause(system) == first

    for marker in ("逐人分述", "镜头左侧", "镜头右侧", "即使两人动作完全相同",
                   "不得被写成互相矛盾的姿态", "构图以小说原文为准"):
        assert marker in first, marker


def test_第一人称身体局部必须有出处不得凭空添加():
    """用户实锤：L1·natural 为体现第一人称凭空补了 `The foreground includes the narrator's own hand`。"""
    system = profiles._system(
        "natural_language", {"rating": "nsfw", "viewpoint": "first_person", "narrator": "甲"},
    )
    clause = system[system.index("【视角·第一人称】"):]

    assert "只在输入画面事实写明时才入画" in clause
    assert "没写就一律不写" in clause
    assert "不得为了体现第一人称而凭空补" in clause
    assert "可以且应当" not in clause


def test_括号碎片被拆掉而权重语法不受影响():
    """用户实锤：L1·niji 出现 `(torn) … (above her) (soaked-through) (thin) (panties)`。"""
    assert profiles._unwrap_stray_parentheses(
        "she wears a (torn) white shirt hiked up, exposing a strip of bare lower back "
        "(above her) (soaked-through) (thin) (panties) pulled taut",
    ) == ("she wears a torn white shirt hiked up, exposing a strip of bare lower back "
          "above her soaked-through thin panties pulled taut")
    assert profiles._unwrap_stray_parentheses("(masterpiece:1.2), 1girl") == "(masterpiece:1.2), 1girl"


def test_悬空事实片段不进交付文本而真事实必须补上():
    """第 21 刀（2026-09-20 生产链实锤）：只删 `Required visible facts:` 标签不够——
    `looking at; consistent anatomy, …` 这类**悬空片段**还挂在文末，anima 还被拼成病句
    `The two adult characters are depicted with looking at.`。

    定案：**悬空动词片段**（只带动词+介词、无宾语）不补；**真事实**照旧必须补
    （`bedchamber` 是 1 词地点，也是合法事实 ⇒ **不能按词数过滤**）。
    """
    assert profiles._is_dangling_fact("looking at") is True
    assert profiles._is_dangling_fact("withdrawing the fingers") is False
    assert profiles._is_dangling_fact("bedchamber") is False
    assert profiles._is_dangling_fact("legs raised over the partner's shoulders") is False

    scene = {"profile": "natural_language", "rating": "nsfw", "locale": "bedchamber"}
    prompt = "A woman stands under directional light."
    repaired, _ledger = profiles.complete_field_coverage("natural_language", prompt, scene)
    assert "bedchamber" in repaired
    assert "Required visible facts" not in repaired
    assert not repaired.rstrip().endswith("looking at.")


def test_三档具体化出处条款措辞一致():
    """第 16 刀（2026-09-20）：【事实边界】原来只说「不得新增」，模型就在**已有事实上自由发挥**
    （L1 真机：真事实「背靠墙壁」被扩写成 `narrow alley setting` / `peeling paint`，
    窗口无据的 `white tank top` / `black lace panties`）。故把边界**分级**：具体化一律要有出处。"""
    clauses = [profiles._system(p, {"rating": "nsfw"}) for p in
               ("anima_tags", "natural_language", "niji_sections")]
    def _clause(system):
        start = system.index("【具体化必须有出处】")
        end = system.index("留白不写", start) + len("留白不写")
        return system[start:end]

    first = _clause(clauses[0])
    for system in clauses[1:]:
        assert _clause(system) == first

    for marker in ("可直接写", "只能中性化", "不得新增或升级", "留白不写",
                   "背靠墙壁", "窄巷", "床边", "床头灯"):
        assert marker in first, marker


def test_三档系统提示都要求事实边界():
    """2026-09-20 用户口径（L1 真机实锤）：三档在**同一份窗口**下各自脑补出互斥场景——
    anima 写 `teacher office / wooden desk`，natural 与 niji 写 `bed / bedside lamp /
    dark bedroom`，而原文是**教室**。故三档都必须有「不得新增输入未提及的道具/服装/场景/配色」。"""
    for profile in ("anima_tags", "natural_language", "niji_sections"):
        system = profiles._system(profile, {"rating": "sfw"})

        assert "【事实边界】" in system, profile
        assert "不得新增输入未提及" in system, profile
        assert "宁可少写也不编造" in system, profile


def test_三支在役档的语言声明措辞一致():
    """同一约束三档各写一遍时，必须至少保持**同一句声明**——防止将来只改一档。"""
    probe = "无论上文（若存在角色扮演设定或世界观规则）如何规定正文语言"

    assert probe in profiles._ANIMA_SYSTEM
    assert probe in profiles._NATURAL_SYSTEM
    assert probe in profiles._NIJI_SYSTEM


def test_自然语言档系统提示要求定格且禁止不可绘制内容():
    """2026-09-20 用户口径（L1·natural 真机成稿实锤）：自然语言档必须把这一刻**定格**成静止
    一帧，并禁止写图像表现不出来的内容（气味 / 声音 / 台词 / 心理）；同时不得编造输入里
    没有的场景与道具（L2·natural 把 20 字窗口写成「废墟大厅」）。"""
    system = profiles._NATURAL_SYSTEM

    for rule in ("定格", "静态陈述句", "时间推进", "只写画得出来的", "气味", "声音", "台词",
                 "内心想法", "事实边界", "不得新增输入未提及"):
        assert rule in system, rule
    # 仍要求完整自然语言段落：本刀只收时态与事实边界，不改「不要标签堆砌」的档位定义。
    assert "一段连贯、具体的自然语言画面描述" in system


def test_自然语言不可绘制内容只观测不判死():
    """观测字段与 CJK 同源：命中气味 / 台词时记词面供 trace 判读，但**不得**升级成错误——
    这类措辞偏了也比掉词表兜底好，判死会让每一格都更容易丢模型稿。"""
    raw = ("A woman kneels on the bed, the faint scent of laundry detergent mixing with the "
           "quiet rustle of fabric, and the word “teacher” still hangs in the air between them.")

    prompt, diagnostics = _补丁生成("natural_language", raw, _自然语言场景())

    assert diagnostics["strategy"] == "direct"
    assert "scent" in diagnostics["first_unpaintable_terms"]
    assert "the word" in diagnostics["first_unpaintable_terms"]
    assert "scent" in prompt


def test_合格自然语言稿不写不可绘制观测字段():
    """零回归：稿子合格且无不可绘制词面时不得凭空多出观测字段（下游按存在性判读）。"""
    _prompt, diagnostics = _补丁生成("natural_language", _自然语言合格稿, _自然语言场景())

    assert diagnostics["strategy"] == "direct"
    assert "first_unpaintable_terms" not in diagnostics


def test_覆盖式语言声明不得写进拼接器():
    """反向锁：语言约束的单一真源是 Profile 文本；拼接器只拼顺序，不叠加语言指令。

    真机取证显示预设段会带来 8.7k 字的中文语言要求，此时「顺手在拼接处再补一句语言指令」
    很自然——但那会让同一条约束出现两份、且离生成位置更远，故此处钉死边界。
    """
    import inspect

    source = inspect.getsource(profiles.system_with_preset)

    assert _语言声明片段 not in source
    assert "必须全部使用英文" not in source


@pytest.mark.parametrize("profile", ["natural_language", "niji_sections"])
def test_语言违例稿记下CJK占比供trace区分根因(profile):
    """语言违例的**观测事实**：`first_cjk_ratio` 必须能把「整篇中文叙事」与「零星中文残留」
    分开——两者的修法完全不同（远因改 system / 近因改本地清洗）。

    两档的**触发入口不同**，故 raw 形态也不同：natural_language 只要原文含中文即违例；
    niji_sections 的输入契约是 JSON，必须用规格四字段才会走到语言判据
    （非 JSON 四行文本会被 `_json_object` 解析成空字段 ⇒ 只剩段数/空行错）。
    """
    cjk_prose = "傍晚的走廊光线从窗沿倾泻进来，公告板上密密麻麻的纸张因微风悄悄翻动一角。"
    if profile == "natural_language":
        raw = cjk_prose
    else:
        raw = json.dumps({
            "subject": cjk_prose, "style": cjk_prose, "additions": cjk_prose,
            "suffix": "--ar 2:3 --niji 6",
        }, ensure_ascii=False)

    prompt, diagnostics = _补丁生成(profile, raw, {**_scene(), "wardrobe": ""})

    assert diagnostics["strategy"] == "fallback"
    assert "纯英文" in "；".join(diagnostics["first_errors"])
    assert diagnostics["first_cjk_chars"] > 0
    assert diagnostics["first_cjk_ratio"] > 0.5      # 整篇中文叙事，不是零星残留
    assert prompt.isascii()                          # 兜底产物仍是纯英文


def test_合格稿不写CJK观测字段():
    """零回归：稿子合格时不得凭空多出观测字段（下游按存在性判读）。"""
    _prompt, diagnostics = _补丁生成("natural_language", _自然语言合格稿, _自然语言场景())

    assert diagnostics["strategy"] == "direct"
    assert "first_cjk_chars" not in diagnostics
    assert "first_cjk_ratio" not in diagnostics

# ---------------------------------------------------------------------------
# 词表动作误映射修正（2026-09-19 优化项⑦）
#
# 旧规则一条：`取出|拔出|抽出|移除 → removing the visible object`（粗粒度、按「移除物件」写）。
# 实测（854 段真机语料，三份 test 文本）四源词只命中 3 段，且**没有一段是「取出容器里的物件」**：
#   L1「要拔出来了。」/ L3「我悄悄抽出了正在抚弄阴阜的手指。」/ L3「我猛地抽出了正扒开世熙臀部的手指。」
# ⇒ 产出的是**与画面无关**的串（NSFW 语境还会被读成「移除遮挡物」）。
# 放大效应：规则表是**并列全扫描、无优先级**（粗串不会被更具体的串压掉）；优化项⑤ 的补丁降级
# 又经 complete_field_coverage 把它**嫁接进模型稿**（同轮追加「In this instant, the concrete
# action is …」）⇒ 本该干净的模型稿被污染。修法＝按词义拆成三条各表一个可见动作。
# ---------------------------------------------------------------------------

_事故串 = "removing the visible object"


def test_词表动作把抽出身体部位映射为可画动作而非移除物件():
    """真机 L3 两段实锤：这里是「把手指抽出来」，不是「把物体移开」。
    第 17 刀（2026-09-20 用户实锤）：`withdrawing what was sheathed`（抽出鞘中之物）读起来
    与画面无关 ⇒ 手指语境改为 `withdrawing the fingers`。"""
    assert profiles._scene_action_tags(
        {"narrative": "我悄悄抽出了正在抚弄阴阜的手指。"}
    ) == ["withdrawing the fingers"]
    assert profiles._scene_action_tags(
        {"narrative": "我猛地抽出了正扒开世熙臀部的手指。"}
    ) == ["withdrawing the fingers"]


def test_词表动作把拔出映射为抽出而非移除物件():
    """真机 L1 实锤：「要拔出来了。」（插入语境）⇒ 不是「移除可见物体」。
    第 17 刀：无对象可指时用中性 `pulling out`，不再用「抽出鞘中之物」。"""
    assert profiles._scene_action_tags({"narrative": "「要拔出来了。」"}) == [
        "pulling out"
    ]


def test_词表动作把取出映射为从容器取出():
    """「取出」的**真义**是「从容器里拿出来」，与「抽出被插入物」不是一回事 ⇒ 必须分开。"""
    assert profiles._scene_action_tags(
        {"narrative": "他从匣子里取出一枚玉佩。"}
    ) == ["taking an object out of a container"]


def test_词表动作把移除映射为移开障碍物():
    assert profiles._scene_action_tags(
        {"narrative": "他移除了挡在门口的木箱。"}
    ) == ["moving an obstruction aside"]


def test_词表动作不再产出移除可见物体的事故串():
    """事故串防线：四源词的任一用法都不得再产出 `removing the visible object`。"""
    for narrative in (
        "「要拔出来了。」",
        "我悄悄抽出了正在抚弄阴阜的手指。",
        "我猛地抽出了正扒开世熙臀部的手指。",
        "他从匣子里取出一枚玉佩。",
        "他移除了挡在门口的木箱。",
        "她拔剑出鞘。",
    ):
        tags = profiles._scene_action_tags({"narrative": narrative})
        assert _事故串 not in tags, narrative


# ---------------------------------------------------------------------------
# ★1 凭空视觉装置（2026-09-20 生产链实锤，第 22 刀）
#
# 现场：L1·anima 窗口 44 字、**没有镜子**，成稿却写 tag `wall mirror reflection`
# ＋ 文段 `Behind her, a wall mirror catches a blurred reflection of her face,
# duplicating the backward glance into a layered loop`。
# 两层成因（缺一不可）：
#   ① 校验侧**鼓励**编装置：`_anima_contract_errors` 要求 `visual_hook` 命中
#      `_ANIMA_VISUAL_DEVICE_RE`——而该表把**需要新反光面/材质的词**（mirror / reflection /
#      refraction / translucent）与**处理方式词**（contrast / perspective / shadow /
#      foreground）混在同一张「必须命中」清单里 ⇒ 窗口无镜子时「编一面镜子」是模型的
#      **合规解**（模型不是写坏了，是在按清单交作业）；
#   ② 事实边界只有「条目事实必须落实」方向（`_visual_fact_errors`），**没有「新增项」闸门**
#      ——地点/道具/光源条款只写在 system 提示的【事实边界】里，模型忽略后无机械兜底。
# 修法（两处配套，通用）：
#   ① `_ANIMA_VISUAL_DEVICE_RE` 只留「不引入新物件即可满足」的构图/处理方式词（消除动机）；
#   ② 新增 `_unsourced_visual_device_errors` 出处闸（三档共用，与 `_visual_fact_errors`
#      同层、方向互补：一个管「漏」、一个管「加」），按**类**管而非列禁词表。
# ---------------------------------------------------------------------------

_无出处装置_scene = {"profile": "anima_tags", "rating": "nsfw",
                     "narrative": "她背靠着墙壁，臀部高高翘起，回头看了我一眼。"}


def test_成稿凭空写镜子判红():
    """★1 主路径：窗口无镜子而写 `wall mirror reflection` ⇒ 必须判红（进 repair 重写），
    不得静默放行。"""
    prompt = (
        "masterpiece, best quality, 1girl, solo, adult, arched back, wall mirror reflection, "
        "cinematic shading, depth of field, 4 by 3 aspect ratio,\n"
        "Behind her, a wall mirror catches a blurred reflection of her face as she keeps "
        "looking back over her shoulder."
    )

    errors = profiles._errors("anima_tags", prompt, _无出处装置_scene)

    assert any("视觉装置" in e for e in errors), errors


def test_原文有镜子时不算凭空():
    """通用性锁：出处判定看**输入画面事实**（appearance/wardrobe/narrative），
    原文写了镜子/倒影就必须放行——闸门管「无出处」，不管词本身。"""
    scene = {"profile": "anima_tags", "rating": "nsfw",
             "narrative": "她站在镜子前，镜中映出她的倒影。"}
    prompt = (
        "masterpiece, best quality, 1girl, solo, adult, mirror reflection, "
        "cinematic shading, depth of field, 4 by 3 aspect ratio,\n"
        "A wall mirror catches a blurred reflection of her face while she keeps looking back."
    )

    assert profiles._errors("anima_tags", prompt, scene) == []


def test_抽象构图词不需要出处():
    """构图/处理方式词（前景/对比/透视/阴影/负空间）是画面语言、不引入新物件 ⇒ 永远放行。"""
    scene = {"profile": "natural_language", "rating": "sfw", "narrative": "她站在空教室里。"}
    prompt = ("A woman stands in the foreground with strong contrast and long shadows, "
              "negative space gathering around her silhouette.")

    assert profiles._errors("natural_language", prompt, scene) == []


def test_无出处灯具与屏幕同样判红():
    """通用性锁：闸门按**类**管，不止镜子——灯饰／屏幕／相框等新物件一律要求出处。"""
    scene = {"profile": "niji_sections", "rating": "sfw", "narrative": "她坐在课桌前。"}
    prompt = (
        "1girl, solo, sitting at a desk\n"
        "soft anime style\n"
        "a chandelier above her, a monitor glowing in the dark\n"
        "--ar 3:4"
    )

    errors = profiles._errors("niji_sections", prompt, scene)

    assert errors, "无出处的吊灯与屏幕必须判红"
    assert any("视觉装置" in e for e in errors), errors


def test_原文有灯时灯具放行():
    """边界：原文明确写了灯 ⇒ 同一条规则必须放行（否则会变成「禁词表」而不是出处闸）。"""
    scene = {"profile": "natural_language", "rating": "sfw",
             "narrative": "教室顶上的荧光灯惨白地亮着，她坐在课桌前。"}
    prompt = "A woman sits at a desk under a cold fluorescent lamp, shadows pooling beneath the table."

    assert profiles._errors("natural_language", prompt, scene) == []


def test_三档共用同一装置出处闸():
    """三档（anima/natural/niji）走**同一份**出处闸（与 `_visual_fact_errors` 同层），
    避免只治一档；且不依赖任何专有名词。"""
    scene = {"rating": "nsfw", "narrative": "她背靠着墙壁。"}
    prompts = {
        "anima_tags": ("masterpiece, best quality, 1girl, solo, adult, mirror reflection, "
                       "cinematic shading, depth of field, 4 by 3 aspect ratio,\n"
                       "A wall mirror catches a blurred reflection of her face in dim light."),
        "natural_language": ("A woman leans back against the wall, her mirror image doubled "
                             "in the glass beside her."),
        "niji_sections": ("1girl, solo, leaning back\nsoft anime style\n"
                          "a wall mirror reflection behind her\n--ar 3:4"),
    }

    for profile, prompt in prompts.items():
        errors = profiles._errors(profile, prompt, scene)
        assert any("视觉装置" in e for e in errors), (profile, errors)


def test_视觉装置必需清单不再索要具体道具():
    """动机侧修复：`visual_hook` 的「必须命中」清单若把**需要新反光面/材质的词**与
    处理方式词混列，窗口无镜子时「编一个装置」就是合规解。拆表后 hook 只需给出
    **构图/处理方式**词——不引入任何新物件也能满足。"""
    hook_probe = "The composition uses deep negative space and a strong diagonal leading line."

    assert profiles._ANIMA_VISUAL_DEVICE_RE.search(hook_probe), "构图/处理方式词必须仍在必需清单里"
    # 无道具的合规路径（本刀的核心意图）：只写镜头与构图动词也能过 hook 校验。
    assert profiles._ANIMA_VISUAL_DEVICE_RE.search("a low angle frames the decisive glance"), \
        "模型必须有一条不编装置的合规路径"
    for prop in ("wall mirror reflection", "a translucent screen beside her",
                 "a refraction through the glass"):
        assert not profiles._ANIMA_VISUAL_DEVICE_RE.search(prop), \
            f"需要新反光面/材质的词必须已移出「必须命中」清单：{prop}"


def test_无出处灯泡与荧光灯具同样判红():
    """真机实锤（`_tmp/item22` · L1·natural）：窗口 44 字、原文无任何灯具，成稿却写
    `The lighting comes from a single dim overhead bulb`。闸门按「光源类」管
    ⇒ 灯泡与吊灯同罪（否则按词管就成了禁词表）。"""
    scene = {"profile": "natural_language", "rating": "nsfw", "narrative": "她背靠着墙壁。"}
    prompt = ("A woman presses her back against the wall while the lighting comes from a single "
              "dim overhead bulb above her.")

    errors = profiles._errors("natural_language", prompt, scene)

    assert any("视觉装置" in e for e in errors), errors


def test_动词式装置提及不算凭空():
    """真机实锤（`_tmp/item22c` · L3·niji）：`the primary adult character mirrors the same pose`
    里的 `mirrors` 是**动词**（＝同一姿势），不是新镜子 ⇒ 不得判红（否则白跑一次 repair，
    而 repair 之后模型还会再写一遍同一个动词 ⇒ 一路红到补丁降级）。"""
    scene = {"profile": "niji_sections", "rating": "nsfw", "narrative": "两名女子并肩跪着。"}
    prompt = ("On the right, the primary adult character mirrors the same pose, hands spreading her "
              "own cheeks.\nsoft anime style\na warm dim interior behind them\n--ar 3:4")

    assert profiles._errors("niji_sections", prompt, scene) == []


def test_限定词与mirrors之间只隔一个名词的动词式不算凭空():
    """第 30 刀（2026-09-21 真机实锤：**第 22 刀那层补丁没打穿**）。

    真机原句 `…on the right mirrors the same pose`——限定词 `the` 与动词 `mirrors` 之间
    **只隔一个名词**（`right`／`left`），旧式 `(?:\\w+\\s+){0,2}` 照样命中
    ⇒ L3·natural_language 首轮＋repair **各红一次**（零 LLM 复跑实证：`the right mirrors` /
    `the left mirrors` 双命中；上面那条 `test_动词式装置提及不算凭空` 之所以一直是绿的，
    只是因为它那句隔了 3 个词、**恰好**躲开 `{0,2}`）。

    定案＝补**宾语侧否定前瞻**：`mirror(s)` 作**动词**时必然接宾语
    （`mirrors the same pose` / `mirror each other`），作**名词**时后面接谓语/介词/句读
    （`a wall mirror catches` / `the mirror on the wall` / `… the mirror.`）。
    ⚠ `this|that` 刻意不进宾语清单（`a wall mirror that catches …` 是关系从句＝名词式，必须判红）
    ⇒ 两害相权保**召回**：放走编造镜面＝★1 原病，比白跑一次 repair 严重。
    """
    scene = {"profile": "natural_language", "rating": "nsfw", "narrative": "两名女子并肩跪着。"}
    # 动词式：后紧跟宾语 ⇒ 不是新镜子，不得判红
    for verb_form in (
        "The primary adult character on the right mirrors the same pose, hands spreading her own cheeks.",
        "The second adult character on the left mirrors the same pose as she kneels.",
        "Their hips mirror each other while the two of them kneel side by side.",
        "The second character mirrors her posture and her rhythm.",
    ):
        errors = profiles._errors("natural_language", verb_form, scene)
        assert not any("视觉装置" in e for e in errors), (verb_form, errors)
    # 名词式：后接谓语 / 介词 / 关系从句 / 句读 ⇒ 照旧判红（召回不能丢）
    for noun_form in (
        "She kneels beside a wall mirror that catches a blurred reflection of her face.",
        "She kneels in front of the mirror on the wall.",
        "Behind her a wall mirror catches a blurred reflection of her face.",
    ):
        errors = profiles._errors("natural_language", noun_form, scene)
        assert any("视觉装置" in e for e in errors), (noun_form, errors)


def test_视觉装置必需清单容忍词形变化():
    """真机实锤（`_tmp/item22b` · L3·anima 首轮 hook）：`mirroring each other's posture …
    in a symmetrical V-shaped display` 语义上完全合格，却因表里只有 `symmetry`（无
    `symmetrical`）被判「没写装置」⇒ 白跑一次 repair。清单只判「有没有写出构图/处理方式」，
    不做拼写考试；道具词被排除是**词义**判断，不是词形问题。"""
    for hook in (
        "Two women mirroring each other's posture in a symmetrical V-shaped display",
        "a contrasting focal point created by the reaching hand",
        "repeated highlights running along the two arched spines",
        "the occluded rear figure reduced to a silhouette",
    ):
        assert profiles._ANIMA_VISUAL_DEVICE_RE.search(hook), hook


# ---------------------------------------------------------------------------
# 第 31 刀（2026-09-21 用户实锤，五项）
#   ① 重复词条（被重复的 tag 权重虚高）  ② `1girl` + `2girls` 并存
#   ③ anima 多人体位动作词多而不具体（合并成一条）  ④ 回眸媚态没进文段  ⑤ 视线与朝向口径
# ---------------------------------------------------------------------------

def _anima_raw(content: str) -> str:
    return json.dumps({
        "visual_hook": "a mirrored V-shaped framing that turns the two backs into one diagonal",
        "primary_focus": "the two raised hips meeting the directional light",
        "supporting_elements": ["one directional light", "shadowed bedding"],
        "content": content,
    })


def _anima_场景(**extra) -> dict:
    scene = {
        "narrative": "两侧同时献上小穴和后庭，争着要他插进来，屁股轻轻晃着。",
        "appearance": "",
        "draft_prompt": "",
        "locale": "",
        "actors": ["世熙", "宥夏"],
        "rating": "nsfw",
    }
    scene.update(extra)
    return scene


def test_第31刀_anima首行同名tag折叠且质量行不重复():
    """真机 L3·anima 首行：`shy … shy`、`wet pussy … wet pussy`，且质量词与程序前置行
    **整段重复**（模型自带 `masterpiece, best quality, absurdres`）⇒ 同一 tag 的权重被推高。
    判据是 **tag 粒度全等**（大小写/空白归一）折叠，保序、保首次拼写。"""
    raw = _anima_raw(chr(10).join((
        "masterpiece, best quality, 1girl, shy, shy, wet pussy, wet pussy, "
        "absurdres, red dress, low angle, rim lighting, close-up",
        "A lone adult woman kneels in a warm room.",
    )))

    tags = profiles._normalize("anima_tags", raw, _anima_场景()).splitlines()[0]
    entries = tags.split(", ")

    for duplicated in ("shy", "wet pussy", "masterpiece", "best quality", "absurdres"):
        assert entries.count(duplicated) == 1, (duplicated, tags)
    assert entries.count("red dress") == 1
    assert "close-up" in tags  # 非重复 tag 一个都不许丢（首行末尾的分隔逗号是既有格式）


def test_第31刀_重复tag折叠不做同义合并():
    """⚠ 反向锁：只折叠**全等** tag，不许做同义合并（合并同义＝改画面事实，越权）。"""
    raw = _anima_raw(chr(10).join((
        "1girl, shy, bashful, low angle, low-angle shot, close-up",
        "A lone adult woman kneels in a warm room.",
    )))

    tags = profiles._normalize("anima_tags", raw, _anima_场景()).splitlines()[0]

    assert "shy" in tags and "bashful" in tags
    assert "low angle" in tags and "low-angle shot" in tags


def test_第31刀_多人帧不再同时出现1girl与2girls():
    """真机 L3·anima 首行 `1girl, 2girls` 并存 —— `1girl` 是 krea2/solo 时代的习惯残留。
    定案：保留多人计数词（`2girls` 是合同硬要求、不得升格 `3girls`），剔除低位词与 `solo`。"""
    raw = _anima_raw(chr(10).join((
        "masterpiece, 1girl, 2girls, solo, two-shot, red dress, low angle, rim lighting, close-up",
        "Two adult women kneel side by side in a warm room.",
    )))

    tags = profiles._normalize("anima_tags", raw, _anima_场景()).splitlines()[0]
    entries = tags.split(", ")

    assert "2girls" in entries
    assert "1girl" not in entries
    assert "solo" not in entries
    assert "3girls" not in entries


def test_第31刀_单人帧的人数词逐字节不动():
    """⚠ 零回归锁：没有多人计数词时（单人定格帧），`1girl, solo` 必须原样保留。"""
    raw = _anima_raw(chr(10).join((
        "1girl, solo, red dress, low angle, rim lighting, close-up",
        "A lone adult woman kneels in a warm room.",
    )))

    tags = profiles._normalize("anima_tags", raw, _anima_场景(actors=["世熙"])).splitlines()[0]

    assert "1girl" in tags.split(", ")
    assert "solo" in tags.split(", ")


def test_第31刀_回眸媚态事实有出处才触发且落进文段():
    """④ 用户实锤「回头妩媚勾引的看向镜头的神情没有在文段环节表示出来」。
    源文有出处（`回头看向我` / `回头望来的世熙和宥夏`）⇒ 属「有出处没落实」。
    产出走**名词短语**形态 ⇒ 落 `depicted with` 状态槽，能真正进第二行。"""
    scene = _anima_场景(narrative="宥夏回头看向我，眼神妩媚。")
    fact = "a coquettish come-hither look directed back over her shoulder"

    assert fact in profiles._scene_action_tags(scene)
    prompt = chr(10).join((
        "masterpiece, best quality, 1girl, red dress, low angle, rim lighting, close-up",
        "A lone adult woman kneels in a warm room under directional light.",
    ))

    repaired, _ledger = profiles.complete_field_coverage("anima_tags", prompt, scene)
    _tags_line, prose_line = repaired.splitlines()[:2]

    assert fact in prose_line


def test_第31刀_孤立妩媚不得凭空补神情():
    """⚠ 反向锁（第 22 刀同族）：没有回眸/媚眼语境时，不得凭空补神态事实。"""
    scene = _anima_场景(narrative="她穿着一身妩媚的红裙站在窗边。")

    assert "a coquettish come-hither look directed back over her shoulder" not in (
        profiles._scene_action_tags(scene)
    )


def test_第31刀_视角条款补视线与朝向口径():
    """⑤ 用户定案：**第一人称＝看向镜头；第三人称＝看向主人公**。

    ⚠ **第 33 刀（2026-09-21）收窄**：本定案里「**身体朝向**也必须指向叙事者」那一半被真机推翻
    （L3·NSFW多人 natural 把两名**背向**撅臀的角色写成「面向 + 跪着」⇒ 空间关系读成面对面）⇒
    身体朝向改由**画面事实**决定，见
    `test_第33刀_视角条款把背向式姿势的身体朝向交回画面事实`。这里只锁**仍然成立的那一半**。
    """
    third = profiles._viewpoint_clause({"viewpoint": "third_person"})
    first = profiles._viewpoint_clause({"viewpoint": "first_person"})

    assert "指向叙事者本人" in third
    assert "不得用 toward the camera" in third
    assert "指向**镜头**" in first
    assert "不得把叙事者另写成画面里的第三方人物" in first


def test_第31刀_视角缺失时提示词逐字节不变():
    """⚠ 零回归锁：`viewpoint` 缺失/非法 ⇒ 条款为空串，三档提示词等于旧行为。"""
    base = profiles._system("anima_tags", {"rating": "nsfw"})
    illegal = profiles._system("anima_tags", {"rating": "nsfw", "viewpoint": "auto"})

    assert base == illegal
    assert "视线与朝向" not in base


def test_第31刀_兜底路径不追加同部位已写动作():
    """③ 后半：第 29 刀的同部位闸接入兜底路径。判据是**逐 tag 内**「部位＋动词」共现，
    不是整串共现 —— 否则 `kneeling, soft surface` 这类不相干组合会把体位词误吞掉。"""
    fact = "both hands spreading her buttocks apart"

    assert profiles._tag_already_actions_part(
        "both hands gripping and pulling apart their own buttocks, low angle", fact,
    )
    assert not profiles._tag_already_actions_part("kneeling, soft surface", fact)
    assert not profiles._tag_already_actions_part("", fact)


# ── 兜底波动归因的两处确定性收口（同属第 31 刀）──────────────────────────────

def test_第31刀_JSON字符串裸换行经补救通道可解析():
    """真机 `L2·单人锚点[63]`（anima）首轮＋repair 两轮 raw 都是**合法形状**的 JSON，唯一毛病是
    `content` 字符串里带了**未转义的真实换行** ⇒ `json.loads` 抛 ⇒ `_json_object` 返回 {} ⇒
    掉进「非 JSON」分支（首行变成 `{`）⇒ 两行结构判据级联全红 ⇒ 白烧一次 repair 再掉兜底。
    归因结论：这一格**不是采样波动**，是解析接缝。"""
    broken = (
        '{"visual_hook": "a mirrored V-shaped framing turning the two backs into one diagonal",'
        ' "primary_focus": "the two raised hips",'
        ' "supporting_elements": ["one directional light"],'
        ' "content": "1girl, red dress, low angle,' + chr(10) + 'close-up"}'
    )

    value = profiles._json_object(broken)

    assert value, "裸换行必须走补救通道解析出来"
    assert chr(10) in str(value["content"])
    assert profiles._anima_contract_errors(broken) != ["必须输出包含艺术决策字段的JSON对象"]


def test_第31刀_JSON补救通道不放宽其他残破形状():
    """⚠ 反向锁：补救只治「字符串内裸控制字符」。尾逗号这类形状照旧 fail-closed 返回 {}，
    否则等于放宽结构约束、把「模型没按协议写」掩盖掉。"""
    for still_broken in (
        '{"visual_hook": "x", "primary_focus": "y",}',
        '{"visual_hook": "x" "primary_focus": "y"}',
        "{'visual_hook': 'x'}",
    ):
        assert profiles._json_object(still_broken) == {}, still_broken


def test_第31刀_JSON合法输入逐字节不变():
    valid = json.dumps({"visual_hook": "x", "content": "a" + chr(10) + "b"})

    assert profiles._json_object(valid) == json.loads(valid)


def test_第31刀_镜面闸收裸名词tag与连字符定语():
    r"""用户实锤两处漏判：① 裸名词 tag `wall mirror reflection` 无限定词 ⇒ 旧式 `det + …` 整条
    不入闸；② `a full-length mirror …` 的连字符让 `(?:\w+\s+)` 跨不过去。定案：限定词改可选、
    词形放宽 `[\w-]+` —— 按「漏＝编造」优先于「多＝白跑一轮」的红线口径。"""
    scene = {"profile": "natural_language", "rating": "nsfw", "narrative": "两名女子并肩跪着。"}

    for text in (
        "wall mirror reflection, low angle, 2girls, detailed background",
        "a full-length mirror catches a blurred reflection of her face.",
        "She kneels beside a wall mirror that catches a blurred reflection.",
    ):
        errors = profiles._errors("natural_language", text, scene)
        assert any("视觉装置" in e for e in errors), (text, errors)
    for text in (
        "The primary adult character on the right mirrors the same pose.",
        "Their hips mirror each other while the two of them kneel side by side.",
        "The black lacquer lid acts as a dark mirror on the chest.",
    ):
        errors = profiles._errors("natural_language", text, scene)
        assert not any("视觉装置" in e for e in errors), (text, errors)


def test_第31刀_镜面定语误报是已知取舍():
    """⚠ 特性锁：`a mirror pose`（mirror 作**定语**）在词面上与裸名词真串 `a wall mirror
    reflection` **完全同形**。要治它只能给 mirror 的接续名词开槽（＝禁列禁词表，红线）或要求
    后接谓词/介词（＝掉召回）。按红线「宁可白跑一轮 repair，不可放走编造镜面」⇒ **刻意保留**。
    本锁把它钉成已知取舍：若哪天变绿，说明有人收紧了判据，**必须回来先复核召回面**。"""
    scene = {"profile": "natural_language", "rating": "nsfw", "narrative": "两名女子并肩跪着。"}

    errors = profiles._errors(
        "natural_language", "The composition reads as a mirror pose with her hips raised.", scene,
    )

    assert any("视觉装置" in e for e in errors), "误报被意外治好 ⇒ 先复核召回面再改这条锁"


# ---------------------------------------------------------------------------
# 第 32 刀（2026-09-21 用户实锤）：兜底**逐人分述**的名单必须与契约同源
#
# 真机现场（`D:\pytest-tmp\i30` · L3·NSFW多人 / anima_tags 兜底稿）：
#   取材窗口解析出**在场 3 人**（宥夏 / 世熙 / 泰宇老师＝叙事者），判据 `frame_subjects`
#   只判**画面主体 2 人** ⇒ 契约 `_contract_subject_names` = 2。但兜底分述原先按 `actors`
#   逐人写 ⇒ 多出第三条 `An additional adult character at the center of the frame is present
#   in frame.`。用户原话：「tag 都没有出现第三人…那你**不该出现第三方人物**，而且就算出现
#   第三方人物，那你把主角放在中央，那不是**遮挡**了两名女主角翘起臀部并掰开臀部的动作」。
#   同稿的人数措辞却是 `The two adult characters' …`（来自 `_contract_subject_names`）
#   ⇒ **稿内自相矛盾**：写了 3 个人、却说 two。
#
# 这正是红线 23（优化项⑥）声称已消除的「双口径」在**逐人枚举**这一段没真消——
# 当时统一的只是「人数措辞」与「兜底是否走多人分支」两处。
#
# ⚠ **实施纪律（务必别改成「名单全换成契约」就完事）**：句级归属**仍必须按在场全集**做。
#   否则点名第三人的句子会因「不在输出名单里」被判成无名句，紧跟的**代词句**就被就近误配给
#   在场女性（`泰宇老师站在床边。` + `他把手指抽了出来。` ⇒ 女主角吃到 `withdrawing the fingers`）。
#   正确形态＝「**归因用全集、输出收敛到契约**」。
# ---------------------------------------------------------------------------


def test_第32刀_判据本帧主体两人时兜底分述只列两人():
    """真机 L3：在场 3 人、画面主体 2 人 ⇒ 兜底分述必须**只写 2 条**。"""
    scene = _多人动作场景(
        "\n\n".join([
            "她把双手贴到臀肉上，小心翼翼地掰开屁股。",
            "宥夏也掰开臀肉，连滴答淌水的小穴和后庭都露了出来。",
            "两侧同时献上小穴和后庭，争着要他插进来，屁股轻轻晃着。",
        ]),
        ["宥夏", "世熙", "泰宇老师"],
        frame_subjects=["宥夏", "世熙"],
    )

    sentences = profiles._fallback_multi_actor_sentences(scene)

    assert len(sentences) == 2, sentences
    assert not any("additional" in sentence for sentence in sentences), sentences
    assert not any("at the center of the frame" in sentence for sentence in sentences), sentences


def test_第32刀_不在画面的第三人其代词句不得误配给在场角色():
    """⚠ 反向锁：把输出名单收敛到契约主体时，**归因仍按在场全集**——
    否则 `他`（＝叙事者）的代词句会被就近塞给在场的女主角（第 21/25 刀跨句串味同族）。"""
    scene = _多人动作场景(
        "\n\n".join([
            "宥夏掰开臀肉，露出滴答淌水的小穴。",
            "泰宇老师站在床边。",
            "他把手指抽了出来。",
        ]),
        ["宥夏", "世熙", "泰宇老师"],
        frame_subjects=["宥夏", "世熙"],
    )

    details = {name: actions for name, _facts, actions in profiles._actor_visual_details(scene)}
    sentences = " ".join(profiles._multi_actor_sentences(scene))

    assert "泰宇老师" not in details, "输出名单必须收敛到画面主体"
    assert "withdrawing the fingers" not in sentences, "叙事者的代词句不得落到女主角名下"


def test_第32刀_无正向证据时逐人分述逐字节不变():
    """零回归前提：`subjects` / `frame_subjects` 都缺 ⇒ 契约退回 `actors`
    ⇒ 本刀对「无正向证据」的帧**零影响**（该写第三条的还是第三条）。"""
    scene = _多人动作场景(_L3_代词句窗口, ["世熙", "宥夏", "泰宇老师"])

    sentences = profiles._multi_actor_sentences(scene)

    assert len(sentences) == 3, sentences
    assert sentences[2].startswith("An additional adult character"), sentences[2]


def test_第32刀_判据也说三人时兜底照旧三条():
    """受保护用例：**真三人帧**（判据也说 3 人）不得被本刀误缩成两条。"""
    scene = _多人动作场景(
        _L3_代词句窗口, ["世熙", "宥夏", "泰宇老师"],
        frame_subjects=["世熙", "宥夏", "泰宇老师"],
    )

    sentences = profiles._fallback_multi_actor_sentences(scene)

    assert len(sentences) == 3, sentences


# ---------------------------------------------------------------------------
# 第 33 刀（2026-09-21 · 真机 i33 跑批后的收尾刀，全部按真机 raw 定案）
#   ① anima 的 `content` **没有内部句点** ⇒ 旧实现三条切分路径全拿不到 prose ⇒ 输出退化成
#      1 行 ⇒「必须正好两行」三连判死 ⇒ 掉词表模板，把模型 1715/1826 字的真实画面全数丢掉。
#      两种真机形态：`tags ; 散文句`（L3·单人锚点[386]）与**纯逗号短语**（L1·NSFW单人）。
#   ② 模型按规格把四字段写全、只是**没加花括号**（字段式回复）⇒ 旧实现 `_json_object` 返回 {}
#      ⇒ 整段原文被当 tags 吞进首行（该格首行 2702 字）、而结构判据只看「两行 + ≥6 tags」
#      ⇒ **一条格式红都不报** ⇒ patched 交付 4833 字半 JSON 残留（L3·NSFW多人 tp）。
#   ③ niji 四段：内容段末尾统一 `", "`（用户口径「保持良好的习惯」）。
#   ④ 补丁路径原先**绕过装置出处闸** ⇒ 凭空镜面随补丁交付（红线 22）。
#   ⑤ 视角条款把**身体朝向**一刀切钉成「指向叙事者」⇒ 背向式（撅臀掰开）姿势被写成
#      「面向 + 跪着」，空间关系读成面对面（L3·NSFW多人 natural_language）。
# ---------------------------------------------------------------------------

_字段式回复 = (
    "visual_hook: a low angle pools the light across her shoulders and leads the eye inward.\n\n"
    "primary_focus: her own hands spreading her cheeks apart.\n\n"
    'supporting_elements: ["a soft glow behind her", "creased sheets beneath her knees"]\n\n'
    'content: "two adult women kneeling side by side, both pulling their own cheeks apart, '
    'glistening skin and flushed pink tones. The two women present themselves side by side '
    'for the standing figure, their backs arched and their glances meeting each other."'
)


def test_第33刀_字段式回复不带花括号也按规格解析():
    """② `字段: 值` 四字段齐全 ⇒ 认成等价对象（与 `{}` 形态**结构等价**）。"""
    value = profiles._labelled_fields_object(_字段式回复)

    assert sorted(value) == ["content", "primary_focus", "supporting_elements", "visual_hook"]
    assert isinstance(value["supporting_elements"], list), value["supporting_elements"]
    assert len(value["supporting_elements"]) == 2
    assert not value["content"].startswith('"') and not value["content"].endswith('"')
    assert not value["visual_hook"].startswith('"')
    assert profiles._anima_contract_errors(_字段式回复) == []


def test_第33刀_字段式回复缺字段时仍然failclosed():
    """准入必须窄：少一个规格字段 ⇒ 退回 `{}` ⇒ 与旧行为逐字节相同。"""
    broken = "\n".join(
        line for line in _字段式回复.splitlines() if not line.startswith("primary_focus"))

    assert profiles._labelled_fields_object(broken) == {}
    assert profiles._anima_contract_errors(broken) == ["必须输出包含艺术决策字段的JSON对象"]


def test_第33刀_普通tags行不得被字段式解析误认():
    tags = ("masterpiece, best quality, 1girl, long black hair, crimson eyes, a mirror in the room, "
            "standing, warm light, detailed background")

    assert profiles._labelled_fields_object(tags) == {}
    assert profiles._json_object(tags) == {}


def test_第33刀_字段式回复不得被当成tags吞进首行():
    """② 真机实锤：整段字段式原文进首行 ⇒ 交付半 JSON 残留。修后首行只能是 tags。"""
    prompt = profiles._normalize("anima_tags", _字段式回复, _scene())
    lines = prompt.splitlines()

    assert len(lines) == 2, lines
    for label in ("visual_hook", "primary_focus", "supporting_elements"):
        assert label not in prompt, prompt
    assert len([tag for tag in lines[0].split(",") if tag.strip()]) >= 6
    assert re.search(r"^[A-Za-z][^.?!]{24,}[.?!]", lines[1]), lines[1]


def test_第33刀_content用分号分隔tags与画面句时两行各得其所():
    """① 真机 L3·单人锚点[386]：`1girl, … ; The primary adult character …`。"""
    content = (
        "1girl, adult female, standing, back arched, buttocks thrust toward the camera, "
        "plump round buttocks, bare ass, soft flesh, no panties, from behind, pov; "
        "The primary adult character bends over with her back arched, her full buttocks pushed back "
        "toward the camera while she glances over her shoulder into the lens with an inviting gaze."
    )
    prompt = profiles._normalize("anima_tags", _anima_json(content), _scene())
    lines = prompt.splitlines()

    assert len(lines) == 2, lines
    assert "1girl" in lines[0] and "adult female" in lines[0]
    assert lines[1].startswith("The primary adult character bends over"), lines[1]
    assert "adult female" not in lines[1]
    assert not profiles._body_errors("anima_tags", prompt)


def test_第33刀_content为纯逗号短语无句点时仍须出两行():
    """① 真机 L1·NSFW单人：`content` 整个是一个句点都没有的逗号短语。"""
    content = (
        "close-up of a naked adult female character, her plump buttocks pressed against a bulletin board, "
        "her body trembling and covered in sweat, her hands weakly gripping the edge of the board, "
        "dramatic lighting, cinematic composition, anime style, 3:4 aspect ratio"
    )
    prompt = profiles._normalize("anima_tags", _anima_json(content), _scene())
    lines = prompt.splitlines()

    assert len(lines) == 2, lines
    assert lines[1].endswith("."), lines[1]
    assert not profiles._body_errors("anima_tags", prompt)


def test_第33刀_niji内容段末尾统一补逗号而参数段不动():
    """③ 用户口径：段与段的末尾加上 `", "`；`--` 参数段是 niji 参数串，不得加。"""
    raw = json.dumps({
        "subject": "a woman kneeling with her back arched",
        "style": "anime style, cel shading, warm palette",
        "additions": "low angle, soft rim light, shallow depth of field",
        "suffix": "--ar 4:3 --niji 6",
    })
    lines = profiles._normalize("niji_sections", raw, _scene()).splitlines()

    assert len(lines) == 4, lines
    assert lines[0].endswith(", ") and lines[1].endswith(", ") and lines[2].endswith(", ")
    assert lines[3] == "--ar 4:3 --niji 6"


def test_第33刀_词表兜底的niji四段末尾同样补逗号():
    """③ 同一口径：**词表兜底稿**也是 niji 四段 ⇒ `", "` 收尾必须一致。

    真机 i34 `L3·单人锚点[379]` 掉兜底时，兜底三段末尾没有 `", "`（只有 LLM 路径走
    `_niji_sections_join`）⇒ 同一档「合格稿有、兜底稿没有」自相矛盾。
    """
    lines = profiles._niji_scene_fallback(_scene()).splitlines()

    assert len(lines) == 4, lines
    assert lines[0].endswith(", ") and lines[1].endswith(", ") and lines[2].endswith(", ")
    assert lines[3].startswith("--ar "), lines[3]
    assert not lines[3].endswith(", "), lines[3]


def test_第33刀_补丁路径摘掉凭空装置后交付并记账():
    """④ 真机 fp anima：凭空镜面原样随补丁交付。修后 ⇒ 摘掉装置提及再交付。"""
    scene = _scene("nsfw")
    raw = (
        "masterpiece, best quality, 1girl, buttocks, mirror reflection, standing, warm light, "
        "detailed background,\n"
        "She kneels in the room with her buttocks raised, her own hands spreading them apart. "
        "A large mirror in front of her catches her reflection and shows her front to the viewer."
    )

    def generate(system, user):
        return raw

    diagnostics = {}
    out = profiles.generate("anima_tags", scene, generate, diagnostics)

    assert diagnostics["strategy"] == "patched", diagnostics
    assert diagnostics.get("patched_device_stripped"), diagnostics
    assert "mirror" not in out.lower(), out
    assert profiles._unsourced_visual_device_errors(out, scene) == []


def test_第33刀_摘不掉凭空装置时补丁必须作废():
    """④ 反向锁：整段散文都靠凭空装置立着 ⇒ 摘不动 ⇒ 只能作废，**不得**原样放行（红线 22）。"""
    scene = _scene("nsfw")
    prompt = (
        "masterpiece, best quality, 1girl, buttocks, standing, warm light, detailed background,\n"
        "She kneels facing a large mirror that catches her reflection. The mirror shows her spread "
        "body frontally to the viewer. Her eyes flick sideways toward the mirror."
    )
    pruned = profiles._drop_unsourced_device_mentions("anima_tags", prompt, scene)

    assert pruned.split("\n")[1].strip() == "", pruned
    assert profiles._body_errors("anima_tags", pruned) != []
    # 无论补丁是「作废」还是「摘干净后交付」，交付面都不得残留该装置。
    patched = profiles._patch_failed_prompt("anima_tags", prompt, scene)
    assert profiles._unsourced_visual_device_errors(patched, scene) == []


def test_第33刀_两轮都凭空装置的帧最终不得交付该装置():
    """④ 端到端不变量：同一份含凭空装置的稿两轮都回同样内容 ⇒ 交付里不得出现该装置。"""
    scene = _scene("nsfw")
    raw = (
        "masterpiece, best quality, 1girl, buttocks, mirror reflection, standing, warm light, "
        "detailed background,\n"
        "She kneels facing a large mirror that catches her reflection. The mirror shows her spread "
        "body frontally to the viewer. Her eyes flick sideways toward the mirror."
    )

    def generate(system, user):
        return raw

    diagnostics = {}
    out = profiles.generate("anima_tags", scene, generate, diagnostics)

    assert "mirror" not in out.lower(), out
    assert profiles._unsourced_visual_device_errors(out, scene) == []
    assert diagnostics["strategy"] in ("patched", "fallback"), diagnostics


def test_第33刀_视角条款把背向式姿势的身体朝向交回画面事实():
    """⑤ 真机 L3·NSFW多人 natural：两名**背向**撅臀的角色被写成「面向 + 跪着」。
    定案＝视线归叙事者/镜头，**身体朝向按画面事实**（背向式姿势＝背对）。"""
    third = profiles._viewpoint_clause({"viewpoint": "third_person"})
    first = profiles._viewpoint_clause({"viewpoint": "first_person"})

    assert "身体朝向按画面事实写" in third
    assert "背对叙事者本人" in third
    assert "面向叙事者" in third and "跪在他面前" in third
    assert "掰开臀瓣朝向哪一侧" not in third, "旧的一刀切口径必须消失"
    assert "身体朝向按画面事实写" in first
    assert "背对镜头" in first


def test_第33刀_无viewpoint时视角条款逐字节不出现():
    """零回归锁：`viewpoint` 缺失／非法 ⇒ 空串（三档 system prompt 逐字节不变）。"""
    assert profiles._viewpoint_clause({}) == ""
    assert profiles._viewpoint_clause({"viewpoint": "auto"}) == ""
    assert profiles._viewpoint_clause({"viewpoint": "first_person", "narrator": "甲"}).startswith(
        "【视角·第一人称】")


