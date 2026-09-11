from app.services import manual_table_fill as mtf


def _messages(turns: int) -> list[dict[str, str]]:
    result = []
    for turn in range(1, turns + 1):
        result.extend([
            {"role": "user", "text": f"用户{turn}"},
            {"role": "assistant", "text": f"剧情{turn}"},
        ])
    return result


def test_手动范围与已处理范围重叠时要求确认():
    plan = mtf.plan_manual_fill(
        total_turns=49,
        recent_turns=8,
        selected=["sheet_a", mtf.CHRONICLE_UID],
        last_turns={"sheet_a": 42, mtf.CHRONICLE_UID: 43},
        overwrite=None,
    )

    assert plan.needs_confirmation is True
    assert plan.requested_start == 42
    assert plan.minimum_unrecorded == 6


def test_请求层数超过整个空白会话不误报覆盖():
    plan = mtf.plan_manual_fill(
        total_turns=8,
        recent_turns=10,
        selected=[mtf.CHRONICLE_UID],
        last_turns={mtf.CHRONICLE_UID: 0},
        overwrite=None,
    )

    assert plan.needs_confirmation is False
    assert plan.requested_start == 1


def test_不覆盖时每张表跳过自己已经处理的消息():
    plan = mtf.plan_manual_fill(
        total_turns=49,
        recent_turns=8,
        selected=["sheet_a", mtf.CHRONICLE_UID],
        last_turns={"sheet_a": 42, mtf.CHRONICLE_UID: 43},
        overwrite=False,
    )

    assert plan.needs_confirmation is False
    assert plan.starts == {"sheet_a": 43, mtf.CHRONICLE_UID: 44}


def test_覆盖只清理与消息范围重叠的纪要(tmp_path):
    from app.services import narrative_store
    from app.services.narrative_memory import ChronicleEntry

    base = str(tmp_path)
    rid_old = narrative_store.append(base, "r1", ChronicleEntry("旧纪要", 1, 3))
    rid_overlap = narrative_store.append(base, "r1", ChronicleEntry("重叠纪要", 4, 6))

    removed = mtf.remove_overlapping_chronicles(base, "r1", 5, 8)

    assert removed == 1
    assert narrative_store.get_by_rowid(base, "r1", rid_old) is not None
    assert narrative_store.get_by_rowid(base, "r1", rid_overlap) is None


def test_手动填表确认前不调用模型(monkeypatch, tmp_path):
    from app.services import narrative_store

    base = str(tmp_path)
    narrative_store.set_last_turn(base, "r1", "卡A", 3)
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(4))
    calls = []

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=3, batch_turns=3, overwrite=None,
        base_url="b", api_key="k", model="m", proxy="",
        chat_fn=lambda *args, **kwargs: calls.append(args) or "{}",
    )

    assert result["needs_confirmation"] is True
    assert calls == []


def test_不覆盖时只补未记录回合(monkeypatch, tmp_path):
    from app.services import narrative_store

    base = str(tmp_path)
    narrative_store.set_last_turn(base, "r1", "卡A", 3)
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(4))
    users = []

    def fake_chat(*args, **kwargs):
        users.append(args[4])
        return ('{"ops":[],"chronicles":[{"overview":"第四回合",'
                '"chronicle":"第四回合发生新事件。","dialogue":"",'
                '"characters":["卡A"],"keywords":["事件"]}]}')

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=3, batch_turns=3, overwrite=False,
        base_url="b", api_key="k", model="m", proxy="", chat_fn=fake_chat,
    )

    assert result["chronicles"] == 1
    assert "允许处理范围" in users[0] and '"__chronicle__": [4, 4]' in users[0]
    entries = narrative_store.recent(base, "r1", k=10)
    assert [(entry.turn_start, entry.turn_end) for entry in entries] == [(4, 4)]
