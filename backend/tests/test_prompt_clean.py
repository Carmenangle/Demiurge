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


# ── 表格 / 状态文本破甲还原（2026-09-13 用户实锤：状态表「依据」出现 @为@争@…）──


def test_sanitize_table_text_还原逐字分隔标记():
    raw = "@为@争@被@插@彻@底@撕@碎@体@面@，@不@顾@宗@主@尊@严@"
    assert prompt_clean.sanitize_table_text(raw) == "为争被插彻底撕碎体面，不顾宗主尊严"


def test_sanitize_table_text_兼容包裹式与空值():
    assert prompt_clean.sanitize_table_text("深@(喉)@") == "深喉"
    assert prompt_clean.sanitize_table_text("") == ""
    assert prompt_clean.sanitize_table_text("干净文本") == "干净文本"


# ── 正文元话语泄漏清洗 ──────────────────────────────────────────────
# 2026-09-13 用户实锤：神权大陆最新一轮把「输出纪律」清单抄进了 <content>——
# 首行「质量检查：满足1000-8000字；状态栏更新收养线与目的地；」、
# 末行「【系统提示：以下为推演输出尾部标注，非正文内容】」。
# 注意：<think> 里的「质量检查」是预设思维链9 的设计产物，**不是泄漏**，
# 因此剥离只允许发生在 <content> 正文里（见 roleplay_turn 的 content 级封装）。

_NARR = (
    "*她穿一身压暗纹的玄色窄袍，乌黑齐肩短发被风掀起几缕，灰紫色的瞳仁里映着雪光，冷得像刀。*\n\n"
    "「就是你？」*她开口，声音清凌凌的，带着这个年纪少有的沉。*\n\n"
    "*她转身就走，玄色的衣角在雪地上扫出长长的痕。你跟上她，两个孩子追到门口，怯怯地喊小哥哥。*\n\n"
    "「进了那个门，你就是幽影的人了。」*她说，声音裹在风里，辨不出是冷还是软。*\n"
)


def test_strip_meta_leak_剥掉抄进来的输出纪律整行():
    body = (
        "质量检查：满足1000-8000字；状态栏更新收养线与目的地；\n\n"
        + _NARR
        + "\n【系统提示：以下为推演输出尾部标注，非正文内容】\n"
    )
    cleaned, removed = prompt_clean.strip_meta_leak(body)
    assert "质量检查" not in cleaned
    assert "系统提示" not in cleaned
    assert "1000-8000" not in cleaned
    assert "玄色窄袍" in cleaned and "就是你？" in cleaned
    assert len(removed) == 2


def test_strip_meta_leak_行内同名词不误删():
    body = "*她忽然问：「质量检查进行得如何？」语气平常，像是随口一提。*\n\n" + _NARR
    cleaned, removed = prompt_clean.strip_meta_leak(body)
    assert removed == []
    assert cleaned == body
    assert "质量检查进行得如何" in cleaned


def test_strip_meta_leak_删除过多则整体放弃():
    body = (
        "质量检查：满足字数；\n系统提示：x\n输出纪律：y\n字数要求：z\n尾部标注：w\n"
        + _NARR
    )
    cleaned, removed = prompt_clean.strip_meta_leak(body)
    assert cleaned == body
    assert removed == []


def test_strip_meta_leak_空值与无残留时原样返回():
    assert prompt_clean.strip_meta_leak("") == ("", [])
    assert prompt_clean.strip_meta_leak(_NARR) == (_NARR, [])
