"""聊天追问 / 续聊节奏（2026-11-10）回归测试。

对标 starlit-desktop `ConversationRunner` 的节奏：
首次 60–180s、第 2 次 2–5min、第 3 次 10–40min；最多 3 次追问；
15min 会话窗口结束后 20–90min 可重开、最多 2 次；深夜最多 1 条；
用户一开口就重置计数。
"""
from __future__ import annotations

import pytest

from app.services import chat_followup


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_followup, "_STATE_PATH", tmp_path / "followup.json")
    # 固定随机延迟为中点，便于用确定的秒数断言。
    monkeypatch.setattr(
        chat_followup.random, "uniform",
        lambda low, high: (float(low) + float(high)) / 2.0,
    )
    # 节奏用例默认不在深夜；深夜用例自己把 _in_quiet_hours 翻成 True。
    monkeypatch.setattr(
        chat_followup, "_in_quiet_hours",
        lambda _now, _start="00:30", _end="08:30": False,
    )


def test_bot_message_schedules_followup_then_reopen():
    thread = "repo__chat__甲"
    chat_followup.note_user(thread, now=900)
    assert chat_followup.due(thread, now=900) == ""

    chat_followup.note_bot(thread, now=1000)
    assert chat_followup.due(thread, now=1119) == ""      # 首次延迟 60–180s
    assert chat_followup.due(thread, now=1181) == "followup"

    chat_followup.mark(thread, "followup", now=1200)
    assert chat_followup.due(thread, now=1400) == ""      # 第 2 次 120–300s
    assert chat_followup.due(thread, now=1501) == "followup"

    chat_followup.mark(thread, "followup", now=1600)
    # 第 3 次延迟 600–2400s 已越过 15min 会话窗口 ⇒ 走「重开」而不是继续追问。
    assert chat_followup.due(thread, now=4001) == ""
    assert chat_followup.due(thread, now=4301) == "reopen"


def test_followup_and_reopen_caps_are_configurable(monkeypatch):
    """设置里的追问/重开上限生效；0=关闭对应动作。"""
    thread = "repo__chat__辛"
    monkeypatch.setattr(chat_followup, "_ACTIVE_WINDOW_SECONDS", 10_000)
    chat_followup.note_user(thread, now=900)
    chat_followup.note_bot(thread, now=1_000, max_followups=1, max_reopens=0)

    assert chat_followup.due(thread, now=1_181, max_followups=1, max_reopens=0) == "followup"
    chat_followup.mark(thread, "followup", now=1_200, max_followups=1, max_reopens=0)
    assert chat_followup.due(thread, now=9_999, max_followups=1, max_reopens=0) == ""
    # 重开关闭：窗口结束后也不重开。
    assert chat_followup.due(thread, now=99_999, max_reopens=0) == ""

    reopen_thread = "repo__chat__壬"
    monkeypatch.setattr(chat_followup, "_ACTIVE_WINDOW_SECONDS", 15 * 60)
    chat_followup.note_user(reopen_thread, now=900)
    chat_followup.note_bot(reopen_thread, now=1_000, max_followups=0, max_reopens=1)
    assert chat_followup.due(
        reopen_thread, now=1_181, max_followups=0, max_reopens=1,
    ) == ""
    assert chat_followup.due(
        reopen_thread, now=4_301, max_followups=0, max_reopens=1,
    ) == "reopen"
    chat_followup.mark(
        reopen_thread, "reopen", now=4_400, max_followups=0, max_reopens=1,
    )
    assert chat_followup.due(
        reopen_thread, now=99_999, max_followups=0, max_reopens=1,
    ) == ""


def test_followup_cadence_and_turn_cap_are_configurable():
    """频率档位、连续 bot 上限、会话窗口都可覆盖。"""
    thread = "repo__chat__癸"
    chat_followup.note_user(thread, now=900)
    chat_followup.note_bot(thread, now=1_000, cadence="frequent")
    # frequent 首条追问延迟 30–90s（中点 60s）；normal 首条 60–180s。
    assert chat_followup.due(thread, now=1_061, max_bot_turns=5) == "followup"
    assert chat_followup.due(
        thread, now=1_061, active_window_minutes=15, max_bot_turns=2,
    ) == "followup"  # bot_turns 仍为 1，未达到 cap

    capped = "repo__chat__子"
    chat_followup.note_user(capped, now=900)
    chat_followup.note_bot(capped, now=1_000, bubbles=5)
    assert chat_followup.due(capped, now=1_181, max_bot_turns=5) == ""

    windowed = "repo__chat__丑"
    chat_followup.note_user(windowed, now=900)
    chat_followup.note_bot(windowed, now=1_000)
    # 会话窗口调成 0：最后一条消息之后立刻不算「正在聊」，追问不再 due。
    assert chat_followup.due(
        windowed, now=1_181, active_window_minutes=0,
    ) == ""


def test_followup_cap_is_three_when_window_is_long(monkeypatch):
    thread = "repo__chat__庚"
    monkeypatch.setattr(chat_followup, "_ACTIVE_WINDOW_SECONDS", 10000)
    chat_followup.note_user(thread, now=900)
    chat_followup.note_bot(thread, now=1000)

    chat_followup.mark(thread, "followup", now=1200)
    chat_followup.mark(thread, "followup", now=1600)
    chat_followup.mark(thread, "followup", now=4100)
    # 仍在加长后的会话窗口内：追问计数到 3 后不再 due。
    assert chat_followup.due(thread, now=9000) == ""


def test_user_message_resets_all_followup_counters():
    thread = "repo__chat__乙"
    chat_followup.note_bot(thread, now=1000)
    assert chat_followup.due(thread, now=1181) == "followup"
    chat_followup.mark(thread, "followup", now=1200)

    chat_followup.note_user(thread, now=1300)
    assert chat_followup.due(thread, now=9999) == ""

    chat_followup.note_bot(thread, now=10000)
    assert chat_followup.due(thread, now=10181) == "followup"


def test_followups_reports_sent_count():
    """生成侧用它决定「第一次追问」还是「第二次起必须换话题」。"""
    thread = "repo__chat__丁"
    assert chat_followup.followups(thread) == 0
    chat_followup.note_bot(thread, now=1000)
    assert chat_followup.followups(thread) == 0
    chat_followup.mark(thread, "followup", now=1200)
    assert chat_followup.followups(thread) == 1


def test_reopen_after_active_window_and_two_reopen_cap():
    thread = "repo__chat__丙"
    chat_followup.note_bot(thread, now=1000)
    # 15min 会话窗口内只走 followup；窗口结束后才考虑 reopen。
    assert chat_followup.due(thread, now=1901) == ""
    assert chat_followup.due(thread, now=1000 + 5401) == "reopen"

    chat_followup.mark(thread, "reopen", now=1000 + 5401)
    assert chat_followup.due(thread, now=1000 + 5401 + 900) == ""
    assert chat_followup.due(thread, now=1000 + 5401 + 5401) == "reopen"

    chat_followup.mark(thread, "reopen", now=1000 + 5401 + 5401)
    assert chat_followup.due(thread, now=1000 + 5401 + 5401 + 5401) == ""


def test_bot_turn_cap_stops_followups():
    thread = "repo__chat__丁"
    chat_followup.note_bot(thread, bubbles=5, now=1000)
    assert chat_followup.due(thread, now=1181) == ""
    assert chat_followup.due(thread, now=99999) == ""


def test_quiet_hours_allows_only_one_message(monkeypatch):
    thread = "repo__chat__戊"
    monkeypatch.setattr(
        chat_followup, "_in_quiet_hours",
        lambda _now, _start="00:30", _end="08:30": True,
    )
    chat_followup.note_bot(thread, now=1000)
    assert chat_followup.due(thread, now=1181) == "followup"

    chat_followup.mark(thread, "followup", now=1200)
    assert chat_followup.due(thread, now=10000) == ""


def test_is_tracked_flips_after_first_bot_message():
    thread = "repo__chat__辛"
    assert chat_followup.is_tracked(thread) is False
    chat_followup.note_bot(thread, now=1000)
    assert chat_followup.is_tracked(thread) is True


def test_state_survives_reload():
    thread = "repo__chat__己"
    chat_followup.note_bot(thread, now=1000)
    # _load 每次从文件读；不依赖进程内缓存。
    assert chat_followup.due(thread, now=1181) == "followup"


def test_followup_instruction_mentions_current_state_but_stays_text_only():
    """用户没说话时，角色能说自己此刻在哪（如到了海边），但追问不再发照片。"""
    from app.services import chat_room

    assert "到了海边" in chat_room._FOLLOWUP_INSTRUCTION
    assert "这一轮只补一句文字" in chat_room._FOLLOWUP_INSTRUCTION
    assert "不要发照片/视频" in chat_room._FOLLOWUP_INSTRUCTION
    assert "发自拍" not in chat_room._FOLLOWUP_INSTRUCTION


def test_switch_followup_never_asks_about_photos():
    """第二次追问换话题时也不能问「看到了吗/怎么样」，避免追问不存在的图。"""
    from app.services import chat_room

    assert "这一轮只补一句文字" in chat_room._FOLLOWUP_SWITCH_INSTRUCTION
    assert "不要提照片" in chat_room._FOLLOWUP_SWITCH_INSTRUCTION
    assert "不要问「看到了吗/怎么样」" in chat_room._FOLLOWUP_SWITCH_INSTRUCTION
