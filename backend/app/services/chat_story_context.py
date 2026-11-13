"""聊天 / 群聊的剧情资料块（2026-11-09）。

用户口径（2026-11-10）：私聊只接剧情里**沉淀成表格/状态**的部分，不接世界书、不接
整套预设，也不接往事纪要正文。聊天要像聊天软件，剧情只通过「角色当前状态 + 数据表」
提供背景；预设只保留防拦截/内容许可那一段。

1. **数据表**：全局数据表、主角信息表、当前发言人的重要角色表行（只取相关的行），
   让人物知道自己和用户的关系、当前处境；
2. **角色当前状态**：`character_state` 的叙事/数值字段，优先于旧表格；
3. **关系判断**：资料里没有见过面的记录 → 按陌生人/冷淡处理；有记录 → 按资料里的
   熟悉程度说话。

边界：
- 只读，不写状态/表格；
- 任何一块失败都只丢该块并留 trace，绝不阻断聊天；
- 每块字符上限按用户上下文预算派生（`_budget_limits`），不再写死固定字数。
"""
from __future__ import annotations

import re
from collections.abc import Sequence

from app.services.agent_contracts import CtxLike
from app.services import run_trace

# 私聊剧情背景预算：不再写死 3.6k / 2.2k 字符，按用户上下文预算派生。
# 这是聊天室里的「背景资料」，不是聊天历史；给总预算的 25%（compact 12.5%），
# 避免背景表格反客为主，同时让调大 contextMaxTokens 后真的能带更多资料。
_CHAT_STORY_CONTEXT_SHARE = 0.25
_CHAT_STORY_CONTEXT_COMPACT_SHARE = 0.125

# 2026-11-11 用户实锤：私聊反复揪着「半个时辰的期限／回本部／某个人名」不放。
# 剧情任务目标（当前目标/任务/使命等）是剧情推进器，不是聊天话题；聊天资料只保留
# 能影响语气、身体、衣着与关系的部分，任务目标字段/列不进私聊。
_PLOT_OBJECTIVE_WORDS = ("目标", "任务", "使命", "委托")
# 关键装扮记忆（用户点名的婚纱/兔女郎等）：聊天回忆应当能提起这些纪念回，
# 不能被最近几条剧情事件挤出预算（2026-11-11 用户要测「婚纱回/兔女郎回」）。
_OUTFIT_MEMORY_RE = re.compile(
    r"婚纱|婚礼|兔女郎|礼服|泳装|泳衣|比基尼|沙滩装|女仆|护士|和服|浴衣|旗袍",
)


def _common_prefix_length(a: str, b: str) -> int:
    limit = min(len(a), len(b))
    index = 0
    while index < limit and a[index] == b[index]:
        index += 1
    return index


def names_align(a: str, b: str) -> bool:
    """角色短名/全名别名匹配（与前端 findBindingKey 同口径）。

    世界书条目常用全名（腓特烈大帝），表格/状态里可能只写短名（腓特烈）；
    只接受「一方包含另一方」，且短名至少 2 字，避免「甲」匹配所有人。
    """
    left = (a or "").strip()
    right = (b or "").strip()
    if not left or not right:
        return False
    if left == right:
        return True
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    return len(short) >= 2 and short in long


def best_name_match(target: str, candidates: Sequence[str]) -> str:
    """从候选名里选最像 target 的一个；精确命中优先，同分多义不猜。"""
    name = (target or "").strip()
    if not name:
        return ""
    exact = next(
        (str(item).strip() for item in candidates if str(item).strip() == name),
        "",
    )
    if exact:
        return exact
    best = ""
    best_score: int | None = None
    ambiguous = False
    for raw in candidates:
        candidate = str(raw or "").strip()
        if len(candidate) < 2 or len(name) < 2:
            continue
        if not names_align(name, candidate):
            continue
        score = _common_prefix_length(name, candidate) * 10 - abs(len(name) - len(candidate))
        if best_score is None or score > best_score:
            best = candidate
            best_score = score
            ambiguous = False
        elif score == best_score:
            ambiguous = True
    return "" if ambiguous else best


def _has_plot_objective(label: str) -> bool:
    return any(word in (label or "") for word in _PLOT_OBJECTIVE_WORDS)


def _drop_plot_objective_columns(table: dict) -> dict:
    """聊天用表格裁掉「当前目标/任务」列：目标留给剧情，不拿来催聊天。"""
    columns = [str(column) for column in (table.get("columns") or [])]
    keep = [index for index, column in enumerate(columns) if not _has_plot_objective(column)]
    if len(keep) == len(columns):
        return table
    rows = [
        [row[index] for index in keep if index < len(row)]
        for row in (table.get("rows") or [])
    ]
    return {**table, "columns": [columns[index] for index in keep], "rows": rows}


def _base_repo(ctx: CtxLike) -> tuple[str, str]:
    base = str(ctx.get("output_dir") or "").strip()
    repo_id = str(ctx.get("repo_id") or ctx.get("thread_id") or "").strip()
    return base, repo_id


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…（已截断）"


def _filter_character_rows(table: dict, speaker: str) -> dict:
    """重要角色表只取当前发言人的行；小表（≤3 行）没有精确匹配时回退全表。"""
    columns = list(table.get("columns") or [])
    rows = list(table.get("rows") or [])
    if "姓名" not in columns:
        return table
    index = columns.index("姓名")
    names = [str(row[index]).strip() for row in rows if index < len(row)]
    matched_name = best_name_match(speaker, names)
    matched = [
        row for row in rows
        if index < len(row) and str(row[index]).strip() == matched_name
    ] if matched_name else []
    if not matched and len(rows) <= 3:
        matched = rows
    return {**table, "rows": matched, "mode": "full"}


def _state_block(
    ctx: CtxLike, speaker: str, limit: int = 0,
) -> str:
    """角色当前状态表（state.json 的叙事/数值），优先于纪要摘要。

    2026-11-10 用户实锤：私聊回复说「还没穿过婚纱」，但角色状态表明确是
    「婚纱被揉皱挂在身上」——纪要每几层才统计一次，最近几层的信息只有状态表有。
    这里把当前发言人的 `叙事` / `数值` 字段整块注入，避免模型拿旧纪要覆盖新状态。
    """
    try:
        from app.services import character_state

        base, repo_id = _base_repo(ctx)
        if not (base and repo_id and speaker):
            return ""
        state = character_state.load_state(base, repo_id, speaker)
        narrative_fields = state.叙事 or {}
        numeric_fields = state.数值 or {}

        def owner_of(key: str) -> str:
            parts = str(key).split("·")
            return parts[-2].strip() if len(parts) >= 2 else ""

        owner = best_name_match(
            speaker,
            [owner_of(key) for key in (*narrative_fields.keys(), *numeric_fields.keys())],
        )
        if not owner:
            return ""

        def label_of(key: str) -> str:
            parts = str(key).split("·")
            if len(parts) < 2 or parts[-2].strip() != owner:
                return ""
            return parts[-1].strip()

        lines: list[str] = []
        for key, narrative in narrative_fields.items():
            label = label_of(str(key))
            text_value = str(getattr(narrative, "value", "") or "").strip()
            if label and text_value and not _has_plot_objective(label):
                lines.append(f"- {label}：{text_value}")
        for key, numeric in numeric_fields.items():
            label = label_of(str(key))
            raw_value = getattr(numeric, "value", None)
            if not label or raw_value is None or _has_plot_objective(label):
                continue
            try:
                lines.append(f"- {label}：{float(raw_value):g}")
            except (TypeError, ValueError):
                continue  # 单个数值坏字段只丢该行，不拖垮整块状态
        if not lines:
            return ""
        return _clip(
            "【角色当前状态（最新剧情状态；与纪要/表格冲突时以此为准）】\n"
            "这些只是你此刻的语气、身体感觉、衣着与关系背景，不是聊天话题：不要主动汇报，"
            "也不要反复汇报所在位置、任务、期限或人名（用户问起时再答）；"
            "更不要把某一件剧情当成每轮都要聊的事。\n"
            + "\n".join(lines),
            limit,
        )
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 状态读取失败只丢本块
        run_trace.emit(ctx, "chat.story_state_error", error=str(exc)[:200])
        return ""


def _tables_block(
    ctx: CtxLike, speaker: str, limit: int = 0,
) -> str:
    """数据表：全局 / 主角 / 当前角色的重要角色行；只读、限量。"""
    try:
        from app.services import table_store

        base, repo_id = _base_repo(ctx)
        if not (base and repo_id):
            return ""
        if not table_store.tables_path(base, repo_id).is_file():
            return ""
        tables = table_store.load(base, repo_id)
        selected: list[dict] = []
        for table in tables:
            name = str(table.get("name") or "")
            if name in (table_store.GLOBAL_TABLE, table_store.PROTAGONIST_TABLE):
                if table.get("rows"):
                    selected.append(table)
            elif name == table_store.CHARACTERS_TABLE:
                filtered = _filter_character_rows(table, speaker)
                filtered = _drop_plot_objective_columns(filtered)
                if filtered.get("rows"):
                    selected.append(filtered)
        if not selected:
            return ""
        text = table_store.render_tables_block(selected)
        return (
            "【剧情数据表（只取与当前角色/用户相关的行，作背景参考，不要逐字复述；"
            "若与【角色当前状态】冲突，以角色当前状态为准）】\n"
            "以下只是聊天之前的背景记录：只取人物关系、外貌与角色自身状态；"
            "不要主动提表格里的任务、期限、人名，也不要复述表格里的场景/事件，"
            "更不要把表格当成本轮话题库或用户当前所在。\n"
            + _clip(text, limit)
        )
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 表格失败不阻断聊天
        run_trace.emit(ctx, "chat.story_tables_error", error=str(exc)[:200])
        return ""


def _key_events_block(
    ctx: CtxLike, limit: int = 0,
) -> str:
    """关键事件/纪念装扮账本：从「任务与事件表」过滤关键事件行，新→旧、限量注入。

    2026-11-10 用户实锤：纪要重建/压缩可能漏掉婚礼与婚纱，关键情节不能只靠摘要。
    账本由 `key_events` 零 LLM 写入任务与事件表；这里只读取，不写表。
    """
    try:
        from app.services import table_store

        base, repo_id = _base_repo(ctx)
        if not (base and repo_id):
            return ""
        if not table_store.tables_path(base, repo_id).is_file():
            return ""
        quests = next(
            (table for table in table_store.load(base, repo_id)
             if str(table.get("name") or "") == table_store.QUESTS_TABLE),
            None,
        )
        if not quests:
            return ""
        columns = [str(column) for column in (quests.get("columns") or [])]
        index = {name: columns.index(name) for name in columns}

        def cell(row: list, name: str) -> str:
            position = index.get(name, -1)
            return str(row[position]).strip() if 0 <= position < len(row) else ""

        lines: list[str] = []
        for row in quests.get("rows") or []:
            name = cell(row, "名称")
            kind = cell(row, "类型")
            if not name or not ("关键事件" in kind or name.startswith("[关键事件]")):
                continue
            state = cell(row, "状态")
            progress = cell(row, "进展") or cell(row, "目标")
            line = f"- {name}"
            if state:
                line += f"（{state}）"
            if progress:
                line += f"：{progress[:180]}"
            lines.append(line)
        if not lines:
            return ""
        # 最近 4 条 + 关键装扮记忆（婚纱/兔女郎等）。后者不能被最近剧情挤出预算：
        # 用户点名的纪念回必须还能进聊天，否则「只凭角色卡/剧情回忆」就断了。
        recent = lines[-4:]
        outfit = [line for line in lines if _OUTFIT_MEMORY_RE.search(line)]
        chosen = {*outfit, *recent}
        ordered = [line for line in lines if line in chosen][::-1]
        ordered.sort(key=lambda line: 0 if _OUTFIT_MEMORY_RE.search(line) else 1)
        lines = ordered
        return _clip(
            "【关键事件与纪念（长期保留；只作背景，不要当成当前正在发生）】\n"
            "这些是过去的纪念，可以在合适的时候当作回忆自然提起（例如上次的婚纱/兔女郎装扮），"
            "提一次就够，不要每条都提；当前任务/期限不要主动推进。\n"
            + "\n".join(lines),
            limit,
        )
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 账本读取失败只丢本块
        run_trace.emit(ctx, "chat.key_events_error", error=str(exc)[:200])
        return ""


def _relationship_hint() -> str:
    return (
        "【与用户的关系判断】\n"
        "先根据下面的剧情资料判断你和用户现在是什么关系、是否见过面。\n"
        "- 资料里没有你们见过面/认识的记录 → 你们还是陌生人：保持距离，语气可以冷淡，"
        "不要自来熟，不要知道用户没告诉过你的事。\n"
        "- 资料里有明确关系或共同经历 → 按资料里的称呼和熟悉程度说话，不要退回陌生人。\n"
        "- 如果资料显示你们已经是恋人、夫妻、伴侣、主从或长期亲密关系，就按那个亲密度说话："
        "可以暧昧、撒娇、表达想念或占有欲，不要退回客气朋友，也不要把「尊重边界」演成冷淡。\n"
        "- 聊天室历史里已经发生过的对话仍然有效；剧情资料只补充聊天室之前的背景。\n"
        "- 资料只是背景，不是本轮聊天内容；不要逐字复述，也不要把往事当成刚刚发生。"
    )


def has_story_material(ctx: CtxLike) -> bool:
    """该作品是否有可用的剧情沉淀（非空数据表或角色状态），供主动聊天判定。

    2026-11-10 用户口径：私聊只接表格和状态，往事纪要/世界书不再算「有剧情沉淀」，
    避免没有表格的作品仅凭旧纪要触发主动闲聊。
    """
    try:
        from app.services import character_state, table_store

        base, repo_id = _base_repo(ctx)
        if not (base and repo_id):
            return False
        if table_store.tables_path(base, repo_id).is_file():
            try:
                if any(table.get("rows") for table in table_store.load(base, repo_id)):
                    return True
            except Exception:  # noqa: BLE001 [EXC-保留] 单源失败继续看状态（只读判定，不落库）
                pass
        if character_state.state_path(base, repo_id).is_file():
            try:
                state = character_state.load_state(base, repo_id, "")
                if state.叙事 or state.数值:
                    return True
            except Exception:  # noqa: BLE001 [EXC-保留] 状态失败按无资料处理（只读判定）
                pass
    except Exception:  # noqa: BLE001 [EXC-保留] 主动判定失败按无资料处理
        return False
    return False


def _budget_limits(ctx: CtxLike, *, compact: bool) -> tuple[int, int, int, int]:
    """按用户上下文预算派生 (state, 关键事件, 表格, 总计) 字符上限；0=不限。

    中日韩约 1 字 1 token，直接按字符使用 token 预算（英文更保守）。背景资料只占
    总预算的 25%（追问/主动 compact 12.5%），按 50% / 25% / 25% 分给三块。
    """
    from app.services import agent_context
    setting = ctx.get("context_max_tokens")
    budget = agent_context.resolve_history_budget(
        setting if isinstance(setting, int) else 0,
        ctx.get("preset_sampling"),
    )
    if budget <= 0:
        return 0, 0, 0, 0
    share = (
        _CHAT_STORY_CONTEXT_COMPACT_SHARE if compact else _CHAT_STORY_CONTEXT_SHARE
    )
    total = max(1, int(budget * share))
    state = max(1, total // 2)
    key_events = max(1, total // 4)
    tables = max(1, total - state - key_events)
    return state, key_events, tables, total


def build_for_speaker(
    ctx: CtxLike, *, speaker: str, members: Sequence[str], query: str,
    compact: bool = False,
) -> str:
    """拼出某个发言人的紧凑剧情背景块；无资料时仍返回关系判断提示。

    只取「角色当前状态 + 关键事件 + 数据表」；世界书/预设/往事纪要都不进私聊。
    `compact=True`：追问/主动开场用，预算减半。各块上限按用户上下文预算派生，
    不再写死 3.6k / 2.2k 字符。
    """
    state_limit, key_limit, tables_limit, total_limit = _budget_limits(
        ctx, compact=compact,
    )

    parts = [_relationship_hint()]
    state = _state_block(ctx, speaker, limit=state_limit)
    if state:
        parts.append(state)
    # 关键事件放在普通表格之前：总预算不足时优先保住婚礼/婚纱这类永久事实。
    key_events = _key_events_block(ctx, limit=key_limit)
    if key_events:
        parts.append(key_events)
    tables = _tables_block(ctx, speaker, limit=tables_limit)
    if tables:
        parts.append(tables)
    text = "\n\n".join(part.strip() for part in parts if part.strip())
    text = _clip(text, total_limit)
    run_trace.emit(
        ctx, "chat.story_context", agent="chat", speaker=speaker,
        chars=len(text), has_worldbook=False, compact=compact,
        has_state=bool(state), has_tables=bool(tables),
        has_key_events=bool(key_events),
    )
    return text
