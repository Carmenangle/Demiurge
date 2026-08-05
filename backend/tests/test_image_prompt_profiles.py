import json

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


def test_krea2_sfw使用全中文单段并剥离自检():
    body = "采用低角度广角镜头，" + "冷调侧光勾勒人物与红色长裙，动作和背景保持物理一致。" * 12
    calls = []

    def generate(system, user):
        calls.append((system, json.loads(user)))
        return body + "\n\n——自检——\n自检: 致命项 9/9"

    prompt = profiles.generate("krea2", _scene("sfw"), generate)

    assert prompt == body
    assert "SFW" in calls[0][0]
    assert "服装和姿态也必须使用中文" in calls[0][0]
    assert "——自检——" not in prompt
    assert "\n" not in prompt


def test_krea2_nsfw只允许服装与姿态为英文完整句():
    body = (
        "采用高角度中焦镜头，冷色侧光覆盖寝殿，人物位于画面中央。"
        "She wears a torn red dress that exposes the state established by the scene. "
        "She bends forward while the explicit action from the source scene continues. "
        + "背景、构图与人物关系保持清晰，镜头集中在本轮高潮动作。" * 10
    )
    seen = {}

    def generate(system, user):
        seen["system"] = system
        return body

    assert profiles.generate("krea2", _scene("nsfw"), generate) == body
    assert "仅服装段和姿态/动作段" in seen["system"]
    assert "英文完整句子" in seen["system"]


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


def test_anima剧情有动作时普通肖像输出必须触发重写():
    scene = {
        **_scene(),
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

    prompt = profiles.generate("anima_tags", _scene(), generate)

    assert prompt.splitlines()[1] == designed_content
    assert len(users) == 2
    assert "上次输出未通过" in users[1]
    assert "辅助元素最多两个" in users[1]


def test_anima结构化艺术决策不泄漏进最终两行提示词():
    content = (
        "1girl, lacquer reflection, close-up, crimson eye, silver crest, negative space. "
        "The lacquer reflection compresses the farewell into one eye and one fading silhouette."
    )

    prompt = profiles.generate("anima_tags", _scene(), lambda _s, _u: _anima_json(content))

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
    prompt = profiles.generate("niji_sections", _scene(), lambda _s, _u: raw)
    assert prompt.splitlines() == [
        "A swordswoman standing in rain",
        "refined anime illustration",
        "cinematic rim light, dynamic framing",
        "--stylize 400 --chaos 8 --no text",
    ]


def test_自然语言模式保持完整段落而非tags():
    raw = "一名剑士站在雨中的石阶上。冷色侧逆光勾勒轮廓，镜头从低处仰拍。"
    assert profiles.generate("natural_language", _scene(), lambda _s, _u: raw) == raw


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


def test_主模型内联Krea提示词不受长度硬门禁():
    assert profiles.normalize_inline("krea2", "主模型已经生成的完整画面描述。") == "主模型已经生成的完整画面描述。"


def test_格式不合格时带错误重写一次():
    outputs = iter(["too short", "采用低角度镜头，" + "柔和侧光塑造人物、服装、动作、背景和构图。" * 14])
    users = []

    def generate(_system, user):
        users.append(user)
        return next(outputs)

    prompt = profiles.generate("krea2", _scene(), generate)
    assert prompt.startswith("采用低角度镜头")
    assert len(users) == 2
    assert "上次输出未通过" in users[1]


def test_krea2重写后仍不完全合规也返回非空结果而不阻断生图():
    outputs = iter([
        "第一版过短。",
        "第二版已经根据高潮段完成画面改写，但长度仍未达到原先的硬性范围。",
    ])

    prompt = profiles.generate("krea2", _scene("sfw"), lambda _s, _u: next(outputs))

    assert prompt == "第二版已经根据高潮段完成画面改写，但长度仍未达到原先的硬性范围。"


def test_krea2模型连续返回空内容时直接使用高潮场景兜底():
    scene = _scene("sfw")

    prompt = profiles.generate("krea2", scene, lambda _s, _u: "")

    assert "高潮动作所在段落" in prompt
    assert "红色长裙" in prompt
    assert "寝殿" in prompt


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


def test_krea2_nsfw拒绝人物与光影混入英文():
    invalid = (
        "中景拍摄。golden hour lighting。冷倾雪站在画面中央。"
        "She wears a torn purple robe. She leans against the wooden rail."
        "背景是荒村。主体位于三分线。"
    )
    assert profiles.normalize_inline("krea2", invalid, {"rating": "nsfw"}) == ""


def test_krea2_nsfw只接受服装和姿态两句英文():
    valid = (
        "中景低机位拍摄，浅景深。清晨侧逆光形成冷暖对比。"
        "冷倾雪有漆黑墨发、紫玉金髻、朱唇与成熟美目。"
        "She wears a torn purple gauze robe and stained white silk stockings. "
        "She rests one arm on the rail while raising her hips slightly. "
        "背景是晨雾中的荒废村落。人物位于画面中部，木栏形成前景框架。"
    )
    assert profiles.normalize_inline("krea2", valid, {"rating": "nsfw"}) == valid


def test_krea2系统提示包含十段配额与审美约束():
    system = profiles._system("krea2", {"rating": "sfw"})

    assert "拍摄角度≤55字" in system
    assert "服装≤130字" in system
    assert "构图≤45字" in system
    assert "非常规视角" in system
    assert "光源方向与光质" in system
    assert "材质与光线互动" in system
    assert "二次元插画" in system


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


def test_其他内联模式按自身格式承载同一细节骨架():
    anima = profiles.inline_instruction("anima_tags")
    natural = profiles.inline_instruction("natural_language")
    niji = profiles.inline_instruction("niji_sections")

    for instruction in (anima, natural, niji):
        assert "稳定外貌" in instruction
        assert "镜头" in instruction
        assert "光影" in instruction
        assert "材质" in instruction
        assert "构图" in instruction
        assert "唯一而具体的视觉命题" in instruction
        assert "第一视觉中心" in instruction
        assert "光源→受光对象→材质反应/阴影→视觉中心" in instruction
        assert "最多两个" in instruction
        assert "具体可见的视觉装置" in instruction
        assert "普通剧情清单" in instruction
        assert "人物互动高潮" in instruction
        assert "物件只能作为辅助视觉装置" in instruction
    assert "英文 Danbooru tags" in anima
    assert "英文自然语言画面描述" in anima
    assert "自然语言" in natural
    assert "四行" in niji


def test_未知profile拒绝():
    with pytest.raises(ValueError, match="未知提示词模式"):
        profiles.generate("unknown", _scene(), lambda _s, _u: "x")
