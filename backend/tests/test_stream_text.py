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
