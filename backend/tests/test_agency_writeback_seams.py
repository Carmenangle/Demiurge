"""T1.1 特征测试：`_agency_writeback`（563 行）函数级拆解的行为锚 + 源码形态锁。

拆解方法论（真源 `docs/memory/agent-graph-seam-plan-2026-09-16.md` §4）：
- **等价矩阵**：把改造前的内联判定原样抄成 `_legacy_*` 参照实现，断言新抽出的纯判定
  接缝在输入网格上与其**逐点相等** ⇒「零行为变化」可证伪，不靠读代码相信。
- **源码形态锁**：行为网**抓不到分层/形态回退**（把接缝内联回去照样全绿）⇒ 另加断言
  「主函数确实由接缝组装」+「已降到目标行数以内」。

⚠ `_legacy_*` 是本文件对改造前写法的**逐字抄录**（2026-09-16，自
`agent_agency._agency_writeback` 内联段），不得顺手"优化"——它是等价性的唯一参照。
"""

import importlib
import inspect
import itertools
import json

from app.services import agent_agency as aag


# ---------------------------------------------------------------------------
# legacy* 参照实现：改造前内联判定（逐字抄自 _agency_writeback）
# ---------------------------------------------------------------------------
def _legacy_at_climax(*, illustration_plan, visible_story, lost, scene,
                      local_scene_fallback, missing_plan_fallback,
                      first_story_reply, character_encounter) -> bool:
    return bool(illustration_plan) or (
        bool(visible_story) and (
            lost or scene in ("nsfw", "climax")
            or local_scene_fallback or missing_plan_fallback
            or first_story_reply or character_encounter
        )
    )


def _legacy_illustration_reason(*, illustrate_req, plan_retargeted, illustration_plan,
                                character_encounter, local_scene_fallback,
                                first_story_reply, missing_plan_fallback,
                                prompt_source, at_climax) -> str:
    return ("main_plan_retargeted" if plan_retargeted and illustrate_req else
            "main_plan" if illustration_plan and illustrate_req else
            "character_encounter" if character_encounter and illustrate_req else
            "local_scene_fallback" if local_scene_fallback and illustrate_req else
            "first_story_reply" if first_story_reply and illustrate_req else
            "missing_plan_fallback" if missing_plan_fallback and illustrate_req else
            prompt_source if illustrate_req else
            "scene_not_triggered" if not at_climax else "empty_prompt")


# ---------------------------------------------------------------------------
# 等价矩阵
# ---------------------------------------------------------------------------
def test_at_climax等价矩阵():
    grid = itertools.product(
        (None, {"prompt": "x"}),                 # illustration_plan
        ("", "正文"),                             # visible_story
        (False, True),                            # lost
        ("sfw", "nsfw", "climax", "dialogue"),    # scene
        (False, True),                            # local_scene_fallback
        (False, True),                            # missing_plan_fallback
        (False, True),                            # first_story_reply
        (False, True),                            # character_encounter
    )
    checked = 0
    for plan, visible, lost, scene, lsf, mpf, fsr, ce in grid:
        kw = dict(illustration_plan=plan, visible_story=visible, lost=lost, scene=scene,
                  local_scene_fallback=lsf, missing_plan_fallback=mpf,
                  first_story_reply=fsr, character_encounter=ce)
        assert aag._wb_at_climax(**kw) == _legacy_at_climax(**kw), kw
        checked += 1
    assert checked == 2 * 2 * 2 * 4 * 2 * 2 * 2 * 2


def test_illustration_reason等价矩阵():
    grid = itertools.product(
        (False, True),                      # illustrate_req 真值
        (False, True),                      # plan_retargeted
        (None, {"prompt": "x"}),            # illustration_plan
        (False, True),                      # character_encounter
        (False, True),                      # local_scene_fallback
        (False, True),                      # first_story_reply
        (False, True),                      # missing_plan_fallback
        ("", "extracted", "fallback"),      # prompt_source
        (False, True),                      # at_climax
    )
    seen = set()
    for req, retargeted, plan, ce, lsf, fsr, mpf, source, at_climax in grid:
        kw = dict(illustrate_req=req, plan_retargeted=retargeted, illustration_plan=plan,
                  character_encounter=ce, local_scene_fallback=lsf,
                  first_story_reply=fsr, missing_plan_fallback=mpf,
                  prompt_source=source, at_climax=at_climax)
        got = aag._wb_illustration_reason(**kw)
        assert got == _legacy_illustration_reason(**kw), kw
        seen.add(got)
    # 网格必须真正踩到每一条分支，否则等价矩阵是空转
    assert {"main_plan_retargeted", "main_plan", "character_encounter",
            "local_scene_fallback", "first_story_reply", "missing_plan_fallback",
            "scene_not_triggered", "empty_prompt"} <= seen


# ---------------------------------------------------------------------------
# 源码形态锁
#
# 2026-10-23 方案 B 再拆：15 个接缝按域迁出（agent_agency 2589 → 666 行），属主改指新
# 模块（改锁不删锁，且比原单文件判据更严——逐个接缝钉属主）：
#   阶段 A/B（场景状态域）→ agency_scene_state；阶段 C（comfy 编译域）→ agency_scene_comfy。
# 接缝本体仍可经 agent_agency 门面访问（aag._wb_*），但 getsourcefile 必须落在新属主内。
# ---------------------------------------------------------------------------
_SEAMS = {
    # 阶段 A：think 前缀拆分 / 状态写回 / 连续性 CI
    "_wb_split_media_blocks": "agency_scene_state",
    "_wb_apply_state": "agency_scene_state",
    "_wb_run_narrative_ci": "agency_scene_state",
    # 阶段 B：在场信号 / 配音请求 / 初选出图提示词
    #（_wb_at_climax / _wb_illustration_reason 是纯判定接缝，由 scene_signals /
    #  emit_illustration_trace 调用而非 writeback 直调 ⇒ 归等价矩阵覆盖，不进组装锁）
    "_wb_scene_signals": "agency_scene_state",
    "_wb_build_audio_request": "agency_scene_state",
    "_wb_initial_illustration_prompt": "agency_scene_state",
    # 阶段 C：comfy 七段（候选池 → 提示词/锚点 → scene_spec → Profile → 请求外壳
    #         → 首尾帧与视频 → 事件派发）
    "_wb_comfy_actor_pool": "agency_scene_comfy",
    "_wb_comfy_scene_target": "agency_scene_comfy",
    "_wb_comfy_scene_spec": "agency_scene_comfy",
    "_wb_comfy_profile": "agency_scene_comfy",
    "_wb_comfy_illustrate_env": "agency_scene_comfy",
    "_wb_frames_and_video": "agency_scene_comfy",
    "_wb_emit_illustration_trace": "agency_scene_comfy",
    # 第 57 刀 · 多批派发（2026-09-24）：为「主锚点之外」的其余锚点各编译一条出图请求
    "_wb_comfy_extra_requests": "agency_scene_comfy",
    # 第 57 刀 ③ · 第一人称「画面事实筛分」（2026-09-24）：进编译器之前按取景剔掉装不下的
    # 外貌/服装事实（`appearance`/`wardrobe` 在字段账本里是 required，剔晚了来不及）
    "_wb_first_person_frame_filter": "agency_scene_comfy",
}

_TARGET_LINES = 160


def test_拆解接缝齐备且同属主():
    for name, owner in _SEAMS.items():
        fn = getattr(aag, name, None)
        assert callable(fn), f"接缝缺失：{name}"
        owner_file = importlib.import_module(f"app.services.{owner}").__file__
        assert inspect.getsourcefile(fn) == owner_file, f"{name} 不在 {owner} 属主内"


def test_writeback主函数由接缝组装且已降到目标行数():
    src = inspect.getsource(aag._agency_writeback)
    body_lines = src.count("\n") + 1
    missing = [name for name in _SEAMS if f"{name}(" not in src]
    assert not missing, f"主函数未调用接缝（疑似被内联回去）：{missing}"
    assert body_lines <= _TARGET_LINES, (
        f"_agency_writeback 仍为 {body_lines} 行（目标 ≤{_TARGET_LINES}）——拆解疑似回退"
    )


# ---------------------------------------------------------------------------
# 优化项③ 接线锁：判据档的「本帧主体名集合」必须随 scene_spec 下传
#
# 多人契约原先只看 `actors`（= 取材窗口里被提及的所有角色），窗口一提别人就把单人帧
# 误判成多人帧。契约改看本帧主体数后，判据档给出的 `subjects` 必须真的传到 scene_spec，
# 否则「按画面主体数判」在生产链路上根本不生效（探针绿、真机红）。
# 缺省（空表）不得生成该键 ⇒ 契约退回 actors 的旧行为逐字节不变。
# ---------------------------------------------------------------------------
def _scene_spec_args(**target_over):
    target = {
        "request_prompt": "kneeling, reaching out",
        "prompt_source": "fallback",
        "requested_anchor": "她站在走廊里。",
        "plan_retargeted": False,
        "scene_narrative": "她站在走廊里。",
        "motion": 0,
        "request_actors": ["甲", "乙"],
        "request_appearance": "",
    }
    target.update(target_over)
    return (
        {"prompt_profile": "anima_tags", "appearance_source": "character_card"},
        "repo",
        {},
        "她站在走廊里。",
        {"character_encounter": False, "encounter_narrative": "",
         "wardrobe": "", "locale": "", "image_rating": "nsfw"},
        {"scene_text": "她站在走廊里。", "known": ["甲", "乙"]},
        target,
    )


def test_缺块路径把本帧主体名集合写进scene_spec():
    spec = aag._wb_comfy_scene_spec(*_scene_spec_args(frame_subjects=["甲"]))
    assert spec["frame_subjects"] == ["甲"]


def test_无本帧主体信号时scene_spec不得凭空生成该键():
    """零回归：空表／缺键都不得出现 frame_subjects，契约必须退回 actors。"""
    assert "frame_subjects" not in aag._wb_comfy_scene_spec(
        *_scene_spec_args(frame_subjects=[]),
    )
    assert "frame_subjects" not in aag._wb_comfy_scene_spec(*_scene_spec_args())

# ---------------------------------------------------------------------------
# 接线锁：正文 <status> 块必须走「引擎真源」对账（2026-09-22 用户实锤）
#
# 此前只对账「落盘快照」与「重注入快照」，用户屏幕上看到的 [好感] 行仍是模型自述
# ⇒ 同一角色正文 16→13→10 乱跳、引擎恒 13（观感＝「好感度根本没加上」）。修复点在本
# 接缝：写回后 state 已从磁盘重载（＝已对账真值），此处对账正文；本节点末尾发 replace
# 全文替换气泡，故对账正文即等于修显示。锁住接线与**顺序**，防「探针绿、真机红」。
# ---------------------------------------------------------------------------
def test_写回接缝对账正文status块():
    src = inspect.getsource(aag._wb_apply_state)
    assert "reconcile_reply_status(clean, st)" in src, (
        "_wb_apply_state 未对账正文 <status> ⇒ 用户屏幕上的 [好感] 数字会与引擎账漂移"
    )
    # 顺序锁：必须发生在 st 从磁盘重载**之后**——用旧 st 会拿不到本轮 delta 的结果
    assert src.index("load_state") < src.index("reconcile_reply_status(clean, st)"), (
        "对账早于 load_state ⇒ 拿的是写回前的旧值，等于没对账"
    )


def test_第59刀_多批选择层滤零分锚点():
    """用户定案（2026-09-25）：判据显式打 0 分＝「这个时刻不值得画」⇒ 不建槽。
    过滤落在多批选择层（`_resolve_anchor_intent_list`），不落
    `select_anchor_intents` 本体（旧单锚点形态被包装成 score=0，本体过滤会误杀）。"""
    from app.services import agent_agency

    text = "开头。A句。中间。B句。结尾。C句。"
    raw = (
        '{"anchors": ['
        '{"anchor": "A句。", "score": 70, "composition": "中景", "subjects": ["甲"], "reason": "x"}, '
        '{"anchor": "B句。", "score": 0, "composition": "特写", "subjects": ["甲"], "reason": "x"}, '
        '{"anchor": "C句。", "score": 30, "composition": "特写", "subjects": ["甲"], "reason": "x"}'
        ']}'
    )
    ctx = {"illustration_batch_count": 99}   # 不限制 ⇒ 全出（但 0 分的 B 被滤）

    picked = agent_agency._resolve_anchor_intent_list(ctx, text, raw)

    assert [x["anchor"] for x in picked] == ["A句。", "C句。"]


# ---------------------------------------------------------------------------
# 画幅一致性（2026-10-31 用户定案「图片的宽高要和视频的宽高一致」）
# video_config.size 此前硬编码 "1280x720" ⇒ 竖图（9:16）楼层的 video_prompt 逐字写着
# 「16:9」，模型按横画幅构图，首帧与成片的宽高对不上（真机实锤 da0f9576 的 [元信息] 行）。
# size 必须跟着**本锚点** scene_spec 的画幅走；缺画幅时留空，不得回落成一个假的横屏值。
# ---------------------------------------------------------------------------
def _出图外壳请求(**spec_over):
    scene_spec = {"narrative": "她站在走廊里。", "aspect_ratio": "9:16"}
    scene_spec.update(spec_over)
    return aag._wb_comfy_illustrate_env(
        {"vid_base": "https://vid.example", "vid_model": "h3-mini", "vid_proxy": ""},
        None,   # plan：无计划，只验外壳组装
        {"at_climax": True, "visible_story": True, "local_scene_fallback": False,
         "missing_plan_fallback": False, "first_story_reply": False,
         "character_encounter": False},
        {"request_prompt": "standing in corridor", "request_actors": ["甲"],
         "requested_anchor": "她站在走廊里。"},
        scene_spec,
        motion=3,
    )


def test_视频配置的画幅跟随本锚点scene_spec():
    assert _出图外壳请求(aspect_ratio="9:16")["video_config"]["size"] == "9:16"


def test_视频配置不再硬编码1280x720():
    req = _出图外壳请求(aspect_ratio="3:4")
    assert req["video_config"]["size"] == "3:4"
    assert "1280x720" not in str(req["video_config"])


def test_无画幅时size留空而不是假造横屏():
    """空串与 None 都属「这一帧没判定画幅」⇒ size 必须空着，让提示词里不出现假画幅。"""
    assert _出图外壳请求(aspect_ratio="")["video_config"]["size"] == ""
    assert _出图外壳请求(aspect_ratio=None)["video_config"]["size"] == ""


def test_纯卡时棘轮豁免名单收到卡名与用户名(tmp_path):
    """清单 #5 回归：无世界书时 `exclude` 的两条人名来源（数据表行首列／`_worldbook_character_names`）
    恒空 ⇒ 「露娜抬起头」这类正常叙述会被当「无锚点膨胀」报出来。修后＝卡名／用户名进 `exclude`
    （只进豁免名单，不进任何 prompt），且排在最前——返回列表要 `[:40]` 截断，这两条最不该被挤掉。"""
    from app.services import agency_scene_state as ass

    ctx = {"card_names": ["露娜"], "card_name": "露娜", "opening_card_name": "",
           "user_name": "阿明", "output_dir": str(tmp_path),
           "_worldbook_character_names": [], "_selected_persona_names": [], "history": []}
    _injected, _bodies, exclude, setting = ass._wb_ratchet_sources(ctx, "work")
    assert exclude[:2] == ["露娜", "阿明"]
    assert setting == []   # 没顺手改存量口径：无书时第 4 条那道闸门仍整关，缺席改由臂台账记进报告（#4）

    ctx_with_book = dict(ctx, _worldbook_character_names=["顾凝霜"])
    _i, _b, exclude2, _s = ass._wb_ratchet_sources(ctx_with_book, "work")
    assert exclude2.index("露娜") < exclude2.index("顾凝霜")

    # 本刀往 names 里追加的四条**可能为空**（无卡／无用户名）。空串一旦进 exclude，
    # `n in body` 恒真 ⇒ 每个候选词都算「有人名锚点」⇒ 整道棘轮静默全绿（比误报更坏的失败）。
    _i, _b, exclude3, _s = ass._wb_ratchet_sources(
        dict(ctx, card_names=[], card_name="", user_name=""), "work")
    assert "" not in exclude3


# ---------------------------------------------------------------------------
# 臂参与台账（清单 #3／#4）：没书所以不判 ⇒ 报告要写明「这一臂本轮没参与」
#
# 判据本体一个字没动（`narrative_ci.evaluate` 的 `for rule in world_rules:`、
# `prose_style.term_ratchet_report` 的 `if set_texts:`）。这里锁的是**观测接缝**：
# 台账必须与诊断同轮产出，并同时进 trace 与库——只落一边就会出现「界面说查过、trace 说没查」。
# ---------------------------------------------------------------------------
def _纯卡ctx(tmp_path, **extra):
    ctx = {"output_dir": str(tmp_path), "card_names": ["露娜"], "card_name": "露娜",
           "opening_card_name": "", "user_name": "阿明",
           "_worldbook_character_names": [], "_selected_persona_names": [],
           "_world_rules": [], "history": []}
    return dict(ctx, **extra)


def _快照文件(base: str, repo_id: str, payload: str) -> None:
    """按真实读侧路径落一份世界书快照（不 monkeypatch：让 `is_file()`／JSON 解析都走真代码）。"""
    from app.services import worldbook_store

    path = worldbook_store.repo_snapshot_path(base, repo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _跑一轮CI(ass, ctx, monkeypatch, *, turn: int = 3):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(ass.run_trace, "emit",
                        lambda c, event, **data: events.append((event, data)))
    ass._wb_run_narrative_ci(ctx, "她抬起头，望向远处的雪山。", turn=turn, raw=[], repo_id="work")
    rows = [d for _e, d in events if d.get("status") in ("evaluated", "unavailable")]
    assert len(rows) == 1, events
    return rows[0]


def test_纯卡剧情轮三条臂都留痕未参与(tmp_path, monkeypatch):
    from app.services import agency_scene_state as ass
    from app.services import narrative_ci

    ctx = _纯卡ctx(tmp_path)
    row = _跑一轮CI(ass, ctx, monkeypatch)
    assert row["status"] == "evaluated"        # 观测失败不许把整条 CI 拖成 unavailable
    # 两条世界书臂都缺书 ⇒ `no_worldbook`；裁定臂不看世界书，这条 ctx 没跑过
    # `_agency_propose` ⇒ 只能说「本轮没问过 World」，写成 `no_worldbook` 是把用户引去补书。
    assert {v["reason"] for v in row["arms"].values()} == {
        "no_worldbook", "world_not_consulted"}
    assert row["arms"]["world_rule"]["participated"] is False
    # trace 与库必须同源：面板（GET /ci）读的是库，只写 trace 等于界面看不见
    assert narrative_ci.load_arms(ctx["output_dir"], "work") == {
        "turn": 3, "arms": row["arms"]}


def test_有书且喂到输入时两条世界书臂都记已参与(tmp_path, monkeypatch):
    from app.services import agency_scene_state as ass
    from app.services import worldbook

    静态底座 = "药王谷的药材"
    _快照文件(str(tmp_path), "work", json.dumps(
        {"entries": [{"content": 静态底座 + worldbook.DYNAMIC_MARKER + "当日：起了雾。"}]},
        ensure_ascii=False))

    ctx = _纯卡ctx(tmp_path, _world_rules=["药王谷的药材不可带出谷外。"])
    row = _跑一轮CI(ass, ctx, monkeypatch)

    assert row["status"] == "evaluated"
    assert row["arms"]["world_rule"] == {"participated": True, "count": 1, "reason": ""}
    # 存量臂的输入＝快照里 DYNAMIC_MARKER **之前**的静态底座；动态区是叙事，不算存量。
    assert row["arms"]["term_ratchet_stock"]["participated"] is True


def test_条目只剩动态区时存量臂报的是没底座不是没书(tmp_path, monkeypatch):
    """`empty_worldbook`／`no_static_base` 不能混用：书**有**条目、条目被动态区吃光，
    面板该说的是「设定条目里没有静态底座」，而不是「世界书快照是空的」——后者会把用户
    引去补书，而书其实补了也没用（要补的是条目正文，不是条数）。"""
    from app.services import agency_scene_state as ass
    from app.services import worldbook

    _快照文件(str(tmp_path), "work", json.dumps(
        {"entries": [{"content": worldbook.DYNAMIC_MARKER + "当日：起了雾。"}]},
        ensure_ascii=False))

    ctx = _纯卡ctx(tmp_path, _world_rules=["药王谷的药材不可带出谷外。"])
    row = _跑一轮CI(ass, ctx, monkeypatch)

    assert row["arms"]["world_rule"]["participated"] is True
    assert row["arms"]["term_ratchet_stock"] == {
        "participated": False, "count": 0, "reason": "no_static_base"}


def test_快照在册但读坏时原因不能说成没有书(tmp_path, monkeypatch):
    """清单 #3/#4 的诚实底线：文件在、解析失败 ⇒ 说「读不出来」。把它报成「这个作品没有世界书」
    同样是句假话，而说错原因正是这一刀要消灭的东西。"""
    from app.services import agency_scene_state as ass

    _快照文件(str(tmp_path), "work", "{坏 JSON")
    row = _跑一轮CI(ass, _纯卡ctx(tmp_path), monkeypatch)

    assert row["status"] == "evaluated"
    # 快照读坏只影响两条世界书臂；裁定臂的 `world_not_consulted` 是另一件事，不能被带成同一句话
    assert {v["reason"] for v in row["arms"].values()} == {
        "snapshot_unreadable", "world_not_consulted"}


def test_台账观测不给写回链路新增抛出面(tmp_path, monkeypatch):
    """`_wb_arm_status` 只许在「读得到／读不到」之间选，不许把异常抛回链路：
    连 output_dir 都没有时（仓库域无从谈起）也必须降级成 `no_worldbook`，
    而不是把已经跑完的 CI 拖成 `unavailable`——那是「观测吃掉判据」的反向耦合。"""
    from app.services import agency_scene_state as ass

    row = _跑一轮CI(ass, {"output_dir": "", "history": [], "_world_rules": []}, monkeypatch)

    assert row["status"] == "evaluated"
    assert {v["reason"] for v in row["arms"].values()} == {
        "no_worldbook", "world_not_consulted"}


def test_裁定投影喂进臂台账并同时进trace与库(tmp_path, monkeypatch):
    """缺陷 4 的**接线**：`_agency_propose` 投影进 ctx 的裁定要原样抵达
    `arm_participation` 的 `agency_claims`。参数没传／传错名 ⇒ 面板上第三条臂永远
    报 `world_not_consulted`（哪怕本轮真判过）；台账只落 trace 不落库 ⇒ 界面看不见。
    spy 返回哨兵：库与 trace 拿不到同一份哨兵即红。"""
    from app.services import agency_scene_state as ass
    from app.services import narrative_ci

    投影 = [{"actor": "埃吉尔", "outcome": "reject", "degree": "none", "roll": 81, "chance": 70}]
    哨兵 = {"agency_verdict": {"participated": True, "count": 1, "reason": ""}}
    captured: dict = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return 哨兵

    monkeypatch.setattr(narrative_ci, "arm_participation", fake)
    ctx = _纯卡ctx(tmp_path, _agency_npc_claims=投影)
    row = _跑一轮CI(ass, ctx, monkeypatch)

    assert captured["agency_claims"] == 投影
    assert row["arms"] is 哨兵
    assert narrative_ci.load_arms(ctx["output_dir"], "work") == {"turn": 3, "arms": 哨兵}


def test_未遂对账吃到本轮裁定投影与在场快照(tmp_path, monkeypatch):
    """缺陷 4 第三段的**接线**：投影与状态栏快照都得真的进 `evaluate`。
    少传 `agency_claims` ⇒ 这臂整轮空跑（一条诊断也不出，面板上却看不见「没核对」）；
    少传 `scene_snapshot` ⇒ 只出现在 `[在场]` 里的角色被判「不在场」而静默，实测会吞掉
    4/9 条真违规（召回 100% → 56%）。两个入参都按身份断言，不数次数。"""
    from app.services import agency_scene_state as ass
    from app.services import narrative_ci

    投影 = [{"actor": "埃吉尔", "outcome": "reject", "degree": "none", "roll": 81,
             "chance": 70, "intent": "取回押送令", "kind": "npc"}]
    captured: dict = {}

    def fake(_text, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(narrative_ci, "evaluate", fake)
    ctx = _纯卡ctx(tmp_path, _agency_npc_claims=投影)
    ass._wb_run_narrative_ci(ctx, "她抬起头，望向远处的雪山。", turn=3, raw=[], repo_id="work",
                             scene_snapshot="[在场] 埃吉尔")

    assert captured["agency_claims"] is 投影
    assert captured["scene_snapshot"] == "[在场] 埃吉尔"


def test_写回把本轮快照原样交给CI的在场闸():
    """最后一公里：`_agency_writeback` 里那份 `snapshot`（状态写回后的真快照）必须以
    `scene_snapshot=` 传进 `_wb_run_narrative_ci`。上面那条用例直接调接缝、绕过了写回，
    所以写回侧的形态要在源码上单独锁（空白归一后断言，不依赖换行排版）。"""
    import inspect
    import re as _re

    from app.services import agent_agency

    src = _re.sub(r"\s+", "", inspect.getsource(agent_agency._agency_writeback))
    assert "scene_snapshot=snapshot)" in src


def test_判据本体没被这一刀接上台账():
    """两条判据的源码里不得出现 `arm_participation`／`load_arms`：台账是**读数**，
    一旦被判据读回去，「没书」就会变成一条新诊断（清单 #3／#4 明确不做的那件事）。"""
    import inspect

    from app.services import narrative_ci, prose_style

    ci_src = inspect.getsource(narrative_ci.evaluate)
    assert "for rule in world_rules:" in ci_src          # 判据仍只认自己的入参
    assert "arm_participation" not in ci_src
    ratchet_src = inspect.getsource(prose_style.term_ratchet_report)
    assert "if set_texts:" in ratchet_src                # 存量闸门仍是「有设定才闸」
    assert "arm_participation" not in ratchet_src
