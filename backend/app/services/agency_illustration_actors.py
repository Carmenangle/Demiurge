"""插画主体解析域：出场角色/主人公性别/画面主体名/判据锚点意图/提示词组装。

从 agent_agency.py 按域迁出（方案 B，2026-10-23）：搬移块 sha1 逐字节一致，
旧名经 agent_agency re-export 零破坏。本域被 agency_scene_state / agency_scene_comfy
向下 import（actors 是底座，不反向依赖它们）。"""
from __future__ import annotations

from app.services.agent_contracts import CtxLike
import re
import time

from app.services import agent_context, llm as _llm, prompt_clean, run_trace
from app.services.agent_context_assembly import _apply_regex, _mentioned_bound_names
from app.services.agent_retrieval import _repo_tables_for_facts
from app.services.agent_support import _proxy_kw


def _status_snapshot_value(snapshot: str, label: str) -> str:
    """只在需要确定性恢复时读取状态栏单行；其余快照内容仍保持不透明。"""
    matches = re.findall(
        rf"(?m)^\s*\[{re.escape(label)}\]\s*(.*?)\s*$",
        snapshot or "",
    )
    return matches[-1].strip() if matches else ""

def _illustration_visual_query(ctx: CtxLike, current_text: str) -> str:
    """为角色外貌匹配保留短上下文，并机械补回最新状态栏的在场角色。"""
    from app.services import roleplay_agency

    present = ""
    for item in reversed(ctx.get("history") or []):
        if item.get("role") != "assistant":
            continue
        snapshot = roleplay_agency.extract_status_snapshot(str(item.get("content") or ""))
        if snapshot:
            present = _status_snapshot_value(snapshot, "在场")
            break
    return "\n".join(filter(None, (
        agent_context.history_text(ctx)[-2000:].strip(),
        (current_text or "").strip(),
        present,
    )))

def _ordered_illustration_names(names: list[str], text: str) -> list[str]:
    """按本轮文本中的实际出现顺序返回精确角色名。"""
    matched = _mentioned_bound_names(names, text or "")
    return sorted(matched, key=lambda name: (text.find(name), names.index(name)))

def _resolve_illustration_request_actors(
    known: list[str], *, planned: list[str], user_text: str, narrative: str,
    present: str, encounter: list[str], priority_text: str = "",
    absent: set | None = None,
) -> list[str]:
    """外貌资料不能充当出场证据；只从本轮事实确定插画角色。

    priority_text（视觉高潮段原文）：无插画计划时，角色排序优先按高潮段内出现顺序，
    而非全文首现顺序——2026-09-01 用户实锤「LoRA 用错」：全文先提 A 后提 B，
    但画面主体是 B，single 模式会加载 A 的 LoRA。
    absent（表格在场状态=不在场 的角色）：点名提及不等于画面在场（2026-09-01
    用户实锤：2girls 实际只有 1 女）。显式计划 subjects / 登场角色除外。
    """
    planned = [str(name).strip() for name in planned if str(name).strip()]
    absent = absent or set()
    if not known:
        return list(dict.fromkeys(planned + encounter))
    valid_planned = [name for name in planned if name in known]
    encounter_group = [name for name in encounter if name in known]
    story_names = _ordered_illustration_names(known, narrative)
    if priority_text and not planned:
        priority_names = _ordered_illustration_names(known, priority_text)
        story_names = list(dict.fromkeys([*priority_names, *story_names]))
    planned_absent = set(valid_planned) & absent
    for group in (encounter_group, story_names, valid_planned):
        if not group:
            continue
        selected = list(dict.fromkeys(group))
        if group is not valid_planned and group is not encounter_group:
            selected = [name for name in selected if name not in absent]
            # 全部被表格判为不在场时，宁可退回无绑定角色也不虚构同框
            if not selected:
                return []
        elif group is valid_planned and planned_absent and len(valid_planned) == len(planned_absent):
            # 模型计划的主体全部被表格判为不在场：视为计划不可用，继续走叙事组
            continue
        return selected
    # 模型已经明确声明画面主体，但主体不是任何绑定角色（典型为“我/你”）时，
    # 不得再从用户输入或状态栏借一个仅被提及的角色来加载其 LoRA。
    if planned:
        return []
    for group in (
        _ordered_illustration_names(known, user_text),
        _ordered_illustration_names(known, present),
    ):
        if group:
            return list(dict.fromkeys(group))
    return []

def _protagonist_gender(ctx: CtxLike, repo_id: str, protagonist: str) -> str:
    """主人公性别（零 LLM）：世界书条目正文优先，通用表「性别」列兜底。

    2026-09-20 用户定案：只服务主人公，且性别只作**画面事实**注入（不机械拼硬标签）。
    两道来源都取不到 ⇒ `""`（调用方不注入，宁缺勿猜）。
    """
    name = str(protagonist or "").strip()
    if not name:
        return ""
    from app.services import scene_illustration, worldbook_store
    base = str(ctx.get("output_dir") or "").strip()
    if base:
        try:
            gender = worldbook_store.repo_protagonist_gender(base, repo_id, name)
        except Exception:  # noqa: BLE001 — 世界书读失败不得打断出图链
            gender = ""
        if gender:
            return gender
    return scene_illustration.gender_from_tables(
        _repo_tables_for_facts(ctx, repo_id), name,
    )

def _subject_gender_map(
    ctx: CtxLike, repo_id: str, names: list[str], *,
    protagonist: str, protagonist_gender: str, tables,
) -> dict[str, str]:
    """逐角色性别映射 `{角色名: "女"/"男"}`（零 LLM，第 46 刀）。

    ⚠ **用途仅限**「按画面主体的实际性别组成写人数词」（`1girl, 2boys` 这种）——
      **不做画面事实注入**（性别注入仍只对主人公，23 刀红线）。
    ⚠ 来源定序与主人公一致：世界书条目正文优先、通用表「性别」列兜底；
      解析不出的名**不进映射**（下游见缺 ⇒ 退回中性措辞，宁缺勿猜）。
    ⚠ 主人公复用已算好的 `protagonist_gender`（省一次世界书 I/O）。
    """
    from app.services import scene_illustration, worldbook_store
    base = str(ctx.get("output_dir") or "").strip()
    out: dict[str, str] = {}
    for raw in names:
        name = str(raw or "").strip()
        if not name or name in out:
            continue
        if protagonist and name == protagonist and protagonist_gender:
            out[name] = protagonist_gender
            continue
        gender = ""
        if base:
            try:
                gender = worldbook_store.repo_character_gender(base, repo_id, name)
            except Exception:  # noqa: BLE001 — 世界书读失败不得打断出图链
                gender = ""
        if not gender:
            gender = scene_illustration.gender_from_tables(tables, name)
        if gender:
            out[name] = gender
    return out

def _filter_illustration_appearance(
    appearance: str, actors: list[str], known: list[str],
) -> str:
    """视觉资料只描述已选角色，禁止旧角色外貌进入最终 Profile。

    段落头用通用「名字[：:]」识别（不限 known 白名单），这样 known 之外的角色段
    （如世界书 NPC 未进入 illustration_actor_names）也能被正确识别并过滤，
    否则会把非选中角色段当成选中角色的续行保留（陆莹纱混入缺陷）。
    """
    source = (appearance or "").strip()
    selected = set(actors)
    if not source or not known:
        return source
    if not selected:
        return ""
    # 通用段落头：行首「任意名字 + 冒号」，不再限定 known 白名单。
    marker = re.compile(r"^\s*([^\s：:]+)\s*[：:]")
    if not any(marker.match(line) for line in source.splitlines()):
        return source
    kept: list[str] = []
    include = False
    for line in source.splitlines():
        match = marker.match(line)
        if match:
            include = match.group(1).strip() in selected
        if include:
            kept.append(line)
    return "\n".join(kept).strip()

def _resolve_prev_tail_desc(ctx: CtxLike) -> str:
    """上楼层尾帧画面描述：历史里最后一条角色（assistant）回复正文 → 结尾画面。

    与生成时透传的 lastFrameDesc 同源同逻辑（同一提取函数 extract_story_frames），
    零新增持久化（P4 反查精神：历史正文即资产）。倒序找第一条非空 assistant 消息
    （对齐预设 lastCharMessage 的取法）；无历史 / 上一楼纯对白 → 空串
    （L0 输入缺失 → ambiguous，由前端坑C「有图前提」兜底）。
    """
    from app.services import story_frames
    for h in reversed(ctx.get("history") or []):
        if (h.get("role") or "") == "assistant" and (h.get("content") or "").strip():
            return story_frames.extract_story_frames(
                (h.get("content") or "").strip(),
            ).closing
    return ""

# 判据档单次调用的墙钟上限（项目红线：一切外部调用必须带超时，禁止无超时挂起）。
_ANCHOR_INTENT_TIMEOUT_SECONDS = 60.0

def _frame_subject_names(raw) -> list[str]:
    """归一化「本帧主体名集合」（判据档 `subjects` / 词表档在场角色全集）。

    非序列或全空一律回空表 ⇒ 取材层视为「无信号」＝旧行为，这是 A′ 的零回归前提。
    """
    if not isinstance(raw, (list, tuple, set)):
        return []
    return [str(name).strip() for name in raw if str(name).strip()]

def _resolve_anchor_intent_list(ctx: CtxLike, text: str, raw: str) -> list[dict]:
    """判据原始输出 ⇒ **去重后的锚点列表**，再按面板数量取前 N（第 57 刀）。

    `count` 取 `ctx["illustration_batch_count"]`（面板「批量生图数量」）：
      · 0/缺省 ⇒ 1（单张：与 `resolve_anchor_intent` 同口径，取分最高、同分取正文更早）；
      · ≥ 锚点数 ⇒ 全部；
      · 否则 ⇒ 按判据 `score` 降序取前 N（结果仍保持正文原序）。
    `list[0]` 恒等于 `resolve_anchor_intent` 单条路径的锚点（两条链**同源**，不会各说各话）。
    任何解析失败 ⇒ `[]`（调用方回落已有单条路径/词表档）。
    """
    from app.services import scene_illustration

    items = scene_illustration.dedupe_anchor_intents(
        scene_illustration.parse_anchor_intents(raw), text,
    )
    # 第 59 刀（用户定案 2026-09-25）：判据**显式打 0 分**＝「这个时刻不值得画」
    # ⇒ 无论批量上限多大都不为它建槽。⚠ 过滤落在本层（多批选择）而非
    # `select_anchor_intents` 本体——旧单锚点形态被 parse 层有意包装成 score=0
    # （零回归锁），本体过滤会误杀单条路径（真机回归 11 连红实锤）。
    items = [item for item in items if int(item.get("score") or 0) > 0]
    if not items:
        return []
    return scene_illustration.select_anchor_intents(items, _anchor_batch_count(ctx))

def _anchor_batch_count(ctx: CtxLike) -> int:
    """面板「批量生图数量」：0/缺省/非法 ⇒ 1（单张，与改造前逐字一致）。"""
    try:
        value = int(ctx.get("illustration_batch_count") or 0)
    except (TypeError, ValueError):
        return 1
    return value if value >= 1 else 1

def _anchor_intent_viewpoint_hint(ctx: CtxLike) -> str:
    """第 57 刀 ④：把「画面人称」告知判据（**只加在调用侧，不动用户判据文本**）。

    用户口径（原话，第二次重申）：「第三人称是**内容产出再找视角展示完整内容**，而第一人称
    则是**先内容再核对主人公主视角内容进行筛分**。」
    ⇒ 第一人称下，**选点阶段**就该避开「只有退到远处／绕到对方背后才画得出来」的时刻；
    否则下游再筛也救不回。真机实锤（turn `9bb03d04`）：判据选了事后的**非接触帧**，
    取材窗口只给得出**整夜性场景** ⇒ 编译出「肉棒压在她臀上」这类**只能从外部看到**的画面
    ⇒ 第一人称成图**必然**呈现第三人称观感。

    ⚠ 刻意**不动 `DEFAULT_CLIMAX_ANCHOR_RUBRIC`**：该文件头明令「**不许往判据里加约束/加门**」
      （v7/v9/v10/v11 四次实测净亏）；本提示是**调用侧上下文**、不是判据正文
      ⇒ 用户手写的那份 rubric **逐字不变**、也**不需要 rubric A/B**。
    未手选人称（`""`＝自动）⇒ 返回空串 ⇒ user 消息**逐字节不变**（零回归锁）。
    """
    if str(ctx.get("illustration_viewpoint") or "").strip() != "first_person":
        return ""
    return (
        "画面人称：第一人称（镜头＝叙事者的眼睛、视野有限；叙事者本人不占画面，"
        "他的身体只在正文写到时以局部入画）。\n"
        "⇒ 选点时**优先**挑「就站在叙事者眼睛位置、一眼就能看到」的时刻；"
        "**避开**那些必须退远、绕到对方背后、或要同时看全两人全身才画得出来的时刻"
        "（那种帧在**选点阶段**就注定画不出来）。"
    )

def _illustration_anchor_intent(ctx: CtxLike, clean: str, sig: dict) -> tuple[str, dict]:
    """判据档：用「高潮点定位判据」（用户态配置，属主 illustration_prompt_store）判定锚点。

    只在**同轮无插画计划**（缺块路径，实测占多数回合）时参与。任一步不成立即返回
    `("", {})`，调用方顺序走现有中文词表（**零改动**）：
    判据为空 / 正文为空 / 无模型配置 / 调用异常 / 输出解析不出 anchor / anchor 不逐字落文。

    **本层常开**（2026-09-18 用户定案去掉启用开关）：它只是「词表档之前的一次更聪明的尝试」，
    失败有上界——最坏情况就是退回词表档，所以不需要一个「用不用它」的开关。

    观测四态（`illustration.anchor_intent`）：`skipped`（前置不足：判据空 / 正文空 / 无模型配置）、
    `error`（调用失败，带 error_type/message/耗时）、`invalid`（解析或 evidence 校验不过）、
    `ok`（带 anchor/composition/subjects/reason/耗时）。
    2026-10-19 新增第五态 `refused`：模型**安全层拒答**（正常 200 + 拒绝说明，不是异常）。
    真机实锤：R-18 正文下模型回「I can't help with ... sexually explicit content」，
    旧实现把它记成 `invalid`（看起来像 JSON 格式错），排查方向被带偏。拒答态同时记
    `raw_head` 原文首段，不必再复现。

    返回 `(anchor, detail)`：`detail` 供**取材层**判「锚点句是否自带画面」（A′ 用
    `subjects`），与 trace 字段同源；anchor 为空时 detail 恒为 `{}`。四态 trace 字段不动。
    """
    from app.services import illustration_prompt_store, scene_illustration

    rubric = illustration_prompt_store.effective_climax_anchor_rubric()
    if not rubric:
        # store 侧保证非空（缺文件 / 坏 JSON / 留空都回缺省 v8）；这里是**防御性闸**：
        # 一旦 store 被替换或回归成可能返回空串，也必须「拿不到判据就不调模型、直接回词表」。
        run_trace.emit(
            ctx, "illustration.anchor_intent", status="skipped", reason="rubric_empty",
        )
        return "", {}
    text = (clean or "").strip()
    if not text:
        run_trace.emit(
            ctx, "illustration.anchor_intent", status="skipped", reason="narrative_empty",
        )
        return "", {}
    if not (ctx.get("chat_base") and ctx.get("chat_key") and ctx.get("chat_model")):
        run_trace.emit(
            ctx, "illustration.anchor_intent", status="skipped",
            reason="model_config_missing",
        )
        return "", {}
    actors = [str(name).strip() for name in (sig.get("present") or []) if str(name).strip()]
    user = "\n\n".join(part for part in (
        # 第 57 刀 ④：画面人称（只 disclose，不约束判据正文）。未手选 ⇒ 空串 ⇒ 逐字不变。
        _anchor_intent_viewpoint_hint(ctx),
        f"在场角色：{'、'.join(actors)}" if actors else "",
        f"剧情正文：\n{text}",
    ) if part)
    started = time.monotonic()
    system = scene_illustration.anchor_intent_system(rubric)
    # ⚠ 2026-10-19 实测：这里**不要**套 `system_with_preset`（生图/视频链那层防拦截预设）。
    # 用同一段 R-18 正文对照过：不套 → 模型明确拒答「I can't analyze this content…」（311 字）；
    # 套上 → 变成一句空话「What can I help you with?」（25 字），**更糟且更贵**（+7,319 字预设）。
    # 锚点判定是「可选增强」，拒答时词表兜底本来就是正确路径，不必为它加防拦截层。
    try:
        raw = _llm.chat(
            ctx["chat_base"], ctx["chat_key"], ctx["chat_model"],
            system, user,
            temperature=0.2, timeout=_ANCHOR_INTENT_TIMEOUT_SECONDS, **_proxy_kw(ctx),
        )
    except Exception as exc:  # noqa: BLE001 判据是可选增强：任何失败都必须静默降级到词表
        run_trace.emit(
            ctx, "illustration.anchor_intent", status="error",
            error_type=type(exc).__name__, error_message=str(exc)[:500],
            elapsed_seconds=round(time.monotonic() - started, 1),
        )
        return "", {}
    anchor, detail = scene_illustration.resolve_anchor_intent(text, raw or "")
    # 拒答识别（2026-10-19）：拒答是「正常 200 + 一段拒绝说明」，不是异常 ⇒ 必须单独判，
    # 否则永远被记成 `invalid`（「解析不出锚点」），和真正的 JSON 格式错混在一起无法区分。
    refused = bool(prompt_clean.REFUSAL_RE.search(prompt_clean.restore_jailbreak(raw or "")))
    # 第 57 刀：同步落一份**去重 + 取前 N** 的多锚点列表进 ctx（多批生图的地基）。
    # ⚠ 只在能解析出锚点时写；单张路径的返回值 `(anchor, detail)` **逐字不变**
    #   （`resolve_anchor_intent` 仍是「分最高那条」的老语义 —— 零回归锁）。
    if anchor:
        ctx["illustration_anchor_intents"] = _resolve_anchor_intent_list(
            ctx, text, raw or "",
        )
    elapsed = round(time.monotonic() - started, 1)
    if not anchor:
        run_trace.emit(
            ctx, "illustration.anchor_intent",
            # 拒答与「解析不出」分开记：前者是模型安全层拒绝（改 prompt/预设可救），
            # 后者是输出格式问题（改合同/解析器可救）——混在一起就查不出方向。
            status="refused" if refused else "invalid",
            refused=refused,
            raw_chars=len(raw or ""), parsed=bool(detail),
            anchor_candidate=str(detail.get("anchor") or "")[:120],
            # 原文首段（截断）：拒答时能直接看到是哪类拒绝，不用再复现一次。
            raw_head=(raw or "")[:200],
            elapsed_seconds=elapsed,
        )
        return "", {}
    run_trace.emit(
        ctx, "illustration.anchor_intent", status="ok",
        anchor=anchor[:120], composition=detail.get("composition") or "",
        subjects=detail.get("subjects") or [], reason=detail.get("reason") or "",
        elapsed_seconds=elapsed,
        # 第 57 刀 · 观测补口（2026-09-24 用户问「选的是不是高分」）：把判据的**完整榜单**
        # （含 score）记下来 —— 此前只记「选中那条」，打分发生了却丢掉。
        # ⚠ 仅进诊断 trace，不进提示词（score 不外泄到生图链路）。
        anchors=(ctx.get("illustration_anchor_intents") or [])[:8],
    )
    return anchor, detail

def _illustration_anchor_offset(reply: str, request: dict) -> int | None:
    """在最终显示正文中定位插画槽；本地兜底 anchor 被正则改写时重新选高潮段。

    第 58 刀（2026-09-24 用户口径）：插图插到**段落下方**，不切进段落中间 ——
    句尾偏移统一吸附到所在段落的末尾（主图／extras／首尾帧同走本口，一处吸附全覆盖）。
    """
    from app.services import scene_illustration

    snap = scene_illustration.snap_offset_to_paragraph_end
    # 首尾帧模式：主槽=首帧图（尾帧 :last 副槽由前端追加楼层末尾），首帧画面锚正文
    # 第一段——主图用的「高潮纠偏/末段兜底」会把开篇铺垫改判到中央/末尾
    # （2026-08-29 用户验收问题①），firstlast 不走那套纠偏。
    if str(request.get("video_mode") or "") == "firstlast":
        offset: int | None = scene_illustration.first_frame_anchor_offset(reply)
        return snap(reply, offset) if offset is not None else None
    offset = scene_illustration.illustration_anchor_offset(
        reply, str(request.get("anchor") or ""),
    )
    if offset is None and request.get("allow_anchor_fallback"):
        final_anchor = scene_illustration.fallback_illustration_anchor(reply)
        if final_anchor:
            offset = scene_illustration.illustration_anchor_offset(reply, final_anchor)
    return snap(reply, offset) if offset is not None else None

def _build_image_prompt(ctx: CtxLike, *, paragraph: str, appearance: str,
                        wardrobe: str, locale: str) -> tuple[str, int, list[str]]:
    """零 LLM 组装自动插画提示词，并复用 IMAGE_PROMPT 清洗规则。"""
    from app.services import image_prompt_extract as ipe, scene_illustration
    from app.services.regex_engine import Placement

    card_name = (ctx.get("card_name") or "").strip()
    known = [str(name).strip() for name in (ctx.get("illustration_actor_names") or [])
             if str(name).strip()]
    actors = [name for name in known if name in (paragraph or "")]
    if card_name and card_name in (paragraph or "") and card_name not in actors:
        actors.append(card_name)
    if not actors and card_name:
        actors.append(card_name)
    request = scene_illustration.build_scene_request(
        paragraph=ipe.restore_jailbreak(paragraph),
        appearance=appearance,
        wardrobe=wardrobe,
        locale=locale,
        actors=actors,
    )
    prompt = _apply_regex(ctx, request.prompt, Placement.IMAGE_PROMPT, is_prompt=True)
    return prompt.strip(), ipe.infer_motion(paragraph), request.actors
