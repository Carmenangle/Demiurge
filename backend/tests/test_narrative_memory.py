# -*- coding: utf-8 -*-
"""纪要记忆纯逻辑测试（0 I/O 0 LLM）：门控 / 解析 / 压缩判定 / trigram 查询 / 渲染。"""
from app.services import narrative_memory as nm


def test_门控_未攒够回合不抽():
    assert nm.should_summarize(0, 5, cadence=6) is False
    assert nm.should_summarize(0, 6, cadence=6) is True
    assert nm.should_summarize(6, 11, cadence=6) is False
    assert nm.should_summarize(6, 12, cadence=6) is True


def test_默认每三轮抽一条纪要():
    assert nm.CADENCE == 3
    assert nm.should_summarize(0, 2) is False
    assert nm.should_summarize(0, 3) is True


def test_门控_cadence非法返回False():
    assert nm.should_summarize(0, 100, cadence=0) is False


def test_解析_正常JSON():
    raw = '前言{"summary":"雪山救援后态度转暖","keywords":["雪山","救援","态度"]}后语'
    text, kws = nm.parse_summary(raw)
    assert text == "雪山救援后态度转暖"
    assert kws == ["雪山", "救援", "态度"]


def test_解析_丰富纪要保留概览详情对话与出场人物():
    raw = ('{"overview":"雪山救援后关系转暖","chronicle":"两人协力脱险并约定同行。",'
           '"dialogue":"她说：我欠你一次。","characters":["林月","主角"],'
           '"keywords":["雪山","救援"],"facts":[{"subject":"雪山关隘",'
           '"predicate":"守将","object":"林月","evidence":"林月接过守将令牌"}]}')

    entry = nm.parse_rich_summary(raw, turn_start=1, turn_end=3)

    assert entry is not None
    assert entry.overview == "雪山救援后关系转暖"
    assert entry.text == "两人协力脱险并约定同行。"
    assert entry.dialogue == "她说：我欠你一次。"
    assert entry.characters == ["林月", "主角"]
    assert entry.facts[0]["predicate"] == "守将"


def test_解析_坏JSON返回空():
    assert nm.parse_summary("没有大括号") == ("", [])
    assert nm.parse_summary('{坏json}') == ("", [])


def test_解析_空summary返回空():
    assert nm.parse_summary('{"summary":"","keywords":["x"]}') == ("", [])


def test_解析_summary截断软上限():
    long = "字" * (nm._SUMMARY_MAX + 100)
    text, _ = nm.parse_summary('{"summary":"' + long + '"}')
    assert len(text) == nm._SUMMARY_MAX


def test_解析_关键词去重限量():
    kws_raw = ",".join(f'"k{i}"' for i in range(20))
    text, kws = nm.parse_summary('{"summary":"x","keywords":[' + kws_raw + ']}')
    assert len(kws) == 12


def test_压缩判定_超上限才压且封顶层不压():
    assert nm.should_compact(0, nm.LAYER0_CAP) is False
    assert nm.should_compact(0, nm.LAYER0_CAP + 1) is True
    assert nm.should_compact(nm.MAX_LAYER, 999) is False


def test_trigram查询_切3gram():
    q = nm.to_trigram_query("雪山救援")
    assert '"雪山救"' in q and '"山救援"' in q
    assert " OR " in q


def test_trigram查询_过短返回空():
    assert nm.to_trigram_query("ab") == ""
    assert nm.to_trigram_query("") == ""


def test_trigram查询_去引号防注入():
    q = nm.to_trigram_query('a"b"c"d')
    assert '""' not in q  # 内部引号被剥


def test_渲染召回_空返回空串():
    assert nm.render_recall([]) == ""


def test_渲染召回_按回合升序():
    e1 = nm.ChronicleEntry(text="后来的详情", overview="后来的事", turn_start=10, turn_end=12)
    e2 = nm.ChronicleEntry(text="早先的详情", overview="早先的事", turn_start=1, turn_end=3)
    out = nm.render_recall([e1, e2])
    assert out.index("早先的事") < out.index("后来的事")
    assert "详情" not in out


def test_相关人物纪要优先且最多十条():
    entries = [
        nm.ChronicleEntry(text=f"详情{i}", overview=f"概览{i}", turn_end=i,
                          characters=["林月"] if i % 2 == 0 else ["旁人"])
        for i in range(1, 25)
    ]

    selected = nm.select_relevant_recent(entries, ["林月"], k=10)

    assert len(selected) == 10
    assert all("林月" in entry.characters for entry in selected)
    assert [entry.turn_end for entry in selected] == list(range(24, 4, -2))


def test_body_关键词并入正文():
    e = nm.ChronicleEntry(text="事件", keywords=["甲", "乙"])
    assert e.body() == "事件 甲 乙"
    assert nm.ChronicleEntry(text="事件").body() == "事件"
