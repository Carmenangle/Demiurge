import json

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


# ── 纪要超限压缩兜底（2026-10-21 真机：第 1–3 层永远补不上）────────────────


def _chronicle_payload(overview: str, detail: str) -> str:
    """主请求的整包返回（ManualFillResult）。"""
    return json.dumps({"ops": [], "chronicles": [{
        "overview": overview, "chronicle": detail, "dialogue": "",
        "characters": ["卡A"], "keywords": ["事件"],
    }]}, ensure_ascii=False)


def _entry_payload(overview: str, detail: str) -> str:
    """压缩稿的返回（COMPRESS_SYSTEM 要求单条纪要对象，不是整包）。"""
    return json.dumps({
        "overview": overview, "chronicle": detail, "dialogue": "",
        "characters": ["卡A"], "keywords": ["事件"], "facts": [],
    }, ensure_ascii=False)


def test_纪要超限_压缩轮到第二轮达标_不整批失败(monkeypatch, tmp_path):
    """真机：第 1–3 层模型其实正常返回了纪要（overview 26 字合格 / chronicle 428 字超限），
    只压一次落在 296~320 字之间随机浮动 ⇒ 同一批时成时败，用户看到「补了还是空的」。
    现压最多 2 轮、任一达标即收 ⇒ 第二轮达标照样落盘。"""
    from app.services import narrative_store

    base = str(tmp_path)
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(3))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3})
    calls = []

    def fake_chat(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            return _chronicle_payload("快艇途中装虚弱引发嫉妒", "长" * 428)   # 主请求：超限
        if len(calls) == 2:
            return _entry_payload("快艇途中装虚弱引发嫉妒", "中" * 318)       # 压缩1：仍超
        return _entry_payload("快艇途中装虚弱引发嫉妒", "短" * 250)            # 压缩2：达标

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=3, batch_turns=3, overwrite=True,
        base_url="b", api_key="k", model="m", proxy="", chat_fn=fake_chat,
    )

    assert result["chronicles"] == 1
    assert result["failed_batches"] == []
    assert len(calls) == 3                     # 主请求 1 + 压缩 2 轮
    entries = narrative_store.recent(base, "r1", k=10)
    assert [(e.turn_start, e.turn_end) for e in entries] == [(1, 3)]
    assert len(entries[0].text) == 250


def test_纪要超限_两轮压缩都不达标_仍记失败不无限重试(monkeypatch, tmp_path):
    base = str(tmp_path)
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(3))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3})
    calls = []

    def fake_chat(*_args, **_kwargs):
        calls.append(1)
        return _chronicle_payload("概览", "长" * 400) if len(calls) == 1 \
            else _entry_payload("概览", "长" * 400)   # 怎么压都超

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=3, batch_turns=3, overwrite=True,
        base_url="b", api_key="k", model="m", proxy="", chat_fn=fake_chat,
    )

    assert result["chronicles"] == 0
    assert len(result["failed_batches"]) == 1
    assert "纪要抽取无效或超限" in result["failed_batches"][0]
    assert len(calls) == 3                     # 主请求 1 + 压缩 2 轮，封顶不无限重试


def test_返回空数组_重试带纠错提示_第二次成功(monkeypatch, tmp_path):
    """真机：模型连续返回 `[]`（顶层空数组）⇒ 结构性失败，缺口永远补不上。
    根因是重试原样重发同一条 prompt。现首次 prompt 不变、**重试才追加纠错提示**。
    ⚠ 且 `maxRetry` 默认 0 —— 必须仍给一次纠错机会（否则这段修复永远用不上）。"""
    from app.services import narrative_store

    base = str(tmp_path)
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(3))
    # 故意不设 maxRetry（真实默认 0）
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3})
    prompts: list[str] = []

    def fake_chat(_b, _k, _m, _system, user, **_kwargs):
        prompts.append(user)
        if len(prompts) == 1:
            return "[]"                                  # 首次：模型退化
        return _chronicle_payload("开场与初次接触", "主角落地荒岛并与舰娘初次接触。")

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=3, batch_turns=3, overwrite=True,
        base_url="b", api_key="k", model="m", proxy="", chat_fn=fake_chat,
    )

    assert result["chronicles"] == 1
    assert result["failed_batches"] == []
    assert len(prompts) == 2
    assert "上一轮输出不合格" not in prompts[0]        # 首次 prompt 逐字不变
    assert "上一轮输出不合格" in prompts[1]            # 重试才纠错
    assert "不要输出数组" in prompts[1]
    assert len(narrative_store.recent(base, "r1", k=5)) == 1


def test_空数组重试耗尽_计入失败清单(monkeypatch, tmp_path):
    base = str(tmp_path)
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(3))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3})
    calls = []

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=3, batch_turns=3, overwrite=True,
        base_url="b", api_key="k", model="m", proxy="",
        chat_fn=lambda *_a, **_k: calls.append(1) or "[]",
    )

    assert result["chronicles"] == 0
    assert len(result["failed_batches"]) == 1
    assert "结构无效" in result["failed_batches"][0]
    assert len(calls) == 2                            # 1 次原始 + 1 次纠错，封顶不无限重试


def test_覆盖是真覆盖_封口被完整包住的旧条而不是再追加一条(monkeypatch, tmp_path):
    """2026-10-21 用户质疑「『覆盖』若只追加、不把同层旧条去掉，那你叫覆盖有什么用」。
    真机：6 条重叠里点「确定」变成 9 条。现在 overwrite=True ⇒ 旧条**封口替换**
    （封口=隐藏，数据保留可审计），live 里只剩新生成的。"""
    from app.services import narrative_store

    base = _seed_covered(monkeypatch, tmp_path, [(1, 3), (4, 6)])
    assert len(narrative_store.live_entries(base, "r1")) == 2
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(6))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3})

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=6, batch_turns=3, overwrite=True,
        base_url="b", api_key="k", model="m", proxy="",
        chat_fn=lambda *_a, **_k: _chronicle_payload("新批次概览", "新生成的这一版更完整。"),
    )

    assert result["chronicles"] == 2
    assert result["sealed_overlap"] == 2        # 两条旧条被封口替换
    live = narrative_store.live_entries(base, "r1")
    assert len(live) == 2                       # 不再叠加成 4 条
    assert all(e.text == "新生成的这一版更完整。" for e in live)
    # 旧数据没物理删，仍在 all_entries 里可审计
    assert len(narrative_store.all_entries(base, "r1")) == 4


def test_覆盖时跨批的旧条也能被替换(monkeypatch, tmp_path):
    """真机：旧条 5-7 横跨 4-6 与 7-9 两个新批次 —— 按「单批包含」判断封不掉它，
    会留下一条跨批重叠。改为按**成功批次并集**判断后应被封口。"""
    from app.services import narrative_store

    base = _seed_covered(monkeypatch, tmp_path, [(4, 6), (5, 7), (8, 9), (8, 9)])
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(9))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3})

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=1, batch_turns=3, overwrite=True,
        base_url="b", api_key="k", model="m", proxy="",
        chat_fn=lambda *_a, **_k: _chronicle_payload("批次概览", "本批次发生关键事件。"),
        ranges=[[4, 9]],
    )

    assert result["chronicles"] == 2            # 4-6 / 7-9 两批
    assert result["sealed_overlap"] == 4        # 4-6、5-7、8-9、8-9 四条旧条全被封口
    live = narrative_store.live_entries(base, "r1")
    assert sorted((e.turn_start, e.turn_end) for e in live) == [(4, 6), (7, 9)]


def test_覆盖时某批失败_失败范围的旧条不封口(monkeypatch, tmp_path):
    """新的没来就绝不能先封旧的 —— 否则那几层的纪要直接消失。"""
    from app.services import narrative_store

    base = _seed_covered(monkeypatch, tmp_path, [(1, 3), (4, 6)])
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(6))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3})
    calls = []

    def fake_chat(*_a, **_k):
        calls.append(1)
        if len(calls) == 1:
            return _chronicle_payload("新批次概览", "新生成的这一版更完整。")
        return "[]"                             # 第二批失败（maxRetry=0 ⇒ 一次就放弃）

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=6, batch_turns=3, overwrite=True,
        base_url="b", api_key="k", model="m", proxy="", chat_fn=fake_chat,
    )

    assert result["chronicles"] == 1
    assert result["sealed_overlap"] == 1        # 只封 1-3 那条旧条
    assert result["failed_batches"]             # 4-6 那批如实报失败
    live = narrative_store.live_entries(base, "r1")
    assert sorted((e.turn_start, e.turn_end) for e in live) == [(1, 3), (4, 6)]  # 4-6 旧条还在


def test_跳过模式绝不封口(monkeypatch, tmp_path):
    from app.services import narrative_store

    base = _seed_covered(monkeypatch, tmp_path, [(5, 7)])
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(9))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3})

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=10, batch_turns=3, overwrite=False,
        base_url="b", api_key="k", model="m", proxy="",
        chat_fn=lambda *_a, **_k: _chronicle_payload("批次概览", "本批次发生关键事件。"),
    )

    assert result["sealed_overlap"] == 0
    assert result["covered_skipped"] == [[4, 6], [7, 9]]
    assert len(narrative_store.live_entries(base, "r1")) == 2   # 旧条 1 + 新增 1-3


# ── 已覆盖层的「覆盖 or 跳过」（2026-10-21 用户：8-9 出现多条要说明）────────


def _seed_covered(monkeypatch, tmp_path, ranges: list[tuple[int, int]]) -> str:
    """先落一批纪要，模拟「已覆盖」的层。"""
    from app.services import narrative_memory, narrative_store

    base = str(tmp_path)
    for start, end in ranges:
        narrative_store.append(base, "r1", narrative_memory.ChronicleEntry(
            text=f"第{start}-{end}层的事件经过。", overview=f"{start}-{end}概览",
            turn_start=start, turn_end=end, keywords=["事件"]))
    return base


def test_已覆盖层默认跳过_不再产出重复纪要(monkeypatch, tmp_path):
    """真机：全量补跑 [(1,3)(4,6)(7,9)] 时 7-9 整批落在既有 5-7/8-9 覆盖内 ⇒ 又生成一条
    「7-9 婚礼」⇒ 8-9 变 3 条。overwrite=False 时整批已有纪要 ⇒ 不出纪要。"""
    from app.services import narrative_store

    base = _seed_covered(monkeypatch, tmp_path, [(5, 7), (8, 9)])
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(9))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3})

    def fake_chat(*_a, **_k):
        return _chronicle_payload("批次概览", "本批次发生关键事件。")

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=10, batch_turns=3, overwrite=False,
        base_url="b", api_key="k", model="m", proxy="", chat_fn=fake_chat,
    )

    # 只有 1-3 批（1~3 未覆盖）出纪要；4-6 / 7-9 都含已覆盖层 ⇒ 跳过
    assert result["chronicles"] == 1
    assert result["covered_skipped"] == [[4, 6], [7, 9]]
    assert result["chronicle_overlap"] == [[5, 9]]
    assert len(narrative_store.live_entries(base, "r1")) == 3   # 2 条既有 + 1 条新增


def test_覆盖模式仍追加_并回显重叠层(monkeypatch, tmp_path):
    """overwrite=True（用户在弹窗选「确定」）⇒ 照常追加（与现有并存），但要回显重叠层，
    让用户知道这些层现在有两条纪要。"""
    from app.services import narrative_store

    base = _seed_covered(monkeypatch, tmp_path, [(5, 7)])
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(6))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3})

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=6, batch_turns=3, overwrite=True,
        base_url="b", api_key="k", model="m", proxy="",
        chat_fn=lambda *_a, **_k: _chronicle_payload("批次概览", "本批次发生关键事件。"),
    )

    assert result["chronicles"] == 2            # 1-3 与 4-6 都出
    assert result["covered_skipped"] == []      # 覆盖模式不跳过
    assert result["chronicle_overlap"] == [[5, 6]]
    assert len(narrative_store.live_entries(base, "r1")) == 3


def test_与已有纪要重叠且未表态_要求先确认覆盖还是跳过(monkeypatch, tmp_path):
    """overwrite=None 且本次范围落在已覆盖层 ⇒ 必须先确认，不能默默产出重复。"""

    base = _seed_covered(monkeypatch, tmp_path, [(5, 7)])
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(6))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3})
    calls = []

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=6, batch_turns=3, overwrite=None,
        base_url="b", api_key="k", model="m", proxy="",
        chat_fn=lambda *_a, **_k: calls.append(1) or "",
    )

    assert result["needs_confirmation"] is True
    assert result["chronicle_overlap"] == [[5, 6]]
    assert calls == []                          # 确认前一次模型都不调


def test_无重叠时不需要确认_照常补跑(monkeypatch, tmp_path):
    # 已覆盖 1-3；recent_turns=3 只跑 4-6 ⇒ 本次范围与已有纪要零交集 ⇒ 不弹确认
    base = _seed_covered(monkeypatch, tmp_path, [(1, 3)])
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(6))
    monkeypatch.setattr(mtf.table_store, "load_config", lambda _b, _r: {"chronicleEvery": 3})

    result = mtf.run_manual_fill(
        base=base, repo_id="r1", card_name="卡A", selected=[mtf.CHRONICLE_UID],
        recent_turns=3, batch_turns=3, overwrite=None,
        base_url="b", api_key="k", model="m", proxy="",
        chat_fn=lambda *_a, **_k: _chronicle_payload("批次概览", "本批次发生关键事件。"),
    )

    assert result["needs_confirmation"] is False
    assert result["chronicles"] == 1            # 只有 4-6 这一批（范围内无覆盖）
    assert result["covered_skipped"] == []
    assert result["chronicle_overlap"] == []


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


def test_越界区间不再静默丢弃_进入失败清单(monkeypatch, tmp_path):
    """2026-10-20 真机：缺口显示「第 10–13 层」，而本会话只有 9 层

    ⇒ 该区间每次都被 `if start <= end` 静默丢掉——既不跑、也不报错，
    用户只能靠「怎么还没补上」反推。现在必须写成一条可见的失败，
    并同时给出 `dropped_ranges` 供程序化消费。
    """
    import json

    base = str(tmp_path)
    repo = "repo-gap"
    (tmp_path / "tables.json").write_text("[]", encoding="utf-8")

    def _chat(*_a, **_k):
        return json.dumps({"ops": [], "chronicles": [{
            "overview": "概览", "chronicle": "纪要", "dialogue": "",
            "characters": ["甲"], "keywords": ["关键词"],
        }]})

    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo_id: _messages(9))

    out = mtf.run_manual_fill(
        base=base, repo_id=repo, card_name="", selected=[mtf.CHRONICLE_UID],
        recent_turns=3, batch_turns=3, overwrite=None,
        base_url="", api_key="", model="m", proxy="", chat_fn=_chat,
        ranges=[[1, 4], [10, 13]],
    )
    # 10–13 完全越界 ⇒ 不处理、但必须可见
    assert out["dropped_ranges"] == [[10, 13]]
    assert any("10–13" in item and "越界" in item for item in out["failed_batches"])
    # 1–4 照常处理（仍按 3 层一批切分）
    assert out["processed"] == 4


def test_越界上报_部分越界只裁不报():
    """部分越界是「裁剪」不是「丢弃」：[[8, 99]] 在 9 层会话里变成 8–9，不该报错。"""
    kept, dropped = mtf.normalize_ranges_with_report([[8, 99]], 9)
    assert kept == [(8, 9)]
    assert dropped == []
    kept2, dropped2 = mtf.normalize_ranges_with_report([[12, 20]], 9)
    assert kept2 == []
    assert dropped2 == [(12, 20)]
    # 层数未知（0）时不谎报丢弃
    assert mtf.normalize_ranges_with_report([[1, 3]], 0) == ([], [])
