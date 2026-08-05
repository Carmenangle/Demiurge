"""正则引擎测试：对标 ST engine.js 的过滤/替换/方言转换。"""
from __future__ import annotations

from app.services.regex_engine import Placement, RegexScript, from_st_dict, run_scripts


def _s(**kw) -> RegexScript:
    kw.setdefault("find_regex", "")
    return RegexScript(**kw)


def test_基本查找替换():
    s = _s(find_regex="世界", replace_string="World")
    assert run_scripts("你好世界世界", Placement.AI_OUTPUT, [s]) == "你好WorldWorld"


def test_match宏与捕获组():
    s = _s(find_regex=r"\[(\d+)\]", replace_string="第$1条")
    assert run_scripts("看[3]和[7]", Placement.AI_OUTPUT, [s]) == "看第3条和第7条"


def test_match整体宏():
    s = _s(find_regex="喵+", replace_string="(叫声:{{match}})")
    assert run_scripts("喵喵喵", Placement.AI_OUTPUT, [s]) == "(叫声:喵喵喵)"


def test_命名组js方言转换():
    s = _s(find_regex=r"(?<year>\d{4})年", replace_string="$<year>")
    assert run_scripts("2026年", Placement.AI_OUTPUT, [s]) == "2026"


def test_隐藏think块_dotall_flag():
    # 显示层隐藏 <think>…</think>（跨行），对标用户要的折叠灰魂吐槽
    s = _s(find_regex=r"/<think>[\s\S]*?<\/think>/", replace_string="", markdown_only=True)
    text = "<think>\n内心\n</think>\n正文"
    out = run_scripts(text, Placement.AI_OUTPUT, [s], is_markdown=True)
    assert "内心" not in out and "正文" in out


def test_flags_i_忽略大小写():
    s = _s(find_regex="/hello/i", replace_string="hi")
    assert run_scripts("HELLO Hello", Placement.AI_OUTPUT, [s]) == "hi hi"


def test_trim_strings去除():
    s = _s(find_regex=r"「(.+?)」", replace_string="$1", trim_strings=["…"])
    assert run_scripts("「你好…世界」", Placement.AI_OUTPUT, [s]) == "你好世界"


def test_disabled跳过():
    s = _s(find_regex="x", replace_string="y", disabled=True)
    assert run_scripts("xxx", Placement.AI_OUTPUT, [s]) == "xxx"


def test_placement过滤():
    s = _s(find_regex="a", replace_string="b", placement=[Placement.USER_INPUT])
    # 只在 USER_INPUT 生效，AI_OUTPUT 不动
    assert run_scripts("aaa", Placement.AI_OUTPUT, [s]) == "aaa"
    assert run_scripts("aaa", Placement.USER_INPUT, [s]) == "bbb"


def test_markdown_only只在显示层():
    s = _s(find_regex="秘", replace_string="*", markdown_only=True)
    # 非 markdown（存储源）场景不跑
    assert run_scripts("秘密", Placement.AI_OUTPUT, [s], is_markdown=False) == "秘密"
    assert run_scripts("秘密", Placement.AI_OUTPUT, [s], is_markdown=True) == "*密"


def test_prompt_only只在发送时():
    s = _s(find_regex="A", replace_string="B", prompt_only=True)
    assert run_scripts("AAA", Placement.USER_INPUT, [s], is_prompt=False) == "AAA"
    assert run_scripts("AAA", Placement.USER_INPUT, [s], is_prompt=True) == "BBB"


def test_默认档存储源():
    # 两档皆非 → 只在既非 markdown 也非 prompt 时跑（存储源）
    s = _s(find_regex="X", replace_string="Y")
    assert run_scripts("XX", Placement.AI_OUTPUT, [s]) == "YY"
    assert run_scripts("XX", Placement.AI_OUTPUT, [s], is_markdown=True) == "XX"
    assert run_scripts("XX", Placement.AI_OUTPUT, [s], is_prompt=True) == "XX"


def test_depth门控():
    s = _s(find_regex="z", replace_string="Z", min_depth=2, max_depth=5)
    assert run_scripts("zz", Placement.AI_OUTPUT, [s], depth=0) == "zz"   # 低于 min
    assert run_scripts("zz", Placement.AI_OUTPUT, [s], depth=3) == "ZZ"   # 区间内
    assert run_scripts("zz", Placement.AI_OUTPUT, [s], depth=9) == "zz"   # 高于 max


def test_非法正则不炸():
    s = _s(find_regex="/[unclosed/", replace_string="x")
    assert run_scripts("abc", Placement.AI_OUTPUT, [s]) == "abc"


def test_from_st_dict归一():
    s = from_st_dict({
        "findRegex": "/<think>[\\s\\S]*?</think>/gs",
        "replaceString": "",
        "trimStrings": ["…"],
        "placement": [2, 6],
        "markdownOnly": True,
        "minDepth": 0,
        "maxDepth": None,
        "scriptName": "隐藏思考",
    })
    assert s.markdown_only is True
    assert s.placement == [2, 6]
    assert s.trim_strings == ["…"]
    assert s.min_depth == 0 and s.max_depth is None
    assert s.script_name == "隐藏思考"


def test_空文本原样():
    s = _s(find_regex="a", replace_string="b")
    assert run_scripts("", Placement.AI_OUTPUT, [s]) == ""


def test_substitute_regex宏替换查找():
    from app.services.regex_engine import run_script
    # substituteRegex=1 原始：find 里 {{char}} 换成角色名后匹配
    s = _s(find_regex="{{char}}", replace_string="X", substitute_regex=1)
    assert run_script(s, "灰魂来了", markers={"char_name": "灰魂"}) == "X来了"
    # mode=0 不替换：{{char}} 当字面找不到 → 原样
    s0 = _s(find_regex="{{char}}", replace_string="X", substitute_regex=0)
    assert run_script(s0, "灰魂来了", markers={"char_name": "灰魂"}) == "灰魂来了"
    # from_st_dict 读 substituteRegex
    assert from_st_dict({"findRegex": "a", "substituteRegex": 2}).substitute_regex == 2
