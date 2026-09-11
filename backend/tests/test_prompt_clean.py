"""prompt_clean 共享清洗规则回归测试（独立性保障）。

本测试只 import prompt_clean，不依赖 image_prompt_extract 的 IMAGE_PROMPT 清洗逻辑：
即使删掉 image_prompt_extract 里的破甲还原实现，图像/视频提示词的防拦截清洗
仍由本共享模块庇护（规则文档 docs/PROMPT-CLEANING-RULES.md）。
"""
from app.services import prompt_clean


def test_restore_paren_wrap():
    assert prompt_clean.restore_jailbreak("面@(馆)@") == "面馆"
    assert prompt_clean.restore_jailbreak("温知夏(米@(色)@针织开衫)") == "温知夏(米色针织开衫)"


def test_restore_bare_at_deleted():
    assert prompt_clean.restore_jailbreak("a@b") == "ab"


def test_restore_suffix_and_prefix_paren():
    assert prompt_clean.restore_jailbreak("(x)@y") == "xy"
    assert prompt_clean.restore_jailbreak("@(x)y") == "xy"


def test_restore_empty_and_noop():
    assert prompt_clean.restore_jailbreak("") == ""
    assert prompt_clean.restore_jailbreak("无标记正文") == "无标记正文"


def test_restore_with_offsets():
    text, offsets = prompt_clean.restore_jailbreak_with_offsets("面@(馆)@")
    assert text == "面馆"
    assert len(offsets) == len(text)


def test_clean_spec_text_fields_restores_all():
    spec = {
        "narrative": "三@(人)@举杯同框",
        "appearance": "温知夏(米@(色)@针织开衫)",
        "wardrobe": "全员日常私服",  # 无标记原样保留
        "locale": "面@(馆)@内景",
        "camera": "摇臂俯拍",
        "first_frame_desc": "雨@(夜)@门口",
        "motion": 3,  # 非字符串不动
    }
    cleaned = prompt_clean.clean_spec_text_fields(spec)
    assert cleaned["narrative"] == "三人举杯同框"
    assert cleaned["appearance"] == "温知夏(米色针织开衫)"
    assert cleaned["wardrobe"] == "全员日常私服"
    assert cleaned["locale"] == "面馆内景"
    assert cleaned["first_frame_desc"] == "雨夜门口"
    assert cleaned["motion"] == 3


def test_clean_spec_empty_and_none():
    assert prompt_clean.clean_spec_text_fields({}) == {}
    assert prompt_clean.clean_spec_text_fields(None) == {}
