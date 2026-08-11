from app.services import continuity_compiler as cc


def test_结构化真源优先且rag明确标为证据并受token预算():
    result = cc.compile_context([
        cc.ContextSource("RAG_MEMORY", "旧纪要：她仍在城外。", False, 10),
        cc.ContextSource("CURRENT_STATE", "所在：王宫", True, 100),
        cc.ContextSource("ACTIVE_FACTS", "密道已在第42回合开启", True, 90),
    ], token_budget=100)

    assert result.text.index("CURRENT_STATE") < result.text.index("RAG_MEMORY")
    assert "evidence-only" in result.text
    assert "若与当前状态或有效事实冲突必须忽略" in result.text
    assert result.tokens <= 130


def test_重复证据行只注入一次():
    result = cc.compile_context([
        cc.ContextSource("STATE", "同一事实", True, 10),
        cc.ContextSource("RAG", "同一事实\n另一证据", False, 5),
    ])

    assert result.text.count("同一事实") == 1


def test_时序事实渲染保留回合和证据():
    text = cc.temporal_fact_text([{
        "subject": "城门", "predicate": "状态", "object": "关闭",
        "valid_from_turn": 4, "evidence": "守卫落闩",
    }])
    assert "城门｜状态｜关闭" in text
    assert "第4回合" in text and "守卫落闩" in text
