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
    entry = nm.parse_rich_summary(raw)
    assert entry is not None
    assert entry.overview == "雪山救援后态度转暖"  # 兼容旧 summary 结构
    assert entry.text == "雪山救援后态度转暖"
    assert entry.keywords == ["雪山", "救援", "态度"]


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


def test_解析_坏JSON返回None():
    assert nm.parse_rich_summary("没有大括号") is None
    assert nm.parse_rich_summary('{坏json}') is None


def test_解析_空summary返回None():
    assert nm.parse_rich_summary('{"summary":"","keywords":["x"]}') is None


def test_解析_如实解析不机械截断且字数门槛可检出超限():
    long = "字" * (nm._SUMMARY_MAX + 100)
    entry = nm.parse_rich_summary('{"overview":"概览","summary":"' + long + '"}')
    assert entry is not None
    assert len(entry.text) == len(long)  # 解析层不再截断
    assert nm.chronicle_within_limits(entry.overview, entry.text) is False
    assert nm.chronicle_within_limits("短概览", "正文") is True


def test_压缩改写prompt给出上限与当前字数():
    user = nm.build_compress_user("概" * 40, "详" * 400)
    assert "现40字" in user and "现400字" in user
    assert "不超过30字" in nm.COMPRESS_SYSTEM
    assert "不超过300字" in nm.COMPRESS_SYSTEM


def test_解析_关键词去重限量():
    kws_raw = ",".join(f'"k{i}"' for i in range(20))
    entry = nm.parse_rich_summary(
        '{"overview":"概览","chronicle":"正文","keywords":[' + kws_raw + ']}')
    assert entry is not None and len(entry.keywords) == 16


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


def test_人物名相关优先再按时间新到旧且最多十条():
    # 上下文合同：召回排序先人物名相关、再按时间（新→旧），取 Top-k
    entries = [
        nm.ChronicleEntry(text=f"详情{i}", overview=f"概览{i}", turn_end=i, rowid=i,
                          characters=["林月"] if i % 2 == 0 else ["旁人"])
        for i in range(1, 25)
    ]

    selected = nm.select_by_relevance(entries, [], actors=["林月"], k=10)

    assert len(selected) == 10
    assert all("林月" in entry.characters for entry in selected)  # 人物相关优先占满
    assert [entry.turn_end for entry in selected] == list(range(24, 4, -2))  # 新→旧


def test_无人物命中时按时间新到旧回填():
    hits = [nm.ChronicleEntry(text="命中", overview="概览", rowid=7, turn_end=7)]
    recent = [nm.ChronicleEntry(text=f"最近{i}", overview="概览", rowid=i, turn_end=i)
              for i in (9, 7, 3)]

    selected = nm.select_by_relevance(hits, recent, actors=["路人甲"], k=10)

    assert [entry.rowid for entry in selected] == [9, 7, 3]  # 无人物命中 → 纯时间序，去重


def test_字数门槛按用户定稿_超限不截断而是留给压缩改写():
    payload = (
        '{"overview":"' + "概" * 40 + '","chronicle":"' + "详" * 400 + '",'
        '"dialogue":"","characters":["甲"],"keywords":["甲"],"facts":[]}'
    )
    entry = nm.parse_rich_summary(payload, turn_start=1, turn_end=3)
    assert entry is not None
    assert len(entry.overview) == 40 and len(entry.text) == 400  # 不截断
    assert nm.chronicle_within_limits(entry.overview, entry.text) is False


def test_纪要卡号与回合区间解耦():
    entry = nm.ChronicleEntry(text="事件", rowid=2, layer=0, turn_start=4, turn_end=6)

    assert entry.card_id() == "T1-2"


def test_body_关键词并入正文():
    e = nm.ChronicleEntry(text="事件", keywords=["甲", "乙"])
    assert e.body() == "事件 甲 乙"
    assert nm.ChronicleEntry(text="事件").body() == "事件"
