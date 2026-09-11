"""M1 模拟 E2E（审计 #9）：删除/重生成 → 级联封口 → 同回合重生成重新抽取的全回路。

真实回路复现（LLM 用桩替换）：maybe_summarize 抽纪要+落事实 → 删楼层 →
memory_rollback 级联封口 → 重新生成同回合 → 门控重新命中 → 新纪要落盘、
旧封口纪要不再被召回（数据保留可审计）。这是「删除消息等效记忆清除」合同的闭环验证。
"""
import json
import random

from app.services import (
    memory_rollback,
    narrative_store as ns,
    roleplay_agency,
    temporal_fact_store as tfs,
)

CHRONICLE_JSON = json.dumps({
    "overview": "舞会上她递来了半杯酒",
    "chronicle": "宴会上她借敬酒接近主角，把一枚徽章塞进主角袖口，低声说了句『北门的钥匙』后离场。",
    "dialogue": "北门的钥匙。",
    "characters": ["主角", "神秘女子"],
    "keywords": ["舞会", "徽章", "北门"],
    "facts": [{"subject": "神秘女子", "predicate": "阵营", "object": "北门守军",
               "evidence": "低声说『北门的钥匙』"}],
}, ensure_ascii=False)


def _deps(base: str) -> roleplay_agency.AgencyDeps:
    return roleplay_agency.AgencyDeps(
        chat_fn=lambda *a, **k: CHRONICLE_JSON,  # 桩：抽纪要/压缩都返回同一份合格 JSON
        rng=random.Random(7), state_base=base,
    )


def test_删楼封口后同回合重生成重新抽取闭环(tmp_path):
    base, repo, card = str(tmp_path), "r1", "卡"
    deps = _deps(base)

    # ── 剧情推进：turn 3 首抽（覆盖 1-3），turn 6 再抽（覆盖 4-6）──
    assert roleplay_agency.maybe_summarize(
        deps, repo_id=repo, card_name=card, window_text="开局剧情正文……", turn=3,
        chat_base="b", chat_key="k", chat_model="m") is True
    assert roleplay_agency.maybe_summarize(
        deps, repo_id=repo, card_name=card, window_text="舞会剧情正文……", turn=6,
        chat_base="b", chat_key="k", chat_model="m") is True
    assert len(ns.recent(base, repo, k=10)) == 2
    assert tfs.as_of(base, repo, 6) != []  # 事实已入账本
    assert ns.get_last_turn(base, repo, card) == 6

    # ── 用户重新生成该 AI 楼层（回合 5 被覆盖：旧条目按 rowid 隐藏，回合区间保持开放）──
    result = memory_rollback.seal_turns(base, repo, card_names=[card], turns=[5],
                                        mode="regenerate")
    assert result["chronicle_sealed"] == 1 and result["facts_sealed"] == 1
    assert [e.turn_end for e in ns.recent(base, repo, k=10)] == [3]  # 被删回合的纪要清除，开局保留
    assert [f["object"] for f in tfs.as_of(base, repo, 9)] == ["北门守军"]  # @3 事实不受影响

    # ── 重新生成同区间（turn 6）：进度已回退 → 门控重新命中 → 新纪要落盘 ──
    assert roleplay_agency.maybe_summarize(
        deps, repo_id=repo, card_name=card, window_text="重生成后的舞会剧情……", turn=6,
        chat_base="b", chat_key="k", chat_model="m") is True

    entries = ns.recent(base, repo, k=10)
    assert len(entries) == 2  # 1-3 保留 + 重生成的新 4-6；封口旧 4-6 不掺和
    assert entries[0].turn_end == 6
    # 新事实重新入账本（封口未锁写入，重生成合法）
    assert any(f["object"] == "北门守军" for f in tfs.as_of(base, repo, 6))
    # 旧封口纪要保留可审计（导出全量可见），但永不回到召回
    assert len(ns.all_entries(base, repo)) == 3  # 封口旧纪要保留可审计（导出全量）
    recall_rowids = [e.rowid for e in ns.recall(base, repo, "徽章塞进主角袖口")]
    assert 2 not in recall_rowids  # 被重生成替换的旧纪要（rowid 2）不再召回
    assert 3 in recall_rowids  # 新纪要正常召回（1-3 未封口纪要同文本也合法命中）


def test_指认式替代_抽取链自动收口过期事实(tmp_path):
    """P3②（2026-09-06 用户裁决）：剧情推进使事实过期时，抽取模型引用现存事实 id
    → 旧事实收口、新事实生效、时间线完整、无冲突堆积。"""
    base, repo, card = str(tmp_path), "r1", "卡"
    calls = []

    def chat_fn(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            fact = {"subject": "城主", "predicate": "担任", "object": "旧城主",
                    "evidence": "登基旧典"}
        else:
            known = tfs.as_of(base, repo, 6)
            fact = {"subject": "城主", "predicate": "担任", "object": "新城主",
                    "evidence": "登基大典", "supersedes_id": known[0]["id"][:12]}
        return json.dumps({"overview": "城主更替", "chronicle": "旧城主被废，新城主接任。",
                           "dialogue": "", "characters": ["新城主"], "keywords": ["城主"],
                           "facts": [fact]}, ensure_ascii=False)

    deps = roleplay_agency.AgencyDeps(chat_fn=chat_fn, rng=random.Random(3), state_base=base)
    assert roleplay_agency.maybe_summarize(
        deps, repo_id=repo, card_name=card, window_text="旧城主时代", turn=3,
        chat_base="b", chat_key="k", chat_model="m") is True
    assert roleplay_agency.maybe_summarize(
        deps, repo_id=repo, card_name=card, window_text="政变，新城主登基", turn=6,
        chat_base="b", chat_key="k", chat_model="m") is True

    # 新事实生效、旧事实收口（指认式替代自动完成）
    assert [f["object"] for f in tfs.as_of(base, repo, 9)] == ["新城主"]
    tl = tfs.timeline(base, repo)
    assert [(f["object"], f["valid_to_turn"]) for f in tl] == [("旧城主", 5), ("新城主", None)]
    assert tfs.conflicts(base, repo, 9) == []  # 无冲突堆积
