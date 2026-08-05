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


# ── 阶段 A：门控 + 仲裁 ──

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
    directive = ra.narrative_directive(verdicts, {})

    assert verdicts[0].goal == "把有价值的孩子纳入长期掌控"
    assert verdicts[0].intent == "留下可被幽影商会识别的徽章作为后手"
    assert "把有价值的孩子纳入长期掌控" in directive
    assert "留下可被幽影商会识别的徽章作为后手" in directive


def test_仲裁转叙述指令与失控判定():
    won = agency.Verdict("A", agency.OUTCOME_ACCEPT, agency.DEGREE_FULL, 5, 80, "得手")
    miss = agency.Verdict("B", agency.OUTCOME_REJECT, agency.DEGREE_NONE, 90, 80, "未遂")
    directive = ra.narrative_directive([won, miss], {"A": "下药"})
    assert "A：下药" in directive and "B" not in directive
    assert ra.agency_lost([won, miss]) is True
    assert ra.agency_lost([miss]) is False


def test_无得手时指令为空():
    miss = agency.Verdict("B", agency.OUTCOME_REJECT, agency.DEGREE_NONE, 90, 80, "未遂")
    assert ra.narrative_directive([miss], {}) == ""


def test_已尝试但失败的自主行动也必须进入叙事():
    miss = agency.Verdict(
        "塞西莉亚", agency.OUTCOME_REJECT, agency.DEGREE_NONE, 90, 80, "掷骰失败",
        intent="暗中留下追踪印记", goal="保持对主角的长期掌控",
    )

    directive = ra.narrative_directive([miss], {})

    assert "暗中留下追踪印记" in directive
    assert "失败" in directive


# ── 阶段 C：写回 ──

def test_写回累加好感度并落盘(tmp_path):
    deps = _deps(tmp_path)
    raw = [{"field": "数值/好感度", "op": "add", "value": 5, "evidence": "救援"}]
    before, after = ra.writeback(deps, repo_id="r1", card_name="卡", raw_deltas=raw, turn=1)
    assert before is None and after == 5.0
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
