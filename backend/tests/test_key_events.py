"""关键事件/纪念装扮账本回归（2026-11-10）。

覆盖：
1. 用户点名且正文实际发生的婚礼/婚纱写入任务与事件表，重复扫描按名称更新不重复；
2. 只有计划、正文没发生 ⇒ 不记；<think> 里提到但正文没写也不记；
3. 重建索引按区间补账本，不越界扫描；
4. 私聊资料优先注入关键事件块（先于普通数据表），婚礼/婚纱不会被摘要漏掉。
"""
from __future__ import annotations

from app.services import chat_story_context, key_events, table_store
from app.services.agent_contracts import RunContext


def _ctx(tmp_path) -> RunContext:
    return RunContext(
        thread_id="repo-1__chat",
        message="在吗",
        output_dir=str(tmp_path),
        repo_id="repo-1",
        card_names=["腓特烈大帝"],
        user_name="用户",
    )


def _seed_tables(monkeypatch, tmp_path) -> str:
    base = str(tmp_path)
    monkeypatch.setattr(table_store, "tables_path", lambda *_a: tmp_path / "tables.json")
    tables = table_store.default_tables()
    for table in tables:
        if table.get("name") == table_store.GLOBAL_TABLE:
            table["rows"] = [["深夜", "旧仓库礼堂", "安静", "无"]]
    table_store.save(base, "repo-1", tables)
    return base


def _quests(base: str) -> list[list[str]]:
    tables = table_store.load(base, "repo-1")
    quests = next(t for t in tables if t.get("name") == table_store.QUESTS_TABLE)
    return quests.get("rows") or []


def test_record_turn_writes_wedding_and_dedupes(monkeypatch, tmp_path):
    base = _seed_tables(monkeypatch, tmp_path)
    assistant = (
        "<think>灰魂吐槽：婚礼和婚纱。</think>"
        "<status>[地点] 旧仓库改成的礼堂</status>"
        "<content>白色的婚纱把她近两米的身形整个托起来，裙撑撑得很开，"
        "头纱从发顶垂到腰后，脚上是白色的高跟。</content>"
    )

    changed = key_events.record_turn(
        base, "repo-1", turn=9, user="办婚礼，进行婚纱环节", assistant=assistant,
    )

    assert changed == 1
    rows = _quests(base)
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == "[关键事件] 第9回合·婚礼/婚纱"
    assert "已完成" in row[2]
    assert "白色" in row[4] and "婚纱" in row[4]
    assert "灰魂吐槽" not in row[4] and "[地点]" not in row[4]
    assert "禁止删除" in row[7]

    # 同一回合重跑（例如重生成后正文更细）只更新，不产生第二行。
    changed = key_events.record_turn(
        base, "repo-1", turn=9, user="办婚礼，进行婚纱环节",
        assistant="<content>她换上白色婚纱，头纱被海风吹起。</content>",
    )
    assert changed == 1
    rows = _quests(base)
    assert len(rows) == 1
    assert "头纱被海风吹起" in rows[0][4]


def test_record_turn_skips_plan_without_actual_event(monkeypatch, tmp_path):
    base = _seed_tables(monkeypatch, tmp_path)

    assert key_events.record_turn(
        base, "repo-1", turn=3, user="以后想办婚礼", assistant="好啊，等以后再说。",
    ) == 0
    assert _quests(base) == []


def test_think_only_outfit_does_not_count(monkeypatch, tmp_path):
    """<think> 里提到婚纱但正文没写 ⇒ 不算实际发生。"""
    base = _seed_tables(monkeypatch, tmp_path)

    assert key_events.record_turn(
        base, "repo-1", turn=4, user="换婚纱",
        assistant="<think>本来想写婚纱，但最后没写。</think><content>她换回了军装。</content>",
    ) == 0
    assert _quests(base) == []


def test_record_judged_uses_model_taxonomy_event(monkeypatch, tmp_path):
    """主路径：维护模型按事件类型给出 key_events，不依赖词表也能记下关键情节。"""
    base = _seed_tables(monkeypatch, tmp_path)

    changed = key_events.record_judged(base, "repo-1", [{
        "title": "正式结为伴侣",
        "detail": "在旧仓库礼堂交换誓约戒，她穿白色婚纱、头纱垂到腰后。",
        "turn": 9,
        "characters": ["腓特烈大帝"],
    }])

    assert changed == 1
    row = _quests(base)[0]
    assert row[0] == "[关键事件] 第9回合·正式结为伴侣"
    assert "白色婚纱" in row[4]
    assert row[5] == "腓特烈大帝"


def test_parse_events_reads_maintenance_payload():
    assert key_events.parse_events(
        '{"ops":[],"key_events":[{"title":"婚礼","detail":"白色婚纱"}]}'
    ) == [{"title": "婚礼", "detail": "白色婚纱"}]
    assert key_events.parse_events('{"ops":[]}') == []
    assert key_events.parse_events("not json") == []


def test_backfill_prefers_model_events_over_fallback(monkeypatch, tmp_path):
    base = _seed_tables(monkeypatch, tmp_path)
    turns = [
        key_events_turn(9, "办婚礼，穿婚纱", "她穿着白色婚纱走进礼堂。"),
        key_events_turn(10, "第一次一起看海", "两人第一次一起看海，她靠在肩上。"),
    ]

    changed = key_events.backfill_turns(
        base, "repo-1", turns, ranges=[(9, 10)],
        events=[{
            "title": "第一次一起看海",
            "detail": "两人第一次一起看海，她靠在主角肩上。",
            "turn": 10,
            "characters": ["腓特烈大帝"],
        }],
    )

    assert changed == 2
    names = [row[0] for row in _quests(base)]
    assert "[关键事件] 第9回合·婚礼/婚纱" in names
    assert "[关键事件] 第10回合·第一次一起看海" in names


def test_summary_prompt_includes_key_event_taxonomy():
    """纪要主路径：按事件类型判断，不靠前端/后端穷举词表。"""
    from app.services import narrative_memory, table_update

    assert table_update.maintenance_instruction([]) == ""
    text = narrative_memory.SUMMARY_SYSTEM
    assert '"key_events"' in text
    assert "【什么值得记成 key_events】" in text
    assert "普通换衣" in text


def test_backfill_respects_ranges(monkeypatch, tmp_path):
    base = _seed_tables(monkeypatch, tmp_path)
    turns = [
        key_events_turn(8, "普通推进", "他们继续赶路。"),
        key_events_turn(9, "办婚礼，穿婚纱", "她穿着白色婚纱走进礼堂。"),
        key_events_turn(10, "婚后第二天", "她换回军装。"),
    ]

    changed = key_events.backfill_turns(base, "repo-1", turns, ranges=[(9, 9)])

    assert changed == 1
    rows = _quests(base)
    assert len(rows) == 1
    assert "第9回合" in rows[0][0]


def key_events_turn(turn: int, user: str, assistant: str):
    class _Turn:
        pass

    item = _Turn()
    item.turn = turn
    item.user = user
    item.assistant = assistant
    return item


def test_chat_story_context_injects_key_events_before_tables(monkeypatch, tmp_path):
    base = _seed_tables(monkeypatch, tmp_path)
    key_events.record_turn(
        base, "repo-1", turn=9, user="办婚礼，穿婚纱",
        assistant="她穿着白色婚纱，头纱垂到腰后。",
    )
    monkeypatch.setattr(
        "app.services.character_state.state_path", lambda *_a: tmp_path / "missing.json",
    )

    text = chat_story_context.build_for_speaker(
        _ctx(tmp_path), speaker="腓特烈大帝", members=["腓特烈大帝"], query="还记得婚礼吗",
    )

    assert "【关键事件与纪念（长期保留；只作背景，不要当成当前正在发生）】" in text
    assert "白色婚纱" in text
    assert text.index("【关键事件与纪念") < text.index("【剧情数据表")
