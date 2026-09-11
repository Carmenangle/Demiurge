"""prose_style 文风 lint 单测：词表分支命中 / 密度阈值 / 干净样文零误报。

覆盖真实失败路径：AI 味套路必须被逐条抓出；正常中文（含合法的单次
「不是…而是…」）不得误报；防拦截拆字须先还原再检测。
"""
from __future__ import annotations

from app.services import narrative_ci, prose_style


def codes(result):
    return [item["code"] for item in result]


# ── 固定搭配词表 ─────────────────────────────────────────────────────────────

def test_空洞大词与套话命中():
    result = prose_style.lint("她运转灵力，整套剑法宛如被赋能一般。值得注意的是，剑意仍未收敛。")
    assert prose_style.CODE_STYLE_BANNED_PHRASE in codes(result)
    evidence = "".join(item["evidence"] for item in result)
    assert "赋能" in evidence and "值得注意的是" in evidence


def test_讨好腔命中():
    result = prose_style.lint("他低头看她：你的观察很敏锐。")
    assert prose_style.CODE_STYLE_BANNED_PHRASE in codes(result)


def test_防拦截拆字先还原再检测():
    result = prose_style.lint("这套剑法 @(赋)@(能)@ 了整座剑阵。")
    assert prose_style.CODE_STYLE_BANNED_PHRASE in codes(result)


# ── 密度与句式模板 ───────────────────────────────────────────────────────────

def test_破折号密度超标命中():
    body = "风停了——他回头——刀已出鞘——血溅三尺——" + "夜色压下来。" * 30
    result = prose_style.lint(body)
    assert prose_style.CODE_STYLE_PUNCT_DENSITY in codes(result)


def test_省略号密度超标命中():
    body = "她欲言又止……他退了半步……灯灭了……门开了……" + "走廊尽头没有声音。" * 20
    assert prose_style.CODE_STYLE_PUNCT_DENSITY in codes(prose_style.lint(body))


def test_单次破折号不误报():
    assert prose_style.CODE_STYLE_PUNCT_DENSITY not in codes(
        prose_style.lint("风停了——他回头。夜色压下来，像一层湿透的布。" + "远处传来更声。" * 5))


def test_不是而是单次合法重复两次才报():
    single = "他不是不想救，而是来不及救。刀光一闪，血溅了满墙。"
    assert codes(prose_style.lint(single)) == []
    double = ("他不是不想救，而是来不及救。她不是不知道危险，而是不肯回头。"
              "两人隔着一道门，谁都没有再说话。")
    assert prose_style.CODE_STYLE_PATTERN_REPEAT in codes(prose_style.lint(double))


def test_自问自答命中():
    result = prose_style.lint("难道就这样算了？不，绝不。他握紧了拳。")
    assert prose_style.CODE_STYLE_SELF_QA in codes(result)


# ── 节拍器 ───────────────────────────────────────────────────────────────────

def test_句长节拍器感命中():
    body = "。".join(["他推门走进大厅" ] * 9) + "。"
    result = prose_style.lint(body)
    assert prose_style.CODE_STYLE_RHYTHM_METRONOME in codes(result)


def test_句长交错不报():
    body = ("他推门。大厅里烛火摇晃，长桌尽头坐着一个披斗篷的女人，"
            "指间夹着一枚磨旧的铜币，铜币边缘刻着半枚被刮花的家徽。"
            "她抬起眼，看了他很久。然后笑了。那笑意没有到眼底。"
            "「你迟到了。」她说着把铜币推过桌面，铜币在木纹上转了半圈才停下。")
    assert prose_style.CODE_STYLE_RHYTHM_METRONOME not in codes(prose_style.lint(body))


# ── 跨轮开场趋同 ─────────────────────────────────────────────────────────────

def test_跨轮开场趋同命中():
    body = "夜色深沉，他站在檐下没有动。雨顺着瓦当滴落，砸碎在青石板上。"
    history = ["夜色深沉，他握紧了手里的信。", "夜色深沉，她没有回答他的问题。"]
    result = prose_style.lint(body, recent_openings=history)
    assert prose_style.CODE_STYLE_OPENING_CUE in codes(result)


def test_开场各异与历史不足均不报():
    body = "晨光落在窗棂上，她已经开始收拾行李。"
    assert codes(prose_style.lint(body, recent_openings=[
        "夜色深沉，他握紧了信。", "酒馆里人声鼎沸。"])) == []
    assert codes(prose_style.lint(body, recent_openings=["夜色深沉，他握紧了信。"])) == []


# ── 干净样文零误报 ───────────────────────────────────────────────────────────

def test_干净样文零误报():
    body = ("雨下到后半夜，檐水连成了线。她把伞往他那边偏了偏，自己半边肩膀很快洇湿。"
            "「前面就是渡口。」她说。他没有应声，只把包袱又往上提了提。"
            "渡口的老艄公披着蓑衣打盹，船板被雨水泡得发黑，踩上去咯吱作响。"
            "她先跳上船，回头伸手拉他，掌心冰凉，却握得很稳。")
    assert prose_style.lint(body, recent_openings=["昨夜他们宿在破庙。", "清晨雾气未散。"]) == []


# ── narrative_ci 并流 ────────────────────────────────────────────────────────

def test_narrative_ci并流文风诊断带turn与id():
    result = narrative_ci.evaluate("这计划简直是完美的闭环。", turn=7)
    style = [item for item in result if item["code"] == prose_style.CODE_STYLE_BANNED_PHRASE]
    assert len(style) == 1
    assert style[0]["turn"] == 7
    assert style[0]["status"] == "open"
    assert style[0]["source"] == "prose_style"
    assert style[0]["id"]


def test_narrative_ci文风检测异常不阻断诊断流():
    # 正文为空/纯控制字符等边界输入不抛错、不产出 style 诊断
    assert narrative_ci.evaluate("", turn=1) == []


def test_同词连用排比检出():
    body = "他想拒绝。没有，没有，没有。他退了一步。"

    assert "style_word_echo" in codes(prose_style.lint(body))


def test_超短强调段单轮两次检出():
    body = (
        "他伸出了左手。\n\n手松了。\n\n指尖还残留着温度。\n\n乳根。\n\n"
        "她别过脸去，耳根泛起一层薄红，指节在袖口里攥得发白，半天没有说话。"
    )

    assert "style_micro_paragraph" in codes(prose_style.lint(body))


def test_对话回声句式检出():
    body = "「你知道了吗？」他问。「我知道了。」她点头。「不，你不知道。」他摇头。"

    result = prose_style.lint(body)

    assert "style_self_qa" in codes(result)


def test_正常单次不是而是与单个微段不误报():
    body = (
        "这不是逃避，而是选择。\n\n左手。她终于摊开了掌心，把那枚旧铜钥匙放在他手里，"
        "指尖还残留着昨夜的凉意，掌心的纹路里嵌着洗不掉的药味，像是把整段路都攥了一遍。"
    )

    result = prose_style.lint(body)

    assert "style_word_echo" not in codes(result)
    assert "style_micro_paragraph" not in codes(result)


def test_破折号两次即报不再看密度():
    """2026-08-30 用户实锤：AI 用一次破折号，后续断句会越来越依赖——≥2 次即报。"""
    body = "风停了——他回头——" + "夜色压下来，像一层湿透的布盖住远处的更声与灯火。" * 40

    assert prose_style.CODE_STYLE_PUNCT_DENSITY in codes(prose_style.lint(body))


def test_文风注入段禁用破折号且自身不带破折号():
    segment = prose_style.style_prompt_segment({"enabled": True})

    assert "禁止使用破折号" in segment
    assert segment.count("——") == 1  # 仅在「——」引用符号本身处出现，禁令句不再示范用法
