"""能动性子图编排：搭车解析、门控塌回、仲裁转指令、写回跨档、插画门控。全假件不碰真 LLM/出图。"""
from __future__ import annotations

import random

from app.services import agency, character_state
from app.services import roleplay_agency as ra
from app.services.scene_illustration import SceneRequest


def _deps(tmp_path, renderer=None, seed=0):
    return ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(seed),
        state_base=str(tmp_path), renderer=renderer)


# ── 阶段 B：搭车解析 ──

def test_搭车解析剥离状态块():
    reply = "她别过脸，指尖仍在发抖。\n<状态更新>[{\"field\":\"数值/好感度\",\"op\":\"add\",\"value\":5,\"evidence\":\"救援\"}]</状态更新>"
    clean, raw = ra.parse_state_block(reply)
    assert clean == "她别过脸，指尖仍在发抖。"
    assert raw == [{"field": "数值/好感度", "op": "add", "value": 5, "evidence": "救援"}]


def test_无块返回原文空列表():
    clean, raw = ra.parse_state_block("纯叙事没有状态块")
    assert clean == "纯叙事没有状态块" and raw == []


def test_块内json坏不丢正文():
    clean, raw = ra.parse_state_block("正文\n<状态更新>{坏json</状态更新>")
    assert clean == "正文" and raw == []


def test_状态更新缺闭标签仍解析并从正文剥离():
    reply = (
        "正文\n<状态更新>"
        '[{"field":"叙事/冷倾雪·精神状态","op":"set",'
        '"value":"清醒","evidence":"本轮明确"}]'
    )

    clean, raw = ra.parse_state_block(reply)

    assert clean == "正文"
    assert raw == [{
        "field": "叙事/冷倾雪·精神状态", "op": "set",
        "value": "清醒", "evidence": "本轮明确",
    }]


def test_模型漏战报时把上轮快照保留在正文开头():
    reply = "<content>本轮正文</content>"

    restored = ra.ensure_status_snapshot(reply, "[时间] 第四日\n[在场] 冷倾雪")

    assert restored.startswith("<status>\n[时间] 第四日\n[在场] 冷倾雪\n</status>")
    assert restored.endswith(reply)


def test_多角色状态指令要求显式角色归属():
    instruction = ra.state_instruction()
    assert "数值/角色名·好感度" in instruction
    assert "叙事/角色名·身体状态" in instruction
    assert "禁止把身体、精神等状态类别拼进角色名" in instruction


def test_状态更新指令要求缺字段时播种初值不默认0():
    """2026-09-22 用户口径：角色应按剧情因果给初始好感/恶感，不应从 0 起算。

    2026-09-23 修正闸门（真机实锤）：旧措辞「某角色**第一次出现**、状态表中尚没有其好感度
    字段时」把叙事条件与引擎条件绑在一起 ⇒ 已登场但被删过字段的角色**永远不满足**（妲千沁
    turn 51 已登场 7 轮、字段为空、三条前提全齐，仍判「不是第一次出现」⇒ 照样 add 从 0 起算）。
    闸门必须只说引擎认的那个条件：**字段是否存在**。
    """
    instruction = ra.state_instruction()
    assert "好感度初值" in instruction
    assert "尚没有某角色的好感度字段" in instruction
    assert "无论其是否已经登场" in instruction          # 闸门＝字段存在性，非叙事首次
    assert "第一次出现" not in instruction            # 旧闸门已废，防回退
    assert "禁止默认从 0 起算" in instruction
    assert "当前应有的好感度" in instruction          # 定「当前应有」而非「初始」
    assert "间接影响" in instruction
    assert '"set"' in instruction or "op=\"set\"" in instruction
    assert "仅" in instruction and "字段不存在时采纳" in instruction


def test_状态更新指令区分建表定锚与后续增量():
    """用户 2026-09-23 口径：初次见面用因果定值（乙方案），后续靠表格已有值循序渐进。

    即「防飘忽」不靠把初值写准，而靠建表后**改为增量累积**、以注入现值为锚。
    """
    instruction = ra.state_instruction()
    assert "首次为某角色建表时按人设与既有关系把基准定准" in instruction
    assert "不要被单场戏的强弱牵着走" in instruction
    assert "字段一旦存在" in instruction
    assert "只报 add 增量" in instruction
    assert "以注入的现值为锚循序渐进" in instruction
    assert "不要重定基准" in instruction

def test_好感度初值规则两处真源同源():
    """闸门措辞必须同时出现在 `state_instruction`（正文通道）与 `table_update._DELTA_RULES`
    （独立维护通道）——只改一处的那一半通路会继续用旧闸门（叙事「第一次出现」）判案。"""
    from app.services import table_update
    a = ra.state_instruction()
    b = table_update._DELTA_RULES
    for frag in (
        "状态表中尚没有某角色的好感度字段时",
        "无论其是否已经登场",
        "禁止默认从 0 起算",
        "首次为某角色建表时",
        "字段一旦存在，",
        "只报 add 增量",
        "不要重定基准",
    ):
        assert frag in a, f"state_instruction 缺：{frag}"
        assert frag in b, f"_DELTA_RULES 缺：{frag}"
    # 旧闸门两处都不得残留
    assert "第一次出现" not in a and "第一次出现" not in b


def test_好感度是综合因素考量而非封闭清单():
    """2026-09-23 用户口径：好感度像掷骰一样是**多因素综合考量**的产物——初次见面的
    仪表/相貌、角色自身的好恶与忌讳、既有关系、直接或间接接触等都可参与；且**角色自身的
    偏好与忌讳优先于通用关系参考**（条目写其厌恶俊美男子，则 {{user}} 的容貌优势反而减分）。

    ⚠ 措辞刻意把因素写成「举例、不限于此、不设固定权重」，**不做封闭清单**——用户明示
    固定清单会限制模型自由发挥的空间；与本仓【输入性质】两种演法刻意不写枚举示例同源。
    三档关系区间同时**降级为「起点参考、可越出」**（保留防 0 起算的锚，但不再是归类判定）。
    """
    instruction = ra.state_instruction()
    assert "综合与该角色相关的各项因素" in instruction
    assert "仅为举例" in instruction and "不限于此" in instruction
    assert "不设固定权重" in instruction              # 不给权重表，避免退化成逐项打分
    assert "偏好与忌讳优先" in instruction            # 角色好恶 > 通用关系参考
    assert "可以按其人设与经历越出这一区间" in instruction
    assert "不要只凭单一因素定值" in instruction
    # 关系区间仍在（防 0 起算的锚不许丢），但必须是「只作起点参考」
    assert "只作起点参考" in instruction
    assert "55-90" in instruction and "20-55" in instruction


def test_综合因素口径两处真源同源():
    """新口径必须**同时**落在 `state_instruction`（正文通道）与 `table_update._DELTA_RULES`
    （独立维护通道）——只改一处 ⇒ 另一半通路继续按「关系区间归类」判案。"""
    from app.services import table_update
    a = ra.state_instruction()
    b = table_update._DELTA_RULES
    for frag in (
        "综合与该角色相关的各项因素",
        "仅为举例",
        "不限于此",
        "不设固定权重",
        "偏好与忌讳优先",
        "不要只凭单一因素定值",
        "可以按其人设与经历越出这一区间",
    ):
        assert frag in a, f"state_instruction 缺：{frag}"
        assert frag in b, f"_DELTA_RULES 缺：{frag}"


def test_状态更新指令要求好感行以引擎现值为准():
    """2026-09-18 用户实锤：模型自编/沿用历史楼层好感数字（52）而引擎账（2.0）冻结。
    指令必须硬性约束：[好感] 行逐字用【当前状态】现值；变化只报相对增量。"""
    instruction = ra.state_instruction()
    assert "【当前状态】" in instruction
    assert "禁止沿用历史楼层旧数字或自编数值" in instruction
    assert "增量相对现值" in instruction


# ── 阶段 A：门控 + 仲裁 ──

def test_世界缺core场景时不调LLM(tmp_path):
    # 回归：consult_world 守卫只认 core/scene 非空（原「not scene_illustration」模块对象恒真为死条件，已摘除）
    called = {"n": 0}

    def spy(*a, **k):
        called["n"] += 1
        return "[]"

    deps = ra.AgencyDeps(
        chat_fn=spy, rng=random.Random(1), state_base=str(tmp_path), gate_base_rate=1.0,
    )
    out = ra.consult_world(deps, chat_base="", chat_key="", chat_model="",
                           core="", scene="舞会", affinity=90)
    assert out == [] and called["n"] == 0


def test_门控关时塌回不调LLM(tmp_path):
    called = {"n": 0}

    def spy(*a, **k):
        called["n"] += 1
        return "[]"

    deps = ra.AgencyDeps(
        chat_fn=spy, rng=random.Random(1), state_base=str(tmp_path), gate_base_rate=0,
    )
    # 用户显式关闭门控 → 不调 chat_fn
    out = deps and ra.consult_world(deps, chat_base="", chat_key="", chat_model="",
                                    core="c", scene="s", affinity=-10)
    assert out == [] and called["n"] == 0


def test_门控开时提案被仲裁(tmp_path):
    # 高好感度让门控几乎必开；世界 Agent 返回一条有 basis 的提案
    proposal_json = ('[{"actor":"埃斯托利亚","intent":"在酒里下药","difficulty":30,'
                     '"min_affinity":20,"basis":"[从舌头开始] 个体机制"}]')
    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: proposal_json,
                         rng=random.Random(3), state_base=str(tmp_path),
                         gate_base_rate=0.2)
    verdicts = ra.consult_world(deps, chat_base="", chat_key="", chat_model="",
                                core="core", scene="舞会", affinity=90)
    assert len(verdicts) == 1 and verdicts[0].actor == "埃斯托利亚"


def test_首轮无好感记录仍调用世界agent(tmp_path):
    called = {"n": 0}

    def propose(*args, **kwargs):
        called["n"] += 1
        return "[]"

    deps = ra.AgencyDeps(
        chat_fn=propose, rng=random.Random(0), state_base=str(tmp_path),
        gate_base_rate=1.0,
    )

    verdicts = ra.consult_world(
        deps, chat_base="", chat_key="", chat_model="",
        core="塞西莉亚角色条目", scene="拒绝收养", affinity=None,
    )

    assert verdicts == []
    assert called["n"] == 1


def test_世界提案保留目标与行动供主模型叙述(tmp_path):
    proposal_json = (
        '[{"actor":"塞西莉亚","goal":"把有价值的孩子纳入长期掌控",'
        '"intent":"留下可被幽影商会识别的徽章作为后手","difficulty":40,'
        '"min_affinity":-10,"basis":"掌控欲极强，越在意越想掌控"}]'
    )
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: proposal_json,
        rng=random.Random(1), state_base=str(tmp_path), gate_base_rate=1.0,
    )

    verdicts = ra.consult_world(
        deps, chat_base="", chat_key="", chat_model="",
        core="塞西莉亚角色条目", scene="主角拒绝收养", affinity=0,
    )
    directive = ra.narrative_directive(verdicts)

    assert verdicts[0].goal == "把有价值的孩子纳入长期掌控"
    assert verdicts[0].intent == "留下可被幽影商会识别的徽章作为后手"
    assert "把有价值的孩子纳入长期掌控" in directive
    assert "留下可被幽影商会识别的徽章作为后手" in directive


def test_仲裁转叙述指令与失控判定():
    # intent 由 judge 从 Proposal 复制到 Verdict，指令组装只认 Verdict 自带 intent
    won = agency.Verdict("A", agency.OUTCOME_ACCEPT, agency.DEGREE_FULL, 5, 80, "得手", intent="下药")
    miss = agency.Verdict("B", agency.OUTCOME_REJECT, agency.DEGREE_NONE, 90, 80, "未遂")
    directive = ra.narrative_directive([won, miss])
    assert "A：下药" in directive and "B" not in directive
    assert ra.agency_lost([won, miss]) is True
    assert ra.agency_lost([miss]) is False


def test_无得手时指令为空():
    miss = agency.Verdict("B", agency.OUTCOME_REJECT, agency.DEGREE_NONE, 90, 80, "未遂")
    assert ra.narrative_directive([miss]) == ""


def test_已尝试但失败的自主行动也必须进入叙事():
    miss = agency.Verdict(
        "塞西莉亚", agency.OUTCOME_REJECT, agency.DEGREE_NONE, 90, 80, "掷骰失败",
        intent="暗中留下追踪印记", goal="保持对主角的长期掌控",
    )

    directive = ra.narrative_directive([miss])

    assert "暗中留下追踪印记" in directive
    assert "失败" in directive


# ── 阶段 C：写回 ──

def test_写回累加好感度并落盘(tmp_path):
    deps = _deps(tmp_path)
    # 2026-09-23（add 命中不存在的好感度字段 ⇒ 拒收闸）：初值必须先 `set` 播种，
    # 之后的 `add` 才有字段可累加——本用例考的是「落盘生效 + 二次从已存值继续累加」。
    seed = [{"field": "数值/好感度", "op": "set", "value": 5, "evidence": "救援"}]
    before, after = ra.writeback(deps, repo_id="r1", card_name="卡", raw_deltas=seed, turn=1)
    assert before is None and after == 5.0
    raw = [{"field": "数值/好感度", "op": "add", "value": 5, "evidence": "救援"}]
    # 二次写回从已存值继续累加，证明落盘生效
    b2, a2 = ra.writeback(deps, repo_id="r1", card_name="卡", raw_deltas=raw, turn=2)
    assert b2 == 5.0 and a2 == 10.0


def test_空delta不改值(tmp_path):
    deps = _deps(tmp_path)
    before, after = ra.writeback(deps, repo_id="r1", card_name="卡", raw_deltas=[], turn=1)
    assert before is None and after is None


# ── <status> 快照：抽取（不剥）+ 落盘 + 抗压缩重建 ──

def test_抽取status快照不剥正文():
    reply = "她别过脸。\n<status>[所在] 山洞\n[臣服] 叶燃眉=100</status>\n尾句。"
    snap = ra.extract_status_snapshot(reply)
    assert snap == "[所在] 山洞\n[臣服] 叶燃眉=100"


def test_无status块返回空():
    assert ra.extract_status_snapshot("纯叙事无状态栏") == ""


def test_多status块取最后一个():
    reply = "<status>旧栏</status>过场<status>新栏</status>"
    assert ra.extract_status_snapshot(reply) == "新栏"


def test_think内预写status草稿不顶替真身():
    # 2026-08-30 trace 实锤：模型在 think 里预写 <status> 草稿（含未闭合），
    # 快照必须取正文里的真栏，思考草稿不得顶替。
    reply = (
        "<think>推演：先列状态栏草稿\n<status>\n[时间] 草稿·被思考污染\n"
        "<encounter>草稿锚点</encounter>\n"
        "继续思考</think>\n正文。\n<status>\n[时间] 真栏\n</status>"
    )
    assert ra.extract_status_snapshot(reply) == "[时间] 真栏"


def test_只有think内status时快照为空():
    reply = "<think>草稿<status>只有草稿</status></think>正文没有状态栏。"
    assert ra.extract_status_snapshot(reply) == ""


def test_搭车解析忽略think内预写的状态更新草稿():
    reply = (
        "<think>推演草稿\n<状态更新>[{\"field\":\"数值/好感度\",\"op\":\"add\","
        "\"value\":99,\"evidence\":\"草稿\"}]</状态更新>\n继续思考</think>\n"
        "正文她别过脸。\n<状态更新>[{\"field\":\"数值/好感度\",\"op\":\"add\","
        "\"value\":5,\"evidence\":\"真身\"}]</状态更新>"
    )
    clean, raw = ra.parse_state_block(reply)
    assert raw == [{"field": "数值/好感度", "op": "add", "value": 5, "evidence": "真身"}]
    assert "正文她别过脸。" in clean  # 正文真身不被误删
    assert "<think>" in clean  # think 留在正文里，渲染属主仍是前端正则
    assert raw[0]["value"] == 5  # 草稿的 99 不顶替真身


def test_think内未闭合status开标签不影响快照():
    # 未闭合 <status> 开在 think 内：不能吞掉 think 之后的正文真栏
    reply = (
        "<think>草稿开始\n<status>\n[时间] 未闭合草稿一直延伸\n"
        "更多思考</think>\n正文。\n<status>\n[所在] 真栏\n</status>"
    )
    assert ra.extract_status_snapshot(reply) == "[所在] 真栏"


def test_快照随写回落盘可重建(tmp_path):
    deps = _deps(tmp_path)
    ra.writeback(deps, repo_id="r1", card_name="卡", raw_deltas=[], turn=3,
                 snapshot="[所在] 山洞")
    st = character_state.load_state(str(tmp_path), "r1", "卡")
    assert st.快照.text == "[所在] 山洞" and st.快照.turn == 3
    # 空快照不覆盖已存（下轮 AI 偶尔漏吐 <status> 不该清空状态栏）
    ra.writeback(deps, repo_id="r1", card_name="卡", raw_deltas=[], turn=4, snapshot="")
    st2 = character_state.load_state(str(tmp_path), "r1", "卡")
    assert st2.快照.text == "[所在] 山洞"


def test_writeback落盘快照前先对账好感行(tmp_path):
    """双账本对齐（2026-09-18）：写回时先应用 delta 再对账快照存盘，
    保证重注入与漏战报兜底（ensure_status_snapshot）拿到的都是引擎真源数字。"""
    deps = _deps(tmp_path)
    ra.writeback(
        deps, repo_id="r1", card_name="卡", turn=34,
        raw_deltas=[{"field": "数值/舞姬恋·好感度", "op": "set", "value": 2,
                     "evidence": "争宠"}],
        snapshot="[好感] 凌若冰=40/冰山融化；舞姬恋=52/旖旎试探")
    st = character_state.load_state(str(tmp_path), "r1", "卡")
    assert st.数值["舞姬恋·好感度"].value == 2.0
    assert "舞姬恋=2/旖旎试探" in st.快照.text
# ── 展示对账：正文 <status> 也走引擎真源（2026-09-22 用户实锤）──
# 此前只对账「落盘快照」与「重注入快照」，用户屏幕上看到的 [好感] 行仍是模型自述
# ⇒ 同一角色正文 16→13→10 乱跳、引擎恒 13。本节点末尾发 replace 全文替换气泡，
# 故 writeback 处对账正文即等于修显示。

def _affinity_state(*pairs) -> character_state.CharacterState:
    st = character_state.CharacterState(card_name="凌霄仙母录", repo_id="r1")
    for leaf, value in pairs:
        st.数值[leaf] = character_state.NumericField(value, min=-100.0, max=100.0)
    return st


def test_正文status好感行对账为引擎真值():
    st = _affinity_state(("妲千沁·好感度", 13.0), ("凌若冰·好感度", 52.0))
    reply = """正文她没回头。
<status>
[在场] 妲千沁
[好感] 妲千沁=10/清冷；凌若冰=52
</status>"""
    out = ra.reconcile_reply_status(reply, st)
    assert "妲千沁=13/清冷" in out      # 引擎真值覆盖模型自述（档位描述保留）
    assert "妲千沁=10" not in out
    assert "凌若冰=52" in out           # 本就一致的不动
    assert out == reply.replace("妲千沁=10", "妲千沁=13")   # 其余逐字不动（含空白填充）


def test_正文对账幂等():
    st = _affinity_state(("妲千沁·好感度", 13.0))
    reply = """<status>
[好感] 妲千沁=10
</status>"""
    once = ra.reconcile_reply_status(reply, st)
    assert ra.reconcile_reply_status(once, st) == once


def test_正文对账只改最后一个status块():
    """多块时末块为最新（与 extract_status_snapshot 同口径）；复述的旧栏不被动。"""
    st = _affinity_state(("妲千沁·好感度", 13.0))
    reply = """<status>
[好感] 妲千沁=77
</status>过场<status>
[好感] 妲千沁=10
</status>"""
    out = ra.reconcile_reply_status(reply, st)
    assert "妲千沁=77" in out       # 旧栏原样
    assert "妲千沁=13" in out       # 末块已对账
    assert "妲千沁=10" not in out


def test_正文无status块原样返回():
    st = _affinity_state(("妲千沁·好感度", 13.0))
    assert ra.reconcile_reply_status("纯叙事，没有状态栏。", st) == "纯叙事，没有状态栏。"


def test_引擎无好感字段时正文原样返回():
    """用户手删光好感字段后（2026-09-23 实机场景）：不得改写正文，也不得抛错。"""
    st = character_state.CharacterState(card_name="卡", repo_id="r1")
    reply = """<status>
[好感] 妲千沁=10
</status>"""
    assert ra.reconcile_reply_status(reply, st) == reply


def test_正文对账不碰think内草稿():
    """think 里的 <status> 草稿既不当真身、也不得被改写（与 parse_state_block 同判据）。"""
    st = _affinity_state(("妲千沁·好感度", 13.0))
    reply = """<think>推演：先列草稿
<status>
[好感] 妲千沁=99
</status>
继续想</think>正文。 <status>
[好感] 妲千沁=10
</status>"""
    out = ra.reconcile_reply_status(reply, st)
    assert "妲千沁=99" in out       # 草稿原样（未被改写，也未顶替真身）
    assert "妲千沁=10" not in out


def test_写回后对账正文拿到的是本轮delta结果(tmp_path):
    """真机序列复刻（_wb_apply_state 四步）：抽快照 → 写回落盘 → 重载 → 对账正文。

    锁「必须用重载后的 st」：拿写回前的 st 对账，正文会停在旧值（等于没修）。
    """
    deps = _deps(tmp_path)
    reply = """正文她没回头。
<status>
[好感] 妲千沁=10/清冷
</status>
<状态更新>[{"field": "数值/妲千沁·好感度", "op": "set", "value": 3, "evidence": "第三夜"}]</状态更新>"""
    snapshot = ra.extract_status_snapshot(reply)
    clean, raw = ra.parse_state_block(reply)
    ra.writeback(deps, repo_id="r1", card_name="卡", raw_deltas=raw, turn=50,
                 snapshot=snapshot)
    st = character_state.load_state(str(tmp_path), "r1", "卡")
    out = ra.reconcile_reply_status(clean, st)
    assert st.数值["妲千沁·好感度"].value == 3.0     # 字段不存在 ⇒ 由 set 播种（add 已被闸拒收）
    assert "妲千沁=3/清冷" in out                   # 正文换成引擎真值
    assert "妲千沁=10" not in out
    assert "状态更新" not in out                    # 搭车块已剥，不进正文

    assert "52" not in st.快照.text


# ── 阶段 D：插画门控 ──

def test_插画跨档触发出图(tmp_path):
    captured = {}

    def fake_renderer(req: SceneRequest) -> str:
        captured["prompt"] = req.prompt
        return "https://img/scene.png"

    deps = _deps(tmp_path, renderer=fake_renderer)
    # 好感度从 -5 升到 5，跨过对称档位边界 0（厌恶→好感）
    out = ra.maybe_illustrate(
        deps, paragraph="她俯身", appearance="银发红瞳", wardrobe="舞裙", locale="大厅",
        actors=["A"], before=-5, after=5, turn=3, cadence=0, explicit=False, lost=False)
    assert out is not None and out["url"] == "https://img/scene.png"
    assert "跨档" in out["reason"] and "银发红瞳" in captured["prompt"]


def test_无renderer不出图(tmp_path):
    deps = _deps(tmp_path, renderer=None)
    out = ra.maybe_illustrate(
        deps, paragraph="p", appearance="a", wardrobe="", locale="", actors=[],
        before=19, after=25, turn=1, cadence=0, explicit=True, lost=True)
    assert out is None


def test_触发不命中返回None(tmp_path):
    deps = _deps(tmp_path, renderer=lambda r: "x")
    # 同档内、无失控、无显式、cadence=0 → 不触发
    out = ra.maybe_illustrate(
        deps, paragraph="p", appearance="a", wardrobe="", locale="", actors=[],
        before=21, after=30, turn=2, cadence=0, explicit=False, lost=False)
    assert out is None


def test_出图失败吞掉不阻断(tmp_path):
    def boom(req):
        raise RuntimeError("comfy down")

    deps = _deps(tmp_path, renderer=boom)
    out = ra.maybe_illustrate(
        deps, paragraph="她俯身", appearance="银发", wardrobe="", locale="", actors=[],
        before=19, after=25, turn=1, cadence=0, explicit=False, lost=False)
    assert out is None


# ── ③ RAG 辅助：机械召回 + 条目维护（门控，全假件） ──

def test_机械召回合并纪要与RAG候选且不调LLM(tmp_path, monkeypatch):
    monkeypatch.setattr(ra.narrative_store, "recall", lambda *a, **k: [])
    monkeypatch.setattr(ra.narrative_memory, "render_recall", lambda hits: "往事纪要候选")
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("召回不应调 LLM")),
        rng=random.Random(0), state_base=str(tmp_path),
    )
    out = ra.recall_chronicle(deps, repo_id="r1", query="她在哪", rag_text="知识库候选")
    assert "往事纪要候选" in out
    assert "知识库候选" in out


def test_维护gate关不写库(tmp_path):
    written = []
    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0),
                         state_base=str(tmp_path), curator_gate=0.0,
                         index_fn=lambda t, ti: written.append((t, ti)))
    assert ra.maybe_curate(deps, window_text="剧情", chat_base="b", chat_key="k", chat_model="m") == 0
    assert written == []


def test_维护gate开只增写库(tmp_path):
    written = []
    reply = '[{"op":"add","title":"新地点","text":"教会地下有密室"},' \
            '{"op":"update","title":"旧","text":"应被跳过"}]'
    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: reply, rng=random.Random(0),
                         state_base=str(tmp_path), curator_gate=1.0,
                         index_fn=lambda t, ti: written.append((t, ti)))
    n = ra.maybe_curate(deps, window_text="剧情", chat_base="b", chat_key="k", chat_model="m")
    assert n == 1  # 只增不改：update 被跳过
    assert written == [("教会地下有密室", "新地点")]


def test_curator每4轮跑一次未到轮次直接跳过(tmp_path):
    """2026-09-04 成本杠杆 L3-A：默认 curator_cadence 3→4，知识抽取按 4 轮节流。"""
    calls = []
    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: calls.append(1) or "[]",
                         rng=random.Random(0), state_base=str(tmp_path),
                         curator_gate=1.0, index_fn=lambda t, ti: None)
    # turn=2（未到节奏）→ 跳过；turn=5（(5-1)%4==0）→ 执行
    assert ra.maybe_curate(deps, window_text="剧情", chat_base="b", chat_key="k",
                           chat_model="m", turn=2) == 0
    assert ra.maybe_curate(deps, window_text="剧情", chat_base="b", chat_key="k",
                           chat_model="m", turn=5) == 0  # 空产出但调用了
    assert len(calls) == 1
    # turn 缺省（0）保持旧行为，兼容旧调用方
    assert ra.maybe_curate(deps, window_text="剧情", chat_base="b", chat_key="k",
                           chat_model="m") == 0
    assert len(calls) == 2


def test_维护无index_fn不写(tmp_path):
    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: '[{"op":"add","text":"x"}]',
                         rng=random.Random(0), state_base=str(tmp_path),
                         curator_gate=1.0, index_fn=None)
    assert ra.maybe_curate(deps, window_text="剧情", chat_base="b", chat_key="k", chat_model="m") == 0


def test_维护可在无RAG时受控修改当前仓库世界书(tmp_path):
    applied = []
    reply = '[{"op":"worldbook_update","index":0,"text":"新设定","evidence":"剧情已确认"}]'
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: reply, rng=random.Random(0), state_base=str(tmp_path),
        curator_gate=1.0, index_fn=None,
        worldbook_context='[{"index":0,"content":"旧设定"}]',
        worldbook_fn=lambda ops: applied.extend(ops) or len(ops),
    )
    assert ra.maybe_curate(
        deps, window_text="剧情", chat_base="b", chat_key="k", chat_model="m",
    ) == 1
    assert applied == [
        {"op": "worldbook_update", "index": 0, "text": "新设定", "evidence": "剧情已确认"},
    ]

def test_思考内幻影协议标签跨界匹配不吞正文():
    """2026-08-31 实锤复刻：think 里复述协议清单产生幻影 <状态更新>，懒匹配从 think 内
    一路吃到真块闭合 (3390,11977)，旧「完全包含」判断放行跨界匹配，正文随整段被剥掉，
    气泡「正文被思考过程覆盖」。交叠判断必须排除跨界匹配、保住正文与真块解析。"""
    reply = (
        "<think>灰魂吐槽：推演。格式确认：\n- <status> 块\n- <roll> 块\n"
        "- <content> 正文\n- <状态更新> 块\n骰点信息：[PLAYER] 凌渊</think>\n"
        "<content>她踏前一步，指尖挑起他的下颌。正文核心段落，必须完整保留。</content>\n"
        '<状态更新>[{"field":"数值/好感度","op":"add","value":5,"evidence":"本轮"}]</状态更新>'
    )
    clean, raw = ra.parse_state_block(reply)

    assert "正文核心段落，必须完整保留。" in clean
    assert "格式确认" in clean  # think 前缀保留在正文前（前端正则折叠为思考过程）
    assert raw == [{"field": "数值/好感度", "op": "add", "value": 5, "evidence": "本轮"}]


def test_真状态块未闭合且有think幻影时保正文丢残块():
    """真块只开不闭（模型常漏闭标签）+ think 幻影并存：正文保留、残块剥离、不抛错。"""
    reply = (
        "<think>推演。输出协议：- <content> 正文 - <状态更新> 块</think>\n"
        "<content>正文段落，不能丢。</content>\n"
        '<状态更新>[{"field":"数值/好感度","op":"add","value":3,"evidence":"本轮"}]'
    )
    clean, raw = ra.parse_state_block(reply)

    assert "正文段落，不能丢。" in clean
    assert "[{\"field\"" not in clean  # 未闭合残块 JSON 从正文剥掉（think 内幻影引述保留）
    # 缺闭标签仍解析（对齐既有契约 test_状态更新缺闭标签仍解析并从正文剥离）
    assert raw == [{"field": "数值/好感度", "op": "add", "value": 3, "evidence": "本轮"}]


def test_split_think_prefix拆出前缀保留正文():
    head, body = ra.split_think_prefix("<think>A</think>\n<content>B</content>")
    assert head == "<think>A</think>"
    assert body == "\n<content>B</content>"
    assert ra.split_think_prefix("无think直接正文") == ("", "无think直接正文")
    assert ra.split_think_prefix("") == ("", "")


def test_strip_orphan_think_close只删孤立闭标签():
    """孤立 </think>（漏写开标签）剥除；成对块与未闭合开标签+后置闭标签不动。"""
    # 成对块原样保留
    paired = "<think>A</think>正文"
    assert ra.strip_orphan_think_close(paired) == paired
    # 孤立闭标签剥除，内容保留
    assert ra.strip_orphan_think_close("正文A</think>正文B") == "正文A正文B"
    # 未闭合开标签与其后的闭标签视为配对（截断路径另有处理），不动
    unclosed = "<think>A未闭合</think>正文"
    assert ra.strip_orphan_think_close(unclosed) == unclosed
    # 多余闭标签只删孤立的那个
    extra = "<think>A</think>正文</think>尾巴"
    assert ra.strip_orphan_think_close(extra) == "<think>A</think>正文尾巴"


def test_repair_orphan_think_close拆出规划注释():
    """闭标签前紧邻的全角括号规划注释拆出（补包 think 由调用方负责）。"""
    note = "（正文到此，继续往后续写一点：凌渊的应对，以及本轮的收束和下一轮的钩子）"
    note_out, joined = ra.repair_orphan_think_close("正文A\n\n" + note + "</think>正文B")
    assert note_out == note
    assert joined == "正文A\n\n正文B"
    # 无规划动词的括注不认（防误伤正文括注），只剥闭标签
    note_out2, joined2 = ra.repair_orphan_think_close("正文A\n\n（她轻轻笑了。）</think>正文B")
    assert note_out2 == "" and joined2 == "正文A\n\n（她轻轻笑了。）\n\n正文B"
    # 无孤立闭标签 → None
    assert ra.repair_orphan_think_close("正文A（括注）正文B") is None


# ── 第 58 刀：模型偶发把状态块写成英文变体 <status_update> —— 两种拼法都要剥＋解析
# （2026-09-24 用户实锤：原始 JSON 块直接留在对话里给用户看、且没走主写回）


def test_状态更新英文变体标签同样被剥离并解析():
    reply = (
        "正文第一段。\n\n<status_update>"
        '[{"field":"数值/好感度","op":"add","value":5,"evidence":"本轮证据"}]'
        "</status_update>"
    )
    clean, deltas = ra.parse_state_block(reply)

    assert "status_update" not in clean
    assert "正文第一段。" in clean
    assert deltas and deltas[0]["field"] == "数值/好感度"
    assert deltas[0]["value"] == 5


def test_状态更新中文标签解析不受影响():
    reply = (
        "正文。\n\n<状态更新>"
        '[{"field":"数值/好感度","op":"add","value":3,"evidence":"e"}]'
        "</状态更新>"
    )
    clean, deltas = ra.parse_state_block(reply)

    assert "状态更新" not in clean
    assert deltas and deltas[0]["value"] == 3
