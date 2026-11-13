"""角色主动找上门（2026-11-09）回归测试。

覆盖：
1. 发起条件：有剧情沉淀 + 已认识且好感度 ≥ 门槛 + 聊天闲置 + 冷却已过；
2. 状态表好感度读取（`角色·好感度` 优先，裸 `好感度` 回退）；
3. 主动生成 1~2 条气泡并落盘，不产生用户消息；
4. 模型沉默/复读不发消息，也不写冷却。
"""
from __future__ import annotations

import threading
import time

import pytest

from app.services import chat_initiative, chat_room, chat_story_context
from app.services.agent_contracts import RunContext
from app.services.character_state import CharacterState, NumericField


def _ctx(tmp_path, **over) -> RunContext:
    base = dict(
        thread_id="repo-1__chat",
        message="",
        output_dir=str(tmp_path),
        repo_id="repo-1",
        card_name="甲",
        card_names=["甲"],
        chat_members=["甲"],
        interaction_mode="chat",
    )
    base.update(over)
    return RunContext(**base)


def _state(affinity: float | None = None, *, bare: bool = False) -> CharacterState:
    state = CharacterState(card_name="甲", repo_id="repo-1")
    if affinity is not None:
        key = "好感度" if bare else "甲·好感度"
        state.数值[key] = NumericField(affinity)
    return state


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from app.services import chat_followup, chat_snapshot

    monkeypatch.setattr(chat_initiative, "_STATE_PATH", tmp_path / "initiative.json")
    monkeypatch.setattr(chat_followup, "_STATE_PATH", tmp_path / "followup.json")
    monkeypatch.setattr(chat_story_context, "has_story_material", lambda _ctx: True)
    monkeypatch.setattr(chat_snapshot, "last_write_at", lambda _thread: 0.0)
    monkeypatch.setattr(chat_room, "_read_card", lambda _ctx, _name: None)


def test_decide_requires_story_affinity_and_idle(monkeypatch, tmp_path):
    from app.services import character_state

    monkeypatch.setattr(character_state, "load_state", lambda *_a: _state(40))
    assert chat_initiative.decide(_ctx(tmp_path), speaker="甲")[0] is True

    monkeypatch.setattr(character_state, "load_state", lambda *_a: _state(10))
    ok, reason = chat_initiative.decide(_ctx(tmp_path), speaker="甲")
    assert ok is False and reason.startswith("affinity_low")

    monkeypatch.setattr(character_state, "load_state", lambda *_a: _state())
    assert chat_initiative.decide(_ctx(tmp_path), speaker="甲") == (False, "no_affinity")

    monkeypatch.setattr(chat_story_context, "has_story_material", lambda _ctx: False)
    assert chat_initiative.decide(_ctx(tmp_path), speaker="甲") == (False, "no_story_material")


def test_decide_respects_recent_chat_and_cooldown(monkeypatch, tmp_path):
    from app.services import character_state, chat_snapshot

    monkeypatch.setattr(character_state, "load_state", lambda *_a: _state(40))
    now = time.time()
    monkeypatch.setattr(chat_snapshot, "last_write_at", lambda _thread: now - 60)
    ok, reason = chat_initiative.decide(_ctx(tmp_path), speaker="甲", now=now)
    assert ok is False and reason.startswith("recent_chat")

    monkeypatch.setattr(chat_snapshot, "last_write_at", lambda _thread: 0.0)
    ctx = _ctx(tmp_path)
    chat_initiative.mark(ctx.thread_id, now=now)
    assert chat_initiative.decide(ctx, speaker="甲", now=now + 60) == (False, "cooldown")
    assert chat_initiative.decide(ctx, speaker="甲", now=now + chat_initiative._COOLDOWN_SECONDS + 1)[0] is True


def test_decide_allows_story_progress_after_recent_chat(monkeypatch, tmp_path):
    """剧情刚推进（如表格记录她到了海边）时，私聊刚写过也可主动开口；仍受冷却约束。"""
    from app.services import character_state, chat_snapshot

    monkeypatch.setattr(character_state, "load_state", lambda *_a: _state(40))
    now = time.time()

    def last_write(thread_id: str) -> float:
        # 私聊 60s 前写过；作品剧情快照 30s 前刚推进。
        return now - 60 if thread_id.endswith("__chat") else now - 30

    monkeypatch.setattr(chat_snapshot, "last_write_at", last_write)
    ctx = _ctx(tmp_path)
    assert chat_initiative.decide(ctx, speaker="甲", now=now)[0] is True

    chat_initiative.mark(ctx.thread_id, now=now - 60)
    assert chat_initiative.decide(ctx, speaker="甲", now=now) == (False, "cooldown")


def test_decide_idle_and_cooldown_follow_settings(monkeypatch, tmp_path):
    """设置里的闲置/冷却分钟数生效；0=取消对应门槛。"""
    from app.services import character_state, chat_snapshot

    monkeypatch.setattr(character_state, "load_state", lambda *_a: _state(40))
    now = time.time()
    monkeypatch.setattr(chat_snapshot, "last_write_at", lambda _thread: now - 600)

    # 默认闲置 120 分钟：10 分钟前刚聊过 → 不发起。
    assert chat_initiative.decide(_ctx(tmp_path), speaker="甲", now=now)[0] is False
    # 设置闲置 5 分钟：允许发起。
    ctx = _ctx(tmp_path, chat_initiative_idle_minutes=5)
    assert chat_initiative.decide(ctx, speaker="甲", now=now)[0] is True

    # 默认冷却 6 小时：刚主动过 → 不发起；冷却 0 → 允许。
    chat_initiative.mark(ctx.thread_id, now=now)
    assert chat_initiative.decide(
        _ctx(tmp_path, chat_initiative_idle_minutes=0), speaker="甲", now=now,
    )[0] is False
    assert chat_initiative.decide(
        _ctx(
            tmp_path, chat_initiative_idle_minutes=0,
            chat_initiative_cooldown_minutes=0,
        ),
        speaker="甲", now=now,
    )[0] is True
    # 好感度门槛也可调：40 < 80 → 不发起。
    assert chat_initiative.decide(
        _ctx(
            tmp_path, chat_initiative_idle_minutes=0,
            chat_initiative_cooldown_minutes=0, chat_min_affinity=80,
        ),
        speaker="甲", now=now,
    )[0] is False


def test_decide_daily_cap_follows_settings(monkeypatch, tmp_path):
    """闲置/冷却都设 0 时，每日主动上限仍兜底；0=不限，跨天自动重置。"""
    from app.services import character_state, chat_snapshot

    monkeypatch.setattr(character_state, "load_state", lambda *_a: _state(40))
    monkeypatch.setattr(chat_snapshot, "last_write_at", lambda _thread: 0.0)
    now = time.time()
    ctx = _ctx(
        tmp_path, chat_initiative_idle_minutes=0,
        chat_initiative_cooldown_minutes=0, chat_initiative_daily_max=2,
    )

    for _ in range(2):
        chat_initiative.mark(ctx.thread_id, now=now)

    assert chat_initiative.daily_count(ctx.thread_id, now=now) == 2
    assert chat_initiative.decide(ctx, speaker="甲", now=now) == (False, "daily_cap:2")
    # 跨天：计数归零，允许再次主动。
    assert chat_initiative.decide(
        ctx, speaker="甲", now=now + 24 * 3600,
    )[0] is True
    # 0=不限：当天已发满也放行。
    assert chat_initiative.decide(
        _ctx(
            tmp_path, chat_initiative_idle_minutes=0,
            chat_initiative_cooldown_minutes=0, chat_initiative_daily_max=0,
        ),
        speaker="甲", now=now,
    )[0] is True


def test_old_flat_state_file_is_still_readable(tmp_path, monkeypatch):
    """旧格式 {thread: 时间戳} 升级后仍能读冷却；新写入转为带当日计数的结构。"""
    path = chat_initiative.state_path()
    path.write_text('{"repo-1__chat": 123.0}', encoding="utf-8")

    assert chat_initiative.last_at("repo-1__chat") == 123.0
    assert chat_initiative.daily_count("repo-1__chat") == 0

    chat_initiative.mark("repo-1__chat", now=456.0)

    assert chat_initiative.last_at("repo-1__chat") == 456.0
    assert chat_initiative.daily_count("repo-1__chat", now=456.0) == 1


def test_affinity_for_prefers_owner_key_and_falls_back_to_bare(monkeypatch, tmp_path):
    from app.services import character_state

    monkeypatch.setattr(character_state, "load_state", lambda *_a: _state(55))
    assert chat_initiative.affinity_for(_ctx(tmp_path), "甲") == 55

    monkeypatch.setattr(character_state, "load_state", lambda *_a: _state(23, bare=True))
    assert chat_initiative.affinity_for(_ctx(tmp_path), "甲") == 23


def _capture_chat(monkeypatch, reply: str):
    calls: list[list[dict]] = []

    def fake_chat(_base, _key, _model, messages, **_kwargs):
        calls.append(messages)
        return reply

    monkeypatch.setattr(chat_room.llm, "chat_messages", fake_chat)
    return calls


def test_proactive_turn_generates_bubbles_and_marks_cooldown(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_initiative, "decide", lambda *_a, **_k: (True, "ok"))
    calls = _capture_chat(monkeypatch, "想你了。\n今天过得怎么样？")
    saved: list[tuple[str, str]] = []

    def fake_persist(_ctx, speaker, text, media_request=None, thread_id=None, poke=False):
        saved.append((speaker, text))
        return {"id": f"m{len(saved)}", "ts": 1}

    monkeypatch.setattr(chat_room, "_persist", fake_persist)
    ctx = _ctx(tmp_path)

    result = chat_room.proactive_turn(ctx)

    # 主动找上门先「戳了戳你」，再发 1–2 条开场；poke 不计入对话气泡。
    assert result[0]["poke"] is True
    assert result[0]["text"] == "戳了戳你"
    assert [item["speaker"] for item in result if not item.get("poke")] == ["甲", "甲"]
    assert [item["text"] for item in result if not item.get("poke")] == [
        "想你了。", "今天过得怎么样？",
    ]
    assert saved == [
        ("甲", "想你了。"), ("甲", "今天过得怎么样？"), ("甲", "戳了戳你"),
    ]
    assert "【主动开场】" in calls[0][0]["content"]
    assert "主动发一条聊天消息" in calls[0][0]["content"]
    assert chat_initiative.last_at(ctx.thread_id) > 0


def test_proactive_turn_ignores_stale_photo_request(monkeypatch, tmp_path):
    """隔夜主动开场不能回答/重复拒绝昨天的照片请求（2026-11-12 用户实锤）。"""
    monkeypatch.setattr(chat_initiative, "decide", lambda *_a, **_k: (True, "ok"))
    replies = iter(["想得美，这种照片不给拍。", "刚吃完面，撑得不想动。"])
    calls: list[list[dict]] = []

    def fake_chat(_base, _key, _model, messages, **_kwargs):
        calls.append(messages)
        return next(replies)

    monkeypatch.setattr(chat_room.llm, "chat_messages", fake_chat)
    monkeypatch.setattr(chat_room, "_persist", lambda *_a, **_k: {"id": "m1", "ts": 1})
    judged: list[str] = []
    monkeypatch.setattr(
        chat_room, "judge_media_action",
        lambda _ctx, _speaker, query, _text: judged.append(query) or None,
    )
    ctx = _ctx(tmp_path, history=[
        {"role": "user", "content": "发张照片给我看看"},
        {"role": "assistant", "content": "【甲】想得美，这种照片不给拍。"},
    ])

    result = chat_room.proactive_turn(ctx)

    bubbles = [item for item in result if not item.get("poke")]
    assert [item["text"] for item in bubbles] == ["刚吃完面，撑得不想动。"]
    assert len(calls) == 2
    assert "【旧话题已经结束】" in calls[0][0]["content"]
    assert "【最后一次提醒】" in calls[1][0]["content"]
    # 旧照片请求不再作为本轮 query 进入发图判断。
    assert judged == ["主动找用户聊天"]


def test_proactive_turn_silent_when_refusal_repeats(monkeypatch, tmp_path):
    """两次都在重复旧拒绝时本轮沉默：不落盘、不戳、不写冷却。"""
    monkeypatch.setattr(chat_initiative, "decide", lambda *_a, **_k: (True, "ok"))
    _capture_chat(monkeypatch, "想得美，这种照片不给拍。")
    saved: list[str] = []
    monkeypatch.setattr(
        chat_room, "_persist",
        lambda _ctx, _speaker, text, **_k: saved.append(text) or {"id": "m1", "ts": 1},
    )
    ctx = _ctx(tmp_path, history=[
        {"role": "user", "content": "发张照片给我看看"},
        {"role": "assistant", "content": "【甲】想得美，这种照片不给拍。"},
    ])

    assert chat_room.proactive_turn(ctx) == []
    assert saved == []
    assert chat_initiative.last_at(ctx.thread_id) == 0


def test_proactive_turn_can_send_selfie_from_current_state(monkeypatch, tmp_path):
    """用户一句话没说，角色也能按当前状态（海边泳装）主动发自拍。"""
    monkeypatch.setattr(chat_initiative, "decide", lambda *_a, **_k: (True, "ok"))
    calls = _capture_chat(
        monkeypatch, "我到海边啦。\n[SELFIE: 泳装自拍，海边逆光，近景半身]",
    )
    saved: list[dict] = []

    def fake_persist(_ctx, speaker, text, media_request=None, thread_id=None, poke=False):
        saved.append({"speaker": speaker, "text": text, "media": media_request})
        return {"id": f"m{len(saved)}", "ts": 1}

    monkeypatch.setattr(chat_room, "_persist", fake_persist)
    ctx = _ctx(tmp_path)
    ctx["comfy_illustrate"] = True

    result = chat_room.proactive_turn(ctx)

    system = calls[0][0]["content"]
    # 主动开口优先从角色卡的兴趣/日常找轻松话题；当前处境只是可选背景，不是硬锚点。
    assert "角色的性格、兴趣" in system or "性格、兴趣" in system
    assert "可选背景" in system
    assert "不要一上来就推进剧情" in system
    assert "每轮收尾：聊天动作" in system
    bubbles = [item for item in result if not item.get("poke")]
    assert bubbles[0]["text"] == "我到海边啦。"
    assert bubbles[0]["media_request"] == {
        "kind": "image", "prompt": "泳装自拍，海边逆光，近景半身",
    }
    assert saved[0]["media"] == {
        "kind": "image", "prompt": "泳装自拍，海边逆光，近景半身",
    }


def test_reopen_instruction_prefers_new_topic_over_plot():
    """重开不要重提上次的期限/任务；当前处境只是可选背景，且只补文字。"""
    assert "不要重提上次的话题" in chat_room._REOPEN_INSTRUCTION
    assert "你此刻在哪" in chat_room._REOPEN_INSTRUCTION
    assert "只补一句文字" in chat_room._REOPEN_INSTRUCTION
    assert "发自拍" not in chat_room._REOPEN_INSTRUCTION
    assert "不要提照片" in chat_room._REOPEN_INSTRUCTION
    assert "不要问「看到了吗/怎么样」" in chat_room._REOPEN_INSTRUCTION


def test_proactive_turn_silent_when_model_says_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_initiative, "decide", lambda *_a, **_k: (True, "ok"))
    _capture_chat(monkeypatch, "[NO_MESSAGE]")
    monkeypatch.setattr(chat_room, "_persist", lambda *_a: {"id": "m1", "ts": 1})
    ctx = _ctx(tmp_path)

    assert chat_room.proactive_turn(ctx) == []
    assert chat_initiative.last_at(ctx.thread_id) == 0


def test_proactive_turn_only_current_character_in_per_character_thread(monkeypatch, tmp_path):
    """私聊按角色分线程后，A 的私聊里不能冒出 B 的主动开场。"""
    seen: list[str] = []

    def fake_decide(_ctx, speaker, thread_id=""):
        seen.append(speaker)
        return (speaker == "苏晚", "affinity_low")

    monkeypatch.setattr(chat_initiative, "decide", fake_decide)
    _capture_chat(monkeypatch, "今晚有空吗？")
    monkeypatch.setattr(chat_room, "_persist", lambda *_a: {"id": "m1", "ts": 1})
    ctx = _ctx(tmp_path)
    ctx["_chat_roster"] = {
        "source": "worldbook",
        "members": ["林昭", "苏晚"],
        "entries": [
            {"name": "林昭", "content": "林昭是青云宗掌门，她性格清冷。"},
            {"name": "苏晚", "content": "苏晚是药王谷弟子，她性格温和。"},
        ],
        "fallback_card": "示例作品甲",
    }
    ctx.chat_members = ["林昭"]

    assert chat_room.proactive_turn(ctx) == []
    assert seen == ["林昭"]


def test_scan_initiatives_writes_each_member_to_own_thread(monkeypatch, tmp_path):
    """扫描名册：够格角色各自写进自己的私聊线程，前端才能给未打开角色亮红点。"""
    monkeypatch.setattr(chat_initiative, "affinity_for", lambda _ctx, _name: 80)
    monkeypatch.setattr(
        chat_initiative, "decide",
        lambda _ctx, speaker, thread_id="": (True, "ok"),
    )
    _capture_chat(monkeypatch, "在吗？")
    saved: list[tuple[str, str]] = []

    def fake_persist(_ctx, speaker, text, media_request=None, thread_id=None, poke=False):
        saved.append((speaker, str(thread_id or "")))
        return {"id": f"m{len(saved)}", "ts": 1}

    monkeypatch.setattr(chat_room, "_persist", fake_persist)
    ctx = _ctx(tmp_path)
    ctx["_chat_roster"] = {
        "source": "worldbook",
        "members": ["林昭", "苏晚"],
        "entries": [
            {"name": "林昭", "content": "林昭是青云宗掌门，她性格清冷。"},
            {"name": "苏晚", "content": "苏晚是药王谷弟子，她性格温和。"},
        ],
        "fallback_card": "示例作品甲",
    }

    results = chat_room.scan_initiatives(ctx, limit=2)

    assert [item["speaker"] for item in results] == ["林昭", "苏晚"]
    assert [item["thread_id"] for item in results] == [
        chat_room.thread_id_for("repo-1", "chat", "林昭"),
        chat_room.thread_id_for("repo-1", "chat", "苏晚"),
    ]
    # 每位角色：正文 + poke 都写进自己的线程（poke 在正文之后落盘）。
    assert saved == [
        ("林昭", results[0]["thread_id"]),
        ("林昭", results[0]["thread_id"]),
        ("苏晚", results[1]["thread_id"]),
        ("苏晚", results[1]["thread_id"]),
    ]
    assert [item["poke"] for item in results[0]["messages"] if item.get("poke")] == [True]


def test_proactive_turn_restores_preset_guard_markers(monkeypatch, tmp_path):
    """主动消息也走聊天室防拦截：预设要求的 @()@ 拆字标记必须在落盘前还原。"""
    from app.services import preset_store

    preset = {
        "prompts": [{"identifier": "guard", "content": "防拦截规则生效"}],
        "prompt_order": [{"order": [{"identifier": "guard", "enabled": True}]}],
    }
    monkeypatch.setattr(preset_store, "read_preset", lambda *_a, **_k: preset)
    monkeypatch.setattr(chat_initiative, "decide", lambda *_a, **_k: (True, "ok"))
    _capture_chat(monkeypatch, "她@的@(乳)@头")
    saved: list[str] = []

    def fake_persist(_ctx, _speaker, text, media_request=None, thread_id=None, poke=False):
        saved.append(text)
        return {"id": f"m{len(saved)}", "ts": 1}

    monkeypatch.setattr(chat_room, "_persist", fake_persist)
    ctx = _ctx(tmp_path, preset_dir=str(tmp_path), preset_name="p")

    result = chat_room.proactive_turn(ctx)

    assert result[0]["poke"] is True
    assert [item["text"] for item in result if not item.get("poke")] == ["她的乳头"]
    assert saved == ["她的乳头", "戳了戳你"]


def test_claim_blocks_same_thread_until_release():
    thread_id = "repo-1__chat__claim_test"
    assert chat_initiative.claim(thread_id) is True
    assert chat_initiative.claim(thread_id) is False
    chat_initiative.release_claim(thread_id)
    assert chat_initiative.claim(thread_id) is True
    chat_initiative.release_claim(thread_id)


def test_proactive_turn_skips_when_thread_has_active_run(monkeypatch, tmp_path):
    """用户发送与本轮主动不能同时写同一角色线程：admission 已占用时直接跳过。"""
    from app.services import thread_admission

    ctx = _ctx(tmp_path)
    calls = _capture_chat(monkeypatch, "在吗？")
    admission = thread_admission.admit(ctx.thread_id, threading.Event())
    try:
        assert chat_room.proactive_turn(ctx) == []
    finally:
        thread_admission.release(admission)
    assert calls == []
