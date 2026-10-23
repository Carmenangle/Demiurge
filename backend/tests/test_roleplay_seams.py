"""T1.3 特征测试：`roleplay_node`（397 行）函数级拆解的行为锚 + 源码形态锁。

拆解方法论（真源 `docs/memory/agent-graph-seam-plan-2026-09-16.md` §4.3）：
- **等价矩阵**：把改造前的内联写法原样抄成 `_legacy_*` 参照实现，断言新抽出的纯判定/近纯
  接缝在输入网格上与其**逐点相等** ⇒「零行为变化」可证伪，不靠读代码相信。
- **纯搬移锁**：抽出的接缝体必须**逐字包含**改造前的语句（防「搬移」被悄悄改成「重写」）。
  这一道专治等价矩阵覆盖不到的**带副作用接缝**（它们要 mock 一堆协作者才谈得上行为网格）。
- **源码形态锁**：行为网**抓不到分层/形态回退**（把接缝内联回去照样全绿）⇒ 另加三条：
  「接缝齐备且同属主」+「主函数已降到目标行数」+「每个接缝自主函数**可达**」。

⚠ 两道锁**分工不重叠**（T1.2 教训：一变异必须只红一道）：
- 纯接缝（`_rp_is_first_story_reply` / `_rp_seed_dynamic_parts` / `_rp_substitute_macros` /
  `_rp_prefix_output_structure` / `_rp_append_static_blocks` / `_rp_build_tail_messages`）
  只进**等价矩阵**，不进纯搬移锁；
- 带副作用接缝只进**纯搬移锁**，不进等价矩阵（否则同一变异两道全红、丢掉诊断力）。

⚠ `_legacy_*` 是本文件对改造前写法的**逐字抄录**（2026-09-16，自 `roleplay_node` 内联段），
不得顺手「优化」——它是等价性的唯一参照。

⚠ 就地覆写清查（T1.1 教训）结论：本函数被就地覆写、且覆写后仍被下游读取的局部变量有三处，
拆解后**都已写回下游读的同一个键 / 同一个返回位**：
- `text`：主函数内被 `_apply_regex(..., Placement.USER_INPUT, ...)` 覆写后仍被世界书组装、
  召回、directive、（阶段 B）、`messages` 多处读 ⇒ 该覆写**留在主函数**，接缝只吃覆写后的值。
- `wb`：`_rp_prepare_worldbook` 内被世界信息正则覆写 ⇒ 以**返回值**回传（下游用于
  `_agency_propose` / `_resolve_preset` / 内置分支拼接）。
- `wb_recall` / `wb_dynamic`：覆写后**写回 ctx**（`_worldbook_recall_text` /
  `_worldbook_dynamic_text`）；`wb_dynamic` 在 `_rp_seed_dynamic_parts` 里**重新从 ctx 读**
  ⇒ 若漏写回，动态段会静默丢失（T1.1 反例：`request_prompt` 未写回 ⇒ prompt_chars 44 vs 0）。
"""

import inspect
import itertools

from app.services import agent_graph as ag
from app.services import agent_graph_roleplay as agr  # roleplay 域新属主（2026-10-23 方案 B 再拆）


# ---------------------------------------------------------------------------
# legacy* 参照实现：改造前内联写法（逐字抄自 roleplay_node 改造前 L1319-1605）
# ---------------------------------------------------------------------------
def _legacy_is_first_story_reply(ctx):
    """逐字抄自 roleplay_node 改造前 L1319-1322。"""
    return not any(
        item.get("role") == "user" and str(item.get("content") or "").strip()
        for item in (ctx.get("history") or [])
    )


def _legacy_seed_dynamic_parts(ctx, wb_recall, dynamic_parts):
    """逐字抄自 roleplay_node 改造前 L1397-1402。"""
    if wb_recall:
        dynamic_parts.append(wb_recall)
    # 世界书动态段（动态区+中途新锚点）：同样只落尾部动态块，理由同上。
    wb_dynamic = str(ctx.get("_worldbook_dynamic_text") or "")
    if wb_dynamic:
        dynamic_parts.append(wb_dynamic)


def _legacy_append_static_blocks(ctx, base, chains_head):
    """逐字抄自 roleplay_node 改造前 L1404-1409。"""
    if chains_head:
        base += "\n\n" + "\n\n".join(chains_head)
    roll_rule = ag._builtin(ctx, "roleplay", "rollInstruction", ag.builtin_agents.ROLL_INSTRUCTION)
    if roll_rule and roll_rule.strip():
        base += roll_rule
    return base


def _legacy_substitute_macros(ctx, base, dynamic_parts):
    """逐字抄自 roleplay_node 改造前 L1537-1545。"""
    from app.services import preset_store as _ps2
    _macros = {
        "char_name": (ctx.get("card_name") or "").strip(),
        "user_name": (ctx.get("user_name") or "").strip(),
    }
    base = _ps2.substitute_macros(base, _macros)
    dynamic_text = _ps2.substitute_macros(
        "\n\n".join(part for part in dynamic_parts if part.strip()), _macros,
    )
    return base, dynamic_text


def _legacy_prefix_output_structure(system):
    """逐字抄自 roleplay_node 改造前 L1552-1560。"""
    system = (
        "【输出结构（每轮固定，顺序不可换）】\n"
        "第一步 <think>完整推演思考</think>——按预设思考框架展开推演；\n"
        "第二步 <status>状态栏</status>——延续上轮格式逐字段更新；\n"
        "第三步 <content>正文</content>——按预设 <length> 字数要求足量输出；"
        "<roll>/<encounter>/<fate> 等事件块按剧情需要穿插在正文中间。\n"
        "所有标签必须成对完整闭合，各类块只输出一次，禁止在思考里预写任何标签草稿。\n\n"
        + system
    )
    return system


def _legacy_build_tail_messages(ctx, dynamic_text, chains_tail):
    """逐字抄自 roleplay_node 改造前 L1563-1605。"""
    tail_msgs: list[dict] = []
    if dynamic_text:
        tail_msgs.append({"role": "system", "content": dynamic_text})
    # 尾部思维链作独立 system 消息落在历史之后、本轮 user 之前 → 离生成点最近，遵守最严
    tail_msgs.extend({"role": "system", "content": c} for c in chains_tail)
    tail_msgs.append({
        "role": "system",
        "content": (
            "[输出纪律] 严格按 system 开头的【输出结构】执行：①<think>完整推演思考</think>"
            "——按预设思考框架展开推演，写成连贯的多段推演，禁止写成一句话摘要糊弄；\n"
            "②<status>状态栏</status>——在思考之后、正文之前，延续上轮格式逐字段更新；\n"
            "③正文——按预设 <length> 的字数区间足量交付，"
            "不因思考延长而缩水正文；<roll>/<encounter>/<fate> 等事件块穿插正文中间。\n"
            "思考预算原则：思考的每个字都按输出计费——总篇幅**不超过正文的一半**。"
            "推演的价值在决策质量不在铺陈，把输出预算留给正文、状态栏与事件块。\n"
            "硬禁令：只输出剧情本体。禁止在任何输出区写面向系统的说明文字——包括但不限于"
            "质量检查、字数统计/字数要求、对状态栏的更新说明、【系统提示】/【系统说明】等"
            "标注块。这类文字不是剧情，一个字都不许出现。"
        ),
    })
    if ctx.get("comfy_audio"):
        from app.services import audio_dialogue_extract
        tail_msgs.append({
            "role": "system",
            "content": audio_dialogue_extract.build_inline_audio_instruction(),
        })
    return tail_msgs


# ---------------------------------------------------------------------------
# 等价矩阵
# ---------------------------------------------------------------------------
def _probe(fn, *args):
    """在退化输入下把「返回值」或「异常类型名」规格化：两侧必须**同病同症**。"""
    try:
        return ("ok", fn(*args))
    except Exception as exc:  # noqa: BLE001
        return ("exc", type(exc).__name__)


def test_is_first_story_reply等价矩阵():
    """「本会话还没有非空用户发言」的等价性（None/空串/纯空白/角色不符/非 dict 全覆盖）。"""
    histories = (
        None, [], [{"role": "assistant", "content": "助手先开口"}],
        [{"role": "user", "content": "第一句"}],
        [{"role": "user", "content": ""}],
        [{"role": "user", "content": "   \n  "}],
        [{"role": "user", "content": None}],
        [{"role": "user"}],
        [{"role": "system", "content": "系统"}],
        ["不是 dict"],
        [{"role": "assistant", "content": "a"}, {"role": "user", "content": "  "}],
        [{"role": "assistant", "content": "a"}, {"role": "user", "content": "b"}],
        [None],
    )
    checked = 0
    hits = set()
    for hist in histories:
        ctx = {} if hist is None else {"history": hist}
        new = _probe(ag._rp_is_first_story_reply, ctx)
        old = _probe(_legacy_is_first_story_reply, ctx)
        assert new == old, (hist, new, old)
        if new[0] == "ok":
            hits.add(new[1])
        checked += 1
    assert checked == len(histories)
    # 真/假两侧都必须被踩到，否则等价矩阵是空转
    assert hits == {True, False}, hits


def test_seed_dynamic_parts等价矩阵():
    """两个世界书段「非空才追加」的等价性（含空串 / 纯空白 / ctx 缺键）。"""
    wb_recalls = ("", "   ", "召回段", None)
    dyn_values = (None, "", "   ", "动态段")
    checked = 0
    for wb_recall, dyn in itertools.product(wb_recalls, dyn_values):
        ctx = {} if dyn is None else {"_worldbook_dynamic_text": dyn}
        got, ref = [], []
        ag._rp_seed_dynamic_parts(ctx, wb_recall, got)
        _legacy_seed_dynamic_parts(ctx, wb_recall, ref)
        assert got == ref, (wb_recall, dyn)
        checked += 1
    assert checked == len(wb_recalls) * len(dyn_values)
    # 全空 → 一个都不追加；两段都给 → 顺序必须是 [召回段, 动态段]
    empty = []
    _legacy_seed_dynamic_parts({}, "", empty)
    assert empty == []
    both = []
    _legacy_seed_dynamic_parts({"_worldbook_dynamic_text": "D"}, "R", both)
    assert both == ["R", "D"], both


def test_append_static_blocks等价矩阵(monkeypatch):
    """静态块追加的等价性（骰点规则的「空 / 纯空白 / 有内容」三分支）。"""
    chains = ([], ["头1"], ["头1", "头2"])
    rules = ("", "   ", "ROLL-INSTRUCTION")
    seen = set()
    checked = 0
    for ch, rule in itertools.product(chains, rules):
        # 2026-10-23 拆域后双绑定：新接缝在 agr 查 _builtin，legacy 参照逐字走 ag 门面
        # ⇒ 两侧都必须打，否则等价矩阵拿「真 _builtin」对「假 _builtin」必红。
        monkeypatch.setattr(agr, "_builtin", lambda *a, _r=rule, **k: _r)
        monkeypatch.setattr(ag, "_builtin", lambda *a, _r=rule, **k: _r)
        ctx = {}
        assert (ag._rp_append_static_blocks(ctx, "BASE", ch)
                == _legacy_append_static_blocks(ctx, "BASE", ch)), (ch, rule)
        seen.add(bool(rule.strip()))
        checked += 1
    assert checked == len(chains) * len(rules)
    assert seen == {True, False}, seen


def test_substitute_macros等价矩阵():
    """两段宏替换的等价性（缺省回退 / 空白裁剪 / 动态块过滤空段）。"""
    names = (None, "", "   ", "沈糯", " 沈糯 ")
    bases = ("", "对{{user}}态度", "{{char}} 与 {{user}}", "无宏")
    dyns = ([], [""], ["   "], ["有 {{user}}"], ["", "动态 {{char}}"], ["  ", "x"])
    checked = 0
    for cn, un, base, dyn in itertools.product(names, names, bases, dyns):
        ctx = {}
        if cn is not None:
            ctx["card_name"] = cn
        if un is not None:
            ctx["user_name"] = un
        assert (ag._rp_substitute_macros(ctx, base, list(dyn))
                == _legacy_substitute_macros(ctx, base, list(dyn))), (ctx, base, dyn)
        checked += 1
    assert checked == len(names) ** 2 * len(bases) * len(dyns)
    # 空动态块拼出来必须是空串（不能是 "None" 或残留空白）
    assert _legacy_substitute_macros({}, "b", ["  "]) == ("b", "")


def test_prefix_output_structure等价矩阵():
    """【输出结构】前置的等价性（任意 system 都以模板为前缀）。"""
    systems = ("", "BASE", "多行\nBASE", "【输出结构（每轮固定，顺序不可换）】已含")
    checked = 0
    for system in systems:
        got = ag._rp_prefix_output_structure(system)
        assert got == _legacy_prefix_output_structure(system), system
        assert got.endswith(system), system          # 原文必须原样落在尾部
        assert got.startswith("【输出结构（每轮固定，顺序不可换）】"), system
        checked += 1
    assert checked == len(systems)


def test_build_tail_messages等价矩阵():
    """尾部 system 消息组装的等价性（动态块有无 × 思维链条数 × comfy_audio 开关）。"""
    dyns = ("", "动态块")
    chains = ([], ["T1"], ["T1", "T2"])
    audios = (False, True)
    checked = 0
    for dyn, ch, audio in itertools.product(dyns, chains, audios):
        ctx = {} if not audio else {"comfy_audio": True}
        got = ag._rp_build_tail_messages(ctx, dyn, list(ch))
        ref = _legacy_build_tail_messages(ctx, dyn, list(ch))
        assert got == ref, (dyn, ch, audio)
        checked += 1
    assert checked == len(dyns) * len(chains) * len(audios)
    # 顺序契约：动态块 → 思维链 → 输出纪律（→ 音频指令）
    ref = _legacy_build_tail_messages({}, "D", ["T1"])
    assert [m["role"] for m in ref] == ["system", "system", "system"]
    assert ref[0]["content"] == "D" and ref[1]["content"] == "T1"
    assert ref[2]["content"].startswith("[输出纪律]")


# ---------------------------------------------------------------------------
# 纯搬移锁：带副作用接缝必须逐字保留改造前语句（子串匹配，可跨行）
# ---------------------------------------------------------------------------
_VERBATIM_MOVED = {
    "_rp_prepare_worldbook": (
        "wb = _resolve_worldbook(ctx, text)",
        'wb = _apply_regex(ctx, wb, Placement.WORLD_INFO, is_prompt=True, depth=0)',
        'wb_recall = str(ctx.get("_worldbook_recall_text") or "")',
        'wb_recall = _apply_regex(ctx, wb_recall, Placement.WORLD_INFO, is_prompt=True, depth=0)',
        'ctx["_worldbook_recall_text"] = wb_recall',
        'wb_dynamic = str(ctx.get("_worldbook_dynamic_text") or "")',
        'ctx["_worldbook_dynamic_text"] = wb_dynamic',
        "return wb, wb_recall",
    ),
    "_rp_emit_worldbook_resolved": (
        '"worldbook.resolved", injected=bool(wb), content=wb or "",',
        "recall_chars=len(wb_recall),",
    ),
    "_rp_last_story_round": (
        "story_round = story_history.last_story_round(ctx.get(\"history\") or [])",
        "injected_chars=sum(len(m[\"content\"]) for m in story_round))",
        "return story_round",
    ),
    "_rp_resolve_persona_block": (
        'ctx["persona"] = _resolve_personas(',
        "ctx, text, opening_only=first_story_reply,",
        "fallback_query=_recent_character_context(ctx),",
        'injected=bool(ctx.get("persona")),',
    ),
    "_rp_load_style_config": (
        'ctx["_style_config"] = prose_style.load_config()',
        '"prose_style.config", status="error", error=str(exc))',
    ),
    "_rp_build_prompt_base": (
        'rp_temp = _builtin(ctx, "roleplay", "temperature", builtin_agents.ROLEPLAY_TEMPERATURE)',
        "temp = preset_temp if preset_temp is not None else _temperature(ctx, rp_temp)",
        'base = rp_base + (f"\\n\\n{persona}" if persona else "")',
        "return base, temp, dialogue",
    ),
    "_rp_recall_memory": (
        'repo_id = ctx.get("repo_id") or ctx.get("thread_id") or ""',
        "retrieval_query = (agent_context.history_text(ctx)[-600:] + text)",
        "rag_text = _rag_recall_text(ctx, repo_id, retrieval_query)",
        "table_recall = _table_recall_text(ctx, repo_id, retrieval_query)",
        "recall = roleplay_agency.recall_chronicle(",
        "return repo_id, table_recall, recall",
    ),
    "_rp_compile_continuity": (
        "active_facts = temporal_fact_store.as_of(",
        'ctx["_continuity_facts"] = active_facts',
        "active_beliefs = character_belief.active(",
        'ctx["_continuity_beliefs"] = active_beliefs',
        "compiled = continuity_compiler.compile_context([",
        "], token_budget=900)",
        "【相关数据表行（独立配额）】",
    ),
    "_rp_append_state_and_style": (
        "base += roleplay_agency.state_instruction()",
        '_style_cfg = ctx.get("_style_config") or {}',
        "_style_seg = _prose_style.style_prompt_segment(_style_cfg)",
        "return base",
    ),
    "_rp_prepare_visual_and_tables": (
        'if getattr(deps, "renderer", None) is not None or ctx.get("comfy_illustrate"):',
        "visual_query = _illustration_visual_query(ctx, text)",
        'ctx["_illustration_visual_profiles"] = visual_profiles',
        '_tables = table_store.load(ctx.get("output_dir") or "", repo_id)',
        "turn_tables = table_store.tables_for_read(_tables)",
        "dynamic_parts.append(table_update.table_context(turn_tables))",
    ),
}


def test_搬移语句逐字保留():
    """带副作用接缝体必须**逐字包含**改造前语句（防「搬移」被改成「重写」）。"""
    for fn_name, fragments in _VERBATIM_MOVED.items():
        src = inspect.getsource(getattr(ag, fn_name))
        missing = [f for f in fragments if f not in src]
        assert not missing, f"{fn_name} 未见逐字搬移语句（疑似被重写）：{missing}"


# ---------------------------------------------------------------------------
# 源码形态锁
# ---------------------------------------------------------------------------
_SEAMS = (
    # 前置装配（世界书 / 事件 / 首轮判定 / 历史 / 人设 / 文风）
    "_rp_prepare_worldbook",
    "_rp_emit_worldbook_resolved",
    "_rp_is_first_story_reply",
    "_rp_last_story_round",
    "_rp_resolve_persona_block",
    "_rp_load_style_config",
    # 提示词 base 与静态块
    "_rp_build_prompt_base",
    "_rp_seed_dynamic_parts",
    "_rp_append_static_blocks",
    # 收口：宏替换 / 输出结构前置 / 尾部消息
    "_rp_substitute_macros",
    "_rp_prefix_output_structure",
    "_rp_user_priority_block",
    "_rp_build_tail_messages",
    # 阶段 B 编排器 + 四个内聚子接缝
    "_rp_inject_agency_context",
    "_rp_recall_memory",
    "_rp_compile_continuity",
    "_rp_append_state_and_style",
    "_rp_prepare_visual_and_tables",
)

_TARGET_LINES = 150


def _calls(src: str, name: str) -> bool:
    """src 里是否出现对 name 的调用（名称后必须**紧跟** `(`，防 `f (` 这类等价改字漏网）。"""
    import re
    return re.search(rf"(?<![\w]){re.escape(name)}\(", src) is not None


def test_拆解接缝齐备且同属主():
    for name in _SEAMS:
        fn = getattr(ag, name, None)
        assert callable(fn), f"接缝缺失：{name}"
        assert inspect.getsourcefile(fn) == agr.__file__, f"{name} 不在 agent_graph_roleplay 属主内"


def test_roleplay_node主函数已降到目标行数():
    """粗闸：主函数不得反向膨胀（内联回退 / 新增巨量逻辑都会先撞到这条）。"""
    src = inspect.getsource(ag.roleplay_node)
    body_lines = src.count("\n") + 1
    assert body_lines <= _TARGET_LINES, (
        f"roleplay_node 仍为 {body_lines} 行（目标 ≤{_TARGET_LINES}）——拆解疑似回退"
    )


def test_主函数由接缝组装_全部接缝自roleplay_node可达():
    """调用传递闭包：从 `roleplay_node` 出发，每个接缝都必须可达。

    一条断言同时锁住三件事（比「逐个列名 + 叶子豁免」更严，且不误伤叶子接缝）：
    ① **组装**——主函数必须真的调用接缝，否则一个都不可达；
    ② **无内联回退**——某段被抄回主函数后，对应接缝不再被任何人调用 ⇒ 不可达；
    ③ **无死接缝**——接缝抽出来却没人用 ⇒ 不可达。
    ⚠ `_rp_recall_memory` 这类**叶子接缝**由 `_rp_inject_agency_context` 调用（非主函数直调），
    本锁天然覆盖，无需另设豁免名单。
    """
    seen = set()
    frontier = ["roleplay_node"]
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        fn = getattr(ag, cur, None)
        if fn is None:
            continue
        csrc = inspect.getsource(fn)
        for n in _SEAMS:
            if n not in seen and _calls(csrc, n):
                frontier.append(n)
    unreachable = [n for n in _SEAMS if n not in seen]
    assert not unreachable, f"接缝从主函数不可达（疑似内联回退 / 死接缝）：{unreachable}"


def test_失败收尾_已收可见正文按中断语义落库(monkeypatch):
    """2026-10-20（turn f77e68b1）：首发断流后失败轮次不写快照 ⇒ 下一轮
    `_resume_interrupted_messages` 认不到「（已打断）」⇒ 用户再点生成仍从头再来。
    失败收尾必须把已收的**可见正文**补落一次；think 不得混进正文。
    """
    from app.services import agent_stream, generation_store
    from app.services import llm as _llm

    calls: list = []
    monkeypatch.setattr(
        generation_store, "persist_text",
        lambda *a, **k: calls.append({"args": a, "kwargs": k}),
    )
    body = "烛火把两个人的影子拉长又揉碎。" * 20  # 15×20=300 字，过 200 字门槛
    exc = _llm.StreamInterrupted(
        "调用对话模型失败：The read operation timed out",
        "<think>内部推演不该落正文</think><content>" + body,
    )
    out = agent_stream._roleplay_failure_result(
        {"thread_id": "t1", "message_id": "m1"}, ["trace"], True, exc)

    # 2026-10-20 用户实锤（turn 14:08，首轮已流式 4867 字正文）：失败时若把
    # 「扮演失败：…」当 result_text 下发，`agent_graph` 的 `if upd.get("result_text")`
    # 会发 replace 事件，**把用户已付费的正文整段覆盖掉**。
    # ⇒ 已流式过就空串（不发 replace），错误只进 trace；与 RoleplayInterrupted 分支同口径。
    assert out["result_text"] == ""
    assert out["_streamed_result"] is True
    assert any("扮演失败" in line for line in out["trace"])
    assert len(calls) == 1
    assert calls[0]["kwargs"].get("interrupted") is True
    text = calls[0]["args"][2]
    assert text == body.strip()
    assert "内部推演" not in text  # think 不得混进正文


def test_失败收尾_短视频不落库(monkeypatch):
    """不足 200 字没有续写价值（阈值与 `_resume_interrupted_messages` 同源）。"""
    from app.services import agent_stream, generation_store
    from app.services import llm as _llm

    calls: list = []
    monkeypatch.setattr(generation_store, "persist_text",
                        lambda *a, **k: calls.append(a))
    exc = _llm.StreamInterrupted("调用对话模型失败：断流", "<content>只有半句")
    agent_stream._roleplay_failure_result(
        {"thread_id": "t1", "message_id": "m1"}, [], False, exc)
    assert calls == []
