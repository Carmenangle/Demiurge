"""角色动态状态：parse/apply 纯逻辑 + 落盘往返 + provenance 语义。"""
from __future__ import annotations

import pytest

from app.services import character_state as cs


def _state() -> cs.CharacterState:
    st = cs.CharacterState(card_name="埃斯托利亚", repo_id="repo1")
    st.数值["好感度"] = cs.NumericField(-30.0, min=-50.0, max=120.0)
    return st


def test_parse_deltas_归一与非法跳过():
    raw = [
        {"field": "数值/好感度", "op": "add", "value": 25, "evidence": "调运食材做了一桌菜"},
        {"field": "叙事/对{{user}}态度", "op": "set", "value": "戒备", "evidence": "第3章救援"},
        {"field": "数值/好感度", "op": "set", "value": 99},   # 数值 set=首建初值 → 接受
        {"field": "数值/好感度", "op": "mul", "value": 2},     # 未知 op → 跳过
        {"field": "叙事/心情", "op": "set", "value": ""},      # 空叙事 → 跳过
        {"field": "没有斜杠", "op": "add", "value": 1},         # 缺 kind → 跳过
        "not a dict",
    ]
    deltas = cs.parse_deltas(raw, turn=42)
    assert len(deltas) == 3
    assert deltas[0].kind() == "数值" and deltas[0].op == "add" and deltas[0].value == 25.0
    assert deltas[1].kind() == "叙事" and deltas[1].leaf() == "对{{user}}态度"
    assert deltas[2].kind() == "数值" and deltas[2].op == "set" and deltas[2].value == 99.0


def test_数值set只在字段不存在时播种初值():
    """2026-09-22 用户口径：角色初次登场按剧情因果给初始好感/恶感，不应全为 0。
    数值 set 只对「尚不存在」的字段生效；字段已存在时忽略，防模型一键重置累积值。"""
    st = cs.CharacterState(card_name="凌霄仙母录", repo_id="r")
    # 首次登场：set 播种初值（高好感），from=None 表「此前不存在」供回滚删除
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/妲千沁·好感度", "op": "set", "value": 60,
          "evidence": "授业师傅、旧情深厚"}], turn=45))
    assert st.数值["妲千沁·好感度"].value == 60.0
    assert st.历史[-1]["from"] is None and st.历史[-1]["to"] == 60.0
    # 已存在 → set 被忽略（不覆盖），add 仍正常累加
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/妲千沁·好感度", "op": "set", "value": 5}], turn=46))
    assert st.数值["妲千沁·好感度"].value == 60.0
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/妲千沁·好感度", "op": "add", "value": 4}], turn=47))
    assert st.数值["妲千沁·好感度"].value == 64.0
    # 回滚首次播种 → 字段整条移除（等效从未出现）
    cs.rollback_last(st, n=3)
    assert "妲千沁·好感度" not in st.数值


def test_数值set播种初值仍受边界clamp():
    st = cs.CharacterState(card_name="c", repo_id="r")
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "set", "value": 999}], turn=1))
    assert st.数值["好感度"].value == 100.0
    assert (st.数值["好感度"].min, st.数值["好感度"].max) == (-100.0, 100.0)


def test_apply_数值累加并clamp():
    st = _state()
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": 200, "evidence": "x"}], turn=1))
    assert st.数值["好感度"].value == 120.0  # clamp 到 max
    assert st.历史[-1]["from"] == -30.0 and st.历史[-1]["to"] == 120.0


def test_add命中不存在的好感度字段被拒收不建值():
    """2026-09-23 用户口径（闸）：`add` 命中**不存在**的好感度字段 ⇒ **拒收**（不建值、不记审计）。

    旧行为＝`before_n = 0.0` 静默从 0 起算并建值 ⇒ 一旦成 0，后续 `set` 永远被忽略
    （`set` 只对不存在字段生效）⇒ 该角色初值被永久钉死；回滚路径更隐蔽（`add` 建的字段
    审计 `from=0.0`，被 `_undo_entry` 打回 0 而**字段保留**，同样再修不回来）。
    ⇒ 逼模型走 `set` 播种（`set` 的口径要求带因果依据）。拒收项经 `rejected` 出参收集，
    本模块保持纯逻辑（不落盘、不改审计），由上层 trace 上报。
    """
    st = cs.CharacterState(card_name="c", repo_id="r")
    rejected: list[dict] = []
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/妲千沁·好感度", "op": "add", "value": 6,
          "evidence": "贴身接触"}], turn=1), rejected=rejected)
    assert "妲千沁·好感度" not in st.数值           # 不建值
    assert st.历史 == []                            # 不记审计
    assert rejected and rejected[0]["field"] == "数值/妲千沁·好感度"
    assert rejected[0]["reason"] == "affinity_field_absent"
    assert rejected[0]["value"] == 6.0 and rejected[0]["turn"] == 1
    # 裸键（单主角写法）同样受闸
    rejected2: list[dict] = []
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": 3}], turn=2), rejected=rejected2)
    assert "好感度" not in st.数值 and len(rejected2) == 1


def test_拒收闸只限好感度字段且set与既有add照常():
    """适用范围限定（红线「判据只加适用范围限定」）：① `set` 仍可播种并锁到 ±100
    ② 既有字段的 `add` 照常累加 ③ **其他数值字段**（如 金币）维持旧的
    「add 命中不存在则从 0 起算」行为——闸不得外溢。"""
    st = cs.CharacterState(card_name="c", repo_id="r")
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "set", "value": 999}], turn=1))
    assert st.数值["好感度"].value == 100.0
    assert (st.数值["好感度"].min, st.数值["好感度"].max) == (-100.0, 100.0)
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": -7}], turn=2))
    assert st.数值["好感度"].value == 93.0
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/金币", "op": "add", "value": 5000}], turn=3))
    assert st.数值["金币"].value == 5000.0         # 未登记字段仍无界、仍可 0 起算


def test_add建字段的审计from记None_回滚能删干净():
    """2026-09-23 同日配套修：`add` 命中**不存在**的（非好感度）数值字段 ⇒ 值仍 0 起算建，
    但审计 `from` 记 **None**（与 `set` 分支同口径）。

    `_undo_entry` 的判据是「`from is None` ⇒ 此前不存在 ⇒ pop 字段」；旧写法
    `before_n = nf.value if nf else 0.0` 把 0 写成 from，回滚只打回 0 而**保留字段**，
    留下「值=0 的空壳」（比字段消失更难救：`set` 之后永远被忽略、`add` 只能在 0 上加）。
    """
    st = cs.CharacterState(card_name="c", repo_id="r")
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/金币", "op": "add", "value": 5000}], turn=3))
    assert st.数值["金币"].value == 5000.0        # 值语义不变（仍 0 起算）
    assert st.历史[-1]["from"] is None            # 但审计记 None ⇒ 回滚等价删除
    cs.rollback_last(st, n=1)
    assert "金币" not in st.数值                   # 不留下「值=0 的空壳」


def test_add既有字段的审计from仍记原值_回滚是还原不是删除():
    """对照：字段**已存在**时 `add` 照旧记 `from=原值`，回滚还原该值、字段保留。"""
    st = cs.CharacterState(card_name="c", repo_id="r")
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/金币", "op": "add", "value": 100}], turn=1))
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/金币", "op": "add", "value": 50}], turn=2))
    assert st.数值["金币"].value == 150.0
    assert st.历史[-1]["from"] == 100.0
    cs.rollback_last(st, n=1)
    assert st.数值["金币"].value == 100.0          # 还原，不是删除


def test_apply_叙事覆盖并记history():
    st = _state()
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "叙事/心情", "op": "set", "value": "疲惫", "evidence": "莫妮卡战败"}], turn=15))
    assert st.叙事["心情"].value == "疲惫"
    assert st.历史[-1]["from"] == "" and st.历史[-1]["evidence"] == "莫妮卡战败"


def test_同角色状态字段别名归一并由最新值替换():
    st = cs.CharacterState(card_name="冷倾雪", repo_id="r")
    st.叙事["冷倾雪身体状态"] = cs.NarrativeField("旧值", turn=1)
    st.叙事["身体状态"] = cs.NarrativeField("中间值", turn=2)
    st.叙事["冷倾雪·身体状态"] = cs.NarrativeField("最新值", turn=3)

    cs.consolidate_fields(st)

    assert list(st.叙事) == ["冷倾雪·身体状态"]
    assert st.叙事["冷倾雪·身体状态"].value == "最新值"


def test_无归属状态沿用同字段唯一已有角色而非主卡名():
    st = cs.CharacterState(card_name="白给谷", repo_id="r")
    st.叙事["冷倾雪身体状态"] = cs.NarrativeField("旧值", turn=1)
    st.叙事["身体状态"] = cs.NarrativeField("最新值", turn=2)

    cs.consolidate_fields(st)

    assert list(st.叙事) == ["冷倾雪·身体状态"]
    assert st.叙事["冷倾雪·身体状态"].value == "最新值"


def test_明确归属字段淘汰同名无归属旧副本():
    st = cs.CharacterState(card_name="神权大陆", repo_id="r")
    st.数值["塞西莉亚·好感度"] = cs.NumericField(10, turn=4)
    st.数值["好感度"] = cs.NumericField(2, turn=3)
    st.叙事["塞西莉亚·态度"] = cs.NarrativeField("玩味浓厚", turn=4)
    st.叙事["院长·态度"] = cs.NarrativeField("压抑不安", turn=4)
    st.叙事["态度"] = cs.NarrativeField("旧值", turn=3)
    st.叙事["我·所在"] = cs.NarrativeField("自己房间", turn=4)
    st.叙事["塞西莉亚·所在"] = cs.NarrativeField("马车内", turn=4)
    st.叙事["所在"] = cs.NarrativeField("旧地点", turn=3)

    cs.consolidate_fields(st)

    # 「好感度」例外：单主角全局好感度就是裸键，被多角色字段连带清理会让好感度
    # 凭空消失/换新（2026-09-01 用户实锤），故保留；其余裸键照旧淘汰。
    assert set(st.数值) == {"塞西莉亚·好感度", "好感度"}
    assert set(st.叙事) == {"塞西莉亚·态度", "院长·态度", "我·所在", "塞西莉亚·所在"}


def test_写入同角色同字段时替换而非新增别名():
    st = cs.CharacterState(card_name="冷倾雪", repo_id="r")
    st.叙事["冷倾雪·精神状态"] = cs.NarrativeField("平静", turn=1)
    deltas = cs.parse_deltas(
        [{"field": "叙事/精神状态", "op": "set", "value": "崩溃"}],
        turn=2,
        card_name="冷倾雪",
    )

    cs.apply_deltas(st, deltas)

    assert list(st.叙事) == ["冷倾雪·精神状态"]
    assert st.叙事["冷倾雪·精神状态"].value == "崩溃"


def test_自动写入无归属字段沿用状态中唯一角色():
    st = cs.CharacterState(card_name="白给谷", repo_id="r")
    st.叙事["冷倾雪·身体状态"] = cs.NarrativeField("旧值", turn=1)
    deltas = cs.parse_deltas(
        [{"field": "叙事/身体状态", "op": "set", "value": "新值"}],
        turn=2,
        card_name="白给谷",
        existing_state=st,
    )

    cs.apply_deltas(st, deltas)

    assert list(st.叙事) == ["冷倾雪·身体状态"]
    assert st.叙事["冷倾雪·身体状态"].value == "新值"


def test_人为改无证据保留供识别():
    st = _state()
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "叙事/态度", "op": "set", "value": "痴迷"}], turn=0, source=cs.SRC_USER))
    f = st.叙事["态度"]
    assert f.source == cs.SRC_USER and f.evidence == ""  # 无据 + user → 上层可识别为设定注入


def test_render_state_block_带provenance():
    st = _state()
    cs.apply_deltas(st, cs.parse_deltas([
        {"field": "数值/好感度", "op": "add", "value": 50, "evidence": "一桌菜"},
        {"field": "叙事/所在", "op": "set", "value": "北境要塞"},
    ], turn=3))
    block = cs.render_state_block(st)
    assert "【当前状态】" in block
    assert "埃斯托利亚·好感度: 20 (一桌菜)" in block
    assert "埃斯托利亚·所在: 北境要塞" in block  # 无证据不带括号


def test_落盘往返(tmp_path):
    base = str(tmp_path)
    st = _state()
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "叙事/所在", "op": "set", "value": "舞会", "evidence": "赴宴"}], turn=2))
    cs.save_state(base, st)
    got = cs.load_state(base, "repo1", "埃斯托利亚")
    assert got.数值["好感度"].value == -30.0
    assert got.叙事["所在"].value == "舞会" and got.叙事["所在"].evidence == "赴宴"
    assert len(got.历史) == 1


def test_空作品返回空状态(tmp_path):
    got = cs.load_state(str(tmp_path), "nope", "卡")
    assert got.数值 == {} and got.叙事 == {} and cs.render_state_block(got) == ""


def test_快照落盘往返(tmp_path):
    base = str(tmp_path)
    st = _state()
    st.快照 = cs.Snapshot("[所在] 沉梦峡谷\n[臣服] 叶燃眉=100", turn=7)
    cs.save_state(base, st)
    got = cs.load_state(base, "repo1", "埃斯托利亚")
    assert got.快照.text == "[所在] 沉梦峡谷\n[臣服] 叶燃眉=100"
    assert got.快照.turn == 7


def test_render_snapshot_injection():
    st = _state()
    assert cs.render_snapshot_injection(st) == ""  # 空快照 → 空串
    st.快照 = cs.Snapshot("[所在] 山洞", turn=3)
    inj = cs.render_snapshot_injection(st)
    assert "【上轮状态栏" in inj and "[所在] 山洞" in inj


def test_current_turn_取最大():
    st = _state()
    st.数值["好感度"].turn = 2
    st.叙事["态度"] = cs.NarrativeField("戒备", turn=5)
    st.快照 = cs.Snapshot("x", turn=3)
    assert cs.current_turn(st) == 5


def test_set_fields_精确设值非累加():
    st = _state()  # 好感度 = -30
    n = cs.set_fields(st, [{"field": "数值/好感度", "value": 50}], turn=4)
    assert n == 1
    assert st.数值["好感度"].value == 50.0        # 设值,非 -30+50
    assert st.数值["好感度"].source == cs.SRC_USER
    assert st.历史[-1]["op"] == "set" and st.历史[-1]["from"] == -30.0


def test_set_fields_数值clamp与叙事():
    st = _state()  # min=-50 max=120
    cs.set_fields(st, [{"field": "数值/好感度", "value": 999},
                       {"field": "叙事/态度", "value": "臣服"}], turn=4)
    assert st.数值["好感度"].value == 120.0       # clamp 到 max
    assert st.叙事["态度"].value == "臣服"
    assert st.叙事["态度"].source == cs.SRC_USER


def test_set_fields_非法跳过():
    st = _state()
    n = cs.set_fields(st, [{"field": "没斜杠"}, {"field": "数值/好感度", "value": 3}], turn=1)
    assert n == 1


def test_rollback_last_还原并移除历史():
    st = _state()
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": 20}], turn=1))  # -30→-10
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": 30}], turn=2))  # -10→20
    hist_len = len(st.历史)
    undone = cs.rollback_last(st, n=1)
    assert undone == 1
    assert st.数值["好感度"].value == -10.0       # 还原到第2次前
    assert len(st.历史) == hist_len - 1
    assert st.数值["好感度"].source == cs.SRC_USER


def test_rollback_空历史不报错():
    st = _state()
    assert cs.rollback_last(st, n=3) == 0


def test_delete_field_删数值与叙事():
    st = _state()
    st.叙事["心情"] = cs.NarrativeField("疲惫")
    assert cs.delete_field(st, "数值/好感度") is True
    assert "好感度" not in st.数值
    assert st.历史[-1]["op"] == "delete" and st.历史[-1]["to"] is None
    assert cs.delete_field(st, "叙事/心情") is True
    assert "心情" not in st.叙事


def test_delete_field_不存在返回False():
    st = _state()
    assert cs.delete_field(st, "数值/金币") is False
    assert cs.delete_field(st, "没有斜杠") is False


# ── 删除 vs 彻底清除（2026-09-23 用户拍板：修「UI 删除被重生成拉回」）──────────


def _aff_state() -> cs.CharacterState:
    """带显式属主好感度 + 快照好感行的状态（测快照摘除必须用显式属主，裸键无归属）。"""
    st = cs.CharacterState(card_name="卡", repo_id="r")
    st.快照 = cs.Snapshot("[时间] 第一日\n[好感] 甲=35；乙=10", 2)
    return st


def _seed_owner_affinity(st: cs.CharacterState, owner: str = "甲") -> None:
    cs.apply_deltas(st, [cs.StateDelta(1, f"数值/{owner}·好感度", "set", 30.0, "初见", cs.SRC_AUTO)])
    cs.apply_deltas(st, [cs.StateDelta(2, f"数值/{owner}·好感度", "add", 5.0, "升温", cs.SRC_AUTO)])


def test_delete_field_摘掉快照好感行里的该角色():
    st = _aff_state()
    _seed_owner_affinity(st)
    assert cs.delete_field(st, "数值/甲·好感度") is True
    assert "甲" not in st.快照.text            # 旧数字不再注入（否则模型照旧走 add）
    assert "乙=10" in st.快照.text             # 其他条目不受影响
    assert "[时间] 第一日" in st.快照.text      # 其他行不动


def test_delete_field_是撤销得回来的_这正是要修的那个坑():
    st = _aff_state()
    _seed_owner_affinity(st)
    assert cs.delete_field(st, "数值/甲·好感度") is True
    assert "甲·好感度" not in st.数值
    cs.rollback_last(st, n=1)                  # 撤销最近一条 = 这条 delete
    assert "甲·好感度" in st.数值               # ⚠ 字段复现＝「UI 删除被重生成拉回」的机制


def test_purge_field_删字段并清审计_断根后回滚也拉不回():
    st = _aff_state()
    _seed_owner_affinity(st)
    assert cs.purge_field(st, "数值/甲·好感度") is True
    assert "甲·好感度" not in st.数值
    assert all(h.get("field") != "数值/甲·好感度" for h in st.历史)
    assert "甲" not in st.快照.text
    cs.rollback_from_turn(st, turn=1)          # 「重新生成」的底层动作：撤到最早
    assert "甲·好感度" not in st.数值           # 审计链里已无该字段可供还原
    assert "甲" not in st.快照.text


def test_purge_field_叙事与不存在():
    st = _aff_state()
    st.叙事["甲·心情"] = cs.NarrativeField("戒备")
    assert cs.purge_field(st, "叙事/甲·心情") is True
    assert "甲·心情" not in st.叙事
    assert cs.purge_field(st, "数值/甲·好感度") is False
    assert cs.purge_field(st, "没有斜杠") is False


def test_purge_field_裸键好感度不碰快照():
    st = _aff_state()
    st.数值["好感度"] = cs.NumericField(50.0)
    assert cs.purge_field(st, "数值/好感度") is True
    assert "好感度" not in st.数值
    assert "甲=35" in st.快照.text             # 裸键无归属 ⇒ 快照行原样保留（宁缺勿猜）


def test_from_dict_重建并钳边界():
    src = _state()
    src.叙事["态度"] = cs.NarrativeField("戒备")
    dumped = src.to_dict()
    dumped["数值"]["好感度"]["value"] = 9999    # 超界值应被 from_dict 钳回
    rebuilt = cs.from_dict(dumped, repo_id="r2", card_name="c2")
    assert rebuilt.repo_id == "r2" and rebuilt.card_name == "c2"
    assert rebuilt.数值["好感度"].value == 120.0   # clamp 到该字段 max
    assert rebuilt.叙事["态度"].value == "戒备"


def test_from_dict_非dict安全返回空():
    st = cs.from_dict("garbage", repo_id="r", card_name="c")
    assert st.数值 == {} and st.叙事 == {}


def test_第一人称主角字段不入状态表():
    """2026-09-01 用户定案：第一人称的「我/主角/你」不是剧情角色，不入状态表。
    AI 常把主角当角色名写 field，parse_deltas 直接丢弃，只留真正的角色/全局字段。

    ⚠ 2026-09-23：末条改用 `set` 建字段——`add` 命中不存在的**好感度**字段已被新闸拒收
    （见 `test_add命中不存在的好感度字段被拒收不建值`），本用例考的是主角名过滤，不考 add 语义。
    """
    st = cs.CharacterState(card_name="凌霄仙母录", repo_id="r")
    deltas = cs.parse_deltas([
        {"field": "叙事/我·所在", "op": "set", "value": "寝殿"},
        {"field": "数值/主角·好感度", "op": "add", "value": 5},
        {"field": "数值/你·信任", "op": "add", "value": 2},
        {"field": "数值/好感度", "op": "set", "value": 3},
    ], turn=1, card_name="凌霄仙母录", existing_state=st)

    assert [d.field for d in deltas] == ["数值/好感度"]
    cs.apply_deltas(st, deltas)
    assert list(st.数值) == ["好感度"]
    assert st.叙事 == {}


def test_无归属状态无唯一角色时不再回退卡名():
    """合集卡名（如「凌霄仙母录」）不是角色名：无归属裸字段在 owner_hints 给不出
    唯一角色时保留裸键，禁止回退 card_name（2026-09-01 用户实锤：卡名被记成角色）。"""
    st = cs.CharacterState(card_name="凌霄仙母录", repo_id="r")
    deltas = cs.parse_deltas(
        [{"field": "叙事/身体状态", "op": "set", "value": "平静"}],
        turn=1,
        card_name="凌霄仙母录",
        existing_state=st,
    )

    cs.apply_deltas(st, deltas)

    assert list(st.叙事) == ["身体状态"]
    assert "凌霄仙母录" not in st.叙事


def test_无归属状态有唯一显式角色时仍沿用该角色():
    """owner_hints 命中时保持旧行为：无归属「身体状态」沿用到唯一显式角色「冷倾雪」，
    不因卡名保护而退化。"""
    st = cs.CharacterState(card_name="白给谷", repo_id="r")
    st.叙事["冷倾雪·身体状态"] = cs.NarrativeField("旧值", turn=1)
    deltas = cs.parse_deltas(
        [{"field": "叙事/身体状态", "op": "set", "value": "新值"}],
        turn=2,
        card_name="白给谷",
        existing_state=st,
    )

    cs.apply_deltas(st, deltas)

    assert list(st.叙事) == ["冷倾雪·身体状态"]
    assert st.叙事["冷倾雪·身体状态"].value == "新值"


def test_render_state_block_跳过主角字段():
    """存量主角字段不注入主控叙述：AI 看不到就不再继续更新它们。"""
    st = cs.CharacterState(card_name="凌霄仙母录", repo_id="r")
    st.叙事["我·所在"] = cs.NarrativeField("寝殿", turn=1)
    st.叙事["主角·心情"] = cs.NarrativeField("焦急", turn=1)
    st.叙事["凌若冰·心情"] = cs.NarrativeField("平静", turn=1)

    block = cs.render_state_block(st)

    assert "我·所在" not in block and "主角" not in block
    assert "凌若冰·心情" in block


def test_好感度裸键不被多角色字段清理():
    """单主角全局好感度 = 裸键「好感度」；consolidate 的裸键清理必须跳过它，
    否则多角色字段混入后单主角好感度被删（2026-09-01 用户实锤「好感度换新/消失」）。"""
    st = cs.CharacterState(card_name="白给谷", repo_id="r")
    st.数值["好感度"] = cs.NumericField(50, turn=5)
    st.数值["冷倾雪·好感度"] = cs.NumericField(30, turn=6)
    st.叙事["心情"] = cs.NarrativeField("平静", turn=5)
    st.叙事["冷倾雪·心情"] = cs.NarrativeField("戒备", turn=6)

    cs.consolidate_fields(st)

    assert set(st.数值) == {"好感度", "冷倾雪·好感度"}
    assert list(st.叙事) == ["冷倾雪·心情"]  # 裸「心情」仍有显式归属 → 照旧淘汰


# ── M1 记忆封口（2026-09-06 用户定案）：删除/重生成回合的状态变更整体撤销 ──


def test_rollback_from_turn撤销该回合及之后的状态变更():
    st = _state()
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": 10, "evidence": "回合2事件"}], turn=2))
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": 15, "evidence": "回合5事件"},
         {"field": "叙事/态度", "op": "set", "value": "热络", "evidence": "回合5事件"}], turn=5))
    assert st.数值["好感度"].value == -5.0

    rolled = cs.rollback_from_turn(st, turn=5)

    assert rolled == {"undone": 2, "incomplete": []}
    assert st.数值["好感度"].value == -20.0  # 只剩回合 2 的加成
    # 第 58 刀：叙事/态度是本回合的**初次登记**（from=None）⇒ 豁免回滚、保留初值
    #（用户工作流＝同一条消息反复重生成；回滚掉登记会让模型永远不再写这个字段）
    assert st.叙事["态度"].value == "热络"
    # 更早回合的审计与变更保留；豁免保留条目重盖为本封口回合（source=rescue）
    assert all(
        int(entry.get("turn") or 0) < 5 or entry.get("source") == "rescue"
        for entry in st.历史
    )
    assert cs.rollback_from_turn(st, turn=5) == {"undone": 0, "incomplete": []}  # 幂等


def test_第58刀_初次登记豁免回滚_同回合重生成不丢角色():
    """用户工作流实锤（2026-09-24）：「同一条消息反复重新生成直到满意」。旧行为把
    初次登记（from=None）也撤销 ⇒ 回滚后注入状态不再含该角色 ⇒ 模型不再写她 ⇒
    静默永久缺失（真机：师傅好感度两度被卷走）。新语义：登记保留、审计重盖、幂等。"""
    st = _state()
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/妲千沁·好感度", "op": "set", "value": 58,
          "evidence": "授业师尊、连续十二夜同床渐进（初次登记）"}], turn=53))
    assert st.数值["妲千沁·好感度"].value == 58.0
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/妲千沁·好感度", "op": "add", "value": 5,
          "evidence": "回合55事件"}], turn=55))

    rolled = cs.rollback_from_turn(st, turn=55)

    assert rolled == {"undone": 1, "incomplete": []}
    assert st.数值["妲千沁·好感度"].value == 58.0   # add 被撤、**登记保留**
    # 同封口再回滚 ⇒ 命中 source=rescue 原样放回 ⇒ 幂等
    assert cs.rollback_from_turn(st, turn=55) == {"undone": 0, "incomplete": []}
    assert st.数值["妲千沁·好感度"].value == 58.0


def test_第58刀_深封口越过登记回合时登记也豁免():
    """封口回合早于登记回合（如删更早的楼层）：创建条目被弹出 ⇒ 仍豁免（保留初值），
    不再像旧行为那样把整个字段连根拔掉。"""
    st = _state()
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "叙事/妲千沁·师徒界限", "op": "set", "value": "夜间默许",
          "evidence": "初次登记"}], turn=53))

    rolled = cs.rollback_from_turn(st, turn=48)   # 封口早于登记 ⇒ 创建条目也在撤销窗内

    assert rolled["undone"] == 1
    assert st.叙事["妲千沁·师徒界限"].value == "夜间默许"   # 保留初值
    assert any(e.get("source") == "rescue" for e in st.历史)


def test_第58刀_递减封口回滚后历史仍按turn升序():
    """审计 bug（2026-09-24 审计实锤）：rescue_keep 按 pop 顺序（降序）收集，若直接
    extend 会在「递减封口回滚」下打乱历史顺序 ⇒ 后续回滚循环提前终止。锁：多次递减
    封口后历史 turn 必须保持升序、[-1] 是最大 turn。"""
    st = _state()
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "叙事/甲·界限", "op": "set", "value": "初值甲", "evidence": "登记"}], turn=53))
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "叙事/乙·界限", "op": "set", "value": "初值乙", "evidence": "登记"}], turn=54))
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "叙事/丙·界限", "op": "set", "value": "初值丙", "evidence": "登记"}], turn=55))

    cs.rollback_from_turn(st, turn=55)   # 丙 豁免 → rescue(55)
    cs.rollback_from_turn(st, turn=54)   # 乙 豁免 → rescue(54)
    cs.rollback_from_turn(st, turn=53)   # 甲 豁免 → rescue(53)

    turns = [int(e.get("turn") or 0) for e in st.历史]
    assert turns == sorted(turns)        # 历史保持升序（审计 bug 的锁）
    # 三个初值全部保留
    assert st.叙事["甲·界限"].value == "初值甲"
    assert st.叙事["乙·界限"].value == "初值乙"
    assert st.叙事["丙·界限"].value == "初值丙"
    # 再回滚一次高回合仍幂等、不残留
    cs.rollback_from_turn(st, turn=55)
    assert st.叙事["丙·界限"].value == "初值丙"


def test_审计窗口被截断时封口显式上报不完整字段():
    """M1 审计 #2：长剧情审计超 _HISTORY_CAP 后，封口老回合可能无法精确还原——
    必须显式上报（state_incomplete），绝不静默错账。"""
    st = _state()
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": 5, "evidence": "回合1"}], turn=1))
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": 5, "evidence": "回合2"}], turn=2))
    for i in range(cs._HISTORY_CAP + 60):  # 灌爆审计窗口，回合1/2 的条目被丢最旧
        cs.apply_deltas(st, cs.parse_deltas(
            [{"field": "数值/好感度", "op": "add", "value": 0, "evidence": f"填充{i}"}],
            turn=3 + i // 50))
    assert len(st.历史) == cs._HISTORY_CAP

    rolled = cs.rollback_from_turn(st, turn=2)

    assert rolled["undone"] == cs._HISTORY_CAP
    assert "数值/好感度" in rolled["incomplete"]  # 撤销链最老条目回合(3) > 封口回合(2) → 断层


# ── 表格文本破甲还原（2026-09-13 用户实锤）──────────────────────────────


def test_模型输出的证据与叙事值落库前还原拆字标记():
    """模型按防拦截预设写正文，evidence / 叙事值引用正文时把 `@` 拆字标记带出；
    表格文本不参与网关检测 ⇒ 落库前必须机械还原（状态表「依据」栏不再是 @为@争@…）。"""
    deltas = cs.parse_deltas([
        {"field": "数值/好感度", "op": "add", "value": 5,
         "evidence": "@为@争@被@插@彻@底@撕@碎@体@面@，@不@顾@宗@主@尊@严@"},
        {"field": "叙事/态度", "op": "set",
         "value": "@完@全@臣@服@", "evidence": "@威@压@"},
    ], turn=7)

    assert deltas[0].evidence == "为争被插彻底撕碎体面，不顾宗主尊严"
    assert deltas[1].value == "完全臣服"
    assert deltas[1].evidence == "威压"


def test_人为编辑的表格文本原样保留不清洗():
    """用户在前端手改的状态字段（source=user）不经过破甲还原——不动用户输入。"""
    deltas = cs.parse_deltas([
        {"field": "叙事/态度", "op": "set", "value": "@保@留@", "evidence": "@原@样@"},
    ], turn=3, source=cs.SRC_USER)

    assert deltas[0].value == "@保@留@"


# ── 快照好感行机械对账（2026-09-18 用户实锤：双账本分叉）──────────────────
# status 行是模型自述（快照存原样）、引擎数值是 delta 累加，两套账漂移后快照每轮
# 重注入被模型照抄 → 对话里 [好感] 永远停在旧自述值。对账＝注入/落盘前用引擎真源
# 覆盖行内数字。


def _affinity_state() -> cs.CharacterState:
    st = cs.CharacterState(card_name="凌霄仙母录", repo_id="r1")
    st.数值["凌若冰·好感度"] = cs.NumericField(40.0, min=-100.0, max=100.0)
    st.数值["舞姬恋·好感度"] = cs.NumericField(2.0, min=-100.0, max=100.0)
    return st


def test_快照对账_好感行数字替换为引擎现值():
    st = _affinity_state()
    snap = ("[时间] 大比前三日·午后\n"
            "[好感] 凌若冰=40/冰山融化（气急败坏欲夺回交合权）；舞姬恋=52/旖旎试探（骑乘交合中）\n"
            "[在场] 凌若冰、舞姬恋")
    out = cs.reconcile_snapshot_affinity(st, snap)
    assert "凌若冰=40/冰山融化（气急败坏欲夺回交合权）" in out   # 与引擎一致 → 不动
    assert "舞姬恋=2/旖旎试探（骑乘交合中）" in out               # 52 → 引擎现值 2
    assert "52" not in out                                        # 旧自述值不得残留
    assert "[时间] 大比前三日·午后" in out and "[在场] 凌若冰、舞姬恋" in out


def test_快照对账_冒号写法同样替换():
    st = cs.CharacterState(card_name="c", repo_id="r")
    st.数值["好感度"] = cs.NumericField(12.0, min=-100.0, max=100.0)
    snap = "[好感] 凌若冰:0"
    out = cs.reconcile_snapshot_affinity(st, snap)
    # 引擎只有无归属裸好感度、快照行只有一名角色 → 裸值归属该角色
    assert out == "[好感] 凌若冰=12"


def test_快照对账_引擎有而快照缺的角色追加():
    st = cs.CharacterState(card_name="c", repo_id="r")
    st.数值["凌若冰·好感度"] = cs.NumericField(40.0, min=-100.0, max=100.0)
    st.数值["舞姬恋·好感度"] = cs.NumericField(2.0, min=-100.0, max=100.0)
    snap = "[好感] 凌若冰=40/冰山融化"
    out = cs.reconcile_snapshot_affinity(st, snap)
    assert out == "[好感] 凌若冰=40/冰山融化；舞姬恋=2"


def test_快照对账_无好感行保持原样():
    st = _affinity_state()
    snap = "[时间] 午后\n[在场] 凌若冰"
    assert cs.reconcile_snapshot_affinity(st, snap) == snap


def test_快照对账_解析不出条目保持原样():
    st = _affinity_state()
    snap = "[好感] 暂无"
    assert cs.reconcile_snapshot_affinity(st, snap) == snap


def test_快照对账_幂等():
    st = _affinity_state()
    snap = "[好感] 凌若冰=40/冰山融化；舞姬恋=52/旖旎试探"
    once = cs.reconcile_snapshot_affinity(st, snap)
    twice = cs.reconcile_snapshot_affinity(st, once)
    assert once == twice


def test_快照对账_引擎为空原样返回():
    st = cs.CharacterState(card_name="c", repo_id="r")
    snap = "[好感] 凌若冰=40/冰山融化"
    assert cs.reconcile_snapshot_affinity(st, snap) == snap


def test_render_snapshot_injection_注入前对账好感行():
    st = _affinity_state()
    st.快照 = cs.Snapshot("[好感] 凌若冰=40/冰山融化；舞姬恋=52/旖旎试探", turn=36)
    inj = cs.render_snapshot_injection(st)
    assert "舞姬恋=2/旖旎试探" in inj and "52" not in inj


# ── 播种死锁修复：引擎无、行内有 ⇒ 从行里删条目（2026-09-23 深夜）─────────────
# 真机：模型把幻觉数字写进 <status>[好感] 行（妲千沁=38），旧行为原样保留 ⇒ 下轮
# prompt 同屏出现「快照说她=38」与「点名块说她还没有字段」两条互斥信息 ⇒ 永不播种。

def test_快照对账_引擎无的条目从行里删掉_这是播种死锁的根():
    st = _affinity_state()          # 引擎：凌若冰=40、舞姬恋=2
    snap = "[好感] 舞姬恋=2；凌若冰=40；妲千沁=38"
    out = cs.reconcile_snapshot_affinity(st, snap)
    assert "妲千沁" not in out       # 幻觉条目被抹掉，不再与点名块打架
    assert "38" not in out
    assert out == "[好感] 舞姬恋=2；凌若冰=40"


def test_快照对账_幻觉条目在行首时不留多余分隔符():
    st = _affinity_state()
    snap = "[好感] 妲千沁=38；凌若冰=40；舞姬恋=2"
    out = cs.reconcile_snapshot_affinity(st, snap)
    assert out == "[好感] 凌若冰=40；舞姬恋=2"
    assert not out.startswith("[好感] ；")
    assert "；；" not in out


def test_快照对账_幻觉条目在行尾时不留尾随分隔符():
    st = _affinity_state()
    snap = "[好感] 凌若冰=40；舞姬恋=2；骆姑姑=18"
    out = cs.reconcile_snapshot_affinity(st, snap)
    assert out == "[好感] 凌若冰=40；舞姬恋=2"
    assert not out.rstrip().endswith("；")


def test_快照对账_连续多条幻觉一次删净():
    st = _affinity_state()
    snap = "[好感] 妲千沁=38；骆姑姑=18；凌若冰=40；舞姬恋=2；路人甲=5"
    out = cs.reconcile_snapshot_affinity(st, snap)
    assert out == "[好感] 凌若冰=40；舞姬恋=2"


def test_快照对账_行内全被删空时保留裸行头():
    st = _affinity_state()
    snap = "[好感] 妲千沁=38；骆姑姑=18\n[在场] 妲千沁"
    out = cs.reconcile_snapshot_affinity(st, snap)
    # 引擎两条都不在行内 ⇒ 追加，同时确保没有残留幻觉
    assert "38" not in out and "18" not in out
    assert "[好感]" in out
    assert "[在场] 妲千沁" in out


def test_快照对账_删幻觉后仍会把引擎有而行内缺的补上():
    st = _affinity_state()
    snap = "[好感] 妲千沁=38"
    out = cs.reconcile_snapshot_affinity(st, snap)
    assert "妲千沁" not in out and "38" not in out
    assert "凌若冰=40" in out and "舞姬恋=2" in out


def test_快照对账_删幻觉后仍幂等():
    st = _affinity_state()
    snap = "[好感] 舞姬恋=2；妲千沁=38；凌若冰=40"
    once = cs.reconcile_snapshot_affinity(st, snap)
    twice = cs.reconcile_snapshot_affinity(st, once)
    assert once == twice


def test_快照对账_带档位描述的幻觉条目整条删掉不残留描述():
    st = cs.CharacterState(card_name="c", repo_id="r")
    st.数值["凌若冰·好感度"] = cs.NumericField(40.0, min=-100.0, max=100.0)
    snap = "[好感] 凌若冰=40/冰山融化；妲千沁=38/动摇（师尊架子尚存）"
    out = cs.reconcile_snapshot_affinity(st, snap)
    assert out == "[好感] 凌若冰=40/冰山融化"
    assert "动摇" not in out and "师尊架子" not in out


def test_快照对账_冒号写法的幻觉条目同样删掉():
    st = cs.CharacterState(card_name="c", repo_id="r")
    st.数值["凌若冰·好感度"] = cs.NumericField(40.0, min=-100.0, max=100.0)
    snap = "[好感] 凌若冰:40；妲千沁:38"
    out = cs.reconcile_snapshot_affinity(st, snap)
    assert out == "[好感] 凌若冰=40"


@pytest.mark.parametrize("snap", [
    "[好感] A=1；B=9",           # 幻觉在尾
    "[好感] B=9；A=1",           # 幻觉在首
    "[好感] B=9；A=1；C=7",      # 幻觉夹在中间
    "[好感] A=1；B=9；C=7",      # 幻觉在尾（多）
    "[好感] B=9；C=7",           # 全是幻觉，靠 missing 补回
    "[好感]  B=9；A=1",          # 两个前导空格
    "[好感] A=1；B=9/档位",      # 幻觉带档位描述
])
def test_快照对账_任意位置的幻觉删净后不留悬空分隔符(snap):
    st = cs.CharacterState(card_name="c", repo_id="r")
    st.数值["A·好感度"] = cs.NumericField(1.0, min=-100.0, max=100.0)
    out = cs.reconcile_snapshot_affinity(st, snap)
    body = out.split("\n")[0]
    assert "；；" not in out
    assert not body.rstrip().endswith("；")
    assert not body.startswith("[好感]；")
    assert not body.startswith("[好感] ；")
    assert "A=1" in out
    for ghost in ("B=9", "C=7"):
        assert ghost not in out


def test_快照对账_引擎外的第三角色不受影响():
    st = cs.CharacterState(card_name="c", repo_id="r")
    st.数值["A·好感度"] = cs.NumericField(1.0, min=-100.0, max=100.0)
    st.数值["D·好感度"] = cs.NumericField(4.0, min=-100.0, max=100.0)
    out = cs.reconcile_snapshot_affinity(st, "[好感] B=9；A=1；D=4")
    assert out == "[好感] A=1；D=4"
