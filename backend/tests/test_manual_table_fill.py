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
    # 2026-09-13：重叠明细——sheet_a 已处理到 42（请求 42-49 ⇒ 重叠 42 共 1 层）、
    # 纪要已到 43（重叠 42-43 共 2 层）。
    assert plan.overlap_turns == {"sheet_a": 1, mtf.CHRONICLE_UID: 2}


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
    narrative_store.set_last_turn(base, "r1", "卡A", 1)
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(4))
    users = []

    def fake_chat(*args, **kwargs):
        users.append(args[4])
        return ('{"ops":[],"chronicles":[{"overview":"二三回合",'
                '"chronicle":"第二三回合发生新事件。","dialogue":"",'
                '"characters":["卡A"],"keywords":["事件"]}]}')

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=3, batch_turns=3, overwrite=False,
        base_url="b", api_key="k", model="m", proxy="", chat_fn=fake_chat,
    )

    assert result["chronicles"] == 1
    assert "允许处理范围" in users[0] and '"__chronicle__": [2, 4]' in users[0]
    entries = narrative_store.recent(base, "r1", k=10)
    assert [(entry.turn_start, entry.turn_end) for entry in entries] == [(2, 4)]


def test_不满纪要频率的尾批不出纪要(monkeypatch, tmp_path):
    """2026-09-13 用户要求：37 层按每 3 层一卷补纪要时，第 37 层单层残留不单独总结；
    纪要只出到 34–36，进度仍推到 37（下条自动纪要从 38–40 起）。"""
    from app.services import narrative_store

    base = str(tmp_path)
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(7))
    users = []

    def fake_chat(*args, **kwargs):
        users.append(args[4])
        return ('{"ops":[],"chronicles":[{"overview":"批次纪要",'
                '"chronicle":"本批次发生关键事件。","dialogue":"",'
                '"characters":["卡A"],"keywords":["事件"]}]}')

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=7, batch_turns=3, overwrite=False,
        base_url="b", api_key="k", model="m", proxy="", chat_fn=fake_chat,
    )

    # 批 1–3、4–6 各出一条；批 7–7（1 层 < 频率 3）不出
    assert result["chronicles"] == 2
    entries = narrative_store.recent(base, "r1", k=10)
    assert [(entry.turn_start, entry.turn_end) for entry in entries] == [(4, 6), (1, 3)]
    # 进度仍推进到 7：下条自动纪要从下个完整窗口起
    assert narrative_store.get_last_turn(base, "r1", "卡A") == 7


def test_纪要表状态按实际覆盖口径而非游标(monkeypatch, tmp_path):
    """2026-09-13 用户实锤：只写过 1 条 35–37 的纪要、游标 T37，旧口径显示「未记录
    0 层」明显误导。新口径：unrecorded=未覆盖层数（区间并集），entries=实际条数。"""
    from app.services import narrative_store
    from app.services.narrative_memory import ChronicleEntry

    base = str(tmp_path)
    narrative_store.append(base, "r1", ChronicleEntry("只有一条", 35, 37))
    narrative_store.set_last_turn(base, "r1", "卡A", 37)   # 真实状态：游标已推到 37
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(37))

    status = mtf.table_status(base, "r1", "卡A", _messages(37))
    chron = next(item for item in status["items"] if item["uid"] == mtf.CHRONICLE_UID)

    assert chron["entries"] == 1                 # 实际条数可见
    assert chron["covered_to"] == 37
    assert chron["unrecorded"] == 34             # 37 层只盖住 3 层 ⇒ 缺 34 层（旧口径=0）
    assert chron["last_turn"] == 37              # 游标仍单独展示


def test_空白纪要表状态显示全部未覆盖(monkeypatch, tmp_path):
    """空白库（0 条纪要）曾因游标 0 而恰好显示「未记录=total」——游标若被推满
    （如只推进度没写条）就会误报 0 层；区间口径下永远反映真实缺口。"""
    from app.services import narrative_store

    base = str(tmp_path)
    narrative_store.set_last_turn(base, "r1", "卡A", 37)   # 只推进度、没写任何条
    status = mtf.table_status(base, "r1", "卡A", _messages(37))
    chron = next(item for item in status["items"] if item["uid"] == mtf.CHRONICLE_UID)

    assert chron["entries"] == 0
    assert chron["unrecorded"] == 37             # 0 条纪要 ⇒ 全部未覆盖（不会被游标掩盖）


def test_单批解析失败容错_跳过失败批_成功批照常落盘(monkeypatch, tmp_path):
    """2026-09-13 用户实锤：一批 JSON 截断曾毁掉整次任务（ValueError 直接抛出、
    已成功批次全部白跑）。现按 maxRetry 重试，仍失败跳过该批并记入 failed_batches。"""
    from app.services import narrative_store

    base = str(tmp_path)
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(6))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3, "maxRetry": 1})
    payload_ok = ('{"ops":[],"chronicles":[{"overview":"批次纪要",'
                  '"chronicle":"本批次发生关键事件。","dialogue":"",'
                  '"characters":["卡A"],"keywords":["事件"]}]}')
    calls = []

    def fake_chat(*args, **kwargs):
        calls.append(1)
        # 第 1 批（1-3 层）第一次成功；第 2 批（4-6 层）连续两次都返回截断 JSON
        return payload_ok if len(calls) <= 1 else '{"ops":[{"table":"背包'

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=6, batch_turns=3, overwrite=False,
        base_url="b", api_key="k", model="m", proxy="", chat_fn=fake_chat,
    )

    assert len(calls) == 3                       # 批1 成功 1 次 + 批2 尝试 1+maxRetry(默认1) 次
    assert result["chronicles"] == 1             # 成功批照常落盘
    assert len(result["failed_batches"]) == 1
    assert "4–6" in result["failed_batches"][0]
    entries = narrative_store.recent(base, "r1", k=10)
    assert [(entry.turn_start, entry.turn_end) for entry in entries] == [(1, 3)]


def test_填表进度上报_批次推进与结束清理(monkeypatch, tmp_path):
    base = str(tmp_path)
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(4))

    def fake_chat(*args, **kwargs):
        return '{"ops":[],"chronicles":[]}'

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=4, batch_turns=3, overwrite=False,
        base_url="b", api_key="k", model="m", proxy="", chat_fn=fake_chat,
    )

    assert result["ok"] is True
    # 任务结束后进度清理（running=0），轮询端点不会残留旧进度
    assert mtf.get_fill_progress(f"{base}|r1") == {"running": 0, "batch_done": 0, "batch_total": 0}


def test_空纪要纳入重试_第二次成功(monkeypatch, tmp_path):
    """2026-09-13 用户实锤 4–6/7–9 静默丢失：模型返回合法 JSON 但 chronicles 为空，
    旧逻辑既不重试也不反馈。现空纪要触发重试，重试成功照常落盘。"""
    from app.services import narrative_store

    base = str(tmp_path)
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(6))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3, "maxRetry": 1})
    payload_ok = ('{"ops":[],"chronicles":[{"overview":"批次纪要",'
                  '"chronicle":"本批次发生关键事件。","dialogue":"",'
                  '"characters":["卡A"],"keywords":["事件"]}]}')
    calls = []

    def fake_chat(*args, **kwargs):
        calls.append(1)
        return '{"ops":[],"chronicles":[]}' if len(calls) == 1 else payload_ok

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=6, batch_turns=3, overwrite=False,
        base_url="b", api_key="k", model="m", proxy="", chat_fn=fake_chat,
    )

    assert result["chronicles"] == 2              # 批1 空纪要重试成功 + 批2 正常
    assert result["failed_batches"] == []
    entries = narrative_store.recent(base, "r1", k=10)
    assert [(entry.turn_start, entry.turn_end) for entry in entries] == [(4, 6), (1, 3)]


def test_空纪要重试耗尽计入失败清单(monkeypatch, tmp_path):
    from app.services import narrative_store

    base = str(tmp_path)
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(6))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3, "maxRetry": 1})

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=6, batch_turns=3, overwrite=False,
        base_url="b", api_key="k", model="m", proxy="",
        chat_fn=lambda *a, **k: '{"ops":[],"chronicles":[]}',
    )

    assert result["chronicles"] == 0
    assert len(result["failed_batches"]) == 2     # 两批都空纪要 → 各记一条
    assert "模型未返回纪要" in result["failed_batches"][0]
    assert narrative_store.get_last_turn(base, "r1", "卡A") == 6


# ── 缺口补跑（2026-09-13 用户要求「针对空缺建立索引」）────────────────────


def test_区间规整_丢非法_裁越界_合并相邻():
    assert mtf.normalize_ranges(None, 37) == []
    assert mtf.normalize_ranges([[25, 27], [37, 37]], 37) == [(25, 27), (37, 37)]
    assert mtf.normalize_ranges([[27, 25]], 37) == [(25, 27)]      # 反向区间自动纠正
    assert mtf.normalize_ranges([[30, 99]], 37) == [(30, 37)]      # 越界裁剪
    assert mtf.normalize_ranges([[5, 9], [10, 12]], 37) == [(5, 12)]  # 相邻合并
    assert mtf.normalize_ranges([[5, 9], [8, 12]], 37) == [(5, 12)]   # 重叠合并
    assert mtf.normalize_ranges([[0, 0], "坏", [3], [7, 8]], 37) == [(7, 8)]
    assert mtf.normalize_ranges([[1, 3]], 0) == []                 # 空会话无区间


def test_区间切批_每段单独起批不跨缺口():
    assert mtf.chunk_spans([(25, 27), (37, 37)], 3) == [(25, 27), (37, 37)]
    assert mtf.chunk_spans([(1, 7)], 3) == [(1, 3), (4, 6), (7, 7)]
    assert mtf.chunk_spans([], 3) == []


def test_缺口补跑只处理指定层且短区间也出纪要(monkeypatch, tmp_path):
    """用户实锤「未看到针对空缺建立索引的功能」：给了显式区间后，只跑这几层，
    且短于纪要频率的缺口（37–37）也照常出纪要——否则缺口永远补不上。"""
    from app.services import narrative_store

    base = str(tmp_path)
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(37))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3})
    users = []

    def fake_chat(*args, **kwargs):
        users.append(args[4])
        return ('{"ops":[],"chronicles":[{"overview":"缺口纪要",'
                '"chronicle":"补齐的缺口事件。","dialogue":"",'
                '"characters":["卡A"],"keywords":["事件"]}]}')

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=5, batch_turns=3, overwrite=None, ranges=[[25, 27], [37, 37]],
        base_url="b", api_key="k", model="m", proxy="", chat_fn=fake_chat,
    )

    # 只跑了 25–27 与 37 两批（不是从某处一路到 37），且不弹覆盖确认
    assert result["needs_confirmation"] is False
    assert result["processed"] == 4      # 25–27 共 3 层 + 37 共 1 层
    assert len(users) == 2
    assert '"__chronicle__": [25, 27]' in users[0]
    assert '"__chronicle__": [37, 37]' in users[1]     # 尾批只发缺口两层，不带 28–36
    entries = narrative_store.recent(base, "r1", k=10)
    assert [(e.turn_start, e.turn_end) for e in entries] == [(37, 37), (25, 27)]
    # 缺口补跑不推游标：进度必须由覆盖并集体现，否则「中间补一卷」被记成「37 层全处理」
    assert narrative_store.get_last_turn(base, "r1", "卡A") == 0


def test_缺口补跑进度不推游标但覆盖并集可见(monkeypatch, tmp_path):
    from app.services import narrative_store

    base = str(tmp_path)
    narrative_store.set_last_turn(base, "r1", "卡A", 36)     # 已有游标 36（27 之后到此）
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(37))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3})

    mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=5, batch_turns=3, overwrite=None, ranges=[[25, 27]],
        base_url="b", api_key="k", model="m", proxy="",
        chat_fn=lambda *a, **k: ('{"ops":[],"chronicles":[{"overview":"缺口纪要",'
                                 '"chronicle":"补齐的缺口事件。","dialogue":"",'
                                 '"characters":["卡A"],"keywords":["事件"]}]}'),
    )

    assert narrative_store.get_last_turn(base, "r1", "卡A") == 36   # 游标不动
    status = mtf.table_status(base, "r1", "卡A", _messages(37))
    chron = next(item for item in status["items"] if item["uid"] == mtf.CHRONICLE_UID)
    assert chron["entries"] == 1 and chron["covered_to"] == 27      # 缺口补上，覆盖可见


def test_区间模式下通用表进度不被误推(monkeypatch, tmp_path):
    """缺口补跑只点名纪要表时，通用表的已处理游标绝不能被推到 len(turns)。"""
    from app.services import narrative_store
    from app.services.narrative_memory import ChronicleEntry

    base = str(tmp_path)
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(37))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3})
    narrative_store.append(base, "r1", ChronicleEntry("旧条", 1, 3))

    mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=["sheet_a", mtf.CHRONICLE_UID],
        recent_turns=5, batch_turns=3, overwrite=None, ranges=[[25, 27]],
        base_url="b", api_key="k", model="m", proxy="",
        chat_fn=lambda *a, **k: '{"ops":[],"chronicles":[{"overview":"缺口纪要",'
                                '"chronicle":"补齐的缺口事件。","dialogue":"",'
                                '"characters":["卡A"],"keywords":["事件"]}]}',
    )

    assert mtf.load_progress(base, "r1") == {}          # sheet_a 未被标记已处理
    assert narrative_store.get_last_turn(base, "r1", "卡A") == 0


def test_整理让位后状态条数与可见列表一致(monkeypatch, tmp_path):
    """2026-09-13 用户实锤：整理封口后界面 12 条、状态页仍报 23 条。条数/覆盖必须按
    **未封口**集合（与列表、位序同一集合）统计。"""
    from app.services import narrative_store
    from app.services.narrative_memory import ChronicleEntry

    base = str(tmp_path)
    old = narrative_store.append(base, "r1", ChronicleEntry("重复旧条", 1, 3))
    narrative_store.append(base, "r1", ChronicleEntry("在位的条", 1, 3))
    narrative_store.append(base, "r1", ChronicleEntry("后半段", 4, 6))
    narrative_store.normalize_apply(base, "r1", seal_rowids=[old])

    status = mtf.table_status(base, "r1", "卡A", _messages(6))
    chron = next(item for item in status["items"] if item["uid"] == mtf.CHRONICLE_UID)

    assert len(narrative_store.all_entries(base, "r1")) == 3   # 库内仍 3 条（可审计）
    assert chron["entries"] == 2                               # 界面可见 2 条
    assert chron["covered_to"] == 6
    assert chron["unrecorded"] == 0


def test_会话层数取自快照且不可读时退回零(monkeypatch):
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(7))
    assert mtf.session_turn_count("r1") == 7

    monkeypatch.setattr(mtf.chat_snapshot, "load",
                        lambda _repo_id: (_ for _ in ()).throw(OSError()))
    assert mtf.session_turn_count("r1") == 0
