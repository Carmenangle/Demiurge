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


# ── P2：importance × recency 加权召回（2026-09-06 交接路线）──


def test_recency_weight_每过半衰期回合权重减半():
    assert nm.recency_weight(turn_end=100, reference_turn=100) == 1.0
    assert nm.recency_weight(turn_end=70, reference_turn=100) == 0.5 ** (30 / nm.RECENCY_HALF_LIFE_TURNS)
    assert nm.recency_weight(turn_end=70, reference_turn=100, half_life=10) == 0.5 ** 3
    # 未来回合（脏数据）不放大，钳到 1.0
    assert nm.recency_weight(turn_end=120, reference_turn=100) == 1.0


def test_召回打分_重要性乘时效_旧粗纪要可胜新细纪要():
    # layer2（世界观级）比 layer0 重要；重要性只能兜住「不太旧」的条目——
    # 半衰期内差距约 17.5 回合内可翻盘，更远的差距仍由 recency 主导（衰减钳制重要性）。
    old_coarse = nm.ChronicleEntry(text="旧粗", overview="概览", rowid=1, layer=2, turn_end=80)
    new_fine = nm.ChronicleEntry(text="新细", overview="概览", rowid=2, layer=0, turn_end=95)
    far_coarse = nm.ChronicleEntry(text="远粗", overview="概览", rowid=3, layer=2, turn_end=20)
    pool = [old_coarse, new_fine, far_coarse]

    assert nm.recall_score(old_coarse, reference_turn=100) > nm.recall_score(new_fine, reference_turn=100)
    assert nm.recall_score(new_fine, reference_turn=100) > nm.recall_score(far_coarse, reference_turn=100)

    selected = nm.select_by_relevance(pool, [], actors=None, k=3)
    assert [entry.text for entry in selected] == ["旧粗", "新细", "远粗"]


def test_同层同回合并列时rowid新者先():
    a = nm.ChronicleEntry(text="a", overview="概览", rowid=5, layer=1, turn_end=10)
    b = nm.ChronicleEntry(text="b", overview="概览", rowid=6, layer=1, turn_end=10)
    selected = nm.select_by_relevance([a, b], [], actors=None, k=2)
    assert [entry.text for entry in selected] == ["b", "a"]


def test_召回加权不破坏人物相关优先与同层时序():
    # 既有合同回归：人物相关优先占满 + 组内同层新→旧（重要性同层相等 → 纯 recency）
    entries = [
        nm.ChronicleEntry(text=f"详情{i}", overview=f"概览{i}", turn_end=i, rowid=i,
                          characters=["林月"] if i % 2 == 0 else ["旁人"])
        for i in range(1, 25)
    ]
    selected = nm.select_by_relevance(entries, [], actors=["林月"], k=10)
    assert [entry.turn_end for entry in selected] == list(range(24, 4, -2))


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


def test_卡号优先用位序_无位序才退回rowid():
    """读路径填了 seq ⇒ 卡号是「第几条」；写路径（未落盘/未定序）仍退回 rowid 占位。"""
    persisted = nm.ChronicleEntry(text="事件", rowid=13, layer=0, seq=1)
    assert persisted.card_id() == "T1-1"

    fresh = nm.ChronicleEntry(text="事件", layer=1)
    assert fresh.card_id() == "T2-new"


def test_位序分配_按回合先后分层连续编号():
    """2026-09-13 用户定案：整理/补跑后 rowid 与回合序脱钩，卡号须按回合位序。
    同层按 (turn_start, turn_end, rowid) 排序后 1..n；不同层各自从 1 起。"""
    late = nm.ChronicleEntry(text="第10到12回合", rowid=1, layer=0, turn_start=10, turn_end=12)
    first = nm.ChronicleEntry(text="第1到3回合", rowid=13, layer=0, turn_start=1, turn_end=3)
    mid = nm.ChronicleEntry(text="第4到6回合", rowid=7, layer=0, turn_start=4, turn_end=6)
    coarse = nm.ChronicleEntry(text="世界观级", rowid=20, layer=2, turn_start=1, turn_end=12)

    nm.assign_sequence([late, first, mid, coarse])

    assert (first.seq, mid.seq, late.seq, coarse.seq) == (1, 2, 3, 1)
    assert [e.card_id() for e in (first, mid, late, coarse)] == ["T1-1", "T1-2", "T1-3", "T3-1"]


def test_body_关键词并入正文():
    e = nm.ChronicleEntry(text="事件", keywords=["甲", "乙"])
    assert e.body() == "事件 甲 乙"
    assert nm.ChronicleEntry(text="事件").body() == "事件"


# ── P3② 指认式替代（2026-09-06 用户裁决）：现存事实清单 + supersedes_id 透传 ──


def test_抽取prompt带现存事实清单供指认():
    known = [{"id": "a" * 64, "subject": "城主", "predicate": "担任",
              "object": "旧城主", "valid_from_turn": 3}]
    out = nm.build_summary_user("窗口文本", known)
    assert "#aaaaaaaaaaaa 城主·担任=旧城主（第3回合起）" in out
    assert "supersedes_id" in out
    assert nm.build_summary_user("窗口文本", None) == nm.build_summary_user("窗口文本")


def test_解析facts透传supersedes_id():
    raw = ('{"overview":"新城主登基","chronicle":"旧城主被废，新城主接任。",'
           '"characters":["新城主"],"keywords":["城主"],'
           '"facts":[{"subject":"城主","predicate":"担任","object":"新城主",'
           '"evidence":"登基大典","supersedes_id":"abcdef123456"}]}')
    entry = nm.parse_rich_summary(raw, turn_start=4, turn_end=6)
    assert entry is not None
    assert entry.facts[0]["supersedes_id"] == "abcdef123456"


# ── 区间规范化（2026-09-13 用户定案）：覆盖计数 / 同区间归组 / 相邻边界顺延保宽 ──


def _r(rowid, ts, te):
    return nm.ChronicleEntry(text=f"事件{rowid}", turn_start=ts, turn_end=te, rowid=rowid)


def test_覆盖计数_每回合被几条纪要覆盖():
    counts = nm.coverage_counts([_r(1, 1, 3), _r(2, 3, 5)])
    assert counts[1] == 1
    assert counts[3] == 2   # 第 3 层被两条纪要覆盖（用户说的「35 出现了两次」）
    assert counts[5] == 1


def test_覆盖计数_可截到总层数():
    assert nm.coverage_counts([_r(1, 1, 99)], total_turns=5) == {
        1: 1, 2: 1, 3: 1, 4: 1, 5: 1}


def test_压缩区间集合():
    assert nm.compress_ranges(set()) == []
    assert nm.compress_ranges({1, 2, 3, 7, 9, 10}) == [(1, 3), (7, 7), (9, 10)]


def test_规范化_相邻边界顺延保宽():
    plan = nm.normalize_plan([_r(1, 33, 35), _r(2, 35, 37)])

    assert len(plan.rebases) == 1
    rebase = plan.rebases[0]
    assert (rebase.old_start, rebase.old_end) == (35, 37)
    # 用户定案：顺延为 36-38 而非截断 36-37，该条信息密度不变
    assert (rebase.new_start, rebase.new_end) == (36, 38)


def test_规范化_同区间多条归组保留最新其余让位():
    plan = nm.normalize_plan([_r(1, 1, 3), _r(5, 1, 3), _r(9, 1, 3)], total_turns=3)

    assert len(plan.duplicates) == 1
    group = plan.duplicates[0]
    assert group.rowids == [1, 5, 9]
    assert group.keep == 9        # 覆盖语义：保留最近一次生成
    assert group.drop == [1, 5]
    assert plan.rebases == []     # 去重后已无重叠，不再顺延
    assert plan.clean is False


def test_规范化_先去重再顺延_重复条目不占用未来回合():
    # 若先顺延，重复的 [4-6] 会被推到 7-9；正确顺序是先归组去重
    plan = nm.normalize_plan([_r(1, 1, 3), _r(2, 4, 6), _r(3, 4, 6)], total_turns=6)

    assert plan.duplicates[0].drop == [2]
    assert plan.rebases == []
    assert plan.gaps == []


def test_规范化_缺口按规范化后的并集计算():
    plan = nm.normalize_plan([_r(1, 1, 3), _r(2, 7, 9)], total_turns=10)

    assert plan.gaps == [(4, 6), (10, 10)]
    assert plan.covered_turns == 6


def test_规范化_顺延超出总层数会被标记():
    plan = nm.normalize_plan([_r(1, 33, 35), _r(2, 35, 37)], total_turns=37)

    assert plan.overrun == [2]    # 顺延到 38 > 37：标记提示，不改写顺延结果
    assert plan.rebases[0].new_end == 38


def test_规范化_无重叠时clean为真():
    plan = nm.normalize_plan([_r(1, 1, 3), _r(2, 4, 6)], total_turns=6)

    assert plan.clean is True
    assert plan.gaps == []


def test_规范化_忽略非法区间与未落盘条目():
    plan = nm.normalize_plan([_r(0, 1, 3), _r(1, 5, 2)], total_turns=6)

    assert plan.duplicates == [] and plan.rebases == []
    assert plan.gaps == [(1, 6)]
    assert plan.covered_turns == 0
