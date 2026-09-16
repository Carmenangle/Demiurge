"""T1.1 特征测试：`_agency_writeback`（563 行）函数级拆解的行为锚 + 源码形态锁。

拆解方法论（真源 `docs/memory/agent-graph-seam-plan-2026-09-16.md` §4）：
- **等价矩阵**：把改造前的内联判定原样抄成 `_legacy_*` 参照实现，断言新抽出的纯判定
  接缝在输入网格上与其**逐点相等** ⇒「零行为变化」可证伪，不靠读代码相信。
- **源码形态锁**：行为网**抓不到分层/形态回退**（把接缝内联回去照样全绿）⇒ 另加断言
  「主函数确实由接缝组装」+「已降到目标行数以内」。

⚠ `_legacy_*` 是本文件对改造前写法的**逐字抄录**（2026-09-16，自
`agent_agency._agency_writeback` 内联段），不得顺手"优化"——它是等价性的唯一参照。
"""

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
# ---------------------------------------------------------------------------
_SEAMS = (
    # 阶段 A：think 前缀拆分 / 状态写回 / 连续性 CI
    "_wb_split_media_blocks",
    "_wb_apply_state",
    "_wb_run_narrative_ci",
    # 阶段 B：在场信号 / 配音请求 / 初选出图提示词
    "_wb_scene_signals",
    "_wb_build_audio_request",
    "_wb_initial_illustration_prompt",
    # 阶段 C：comfy 七段（候选池 → 提示词/锚点 → scene_spec → Profile → 请求外壳
    #         → 首尾帧与视频 → 事件派发）
    "_wb_comfy_actor_pool",
    "_wb_comfy_scene_target",
    "_wb_comfy_scene_spec",
    "_wb_comfy_profile",
    "_wb_comfy_illustrate_env",
    "_wb_frames_and_video",
    "_wb_emit_illustration_trace",
)

_TARGET_LINES = 160


def test_拆解接缝齐备且同属主():
    for name in _SEAMS:
        fn = getattr(aag, name, None)
        assert callable(fn), f"接缝缺失：{name}"
        assert inspect.getsourcefile(fn) == aag.__file__, f"{name} 不在 agent_agency 属主内"


def test_writeback主函数由接缝组装且已降到目标行数():
    src = inspect.getsource(aag._agency_writeback)
    body_lines = src.count("\n") + 1
    missing = [name for name in _SEAMS if f"{name}(" not in src]
    assert not missing, f"主函数未调用接缝（疑似被内联回去）：{missing}"
    assert body_lines <= _TARGET_LINES, (
        f"_agency_writeback 仍为 {body_lines} 行（目标 ≤{_TARGET_LINES}）——拆解疑似回退"
    )
