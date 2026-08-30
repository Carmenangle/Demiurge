"""剧情模式历史瘦身：上一次剧情轮 + 纯正文过滤（上下文合同回归测试）。"""
from app.services import story_history as sh


def test_prose_only_剥离全部生成侧控制块():
    reply = (
        "<think>推理过程不得回流</think>\n"
        "第一段正文。\n\n"
        "<status>\n[时间] 仲春\n</status>\n"
        "<roll>\n【骰点】D100=88\n</roll>\n"
        "第二段正文。\n"
        '<illustration>{"anchor":"第二段正文。","motion":1}</illustration>\n'
        "<transition>{\"decision\":\"reuse\"}</transition>\n"
        "<audio>{\"lines\":[]}</audio>\n"
        "<状态更新>[{\"field\":\"数值/好感度\",\"op\":\"add\",\"value\":5}]</状态更新>\n"
        "第三段正文。"
    )

    out = sh.prose_only(reply)

    assert out == "第一段正文。\n\n第二段正文。\n\n第三段正文。"
    assert "推理过程" not in out
    assert "骰点" not in out
    assert "好感度" not in out
    assert "anchor" not in out


def test_prose_only_优先取_content_内层并剥块():
    reply = (
        "<status>\n[时间] 开头状态不得入正文\n</status>\n"
        "<content>\n<status>\n[时间] 尾部状态\n</status>\n可见剧情全文。\n</content>"
    )

    out = sh.prose_only(reply)

    assert out == "可见剧情全文。"
    assert "开头状态" not in out
    assert "尾部状态" not in out


def test_prose_only_未闭合尾部块整体剥除():
    reply = "正文一段。\n<think>\n未闭合的思考一直延伸到结尾"

    out = sh.prose_only(reply)

    assert out == "正文一段。"
    assert "未闭合的思考" not in out


def test_last_story_round_只进上一次剧情轮():
    history = [
        {"role": "user", "content": "第一轮输入"},
        {"role": "assistant", "content": "<status>x</status>第一轮正文。"},
        {"role": "user", "content": "第二轮输入"},
        {"role": "assistant", "content": "<think>想</think>第二轮正文。\n<status>y</status>"},
    ]

    out = sh.last_story_round(history)

    assert out == [
        {"role": "user", "content": "第二轮输入"},
        {"role": "assistant", "content": "第二轮正文。"},
    ]


def test_last_story_round_末轮无正文时回退上一轮():
    history = [
        {"role": "user", "content": "第一轮输入"},
        {"role": "assistant", "content": "第一轮正文。"},
        {"role": "user", "content": "第二轮输入"},
        {"role": "assistant", "content": "<status>只有状态没有正文</status>"},
    ]

    out = sh.last_story_round(history)

    assert [item["content"] for item in out] == ["第一轮输入", "第一轮正文。"]


def test_last_story_round_开场问候只返回一条():
    history = [{"role": "assistant", "content": "开场白正文。<status>s</status>"}]

    out = sh.last_story_round(history)

    assert out == [{"role": "assistant", "content": "开场白正文。"}]


def test_last_story_round_空历史():
    assert sh.last_story_round([]) == []
    assert sh.last_story_round(None) == []
