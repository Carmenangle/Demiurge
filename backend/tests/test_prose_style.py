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


# ── 跨来源术语棘轮（style_term_ratchet）──────────────────────────────────────
# 2026-09-23 用户实锤：与破折号棘轮同构的**语义棘轮**——「经脉」在注入块里 33 次、跨 31 个
# 不同句子（冗余倍数 1.0×，去重无效），而 assistant 正文里「看经脉」「守经脉」**0 命中**
# ⇒ 它从未被说出口，只活在持久层里被反复重新确认。载体从「单轮 token」换成「跨轮持久层」。

def test_术语棘轮命中_同词跨注入层与正文():
    injected = "凌渊以“守经脉”为名贴身上前，替她守着经脉。" * 3
    bodies = ("她按住他腕上那处经脉，没有说话。", "他指尖顺着经脉往下走了一寸。")

    items = prose_style.term_ratchet_report(injected=(injected,), bodies=bodies)

    assert items
    assert items[0]["code"] == prose_style.CODE_STYLE_TERM_RATCHET
    assert "经脉" in items[0]["message"]
    assert "注入层" in items[0]["message"] and "正文" in items[0]["message"]


def test_术语棘轮只躺在注入层不报():
    """只在世界书/角色卡里反复出现的词不是本缺陷，否则报告被切词碎片刷屏。

    实测：「好感／感度／度表」是「好感度表」的切词碎片，只在注入层。
    """
    injected = ("【好感分阶·随好感度表·汐颜】好感度越高越黏人。" * 8,)

    assert prose_style.term_ratchet_report(injected=injected, bodies=("他什么也没说。",)) == []
    # 正文层缺省（空）同样不出——默认路径不变
    assert prose_style.term_ratchet_report(injected=injected) == []


def test_术语棘轮按最长匹配抑制同族词():
    """「经脉」被收录后，含它的「守经脉／着经脉」不再重复报，避免一族词刷满报告。"""
    injected = ("他守着经脉。经脉之事未了。" * 5,)

    terms = [i["message"].split("「")[1].split("」")[0]
             for i in prose_style.term_ratchet_report(injected=injected, bodies=("经脉。",))]

    assert "经脉" in terms
    assert not any(t != "经脉" and "经脉" in t for t in terms)


def test_术语棘轮默认不参与lint_默认路径不变():
    """默认不传棘轮输入 ⇒ 不出该码（等价矩阵：改造前逐字同行为）。"""
    body = "她按住他腕上那处经脉，没有说话。" * 6

    assert prose_style.CODE_STYLE_TERM_RATCHET not in codes(prose_style.lint(body))
    assert prose_style.CODE_STYLE_TERM_RATCHET in codes(prose_style.lint(
        body, ratchet_injected=(body,), ratchet_bodies=(body,)))


def test_术语棘轮豁免角色名及其切词碎片():
    """exclude 传名字后，含名字的更长/更短片段也必须一并豁免（妲千／千沁 不许冒出来）。"""
    injected = ("凌渊看着妲千沁，凌渊没有开口，妲千沁也没有开口。" * 4,)

    items = prose_style.term_ratchet_report(
        injected=injected, bodies=("妲千沁没有动。凌渊也没有动。",),
        exclude=("凌渊", "妲千沁"))

    assert not any(k in i["message"] for i in items for k in ("凌渊", "妲千", "千沁"))


def test_narrative_ci转发棘轮源且缺省不出码():
    text = "她按住他腕上那处经脉，没有说话。" * 2
    injected = ("凌渊以“守经脉”为名上前，替她守着经脉。",) * 3

    forwarded = [i["code"] for i in narrative_ci.evaluate(
        text, turn=1, style_injected=injected, style_bodies=(text,))]
    default = [i["code"] for i in narrative_ci.evaluate(text, turn=1)]

    assert prose_style.CODE_STYLE_TERM_RATCHET in forwarded
    assert prose_style.CODE_STYLE_TERM_RATCHET not in default


def _terms(items):
    return [i["message"].split("「")[1].split("」")[0] for i in items]


def test_术语棘轮剔除虚词边界切词():
    """「了一／一下／了一下」这类以虚词起止的动词短语切词不得进报告（实测噪音主力）。"""
    injected = ("他笑了一下，又笑了一下，再笑了一下。" * 3,)

    got = _terms(prose_style.term_ratchet_report(
        injected=injected, bodies=("了一下。",)))

    assert "了一" not in got and "一下" not in got and "了一下" not in got


def test_术语棘轮剔除被更长词解释的碎片():
    """「天狐」多是以「天狐宫／天狐媚体」的片段出现 ⇒ 判碎片剔除，只留「天狐宫」。"""
    injected = ("天狐宫的天狐媚体。天狐宫弟子。" * 4,)

    got = _terms(prose_style.term_ratchet_report(
        injected=injected, bodies=("天狐宫。",)))

    assert "天狐宫" in got
    assert "天狐" not in got and "狐宫" not in got


def test_术语棘轮按设定存量排序_区分合理高频与无锚点膨胀():
    """2026-09-23 用户口径：「天狐宫多次出现能理解（主角大半后宫在此、后期势力归男主），
    但经脉——你看设定条目都不怎么提及，就前期提了一嘴，结果一个个女主角找的借口都是经脉」。

    判据＝**设定存量 vs 叙事用量**。三档：
      · 经脉（设定里只有 1 处） ⇒ 用量 6 / 存量 1 = 6.0 ⇒ 排第一（无锚点膨胀）
      · 天狐宫（设定里 3 处）   ⇒ 6 / 3 = 2.0          ⇒ 排第二（设定支撑得住）
      · 指腹（设定里从未出现）  ⇒ 日常描写词，排最后，message 单独标注
    """
    injected = ("经脉、经脉、经脉；天狐宫、天狐宫、天狐宫；指腹、指腹、指腹。",)
    bodies = injected
    setting = ("经脉。天狐宫、天狐宫、天狐宫。",)

    items = prose_style.term_ratchet_report(
        injected=injected, bodies=bodies, setting=setting)
    got = _terms(items)
    msgs = [i["message"] for i in items]

    assert got[:2] == ["经脉", "天狐宫"]
    assert got[-1] == "指腹"
    assert "设定存量 1 次、用量/存量 6.0" in msgs[0]
    assert "设定存量 3 次、用量/存量 2.0" in msgs[1]
    assert "设定里从未出现" in msgs[-1]


def test_术语棘轮缺省setting时message逐字不变():
    """不传 setting ⇒ message 逐字等于改造前（等价矩阵锁）。"""
    injected = ("经脉、经脉、经脉。",)

    items = prose_style.term_ratchet_report(injected=injected, bodies=injected)

    assert items, "应命中"
    assert items[0]["message"] == (
        "术语棘轮：多字词「经脉」注入层 1 处、正文 1 处（合计 6 次 ≥6）"
        "——疑似被反复重新确认，请核对该说法在剧情里是否真的发生过；"
        "若只是给动作补的名目，应清掉。")
