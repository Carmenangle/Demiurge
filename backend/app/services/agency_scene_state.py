"""插画场景状态域：think/媒体块拆分、状态写回、连续性 CI、在场信号、配音请求、初选提示词。

从 agent_agency.py 按域迁出（方案 B，2026-10-23）：搬移块 sha1 逐字节一致，
旧名经 agent_agency re-export 零破坏。向下 import agency_illustration_actors（主体解析底座），
不反向依赖 agency_scene_comfy。"""
from __future__ import annotations

from app.services.agent_contracts import CtxLike
from typing import Any

from app.services import run_trace
from app.services.agency_illustration_actors import _build_image_prompt, _status_snapshot_value
from app.services.agent_context_assembly import _apply_regex, _history_messages, _illustration_appearance

def _wb_split_media_blocks(ctx: CtxLike, reply: str) -> tuple[str, str, object, dict, dict]:
    """阶段 C 前段：think 前缀拆出 + <transition>/<illustration>/<audio> 搭车块抽取。

    返回 `(think_head, clean, transition_decision, illustration_plan, audio_plan)`。
    逐字自 `_agency_writeback` 搬移（T1.1 函数级拆解），判定逻辑与文案一字未改。
    """
    from app.services import roleplay_agency
    from app.services import image_prompt_extract, transition_extract
    from app.services.regex_engine import Placement

    # 根因修复（2026-08-31 实锤 3389/11977：幻影协议标签跨界匹配吞正文）：think 是
    # 输出协议的第一块，块提取链只应作用于其后的正文。think 内复述协议清单产生的
    # 幻影开标签会让各提取器的懒匹配从 think 内一路吃到真块闭合，把 </think> 与
    # 全部正文整段当块剥掉。拆出 think 前缀原样保留（前端正则折叠为思考过程），
    # 提取链只看正文，发布时拼回前缀。
    think_head, body = roleplay_agency.split_think_prefix(reply)
    # 孤立 </think>（模型漏写 <think> 开标签，2026-09-12 实锤）会随正文发布，
    # 发布链先剥；成对块与未闭合开标签不受影响。规划注释情形由裸正文兜底路径
    # （_wrap_bare_body → repair_orphan_think_close）补包 think 处理。
    body = roleplay_agency.strip_orphan_think_close(body)
    # V1.5/W1：<transition> 剥离放最前（与 <illustration>/<audio> 同为生成时搭车块，
    # 先抽避免干扰插画解析；漏块/非法/只开不闭 → None，L0 永远兕底，不得抛错）
    clean, transition_decision = transition_extract.extract_transition(body)
    clean, illustration_plan = image_prompt_extract.extract_illustration_plan(
        clean,
        block_filter=lambda value: _apply_regex(
            ctx, value, Placement.AI_OUTPUT, is_prompt=False, depth=0,
        ),
    )
    # 音频对白配音：comfy_audio 时剥离 <audio> 块并解析台词 + 8 维情感向量。
    # 与 illustration 块正交（用户可只开配音不开图）；失败只走降级，不阻断正文。
    audio_plan: dict = {}
    if ctx.get("comfy_audio"):
        from app.services import audio_dialogue_extract
        clean, audio_plan = audio_dialogue_extract.extract_audio_dialogue(
            clean,
            block_filter=lambda value: _apply_regex(
                ctx, value, Placement.AI_OUTPUT, is_prompt=False, depth=0,
            ),
        )
    return think_head, clean, transition_decision, illustration_plan, audio_plan

def _wb_apply_state(ctx: CtxLike, deps, clean: str, *, turn: int, repo_id: str,
                    card_name: str) -> tuple[str, list, float | None, float | None, str, Any]:
    """阶段 C 中段：<status> 快照抽取 + <状态更新> 写回 + 快照兜底 + 旧表格块清洗。

    返回 `(clean, raw, before, after, snapshot, st)`。逐字自 `_agency_writeback` 搬移。
    """
    from app.services import character_state, roleplay_agency
    from app.services import table_update

    # 抽 <status> 快照（不剥，留正文供前端正则渲染）+ 剥 <状态更新> 小数值 JSON
    snapshot = roleplay_agency.extract_status_snapshot(clean)
    clean, raw = roleplay_agency.parse_state_block(clean)
    if not snapshot or not raw:
        # 2026-09-18 兜底状态抽取信号：主协议（<状态更新>/<status>）任一失守
        # （2026-09-17 实锤 deepseek-v4-flash 整轮双缺），交给本轮维护调用顺带
        # 补抽取并补写回，0 额外 LLM。元组 = (need_deltas, need_snapshot)。
        # 真值判断而非 .strip()：extract_status_snapshot 产出已 strip，等价且对测试假件宽容。
        ctx["_agency_state_fallback"] = (not raw, not snapshot)
    raw = [*raw, *(ctx.pop("_agency_goal_deltas", []) or [])]
    rejected_adds: list = []
    before, after = roleplay_agency.writeback(
        deps, repo_id=repo_id, card_name=card_name, raw_deltas=raw, turn=turn,
        snapshot=snapshot, rejected=rejected_adds)
    run_trace.emit(ctx, "state.writeback", raw_deltas=raw, snapshot=snapshot,
                   affinity_before=before, affinity_after=after,
                   rejected_adds=rejected_adds)
    st = character_state.load_state(deps.state_base, repo_id, card_name)
    if not snapshot:
        previous_snapshot = getattr(getattr(st, "快照", None), "text", "")
        clean = roleplay_agency.ensure_status_snapshot(clean, previous_snapshot)
    # 展示对账（2026-09-22 用户实锤）：正文 <status> 的数字此前只对账「落盘快照」与
    # 「重注入快照」，用户屏幕上看到的 [好感] 行仍是模型自述 ⇒ 与引擎账漂移（实测同一角色
    # 正文 16→13→10 乱跳，引擎恒 10→13）。此处用同一函数对账**正文**——本节点末尾发
    # replace 全文替换气泡，故显示即修。st 已在上面从磁盘重载（即已对账的真值）。
    clean = roleplay_agency.reconcile_reply_status(clean, st)
    # 旧版/异常模型仍可能输出表格块：这里只清洗，实际更新统一交给独立维护调用。
    had_legacy_table_block = table_update.has_table_block(clean)
    clean, _legacy_ops = table_update.parse_table_block(clean)
    if had_legacy_table_block:
        run_trace.emit(ctx, "table.writeback", status="legacy_ignored")
    return clean, raw, before, after, snapshot, st

def _wb_ratchet_sources(
    ctx: CtxLike, repo_id: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """术语棘轮（CODE_STYLE_TERM_RATCHET）的输入：注入层 + 最近数轮正文 + 豁免人名 + 设定存量。

    2026-09-23：真机「经脉」在注入块里 33 次、跨 31 个不同句子，而正文里"看经脉"0 命中
    ⇒ 该码要能看见**持久层**，否则永远量不到。此处注入层＝**本轮维护后**的读侧数据表
    （＝下一轮会注入的那一份）+ 世界书召回/动态段；正文层＝最近 5 轮 assistant 全文。
    设定存量（第 4 项）见 ``_wb_setting_sources``。仅只读；失败退回空（不产生诊断）。
    """
    injected: list[str] = []
    names: list[str] = []
    dyn = str(ctx.get("_worldbook_dynamic_text") or "")
    recall = str(ctx.get("_worldbook_recall_text") or "")
    if dyn:
        injected.append(dyn)
    if recall:
        injected.append(recall)
    try:
        from app.services import table_store, table_update

        tables = table_store.load(ctx.get("output_dir") or "", repo_id)
        read_tables = table_store.tables_for_read(tables)
        block = table_update.table_context(read_tables)
        if block:
            injected.append(block)
        for tb in read_tables:
            for row in (tb.get("rows") or []):
                first = str(row[0]).strip() if row else ""
                if 1 < len(first) <= 6:
                    names.append(first)
    except Exception:  # noqa: BLE001 棘轮输入缺失不影响 CI 其余项
        pass
    for name in ctx.get("_worldbook_character_names") or ():
        names.append(str(name).strip())
    bodies = [
        (m.get("content") or "").strip()
        for m in reversed(_history_messages(ctx))
        if m.get("role") == "assistant"
    ][:5]
    bodies = [b for b in bodies if b]
    return (injected, bodies, [n for n in dict.fromkeys(names) if n][:40],
            _wb_setting_sources(ctx, repo_id))

def _wb_setting_sources(ctx: CtxLike, repo_id: str) -> list[str]:
    """设定存量来源＝世界书各条目的**静态底座**（`DYNAMIC_MARKER` 之前部分）。

    用途：用户 2026-09-23 口径——「天狐宫多次出现能理解（主角大半后宫在此、后期势力归男主），
    但经脉，你看设定条目都不怎么提及，就前期提了一嘴，结果一个个女主角找的借口都是经脉」。
    ⇒ 判据＝「**叙事用量 vs 设定存量**」。此处给出存量：含设定条目 + 角色卡（不含动态区，
    动态区是叙事、已算进注入层）。失败退回空 ⇒ 报告退回按频次排序，与改造前一致。
    """
    try:
        from app.services import worldbook, worldbook_store

        base = ctx.get("output_dir") or ""
        book = worldbook_store.read_repo_snapshot(base, repo_id)
        if not book:
            return []
        out: list[str] = []
        for entry in book.get("entries") or []:
            content = str(entry.get("content") or "")
            if worldbook.DYNAMIC_MARKER in content:
                content = content.split(worldbook.DYNAMIC_MARKER, 1)[0]
            if content.strip():
                out.append(content)
        return out
    except Exception:  # noqa: BLE001 棘轮输入缺失不影响 CI 其余项
        return []

def _wb_run_narrative_ci(ctx: CtxLike, clean: str, *, turn: int, raw: list,
                         repo_id: str) -> None:
    """连续性 CI 评估 + 落盘（永不阻断/改写正文）。逐字自 `_agency_writeback` 搬移。"""
    try:
        from app.services import narrative_ci

        injected, bodies, exclude, setting = _wb_ratchet_sources(ctx, repo_id)
        diagnostics = narrative_ci.evaluate(
            clean, turn=turn, facts=ctx.get("_continuity_facts") or (), raw_deltas=raw,
            beliefs=ctx.get("_continuity_beliefs") or (),
            world_rules=ctx.get("_world_rules") or (),
            recent_openings=[
                (m.get("content") or "").strip()[:15]
                for m in reversed(_history_messages(ctx))
                if m.get("role") == "assistant"
            ][:3],
            style_config=ctx.get("_style_config"),
            style_injected=injected,
            style_bodies=bodies,
            style_exclude=exclude,
            style_setting=setting,
        )
        saved = narrative_ci.save(
            ctx.get("output_dir") or "", repo_id, diagnostics,
        )
        run_trace.emit(
            ctx, "narrative.ci", status="evaluated", count=len(diagnostics), saved=saved,
            codes=[item.get("code", "") for item in diagnostics],
        )
    except Exception as exc:  # noqa: BLE001 CI 永不阻断或改写正文
        run_trace.emit(ctx, "narrative.ci", status="unavailable", error=str(exc))

def _wb_at_climax(*, illustration_plan, visible_story, lost, scene,
                  local_scene_fallback, missing_plan_fallback,
                  first_story_reply, character_encounter) -> bool:
    """是否进入插画高潮（纯判定，无副作用）。

    逐字自 `_agency_writeback` 内联判定搬移；等价性由
    `tests/test_agency_writeback_seams.py::test_at_climax等价矩阵` 在输入网格上钉住。
    """
    return bool(illustration_plan) or (
        bool(visible_story) and (
            lost or scene in ("nsfw", "climax")
            or local_scene_fallback or missing_plan_fallback
            or first_story_reply or character_encounter
        )
    )

def _wb_scene_signals(ctx: CtxLike, deps, st, clean: str, snapshot, scene: str,
                      user_text: str, plan, lost: bool) -> dict:
    """阶段 D 前置：可见正文 + 在场/衣着/所在 + 高潮触发信号（纯读取，无副作用）。

    返回 dict，**键名即原内联局部名**（调用方按原样解包 ⇒ 下游正文零改动）。
    逐字自 `_agency_writeback` 搬移。
    """
    from app.services import image_prompt_extract, roleplay_agency, scene_classify
    from app.services import scene_illustration

    # 阶段 D：插画（renderer=None 时 maybe_illustrate 直接返回 None）；用去块正文当段落
    wardrobe = roleplay_agency._narr(st, "衣着")
    locale = roleplay_agency._narr(st, "所在")
    snapshot_text = snapshot or str(
        getattr(getattr(st, "快照", None), "text", "") or "",
    )
    # `<status>` 是显示快照，正常不解析；但它经常是唯一的在场人物真源。
    # LoRA 选择只读取其中精确的 `[在场]` 单行，避免要求模型额外写一份叙事 delta。
    present = (
        _status_snapshot_value(snapshot_text, "在场")
        or roleplay_agency._narr(st, "在场")
    )
    locale = _status_snapshot_value(snapshot_text, "所在") or locale
    # 2026-09-20 用户口径：状态表/快照都取不到「所在 / 衣着」时，回落**数据表**里的旧值
    # （全局数据表·地点 / 主角信息表·穿着打扮）。表格本质是**上一轮**参数 ⇒ 只补空缺：
    # 本轮正文或状态表已经给出的一律优先，绝不覆盖（衣服战损、被撕破这类动态以剧情为准）。
    if not (locale or "").strip() or not (wardrobe or "").strip():
        from app.services import table_store
        table_locale, table_wardrobe = table_store.illustration_defaults(
            str(ctx.get("output_dir") or ""),
            str(ctx.get("repo_id") or ctx.get("thread_id") or ""),
        )
        locale = (locale or "").strip() or table_locale
        wardrobe = (wardrobe or "").strip() or table_wardrobe
    # 插画提示词直接由正文 + 已有视觉锚组装，不再额外调用一次聊天模型。
    visible_story = image_prompt_extract.visible_narrative_text(clean)
    local_scene = scene_classify.infer_scene(
        "\n".join((user_text, visible_story)),
    )
    local_scene_fallback = (
        not plan and local_scene in ("nsfw", "climax")
    )
    # ComfyUI 自动插画开启后，主模型即使没带内联计划，也不能让整条请求静默消失。
    # 普通 dialogue/action 直接复用本轮正文最强视觉段落；完整 Profile 由独立 LLM 链
    # （_profile_llm_fallback）或本地事实编译提供，前端无需再补调文本模型。
    missing_plan_fallback = bool(
        ctx.get("comfy_illustrate") and not plan and visible_story
    )
    (encounter_anchor, encounter_narrative, encounter_actors,
     encounter_facts) = scene_illustration.encounter_illustration_context(clean)
    character_encounter = bool(
        not plan and encounter_anchor
        and (deps.renderer is not None or ctx.get("comfy_illustrate"))
    )
    first_story_reply = not any(
        item.get("role") == "user" and str(item.get("content") or "").strip()
        for item in (ctx.get("history") or [])
    )
    at_climax = _wb_at_climax(
        illustration_plan=plan, visible_story=visible_story, lost=lost, scene=scene,
        local_scene_fallback=local_scene_fallback,
        missing_plan_fallback=missing_plan_fallback,
        first_story_reply=first_story_reply,
        character_encounter=character_encounter,
    )
    image_rating = (
        "nsfw" if scene in ("nsfw", "climax")
        or local_scene in ("nsfw", "climax") else "sfw"
    )
    return {
        "wardrobe": wardrobe, "locale": locale, "snapshot_text": snapshot_text,
        "present": present, "visible_story": visible_story, "local_scene": local_scene,
        "local_scene_fallback": local_scene_fallback,
        "missing_plan_fallback": missing_plan_fallback,
        "encounter_anchor": encounter_anchor, "encounter_narrative": encounter_narrative,
        "encounter_actors": encounter_actors, "encounter_facts": encounter_facts,
        "character_encounter": character_encounter,
        "first_story_reply": first_story_reply, "at_climax": at_climax,
        "image_rating": image_rating,
    }

def _wb_build_audio_request(ctx: CtxLike, audio_plan: dict, visible_story: str) -> dict:
    """comfy_audio 时组装对白配音请求（含机械降级兜底）；关时返回空 dict。

    逐字自 `_agency_writeback` 搬移（含两条 run_trace.emit）。
    """
    audio_req: dict = {}
    if ctx.get("comfy_audio"):
        from app.services import audio_dialogue_extract
        audio_lines = audio_plan.get("lines") or audio_dialogue_extract.build_fallback_dialogue(
            visible_story, [str(n).strip() for n in (ctx.get("card_names") or []) if str(n).strip()],
        )
        if audio_lines:
            audio_req = {"lines": audio_lines}
            run_trace.emit(
                ctx, "audio.request", status="emitted", line_count=len(audio_lines),
                speakers=[ln.get("speaker", "") for ln in audio_lines],
                text_chars=[len(ln.get("text", "")) for ln in audio_lines],
            )
        else:
            run_trace.emit(ctx, "audio.request", status="skipped", reason="no_dialogue")
    return audio_req

def _wb_initial_illustration_prompt(ctx: CtxLike, deps, plan, sig: dict, *,
                                    user_text: str) -> tuple[str, int, list]:
    """初选出图提示词/动作/角色（comfy 与非 comfy 两条路径共用的第一轮选择）。

    返回 `(prompt_override, motion, actors)`。逐字自 `_agency_writeback` 搬移。
    """
    from app.services import image_prompt_extract
    from app.services.regex_engine import Placement

    visible_story = sig["visible_story"]
    prompt_override, motion, actors = "", 0, []
    if plan:
        prompt_override = _apply_regex(
            ctx, plan["prompt"], Placement.IMAGE_PROMPT, is_prompt=True).strip()
        motion = plan["motion"]
        actors = plan["actors"]
    elif ctx.get("comfy_illustrate") and sig["local_scene_fallback"]:
        prompt_override = image_prompt_extract.build_fallback_content_tags(
            "\n".join((user_text, visible_story)),
        )
        motion = image_prompt_extract.infer_motion(visible_story)
        actors = []
    elif (deps.renderer is not None or ctx.get("comfy_illustrate")) and sig["at_climax"]:
        prompt_override, motion, actors = _build_image_prompt(
            ctx,
            paragraph=(sig["encounter_narrative"] if sig["character_encounter"]
                       else visible_story),
            appearance=_illustration_appearance(ctx),
            wardrobe=sig["wardrobe"], locale=sig["locale"])
        if sig["character_encounter"]:
            actors = sig["encounter_actors"]
    return prompt_override, motion, actors

def _wb_illustration_reason(*, illustrate_req, plan_retargeted, illustration_plan,
                            character_encounter, local_scene_fallback,
                            first_story_reply, missing_plan_fallback,
                            prompt_source, at_climax) -> str:
    """`illustration.request` 事件的 reason（纯判定，判定次序即优先级）。

    逐字自 `_agency_writeback` 内联嵌套条件搬移；等价性由
    `tests/test_agency_writeback_seams.py::test_illustration_reason等价矩阵` 钉住。
    """
    return ("main_plan_retargeted" if plan_retargeted and illustrate_req else
            "main_plan" if illustration_plan and illustrate_req else
            "character_encounter" if character_encounter and illustrate_req else
            "local_scene_fallback" if local_scene_fallback and illustrate_req else
            "first_story_reply" if first_story_reply and illustrate_req else
            "missing_plan_fallback" if missing_plan_fallback and illustrate_req else
            prompt_source if illustrate_req else
            "scene_not_triggered" if not at_climax else "empty_prompt")
