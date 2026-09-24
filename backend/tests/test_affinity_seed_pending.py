"""待建好感度字段清单：状态表里有其他状态、却始终没有被播种好感度的已登场角色。

2026-09-23 用户口径（真机实锤）：骆姑姑在「重要角色表」里有行、状态表里有 4 条叙事字段
（所在/身体状态/在场状态/当前目标），却**始终没有**好感度字段 ⇒ 「角色状态表（好感度/状态）」
里她的卡片只有状态、没有好感度。规则（`state_instruction`）只说「字段不存在时要 set 播种」，
但**模型看不到"谁缺字段"** ⇒ 只靠它自查必然漏。
本清单把「候选角色 − 已有好感度属主」确定性算出来注入，让播种不再靠自觉。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import agent_graph as ag
from app.services import character_state as cs
from app.services import table_store


def _characters_table(names: list[str]) -> list[dict]:
    return [
        table_store._table_dict(  # noqa: SLF001  测试夹具复用生产建表口径
            "sheet_default_characters", table_store.CHARACTERS_TABLE,
            ["姓名", "性别", "所在地点"], [[n, "女", "某处"] for n in names],
            key_col="姓名",
        ),
    ]


# ── 1. 纯逻辑：缺字段清单 ──

def test_缺好感度清单只收候选里缺字段的角色():
    st = cs.CharacterState(card_name="凌霄仙母录", repo_id="r")
    st.数值["妲千沁·好感度"] = cs.NumericField(13.0, turn=51)
    st.叙事["妲千沁·心情"] = cs.NarrativeField("口硬发痒", turn=51)
    st.叙事["骆姑姑·所在"] = cs.NarrativeField("已离场", turn=47)
    st.叙事["师徒名分"] = cs.NarrativeField("濒临破裂", turn=46)  # 无属主裸键，不是角色

    got = cs.missing_affinity_owners(st, ["妲千沁", "骆姑姑", "路人甲"])
    # 有字段的不报（妲千沁）；候选里有、状态表完全没字段的照样要建（新角色也要有初值）
    assert got == ["骆姑姑", "路人甲"]


def test_缺好感度清单排除主角与空名():
    st = cs.CharacterState(card_name="卡", repo_id="r")
    st.叙事["我·所在"] = cs.NarrativeField("寝殿", turn=1)
    st.叙事["主角·心情"] = cs.NarrativeField("焦急", turn=1)
    st.叙事["乙·身体状态"] = cs.NarrativeField("重伤", turn=2)

    got = cs.missing_affinity_owners(st, ["我", "主角", "乙", "  ", ""])
    assert got == ["乙"]


def test_缺好感度清单保持候选顺序且幂等():
    st = cs.CharacterState(card_name="卡", repo_id="r")
    st.叙事["乙·所在"] = cs.NarrativeField("北境", turn=1)
    st.叙事["甲·所在"] = cs.NarrativeField("南境", turn=1)

    assert cs.missing_affinity_owners(st, ["甲", "乙"]) == ["甲", "乙"]
    assert cs.missing_affinity_owners(st, ["甲", "乙", "甲"]) == ["甲", "乙"]
    # 重复调用不改变状态（纯逻辑）
    assert cs.missing_affinity_owners(st, ["甲", "乙"]) == ["甲", "乙"]


def test_无数值无叙事时清单为空():
    st = cs.CharacterState(card_name="卡", repo_id="r")
    assert cs.missing_affinity_owners(st, ["甲", "乙"]) == ["甲", "乙"]  # 候选仍要求建
    assert cs.missing_affinity_owners(st, []) == []


# ── 2. 纯逻辑：注入块文案 ──

def test_待建好感度注入块文案自包含():
    block = cs.render_missing_affinity_block(["骆姑姑", "路人甲"])
    assert "骆姑姑" in block and "路人甲" in block
    assert 'op="set"' in block            # 明确动作：set 播种
    assert "禁止从 0 起算" in block        # 明确禁止项
    assert "好感度" in block
    assert cs.render_missing_affinity_block([]) == ""


def test_表格列取值去重保序():
    tables = _characters_table(["妲千沁", "骆姑姑", "", "妲千沁"])
    assert table_store.column_values(
        tables, table_store.CHARACTERS_TABLE, "姓名") == ["妲千沁", "骆姑姑"]
    assert table_store.column_values(tables, "不存在的表", "姓名") == []
    assert table_store.column_values(tables, table_store.CHARACTERS_TABLE, "不存在列") == []
    assert table_store.column_values([], table_store.CHARACTERS_TABLE, "姓名") == []


# ── 3. 接线：表格有行、状态表无字段 ⇒ 注入待建清单 ──

def _prep_ctx(tmp_path, st):
    return {
        "output_dir": str(tmp_path), "repo_id": "r", "thread_id": "r",
        "chat_base": "", "chat_key": "", "chat_model": "",
        "_character_state": st,
    }


def test_插画关时也注入待建清单(monkeypatch, tmp_path):
    """视觉分支关闭（deps.renderer=None 且未开 comfy）时，清单仍要进 dynamic_parts。"""
    st = cs.CharacterState(card_name="卡", repo_id="r")
    st.数值["甲·好感度"] = cs.NumericField(10.0, turn=1)
    st.叙事["乙·所在"] = cs.NarrativeField("北境", turn=2)

    monkeypatch.setattr(table_store, "load", lambda base, repo_id: _characters_table(["甲", "乙"]))
    emitted: list[tuple] = []
    monkeypatch.setattr(ag.run_trace, "emit", lambda ctx, event, **data: emitted.append((event, data)))

    dynamic_parts: list[str] = []
    ag._rp_prepare_visual_and_tables(  # noqa: SLF001  接缝函数直测
        _prep_ctx(tmp_path, st), deps=SimpleNamespace(renderer=None),
        repo_id="r", text="", turn=3, dynamic_parts=dynamic_parts,
    )

    joined = "\n".join(dynamic_parts)
    assert "乙" in joined and "待建" in joined
    assert "甲" not in joined.split("待建")[-1]  # 已有好感度的角色不入清单
    assert any(ev == "affinity.seed_pending" and data.get("roles") == ["乙"]
               for ev, data in emitted)


def test_无缺字段角色时不注入(monkeypatch, tmp_path):
    st = cs.CharacterState(card_name="卡", repo_id="r")
    st.数值["甲·好感度"] = cs.NumericField(10.0, turn=1)

    monkeypatch.setattr(table_store, "load", lambda base, repo_id: _characters_table(["甲"]))
    monkeypatch.setattr(ag.run_trace, "emit", lambda ctx, event, **data: None)

    dynamic_parts: list[str] = []
    ag._rp_prepare_visual_and_tables(  # noqa: SLF001
        _prep_ctx(tmp_path, st), deps=SimpleNamespace(renderer=None),
        repo_id="r", text="", turn=3, dynamic_parts=dynamic_parts,
    )
    assert not any("待建" in part for part in dynamic_parts)


def test_没有状态对象时不注入且不抛(monkeypatch, tmp_path):
    """旧调用/无卡路径下 ctx 里没有 state ⇒ 静默跳过（不改变既有文案）。"""
    monkeypatch.setattr(table_store, "load", lambda base, repo_id: _characters_table(["乙"]))
    monkeypatch.setattr(ag.run_trace, "emit", lambda ctx, event, **data: None)

    ctx = _prep_ctx(tmp_path, None)
    ctx.pop("_character_state")
    dynamic_parts: list[str] = []
    ag._rp_prepare_visual_and_tables(  # noqa: SLF001
        ctx, deps=SimpleNamespace(renderer=None),
        repo_id="r", text="", turn=3, dynamic_parts=dynamic_parts,
    )
    assert not any("待建" in part for part in dynamic_parts)
