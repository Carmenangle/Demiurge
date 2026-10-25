"""ReDoS 防护回归（2026-10-24 二轮审计）。

findRegex 是第三方内容（社区角色卡/预设导入），每条消息自动执行；Python re 无执行
超时。本文件锁三道确定性防线：嵌套无界量词形态拒编译（fail-open 跳过脚本）、
pattern/输入/脚本数上限。双端合同：frontend/src/lib/regexEngine.ts 同逻辑同常量，
TS 侧测试锁同一批形态与值。
"""
from __future__ import annotations

from app.services.regex_engine import (
    MAX_INPUT_CHARS,
    MAX_PATTERN_CHARS,
    MAX_SCRIPTS_PER_RUN,
    Placement,
    RegexScript,
    compile_js_regex,
    run_scripts,
)


def _s(find: str) -> RegexScript:
    return RegexScript(find_regex=find, replace_string="[X]")


# ── 形态拦截：指数回溯经典家族必须拒编译 ──────────────────────────────

def test_嵌套无界量词经典形态拒编译():
    for pattern in (
        "(a+)+",          # 最经典
        "(a+)*",
        "(a*)*",
        "(a+){2,}",
        r"(\w+\s?)+$",    # ST/真实卡里出现过的著名灾难模式（词+可选空格整组无界重复）
        "((a+)+)b",       # 双层嵌套同样拦
        "/(a+)+b/i",      # /body/flags 形式同样要拦
    ):
        assert compile_js_regex(pattern) is None, pattern


def test_良性模式不受影响():
    for pattern in (
        "(ab)+",
        "(a+)?",              # 有界外量词放行（仍可能多项式，但不指数）
        r"(?:https?://)?(\S+)",  # 真实卡常见形态，不得误伤
        r"(\d+)-(\d+)",
        r"\s*(\w+)\s*",
        "a{2,3}",
        r"(?<year>\d{4})年",  # JS 命名组方言（guard 跑在翻译前，不得误判）
        r"[\d]+x",            # 字符类内的 + 是字面量，不构成嵌套
    ):
        assert compile_js_regex(pattern) is not None, pattern


def test_pattern长度上限():
    assert compile_js_regex("a" * MAX_PATTERN_CHARS) is not None
    assert compile_js_regex("a" * (MAX_PATTERN_CHARS + 1)) is None


def test_脚本数上限_截前N保顺序():
    # 定宽 token 防子串交叉匹配（z1 会撞 z10）
    scripts = [RegexScript(find_regex=f"z{i:03d}", replace_string=f"<{i:03d}>")
               for i in range(MAX_SCRIPTS_PER_RUN + 10)]
    text = "".join(f"z{i:03d}" for i in range(MAX_SCRIPTS_PER_RUN + 10))
    out = run_scripts(text, Placement.AI_OUTPUT, scripts)
    # 前 64 条（000..063）生效，第 65 条（064）起原样
    assert f"<{MAX_SCRIPTS_PER_RUN - 1:03d}>" in out
    assert f"z{MAX_SCRIPTS_PER_RUN:03d}" in out


def test_超长输入整轮跳过():
    big = "a" * (MAX_INPUT_CHARS + 1)
    assert run_scripts(big, Placement.AI_OUTPUT, [_s("a")]) == big


def test_形态拦截在run_scripts生效():
    # run_script 层的 fail-open：坏形态脚本被跳过，其余脚本照常。
    # 若防线失效，(a+)+ 会吞掉整段文本；守住了则只有第二条脚本替换「坏」。
    scripts = [_s("(a+)+"), _s("坏")]
    assert run_scripts("abc坏", Placement.AI_OUTPUT, scripts) == "abc[X]"
