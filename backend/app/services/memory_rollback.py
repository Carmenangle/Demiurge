"""删除/重新生成的记忆级联封口（M1，2026-09-06 用户定案）。

合同：会话展示内容（消息流/画布）就是完整的记忆与上下文——删除消息等效记忆清除，
被删回合派生的记忆不得残留影响重新生成；同时保叙事可审计可回放。

封口语义（不是物理删除）：
- 纪要（narrative_store）：封口回合旁表，召回/最近/计数不再返回重叠条目；
  meta.last_turn 逐卡回退（M1 审计 #3：仓库可绑多卡各有纪要线；重生成同回合可重新抽取）；
- 时序事实（temporal_fact_store）：该回合起仍有效的事实 valid_to_turn 收口（时间线保留）；
- 角色状态（character_state）：撤销该回合及之后的状态变更（审计可回放；
  state.json 按 repo 单文件，与卡数无关）；
- 表格/RAG/世界书：暂无回合归属（用户定案保持现状，手动清理）。

封口只由「用户删除消息 / 触发重新生成」触发，纯存储操作零 LLM。
"""
from __future__ import annotations

from app.services import character_state, narrative_store, temporal_fact_store


def seal_turns(output_dir: str, repo_id: str, *, turns: list[int],
               card_name: str = "", card_names: list[str] | None = None,
               mode: str = "delete") -> dict:
    """按回合级联封口纪要 + 时序事实 + 角色状态。返回各存储封口计数。

    turns 是被删除/重生成覆盖的剧情回合号（前端持久化在 assistant 消息上的
    turnNo，删除/重生成不再倒退）。card_names 为仓库绑定的全部卡（逐卡回退
    纪要抽取进度）；card_name 为兼容单卡入参。空参数全部零封口（无副作用）。

    mode（M1 E2E 审计补，2026-09-06）：
    - "delete"     删除消息：纪要按**回合**永久封口（sealed_turns）；
    - "regenerate" 重新生成：纪要按**条目**隐藏（sealed_rowids），回合区间保持开放，
                   重生成的新纪要写入后立即可见（旧条目永久隐藏）。
    事实/状态的封口语义两者相同（收口/回滚；重生成重记录同内容事实会重新生效）。
    """
    clean = sorted({int(t) for t in (turns or []) if int(t) > 0})
    result = {"chronicle_sealed": 0, "facts_sealed": 0,
              "state_undone": 0, "state_incomplete": [], "last_turn_rewound": False}
    if not (output_dir and repo_id) or not clean:
        return result
    min_turn, max_turn = clean[0], clean[-1]
    cards = [c for c in ([*(card_names or []), card_name] if (card_names or card_name) else [])
             if str(c or "").strip()]
    cards = list(dict.fromkeys(str(c).strip() for c in cards))
    if mode == "regenerate":
        result["chronicle_sealed"] = narrative_store.seal_entries_overlapping(
            output_dir, repo_id, turn_start=min_turn, turn_end=max_turn)
    else:
        result["chronicle_sealed"] = narrative_store.seal_turns(output_dir, repo_id, clean)
    frontier = narrative_store.unsealed_frontier(output_dir, repo_id)
    for card in cards:
        last = narrative_store.get_last_turn(output_dir, repo_id, card)
        if last > frontier:
            # 抽取进度前沿已被封（前沿纪要被删/重生成）→ 回退到未封口前沿，
            # 重生成同区间可重新抽取；早期回合封口不动进度（不打断抽取节奏）。
            narrative_store.set_last_turn(output_dir, repo_id, card, max(0, frontier))
            result["last_turn_rewound"] = True
    state_card = cards[0] if cards else ""
    if state_card:
        st = character_state.load_state(output_dir, repo_id, state_card)
        rolled = character_state.rollback_from_turn(st, turn=min_turn)
        result["state_undone"] = rolled["undone"]
        result["state_incomplete"] = rolled["incomplete"]
        if rolled["undone"] or rolled["incomplete"]:
            character_state.save_state(output_dir, st)
    result["facts_sealed"] = temporal_fact_store.seal_from_turn(output_dir, repo_id, turn=min_turn)
    return result
