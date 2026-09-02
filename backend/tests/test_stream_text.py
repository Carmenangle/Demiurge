from app.services.stream_text import VisibleTextStream


def test_stream_filter跨分块隐藏内部块并解包正文():
    stream = VisibleTextStream()

    shown = "".join([
        stream.feed("<thi"),
        stream.feed("nk>内部推理</think><content>正"),
        stream.feed("文<状态更新>[{}]</状态更新>结束</content>"),
        stream.finish(),
    ])

    assert shown == "正文结束"


def test_stream_filter不展示同轮完整Profile提示词():
    stream = VisibleTextStream()

    shown = "".join([
        stream.feed("<content>完整正文</content><illustr"),
        stream.feed('ation>{"profile_prompt":"A complete hidden image prompt."}'),
        stream.feed("</illustration>"),
        stream.finish(),
    ])

    assert shown == "完整正文"
    assert "profile_prompt" not in shown


def test_隐藏块内容经on_hidden回调实时外送():
    """2026-08-31 晚「思考全公开」：think 块内容逐段回调（跨 chunk 不丢），正文流
    保持干净；非 think 隐藏块（状态更新等）不回调给思考面板消费方——由回调方按
    name 过滤，这里验证 name 透传正确。"""
    hidden: list = []
    stream = VisibleTextStream(on_hidden=lambda name, text: hidden.append((name, text)))

    shown = "".join([
        stream.feed("<think>先推演"),
        stream.feed("再落笔</think><content>正文一"),
        stream.feed("<状态更新>[时间]夜</状态更新>正文二</content>"),
        stream.finish(),
    ])

    assert shown == "正文一正文二"
    think = "".join(t for name, t in hidden if name == "think")
    assert "先推演" in think and "再落笔" in think  # 跨 chunk 思考内容完整
    assert all(name == "状态更新" for name, _ in hidden if name != "think")
