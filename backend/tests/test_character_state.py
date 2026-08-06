"""角色动态状态：parse/apply 纯逻辑 + 落盘往返 + provenance 语义。"""
from __future__ import annotations

from app.services import character_state as cs


def _state() -> cs.CharacterState:
    st = cs.CharacterState(card_name="埃斯托利亚", repo_id="repo1")
    st.数值["好感度"] = cs.NumericField(-30.0, min=-50.0, max=120.0)
    return st


def test_parse_deltas_归一与非法跳过():
    raw = [
        {"field": "数值/好感度", "op": "add", "value": 25, "evidence": "调运食材做了一桌菜"},
        {"field": "叙事/对{{user}}态度", "op": "set", "value": "戒备", "evidence": "第3章救援"},
        {"field": "数值/好感度", "op": "set", "value": 99},   # 数值只认 add → 跳过
        {"field": "叙事/心情", "op": "set", "value": ""},      # 空叙事 → 跳过
        {"field": "没有斜杠", "op": "add", "value": 1},         # 缺 kind → 跳过
        "not a dict",
    ]
    deltas = cs.parse_deltas(raw, turn=42)
    assert len(deltas) == 2
    assert deltas[0].kind() == "数值" and deltas[0].op == "add" and deltas[0].value == 25.0
    assert deltas[1].kind() == "叙事" and deltas[1].leaf() == "对{{user}}态度"


def test_apply_数值累加并clamp():
    st = _state()
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": 200, "evidence": "x"}], turn=1))
    assert st.数值["好感度"].value == 120.0  # clamp 到 max
    assert st.历史[-1]["from"] == -30.0 and st.历史[-1]["to"] == 120.0


def test_好感度首次创建锁到正负100():
    # 状态里无好感度字段时，首次 delta 应套用已知字段边界 -100..100，而非无界
    st = cs.CharacterState(card_name="c", repo_id="r")
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": 999}], turn=1))
    assert st.数值["好感度"].value == 100.0
    assert (st.数值["好感度"].min, st.数值["好感度"].max) == (-100.0, 100.0)
    # 未登记字段仍无界
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/金币", "op": "add", "value": 5000}], turn=2))
    assert st.数值["金币"].value == 5000.0


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

    assert set(st.数值) == {"塞西莉亚·好感度"}
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
