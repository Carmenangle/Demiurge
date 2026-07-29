"""生图提示词风格识别测试：按模型名（含中转改名）判家族 + 取写法指引。"""
from app.services import image_prompt_style as ips


def test_gpt_family_含中转改名():
    assert ips.detect_family("gpt-image-2-all") == "gpt"
    assert ips.detect_family("gpt-image-2") == "gpt"
    assert ips.detect_family("GPT-4o-image") == "gpt"
    assert ips.detect_family("dall-e-3") == "gpt"


def test_banana_family():
    assert ips.detect_family("nano-banana") == "banana"
    assert ips.detect_family("gemini-2.5-flash-image-hd") == "banana"
    assert ips.detect_family("imagen-3") == "banana"


def test_tag_family_本地checkpoint():
    assert ips.detect_family("sdxl_base") == "tag"
    assert ips.detect_family("ponyDiffusionV6XL") == "tag"
    assert ips.detect_family("Illustrious-XL") == "tag"
    assert ips.detect_family("someModel_XL.safetensors") == "tag"  # 宽松 xl 命中


def test_natural_family_flux_sd3():
    assert ips.detect_family("flux.1-dev") == "natural"
    assert ips.detect_family("sd3-medium") == "natural"


def test_未识别回退generic():
    assert ips.detect_family("") == "generic"
    assert ips.detect_family("some-unknown-model") == "generic"


def test_guidance_自然语言系不含标签咒语():
    for fam in ("gpt", "banana", "natural", "generic"):
        g = ips.gen_guidance(fam)
        assert "逗号" in g  # 都提到「不要逗号堆砌」
        assert "不要" in g


def test_guidance_标签系要标签():
    g = ips.gen_guidance("tag")
    assert "Danbooru" in g and "masterpiece" in g


def test_gen_guidance_for_便捷组合():
    assert ips.gen_guidance_for("gpt-image-2-all") == ips.gen_guidance("gpt")
    assert ips.gen_guidance_for("nano-banana") == ips.gen_guidance("banana")


def test_guidance_for_手动风格优先():
    # 手动选 sd → 强制标签系，即便模型名是 gpt
    assert ips.guidance_for("sd", "gpt-image-2-all") == ips.gen_guidance("tag")
    # 手动选 gpt → 强制 gpt，即便模型名是本地 sdxl
    assert ips.guidance_for("gpt", "sdxl_base") == ips.gen_guidance("gpt")
    assert ips.guidance_for("banana", "anything") == ips.gen_guidance("banana")


def test_guidance_for_自动回退模型名():
    # 空/auto → 按模型名判
    assert ips.guidance_for("", "gpt-image-2-all") == ips.gen_guidance("gpt")
    assert ips.guidance_for("auto", "ponyXL") == ips.gen_guidance("tag")
    # 未知 style 也回退模型名（容错）
    assert ips.guidance_for("xxx", "nano-banana") == ips.gen_guidance("banana")
