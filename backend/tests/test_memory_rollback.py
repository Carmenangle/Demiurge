"""记忆级联封口编排（memory_rollback，M1 2026-09-06 用户定案）回归测试。

删除消息/重新生成 → 按回合级联封口纪要+时序事实+角色状态：注入侧等效清除、
数据保留可审计；meta.last_turn 回退使重生成同回合可重新抽取纪要。
"""
from app.services import (
    character_state as cs,
    memory_rollback,
    narrative_memory as nm,
    narrative_store as ns,
    temporal_fact_store as tfs,
)


def _seed(base: str, repo: str, card: str) -> None:
    ns.append(base, repo, nm.ChronicleEntry(text="雪山救援事件", turn_start=1, turn_end=3,
                                            overview="雪山救援"))
    ns.append(base, repo, nm.ChronicleEntry(text="舞会下药事件", turn_start=4, turn_end=6,
                                            overview="舞会"))
    ns.set_last_turn(base, repo, card, 6)
    tfs.record(base, repo, subject="城主", predicate="担任", object_="新城主",
               valid_from_turn=5, evidence="回合5剧情", source="chronicle")
    st = cs.CharacterState(card_name=card, repo_id=repo)
    st.数值["好感度"] = cs.NumericField(-30.0, min=-50.0, max=120.0)
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": 15, "evidence": "回合5事件"}], turn=5))
    cs.save_state(base, st)


def test_级联封口三存储且重生成可重新抽取(tmp_path):
    base, repo, card = str(tmp_path), "r1", "埃斯托利亚"
    _seed(base, repo, card)

    result = memory_rollback.seal_turns(base, repo, card_name=card, turns=[5])

    assert result == {"chronicle_sealed": 1, "facts_sealed": 1,
                      "state_undone": 1, "state_incomplete": [],
                      "last_turn_rewound": True}
    # 注入侧等效清除
    assert [e.text for e in ns.recent(base, repo, k=10)] == ["雪山救援事件"]
    assert tfs.as_of(base, repo, 9) == []
    st = cs.load_state(base, repo, card)
    assert st.数值["好感度"].value == -30.0
    # 前沿纪要（4-6）被封 → last_turn 回退到未封口前沿 3 → 重生成覆盖 4-6 后可重新抽取
    assert ns.get_last_turn(base, repo, card) == 3
    assert nm.should_summarize(ns.get_last_turn(base, repo, card), 6)


def test_封口早于现有进度的回合不动last_turn(tmp_path):
    base, repo, card = str(tmp_path), "r1", "卡"
    _seed(base, repo, card)
    result = memory_rollback.seal_turns(base, repo, card_name=card, turns=[2])
    assert result["last_turn_rewound"] is False  # last_turn=6 晚于封口回合2的语义是「已推进」，不回退
    assert result["chronicle_sealed"] == 1  # 1-3 含 2


def test_空参数与空仓库零副作用(tmp_path):
    base, repo, card = str(tmp_path), "r1", "卡"
    _seed(base, repo, card)
    empty = {"chronicle_sealed": 0, "facts_sealed": 0,
             "state_undone": 0, "state_incomplete": [], "last_turn_rewound": False}
    assert memory_rollback.seal_turns("", repo, card_name=card, turns=[1]) == empty
    assert memory_rollback.seal_turns(base, repo, card_name=card, turns=[]) == empty
    assert memory_rollback.seal_turns(base, repo, card_name=card, turns=[0, -1]) == empty


def test_多卡绑定逐卡回退抽取进度(tmp_path):
    """M1 审计 #3：一个仓库绑多张卡（各有纪要线 meta.last_turn）时，
    封口必须逐卡检查回退，不能只回退开场卡。"""
    base, repo = str(tmp_path), "r1"
    ns.append(base, repo, nm.ChronicleEntry(text="舞会事件", turn_start=4, turn_end=6,
                                            overview="舞会"))
    for card in ("开场卡", "配角卡"):
        ns.set_last_turn(base, repo, card, 6)

    result = memory_rollback.seal_turns(base, repo, card_names=["开场卡", "配角卡"], turns=[5])

    assert result["last_turn_rewound"] is True
    # 唯一纪要（4-6）被整体封口 → 未封口前沿=0，两卡进度都回退到 0（重生成后可重新抽取）
    assert ns.get_last_turn(base, repo, "开场卡") == 0
    assert ns.get_last_turn(base, repo, "配角卡") == 0
