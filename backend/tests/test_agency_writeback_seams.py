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
