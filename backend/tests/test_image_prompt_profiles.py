import json
import re

import pytest

from app.services import image_prompt_profiles as profiles


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


def _anima_json(content, *, visual_hook="red lacquer reflection frames the decisive glance",
                primary_focus="the reflected crimson eye", supporting_elements=None):
    return json.dumps({
        "visual_hook": visual_hook,
        "primary_focus": primary_focus,
        "supporting_elements": supporting_elements or ["silver crest", "pale silhouette"],
        "content": content,
    })


def test_krea2输出纯英文自然语言并剥离自检():
    body = (
        "A low-angle medium shot follows an adult woman in a red dress as she crosses the quiet bedchamber. "
        "Cool side light catches the moving folds and guides the eye from her face to her leading hand, while "
        "the room falls into restrained shadow and the doorway supplies clean negative space. " * 3
    ).strip()
    calls = []

    def generate(system, user):
        calls.append((system, json.loads(user)))
        return body + "\n\n——自检——\n自检: 致命项 9/9"

    prompt = profiles.generate("krea2", _scene("sfw"), generate)

    assert prompt == body
    assert "纯英文" in calls[0][0]
    assert "英文自然语言" in calls[0][0]
    assert "六个维度" in calls[0][0]
    assert "SFW" not in calls[0][0]
    assert "NSFW" not in calls[0][0]
    assert "——自检——" not in prompt
    assert "\n" not in prompt


def test_krea2不按分级切换模板而直接转译高潮内容():
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
        return body

    assert profiles.generate("krea2", _scene("nsfw"), generate) == body
    assert "纯英文" in seen["system"]
    assert seen["system"] == profiles._system("krea2", _scene("sfw"))
    assert "不得续写剧情" in seen["system"]
    assert "不得改变人物、动作、服装、关系、地点或剧情结果" in seen["system"]


@pytest.mark.parametrize("profile,raw,expected", [
    (
        "krea2",
        "A close medium shot keeps the woman's @(gripping)@ hand and torn red dress in the foreground. "
        "Side light follows her black hair, narrow eyes, and the exact contact point while the chamber "
        "recedes through controlled shadow and negative space. " * 2,
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
    body = (
        "A close medium shot places the gripping hand and torn red dress in the left foreground. "
        "Her established black hair and narrow eyes remain readable as directional side light follows "
        "the contact point, controlled fabric tension, and the chamber's receding negative space. " * 2
    )

    def generate(_system, user):
        seen.update(json.loads(user))
        return body

    assert profiles.generate("krea2", scene, generate) == body.strip()
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
    quality, content = prompt.splitlines()
    assert quality == profiles.ANIMA_QUALITY_TAGS
    assert content.startswith("1girl, solo, red dress")
    assert "Warm rim light separates" in content


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

    quality, content = prompt.splitlines()
    assert "explicit" in quality
    assert content.startswith("1girl, solo, dramatic composition, rim lighting")
    assert "visual focus" in content
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

    assert prompt.splitlines()[1].startswith("adult woman, black hair, red eyes")
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

    content = prompt.splitlines()[1]
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

    content = prompt.splitlines()[1]
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

    assert prompt.splitlines()[1] == designed_content
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
    assert prompt.splitlines()[1] == content


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
    assert prompt.splitlines() == [
        "A swordswoman standing in rain",
        "refined anime illustration",
        "cinematic rim light, dynamic framing",
        "--stylize 400 --chaos 8 --no text",
    ]


def test_自然语言模式保持完整段落而非tags():
    raw = "A swordswoman stands on rain-darkened stone steps. Cool backlight defines her silhouette from a low angle."
    assert profiles.generate(
        "natural_language", {**_scene(), "wardrobe": ""}, lambda _s, _u: raw,
    ) == raw


def test_主模型内联Anima提示词只做本地归一不再调用模型():
    prompt = profiles.normalize_inline(
        "anima_tags",
        "1girl, solo, red dress, low angle, rim lighting, shallow depth of field. "
        "The face and eyes receive the finest detail while the background falls into soft focus.",
    )

    assert prompt.splitlines() == [
        profiles.ANIMA_QUALITY_TAGS,
        "1girl, solo, red dress, low angle, rim lighting, shallow depth of field. "
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

    realistic = profiles.normalize_inline(
        "krea2", "Photorealistic live-action imagery. " + shared, {"rating": "sfw"},
    )
    anime = profiles.normalize_inline(
        "krea2", "Detailed anime illustration. " + shared, {"rating": "sfw"},
    )
    locked = profiles.normalize_inline(
        "krea2", "Live-action photography with realistic human skin and 3D rendering. " + shared,
        {"rating": "sfw"},
    )

    for prompt in (realistic, anime, locked):
        assert "Photorealistic" not in prompt
        assert "live-action" not in prompt
        assert "anime illustration" not in prompt
        assert "realistic human skin" not in prompt
        assert "3D rendering" not in prompt
        assert shared in prompt
    assert "physically coherent skin tones and material response" in locked


def test_格式不合格时带错误重写一次():
    outputs = iter([
        "too short",
        (
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
    assert prompt.startswith("A low-angle medium shot")
    assert len(users) == 2
    assert "上次输出未通过" in users[1]


def test_krea2重写后仍不完全合规也返回非空结果而不阻断生图():
    outputs = iter([
        "First draft is too short.",
        "The second draft preserves the decisive action but remains shorter than the preferred range.",
    ])

    prompt = profiles.generate(
        "krea2", {**_scene("sfw"), "wardrobe": ""}, lambda _s, _u: next(outputs),
    )

    assert prompt == "The second draft preserves the decisive action but remains shorter than the preferred range."


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
    assert "noise-free tonal transitions" in prompt
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
    valid = (
        "A portrait composition places the primary adult woman on the left third as she looks through the broken wall. "
        "Her glossy jet-black hair is gathered into a neat bun, with crimson lips and rosy cheeks under a composed mature gaze. "
        "A close medium three-quarter view keeps her face and hand sharp while the ruined enclosure recedes into cold haze. "
        "Side light follows the hair, skin, and worn fabric before falling into controlled shadow. "
        "The opening supplies directional negative space toward the outside path, with coherent anatomy, clean edges, stable perspective, and polished image fidelity."
    )

    def generate(_system, user):
        seen.update(json.loads(user))
        return valid

    assert profiles.generate("krea2", scene, generate) == valid
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
    vague = (
        "Portrait composition centered on the adult woman Leng Qingxue, with directional negative space toward the exit. "
        "Preserve Leng Qingxue's established facial structure, eye shape, hairstyle silhouette, body proportions, costume, "
        "and illustrated identity exactly as defined by the bound character model, with no replacement face or altered clothing. "
        "Use a close medium three-quarter view with shallow depth of field. Side light defines her face and hand while the dark "
        "interior recedes behind her. Maintain precise anatomy, coherent fabric tension, stable perspective, clean edges, controlled "
        "fine detail, high image fidelity, and complete polished illustration quality across the finished image."
    )
    concrete = (
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

    assert profiles.generate("krea2", scene, generate) == concrete
    assert len(calls) == 2
    assert "空泛" in calls[1]


@pytest.mark.parametrize("profile,raw", [
    (
        "krea2",
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
    valid = (
        "A low medium shot uses shallow depth of field and cool dawn backlight. "
        "The identified woman wears a torn purple gauze robe and rests one arm on the wooden rail. "
        "The rail forms a foreground frame while the abandoned village dissolves into morning mist."
    )
    assert profiles.normalize_inline("krea2", valid, {"rating": "nsfw"}) == valid


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
    assert "高图像保真度" in system
    assert "精确解剖" in system
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
    assert "不得为了填满栏目凭空创造" in system


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


@pytest.mark.parametrize("profile,required", [
    ("krea2", "一个纯英文自然语言段落"),
    ("anima_tags", "英文 tags + 英文关系描述"),
    ("natural_language", "一个纯英文自然语言段落"),
    ("niji_sections", "四段内容"),
])
def test_四种Profile提供主剧情同轮隐藏成稿合同(profile, required):
    instruction = profiles.inline_generation_instruction(profile)

    assert required in instruction
    assert "角色姓名" in instruction
    assert "具体外貌" in instruction
    assert "当前服装" in instruction
    assert "高潮动作" in instruction
    assert "LoRA" in instruction


@pytest.mark.parametrize("profile", profiles.PROFILE_IDS)
def test_同轮隐藏Profile预留输出预算(profile):
    assert profiles.inline_output_token_reserve(profile) >= 700


def test_未知profile拒绝():
    with pytest.raises(ValueError, match="未知提示词模式"):
        profiles.generate("unknown", _scene(), lambda _s, _u: "x")
