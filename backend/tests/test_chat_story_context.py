"""聊天/群聊剧情资料块（2026-11-09）回归测试。

覆盖：
1. 私聊只注入「角色当前状态 + 数据表」，世界书/往事纪要不再进入；
2. 重要角色表只取当前发言人的行；
3. 没有剧情资料时仍给出「未见面试探/陌生人」关系判断；
4. 状态/表格失败只丢该块，不阻断聊天；
5. 世界书读取函数不会被调用（聊天不再接世界书）。
"""
from __future__ import annotations

from app.services import chat_story_context, table_store
from app.services.agent_contracts import RunContext


def _ctx(tmp_path) -> RunContext:
    return RunContext(
        thread_id="repo-1__chat",
        message="你好",
        output_dir=str(tmp_path),
        repo_id="repo-1",
        card_names=["甲", "乙"],
        user_name="用户",
    )


def _tables() -> list[dict]:
    tables = table_store.default_tables()
    for table in tables:
        if table["name"] == table_store.GLOBAL_TABLE:
            table["rows"] = [["深夜", "客栈", "安静", "不可飞行"]]
        elif table["name"] == table_store.PROTAGONIST_TABLE:
            table["rows"] = [[
                "用户", "男", "二十", "旅人", "黑发", "斗篷", "客栈",
                "疲惫", "与甲尚未见过面", "四处游历",
            ]]
        elif table["name"] == table_store.CHARACTERS_TABLE:
            table["rows"] = [
                ["甲", "女", "十九", "剑客", "红发", "劲装", "客栈", "在场",
                 "与用户尚未见过面，保持警惕", "四处游历", "找回师门"],
                ["乙", "女", "二十", "医师", "黑发", "长裙", "药铺", "不在场",
                 "与用户是旧识", "整理药方", "守住药铺"],
            ]
    return tables


def _existing(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return path


def test_build_includes_speaker_state_and_tables_only(monkeypatch, tmp_path):
    monkeypatch.setattr(table_store, "tables_path", lambda *_a: _existing(tmp_path / "tables.json"))
    monkeypatch.setattr(table_store, "load", lambda *_a: _tables())

    ctx = _ctx(tmp_path)
    ctx["_worldbook_recall_text"] = "【召回】山门在云海之上。"
    text = chat_story_context.build_for_speaker(
        ctx, speaker="甲", members=["甲", "乙"], query="你好",
    )

    assert "世界书背景" not in text
    assert "云上宗" not in text and "山门在云海之上" not in text
    assert "前情提要" not in text
    assert "全局数据表" in text and "主角信息表" in text and "重要角色表" in text
    assert "与用户尚未见过面，保持警惕" in text
    assert "与用户是旧识" not in text  # 乙的行不注入甲
    # 2026-11-11：任务目标列不进聊天上下文，避免模型催同一件剧情。
    assert "找回师门" not in text
    assert "当前目标 |" not in text
    assert "资料里没有你们见过面" in text


def test_build_injects_current_character_state_before_tables(monkeypatch, tmp_path):
    """2026-11-10 实锤：纪要还没统计到婚纱，但角色状态表已经是婚纱，必须注入状态表。"""
    from app.services import character_state

    monkeypatch.setattr(table_store, "tables_path", lambda *_a: tmp_path / "missing.json")
    state = character_state.CharacterState(card_name="甲", repo_id="repo-1")
    state.叙事["甲·身体状态"] = character_state.NarrativeField("婚纱被揉皱挂在身上")
    state.叙事["甲·当前目标"] = character_state.NarrativeField("补拍婚纱照")
    state.数值["甲·好感度"] = character_state.NumericField(93)
    monkeypatch.setattr(character_state, "load_state", lambda *_a: state)

    text = chat_story_context.build_for_speaker(
        _ctx(tmp_path), speaker="甲", members=["甲"], query="婚纱照呢",
    )

    assert "【角色当前状态（最新剧情状态；与纪要/表格冲突时以此为准）】" in text
    assert "婚纱被揉皱挂在身上" in text
    # 2026-11-11：剧情任务目标（当前目标/任务）不注入私聊，否则会反复催期限/回本部。
    assert "补拍婚纱照" not in text
    assert "当前目标" not in text
    assert "好感度：93" in text


def test_names_align_short_and_full_character_names():
    assert chat_story_context.names_align("腓特烈大帝", "腓特烈")
    assert chat_story_context.best_name_match(
        "腓特烈大帝", ["腓特烈", "奥古斯特"],
    ) == "腓特烈"
    # 单字名不做包含匹配，避免「甲」匹配所有人。
    assert chat_story_context.names_align("甲", "甲方") is False


def test_character_table_row_matches_short_name_alias():
    table = {
        "columns": ["姓名", "状态", "当前目标"],
        "rows": [["腓特烈", "在海边，穿泳衣", "休息"]],
    }

    filtered = chat_story_context._filter_character_rows(table, "腓特烈大帝")

    assert filtered["rows"] == [["腓特烈", "在海边，穿泳衣", "休息"]]


def test_state_block_matches_short_name_alias(monkeypatch, tmp_path):
    from app.services import character_state

    state = character_state.CharacterState(card_name="腓特烈大帝", repo_id="repo-1")
    state.叙事["腓特烈·衣着状态"] = character_state.NarrativeField("海边，泳衣")
    monkeypatch.setattr(character_state, "load_state", lambda *_a: state)

    text = chat_story_context._state_block(_ctx(tmp_path), "腓特烈大帝")

    assert "海边，泳衣" in text


def test_story_context_limits_follow_context_budget(tmp_path):
    """背景资料上限跟随 contextMaxTokens（旧 3.6k/2.2k 写死已移除）。"""
    ctx = _ctx(tmp_path)  # RunContext 默认 20k
    assert chat_story_context._budget_limits(ctx, compact=False) == (
        2_500, 1_250, 1_250, 5_000,
    )
    assert chat_story_context._budget_limits(ctx, compact=True) == (
        1_250, 625, 625, 2_500,
    )

    small = RunContext(
        thread_id="repo-1__chat", message="", output_dir=str(tmp_path),
        repo_id="repo-1", context_max_tokens=1_000,
    )
    assert chat_story_context._budget_limits(small, compact=False) == (
        125, 62, 63, 250,
    )


def test_key_events_keep_outfit_memories_even_with_many_recent(monkeypatch, tmp_path):
    """婚纱/兔女郎这类纪念回不能被最近 4 条剧情事件挤出聊天回忆。"""
    from app.services import table_store

    columns = ["名称", "类型", "状态", "目标", "进展", "相关人物", "地点", "备注"]
    rows = [
        ["[关键事件] 第9回合·婚礼/婚纱", "关键事件", "已完成", "", "婚礼", "", "", "关键事件/关键装扮"],
        ["[关键事件] 第10回合·兔女郎", "关键事件", "已完成", "", "兔女郎", "", "", "关键事件/关键装扮"],
        ["[关键事件] 第17回合·奥古斯特", "关键事件", "已完成", "", "x" * 180, "", "", "关键事件"],
        ["[关键事件] 第18回合·前任", "关键事件", "已完成", "", "y" * 180, "", "", "关键事件"],
        ["[关键事件] 第19回合·埃吉尔", "关键事件", "已完成", "", "z" * 180, "", "", "关键事件"],
        ["[关键事件] 第19回合·誓约礼", "关键事件", "已完成", "", "w" * 180, "", "", "关键事件"],
    ]
    quests = {"name": table_store.QUESTS_TABLE, "columns": columns, "rows": rows}
    monkeypatch.setattr(table_store, "tables_path", lambda *_a: _existing(tmp_path / "tables.json"))
    monkeypatch.setattr(table_store, "load", lambda *_a: [quests])

    text = chat_story_context._key_events_block(_ctx(tmp_path), limit=900)

    assert "婚礼/婚纱" in text
    assert "兔女郎" in text
    # 装扮纪念置顶，最近的剧情事件排在后面。
    assert text.index("第10回合·兔女郎") < text.index("第19回合")


def test_build_without_story_data_still_returns_stranger_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(table_store, "tables_path", lambda *_a: tmp_path / "missing.json")

    text = chat_story_context.build_for_speaker(
        _ctx(tmp_path), speaker="甲", members=["甲"], query="你好",
    )

    assert "资料里没有你们见过面" in text
    assert "世界书背景" not in text
    assert "前情提要" not in text


def test_build_failures_are_isolated(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(table_store, "tables_path", lambda *_a: _existing(tmp_path / "tables.json"))
    monkeypatch.setattr(table_store, "load", boom)

    text = chat_story_context.build_for_speaker(
        _ctx(tmp_path), speaker="甲", members=["甲"], query="你好",
    )

    assert "资料里没有你们见过面" in text
    assert "boom" not in text


def test_supervisor_chat_injects_story_context_into_persona_block(monkeypatch, tmp_path):
    from app.services import agent_graph_supervisor

    monkeypatch.setattr(
        chat_story_context, "build_for_speaker", lambda *_a, **_k: "【剧情资料】",
    )
    monkeypatch.setattr(
        agent_graph_supervisor, "_supervisor_route",
        lambda *_a, **_k: ("answer", True, [], ""),
    )
    ctx = _ctx(tmp_path)
    ctx["interaction_mode"] = "chat"
    ctx["chat_members"] = ["甲"]

    out = agent_graph_supervisor.supervisor_node({
        "user_text": "你好", "images": [], "trace": [], "_ctx": ctx,
    })

    assert out["route"] == "answer"
    block = str(ctx.get("_chat_persona_block") or "")
    assert "【剧情资料】" in block
    # 话题自主必须压在剧情资料之后（否则资料会反客为主，模型继续揪同一件事）。
    assert block.index("【剧情资料】") < block.index("【聊天话题与自主性（优先于剧情资料）】")


def test_worldbook_is_not_read_for_chat(monkeypatch, tmp_path):
    from app.services import agent_context_assembly

    calls = {"worldbook": 0}

    def worldbook(*_a, **_k):
        calls["worldbook"] += 1
        return "【世界书】不应出现"

    monkeypatch.setattr(agent_context_assembly, "_resolve_worldbook", worldbook)
    monkeypatch.setattr(table_store, "tables_path", lambda *_a: tmp_path / "missing.json")

    text = chat_story_context.build_for_speaker(
        _ctx(tmp_path), speaker="甲", members=["甲"], query="你好",
    )

    assert calls["worldbook"] == 0
    assert "世界书" not in text


def test_has_story_material_requires_tables_or_state(monkeypatch, tmp_path):
    """2026-11-10 用户口径：往事纪要不再算剧情沉淀，只认数据表/角色状态。"""
    from app.services import character_state

    monkeypatch.setattr(table_store, "tables_path", lambda *_a: tmp_path / "missing.json")
    monkeypatch.setattr(
        character_state, "state_path", lambda *_a: tmp_path / "missing-state.json",
    )

    assert chat_story_context.has_story_material(_ctx(tmp_path)) is False

    monkeypatch.setattr(
        table_store, "tables_path", lambda *_a: _existing(tmp_path / "tables.json"),
    )
    monkeypatch.setattr(table_store, "load", lambda *_a: _tables())

    assert chat_story_context.has_story_material(_ctx(tmp_path)) is True
