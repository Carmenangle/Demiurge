"""上下文装配域（原 agent_graph A 簇，逐字节搬移）。

逐字节自 agent_graph.py 搬移（方案 B 第一步，见 docs/memory/agent-graph-seam-plan-2026-09-16.md）；
行为等价由既有测试网 + 搬移块 sha1 校验保证。
"""

from __future__ import annotations
import logging
import re
import time
from typing import Any
from app.services import run_trace
from app.services import llm as _llm
from app.services.agent_support import (  # noqa: F401  跨簇底座
    _proxy_kw,
)

_LOG = logging.getLogger("uvicorn.error")

def _resolve_agent_cfg(agent_id: str) -> dict | None:
    """读自定义 Agent 预设：空 agent_id / 查不到 → None（走内置默认，与单 agent 一致）。"""
    if not (agent_id or "").strip():
        return None
    try:
        from app.services import agent_store
        return agent_store.get_agent(agent_id)
    except Exception:  # noqa: BLE001
        return None


def _resolve_skills(agent_cfg: dict | None) -> list[str]:
    """技能提示词片段：有预设按其 skillIds（空=不用），无预设用全部已启用（与单 agent 一致）。"""
    try:
        from app.services import skills_store
        if agent_cfg is not None:
            return skills_store.fragments_by_ids(agent_cfg.get("skillIds") or [])
        return skills_store.enabled_prompt_fragments()
    except Exception:  # noqa: BLE001
        return []


def _render_user_persona(ctx: dict) -> str:
    """无预设扮演时组装用户人设片段（有预设走 personaDescription marker，不重复注入）。
    名+描述任一非空即渲染，让角色知道'用户是谁'。"""
    name = (ctx.get("user_name") or "").strip()
    desc = (ctx.get("user_persona") or "").strip()
    if not (name or desc):
        return ""
    head = f"【用户扮演（{name}）】" if name else "【用户扮演】"
    return head + ("\n" + desc if desc else "")


def _bound_card_names(ctx: dict) -> list[str]:
    names = [str(name).strip() for name in (ctx.get("card_names") or []) if str(name).strip()]
    opening = str(ctx.get("opening_card_name") or ctx.get("card_name") or "").strip()
    if opening and opening not in names:
        names.insert(0, opening)
    return list(dict.fromkeys(names))


def _recent_character_context(ctx: dict) -> str:
    """角色回退只看最近一条 AI 剧情，避免更早楼层角色持续滞留。"""
    for item in reversed(ctx.get("history") or []):
        if item.get("role") != "assistant":
            continue
        content = str(item.get("content") or "").strip()
        if content:
            return content[-2000:]
    return ""


def _mentioned_bound_names(names: list[str], text: str) -> list[str]:
    """角色名有包含关系时优先最长实体；独立出现的短名仍保留。"""
    spans: list[tuple[int, int, str]] = []
    for name in names:
        start = 0
        while start < len(text):
            index = text.find(name, start)
            if index < 0:
                break
            spans.append((index, index + len(name), name))
            start = index + len(name)
    accepted: list[tuple[int, int, str]] = []
    for span in sorted(spans, key=lambda item: (-(item[1] - item[0]), item[0])):
        if any(span[0] < other[1] and other[0] < span[1] for other in accepted):
            continue
        accepted.append(span)
    matched = {name for _start, _end, name in accepted}
    return [name for name in names if name in matched]


def _active_fallback_names(names: list[str], text: str) -> list[str]:
    """从最近剧情按角色最后一次出现的分句排除明确离场者。"""
    selected: list[str] = []
    for name in _mentioned_bound_names(names, text):
        index = text.rfind(name)
        if index < 0:
            continue
        tail = text[index + len(name):]
        clause = re.split(r"[，,。！？!?；;\n]", tail, maxsplit=1)[0][:80]
        departure = _CHARACTER_DEPARTURE.search(clause)
        returned = _CHARACTER_RETURN.search(clause)
        negated = _NEGATED_DEPARTURE.search(clause)
        if departure and not negated and (not returned or returned.start() < departure.start()):
            continue
        selected.append(name)
    return selected


def _card_source(ctx: dict, selected_name: str = "") -> tuple[str, str]:
    """作品用卡/世界书/正则的读取 base：**快照优先**。

    新建作品时卡已快照进作品文件夹（<outputDir>/<卡名>/角色卡/）；命中则运行时读快照——
    改源库的卡不回灌已建作品（快照隔离）。无快照（存量作品/未快照）→ 回退源库 characterDir。
    返回 (base, card_name)；两值任一空表示无卡，调用方各自处理。
    """
    card_name = selected_name or ctx.get("opening_card_name") or ctx.get("card_name") or ""
    character_dir = ctx.get("character_dir") or ""
    if not card_name:
        return character_dir, card_name
    try:
        from app.services import character_store
        snap = character_store.repo_card_base(
            ctx.get("output_dir") or "", ctx.get("repo_id") or "", card_name,
        )
        if snap:
            return snap, card_name
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 读失败退回 character_dir，但卡来源降级
        _LOG.warning("卡快照读取失败（上下文退回 character_dir，卡来源降级）: %s", exc)
    return character_dir, card_name


def _apply_work_persona(ctx) -> None:
    """作品绑定的用户人设**快照优先**：命中 <outputDir>/<卡名>/persona.json 则覆盖 ctx 的
    user_name/user_persona（改设置里的人设不回灌已建作品）；无快照保留前端透传值。就地改写。"""
    # 仓库显式绑定了人设（前端标 persona_bound）→ 用前端透传值，不被作品快照覆盖
    if ctx.get("persona_bound"):
        return
    card_name = ctx.get("card_name") or ""
    output_dir = ctx.get("output_dir") or ""
    if not (card_name and output_dir):
        return
    try:
        from app.services import character_store
        snap = character_store.read_work_persona(output_dir, card_name)
    except Exception:  # noqa: BLE001
        return
    if not isinstance(snap, dict):
        return
    ctx.user_name = str(snap.get("name") or "")
    ctx.user_persona = str(snap.get("content") or "")


def _resolve_persona(character_dir: str, card_name: str) -> str:
    """按作品关联的角色卡组装 persona 系统片段。无卡/读不到 → 空串（回退通用对话）。

    character_dir 由调用方经 _card_source(ctx) 得到（快照优先，回退源库）。
    """
    if not ((character_dir or "").strip() and (card_name or "").strip()):
        return ""
    try:
        from app.services import character_card, character_store
        card = character_store.read_card(character_dir, card_name)
        return character_card.build_persona_system(card) if card else ""
    except Exception:  # noqa: BLE001
        return ""


def _resolve_personas(
    ctx: dict, query: str = "", *, opening_only: bool = False, fallback_query: str = "",
    worldbook_names: list[str] | None = None,
) -> str:
    """只注入本轮出场角色的非空 description；首轮固定为开场卡。"""
    names = _bound_card_names(ctx)
    if opening_only:
        opening = str(ctx.get("opening_card_name") or ctx.get("card_name") or "").strip()
        selected = [opening] if opening in names else []
    else:
        explicit = set(worldbook_names or [])
        direct = set(_mentioned_bound_names(names, query))
        selected = [name for name in names if name in direct or name in explicit]
        if not selected and fallback_query:
            selected = _active_fallback_names(names, fallback_query)

    profiles: list[str] = []
    personalities: list[str] = []
    scenarios: list[str] = []
    dialogue_examples: list[str] = []
    injected_names: list[str] = []
    try:
        from app.services import character_store, instruction_provenance
        for name in selected:
            base, card_name = _card_source(ctx, name)
            card = character_store.read_card(base, card_name) if base and card_name else None
            description = str((card or {}).get("description") or "").strip()
            if not description:
                continue
            profiles.append(instruction_provenance.wrap(
                f"角色卡：{card_name}",
                f"【角色：{card_name}】\n{description}",
            ))
            for key, target in (
                ("personality", personalities),
                ("scenario", scenarios),
                ("mes_example", dialogue_examples),
            ):
                value = str((card or {}).get(key) or "").strip()
                if value:
                    target.append(instruction_provenance.wrap(
                        f"角色卡：{card_name}:{key}",
                        f"【角色：{card_name}】\n{value}",
                    ))
            injected_names.append(card_name)
    except Exception:  # noqa: BLE001
        profiles = []
        personalities = []
        scenarios = []
        dialogue_examples = []
        injected_names = []
    ctx["_selected_persona_names"] = injected_names
    ctx["_selected_persona_personality"] = "\n\n".join(personalities)
    ctx["_selected_persona_scenario"] = "\n\n".join(scenarios)
    ctx["_selected_persona_examples"] = "\n\n".join(dialogue_examples)
    if not profiles:
        return ""
    selection = (
        "【本轮角色卡描述】只按角色名使用下列实际出场角色的描述；"
        "不得把一名角色的外貌、经历或行为特征转移给另一名角色。"
    )
    return selection + "\n\n" + "\n\n".join(profiles)


def _profile_llm_fallback(ctx: dict, scene_spec: dict[str, Any]) -> tuple[str, str]:
    """同轮成稿被清空（锚点重定向且无存活视觉事实）时，补一次携带当前防拦截预设的
    LLM 调用，按纠正后正文重写图像 Profile（Krea2 英文画面）。

    这是「防拦截生效」的兜底：旧实现在此直接掉本地模板、完全没有 LLM 参与，
    防拦截预设自然无从谈起。这里复用 image_prompt_profiles.generate 的
    system/校验/重写链，并用 system_with_preset 把当前防拦截预设接到独立调用上；
    scene_spec 里的 protected_narrative（防拦截原文）经 _scene_for_model 作为模型输入，
    本地校验则用 _scene_for_facts 的还原事实，两层各司其职。
    返回 (compiled_profile, strategy)；失败返回 ("", "")，调用方回退本地事实兜底。
    """
    profile = str(scene_spec.get("profile") or "anima_tags")
    if not (ctx.get("chat_base") and ctx.get("chat_key") and ctx.get("chat_model")):
        return "", ""
    if not str(scene_spec.get("narrative") or "").strip():
        return "", ""
    from app.services import image_prompt_profiles

    def _generate(system: str, user: str) -> str:
        # 2026-09-01 用户定案：插画兜底 LLM 也走流式，实时读 token 输入输出，
        # 30s 无 token 即断（llm.build_model streaming 读超时），不再用 60s 总超时。
        guarded = image_prompt_profiles.system_with_preset(
            system, scene_spec,
            preset_dir=str(ctx.get("preset_dir") or ""),
            preset_name=str(ctx.get("preset_name") or ""),
            user_name=str(ctx.get("user_name") or ""),
            # 2026-09-01 用户定案：生图提示词要像剧情一样稳定——带完整预设防拦截。
            head_only=False,
        )
        started = time.monotonic()
        chars = [0]
        last_token = [started]
        last_heartbeat = [started]

        def _on_delta(delta: str) -> None:
            now = time.monotonic()
            last_token[0] = now
            chars[0] += len(delta)
            if now - last_heartbeat[0] >= 20:
                last_heartbeat[0] = now
                try:
                    run_trace.emit(
                        ctx, "model.stream_progress", agent="illustration_fallback",
                        elapsed_seconds=round(now - started, 1),
                        visible_chars=chars[0],
                    )
                except Exception:
                    pass

        return _llm.chat_messages_stream(
            ctx["chat_base"], ctx["chat_key"], ctx["chat_model"],
            [{"role": "system", "content": guarded}, {"role": "user", "content": user}],
            on_delta=_on_delta, temperature=0.4, retries=2, **_proxy_kw(ctx),
            provider_profile=ctx.get("provider_profile") or "openai_compatible",
        )

    diagnostics: dict[str, object] = {}
    try:
        compiled = image_prompt_profiles.generate(
            profile, scene_spec, _generate, diagnostics,
        )
    except Exception:  # noqa: BLE001
        run_trace.emit(ctx, "illustration.profile_llm_fallback", status="error")
        return "", ""
    if not compiled or not str(compiled).strip():
        return "", ""
    strategy = str(diagnostics.get("strategy") or "llm_retargeted")
    strategy_map = {
        "direct": "llm_retargeted",
        "repaired": "llm_retargeted+repair",
        "fallback": "llm_retargeted_fallback",
    }
    final_strategy = strategy_map.get(strategy, strategy)
    run_trace.emit(
        ctx, "illustration.profile_llm_fallback",
        status="ok", strategy=final_strategy, output_chars=len(compiled),
        field_ledger=diagnostics.get("field_ledger"),
        first_errors=diagnostics.get("first_errors") or [],
        repair_errors=diagnostics.get("repair_errors") or [],
    )
    return compiled, final_strategy


def _card_visual_profiles(ctx: dict, query: str) -> str:
    """角色卡模式的生图外貌真源；只读取本轮出现的绑定卡，未命中时回退开场卡。"""
    names = _bound_card_names(ctx)
    selected = [name for name in names if name in query]
    if not selected and names:
        selected = [str(ctx.get("opening_card_name") or ctx.get("card_name") or names[0])]
    profiles: list[str] = []
    try:
        from app.services import character_store
        for name in selected:
            base, card_name = _card_source(ctx, name)
            card = character_store.read_card(base, card_name) if base and card_name else None
            if not card:
                continue
            description = str(card.get("description") or "").strip()
            if description:
                profiles.append(f"{card_name}：{description}")
    except Exception:  # noqa: BLE001
        return ""
    return "\n".join(profiles)


def _illustration_appearance(ctx: dict) -> str:
    selected = str(ctx.get("_illustration_visual_profiles") or "").strip()
    if ctx.get("appearance_source") in {"worldbook", "character_card"}:
        return selected
    return selected or str(ctx.get("persona") or "").strip()


def _worldbook_session_state_path(ctx: dict) -> str:
    """世界书稳定序列的持久化文件路径（2026-09-13 动态区外置配套）。

    与 repo 快照同目录（`<output_dir>/<safe repo_id>/`，见 worldbook_store.repo_snapshot_path），
    重启后锚点段不重排、前缀跨进程延续。取不到 output_dir/repo_id 时返回空串 → 不落盘。
    """
    from pathlib import Path

    from app.services.pathnames import safe_seg
    base = str(ctx.get("output_dir") or "").strip()
    repo_id = str(ctx.get("repo_id") or ctx.get("thread_id") or "").strip()
    if not (base and repo_id):
        return ""
    return str(Path(base) / safe_seg(repo_id, strip=False) / "worldbook_session.json")


def _worldbook_session_key(ctx: dict) -> str:
    """世界书稳定序列的会话键（2026-09-12，P4-roleplay 配套）。

    取「作品」粒度（output_dir + repo_id/thread_id）：同一作品连续推进的剧情共享一条序列，
    换作品/换线程各自独立。取不到任何一项时返回空串 → 调用方退回无状态的重排版
    assemble_selection（不引入跨轮状态）。
    """
    parts = [
        str(ctx.get("output_dir") or "").strip(),
        str(ctx.get("repo_id") or ctx.get("thread_id") or "").strip(),
    ]
    return "|".join(part for part in parts if part)


def _resolve_worldbook(ctx: dict, query: str) -> str:
    """卡内嵌世界书：constant 常驻 + 非常驻按当前上下文语义检索，组装注入文本。

    查询用「最近历史 + 本轮输入」以贴合当前剧情。无卡/无书/读不到 → 空串。
    """
    ctx["_selected_worldbook_indices"] = []
    ctx["_keyword_worldbook_indices"] = []
    ctx["_worldbook_character_names"] = []
    # 召回段（每轮重排）单独外带：调用方把它放尾部动态块，避免它一变就把其后整段前缀打掉。
    ctx["_worldbook_recall_text"] = ""
    # 动态段（动态区外置）同样外带：锚点条目【剧情进展·动态】区 + 会话中途新锚点。
    ctx["_worldbook_dynamic_text"] = ""
    try:
        from app.services import worldbook
        from app.services.rag_backend import EmbedConfig
        book = _repo_worldbook(ctx)
        entries = worldbook.parse_entries(book)
        if not entries:
            return ""
        cfg = EmbedConfig(
            ctx.get("embed_base", ""), ctx.get("embed_key", ""),
            ctx.get("embed_model", "") or "text-embedding-3-small",
            proxy=ctx.get("embed_proxy", ""),
        )
        def notify_initial_index(count: int) -> None:
            run_trace.emit(
                ctx, "worldbook.index", status="started", initial=True, count=count,
            )
            sink = ctx.get("stream_sink")
            if callable(sink):
                sink({"rag_status": {
                    "state": "start", "kind": "worldbook", "count": count,
                }})

        worldbook.schedule_index(
            ctx.get("repo_id", ""), entries, cfg, on_initial=notify_initial_index,
        )
        scan = _worldbook_scan_text(ctx, query)
        ctx["_worldbook_scan_chars"] = len(scan)
        # 会话内稳定序列（2026-09-12）：条目按首次激活顺序单调追加 → 注入文本跨轮只增不改，
        # 上游前缀缓存才能命中（重排版每轮整块换序，位于其后的历史与尾部合同全部作废）。
        # 取不到会话键时退回无状态的重排版，行为与改动前一致。
        session_key = _worldbook_session_key(ctx)
        selection = (
            worldbook.assemble_selection_stable(
                ctx.get("repo_id", ""), entries, scan, cfg, session_key=session_key,
                state_path=_worldbook_session_state_path(ctx) or None,
            )
            if session_key
            else worldbook.assemble_selection(ctx.get("repo_id", ""), entries, scan, cfg)
        )
        ctx["_selected_worldbook_indices"] = selection.indices
        ctx["_worldbook_dropped"] = selection.dropped
        current_keyword_indices = set(worldbook.keyword_match_indices(entries, query))
        selected_current_indices = current_keyword_indices.intersection(selection.indices)
        ctx["_keyword_worldbook_indices"] = [
            index for index in selection.indices if index in selected_current_indices
        ]
        bound_names = _bound_card_names(ctx)
        activated_text = "\n".join(
            entry.content + "\n" + entry.comment + "\n" + "\n".join(entry.keys)
            for position, entry in enumerate(entries)
            if (entry.source_index if entry.source_index >= 0 else position)
            in selected_current_indices
        )
        ctx["_worldbook_character_names"] = _mentioned_bound_names(bound_names, activated_text)
        # 提取世界规则条目（含约束词的 entry）供 Narrative CI 的世界规则诊断
        _RULE_HINT_RE = re.compile(
            r"(?:不可|禁止|不得|必须|应当|务必|严禁|绝不|永远不要|只有|唯一)", re.IGNORECASE
        )
        ctx["_world_rules"] = [
            entry.content.strip()
            for entry in entries
            if _RULE_HINT_RE.search(entry.content or "")
        ][:20]
        ctx["_worldbook_recall_text"] = selection.recall_text
        ctx["_worldbook_dynamic_text"] = getattr(selection, "dynamic_text", "")
        return selection.text
    except Exception:  # noqa: BLE001
        return ""


def _worldbook_scan_text(ctx: dict, query: str, *, history_chars: int = 1800) -> str:
    """世界书激活窗口：本轮输入 + 最近一组对话，不扫描整段旧历史。"""
    recent: list[str] = []
    for message in reversed(ctx.get("history") or []):
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        recent.append(content)
        if len(recent) >= 2:
            break
    history = "\n".join(reversed(recent))[-max(0, history_chars):]
    return "\n".join(part for part in (history, (query or "").strip()) if part).strip()


def _worldbook_sources(ctx: dict) -> list[dict]:
    """读取卡快照/源卡与绑定独立书，仅供首次建立小仓库世界书快照。"""
    from app.services import character_store, worldbook_store
    books: list[dict] = []
    for name in _bound_card_names(ctx):
        character_dir, card_name = _card_source(ctx, name)
        if character_dir and card_name:
            embedded = character_store.read_worldbook(character_dir, card_name)
            if isinstance(embedded, dict):
                books.append(embedded)
    wb_dir = ctx.get("worldbook_dir") or ""
    wb_name = ctx.get("worldbook_name") or ""
    if wb_dir and not wb_name and card_name:
        wb_name = card_name
    if wb_dir and wb_name:
        standalone = worldbook_store.read_book(wb_dir, wb_name)
        if isinstance(standalone, dict):
            books.append(standalone)
    return books


def _repo_worldbook(ctx: dict) -> dict | None:
    """返回当前小仓库世界书；首次读取从绑定来源复制，之后只读隔离快照。"""
    from app.services import worldbook_store
    output_dir = ctx.get("output_dir") or ""
    repo_id = ctx.get("repo_id") or ctx.get("thread_id") or ""
    if output_dir and repo_id:
        existing = worldbook_store.read_repo_snapshot(output_dir, repo_id)
        if existing is not None:
            return existing
        return worldbook_store.ensure_repo_snapshot(output_dir, repo_id, _worldbook_sources(ctx))
    sources = _worldbook_sources(ctx)
    if not sources:
        return None
    entries = []
    for source in sources:
        raw = source.get("entries")
        values = raw.values() if isinstance(raw, dict) else raw
        entries.extend(item for item in (values or []) if isinstance(item, dict))
    return {"entries": entries}


def _curator_worldbook_context(ctx: dict, repo_id: str) -> str:
    from app.services import worldbook_store
    if not _repo_worldbook(ctx):
        return ""
    return worldbook_store.repo_snapshot_context(ctx.get("output_dir") or "", repo_id)


def _curator_worldbook_context_fn(ctx: dict, repo_id: str):
    from app.services import worldbook_store
    base = ctx.get("output_dir") or ""
    if not (base and repo_id and _repo_worldbook(ctx)):
        return None
    allowed = frozenset(ctx.get("_selected_worldbook_indices") or [])

    def context(window_text: str) -> str:
        # 2026-08-30 成本实锤：整本世界书快照 ≈2万字符/轮（JSON 转义后实测 4.4 万字符）。
        # curator 的世界书改写范围本就限定在本轮相关条目（allowed），视图只给这些条目、
        # 按与正文的命中排序并截断到片段预算——改写能力保留，全书参照撤销。
        return worldbook_store.repo_snapshot_context(
            base, repo_id, query=window_text or "",
            max_chars=6_000, allowed_indices=allowed,
        )

    return context


def _curator_worldbook_fn(ctx: dict, repo_id: str):
    from app.services import worldbook_store
    base = ctx.get("output_dir") or ""
    if not (base and repo_id and _repo_worldbook(ctx)):
        return None
    allowed = frozenset(ctx.get("_selected_worldbook_indices") or [])

    def apply(ops):
        rejected = []
        for op in ops:
            if not isinstance(op, dict) or str(op.get("op") or "").strip() != "worldbook_update":
                continue
            try:
                index = int(op.get("index"))
            except (TypeError, ValueError):
                index = None
            if index not in allowed:
                rejected.append(index)
        run_trace.emit(
            ctx, "worldbook.update_scope", allowed_indices=sorted(allowed),
            rejected_indices=rejected,
        )
        rejections: list[dict[str, object]] = []
        applied = worldbook_store.apply_repo_ops(
            base, repo_id, ops, allowed_update_indices=allowed,
            rejections=rejections,
        )
        if rejections:
            run_trace.emit(
                ctx, "worldbook.ops_rejected", repo_id=repo_id, rejections=rejections,
            )
        return applied

    return apply


def _resolve_regex_scripts(ctx: dict) -> list:
    """合并全局正则 + 本作品卡内嵌正则 → RegexScript 列表。读一次缓存到 ctx。

    全局跨作品生效在前，卡内嵌在后（ST 顺序：GLOBAL→SCOPED）。读不到任一侧不影响另一侧。
    """
    cached = ctx.get("_regex_scripts")
    if cached is not None:
        return cached
    scripts: list = []
    try:
        from app.services import regex_engine, regex_store
        # ① 全局正则（跨作品，存 data/regex_scripts.json）
        for raw in regex_store.load_scripts():
            scripts.append(regex_engine.from_st_dict(raw))
        # ② 预设级正则（仅当前激活预设生效，存预设 JSON 的 regexScripts 键）
        preset_dir = ctx.get("preset_dir") or ""
        preset_name = ctx.get("preset_name") or ""
        if preset_dir and preset_name:
            from app.services import preset_store
            for raw in preset_store.read_regex(preset_dir, preset_name):
                scripts.append(regex_engine.from_st_dict(raw))
        # ③ 卡内嵌正则（随卡、仅该卡，快照优先回退源库）
        from app.services import character_store
        for name in _bound_card_names(ctx):
            character_dir, card_name = _card_source(ctx, name)
            if not (character_dir and card_name):
                continue
            for raw in character_store.read_regex(character_dir, card_name):
                scripts.append(regex_engine.from_st_dict(raw))
    except Exception:  # noqa: BLE001
        scripts = []
    ctx["_regex_scripts"] = scripts
    return scripts


def _apply_regex(ctx: dict, text: str, placement: int, *,
                 is_prompt: bool = False, depth: int | None = 0,
                 skip_depth_gated: bool = False) -> str:
    """在指定 placement 上跑后端侧正则（存储/发送档，不含 markdownOnly 显示档——那在前端）。

    skip_depth_gated：处理本轮实时输入时置真，跳过深度门控（历史楼层）脚本，避免刚输入的当前轮被
    「删 history 最后一条用户消息」等历史级删除正则误擦成空（本架构 live 输入尚未入历史）。
    """
    if not text:
        return text
    scripts = _resolve_regex_scripts(ctx)
    if not scripts:
        return text
    try:
        from app.services import regex_engine
        return regex_engine.run_scripts(
            text, placement, scripts,
            is_markdown=False, is_prompt=is_prompt, depth=depth,
            skip_depth_gated=skip_depth_gated,
        )
    except Exception:  # noqa: BLE001
        return text


def _history_messages(ctx: dict) -> list[dict]:
    """把 ctx.history 转成真实的多轮消息（保留 user/assistant role），供 chat_messages 用。
    替代旧的 history_text 折叠——历史作真实对话轮出现，模型更好衔接、role 不被抹平。"""
    out: list[dict] = []
    for h in (ctx.get("history") or []):
        content = (h.get("content") or "").strip()
        if content:
            out.append({"role": h.get("role") or "user", "content": content})
    return out


def _resolve_preset(
    ctx: dict, worldbook_text: str, *, scene: str = "", affinity: float | None = None, turn: int = 0,
    history: list[dict] | None = None,
) -> tuple[list[dict], float | None, bool, list[str], list[str]]:
    """有激活偏置预设 → 组装带 role 的多条消息 + 采样温度 + 是否含历史 marker + 命中的思维链(尾/头)。
    无预设/读不到 → ([], None, False, [], [])。

    保留每片段自身 role（system/user/assistant 少样本片段不再被折叠），chatHistory marker 处原位
    插入历史（ST 深度注入语义）。marker 填充：卡字段 + 世界书 + 用户人设。
    history=None 读 ctx 全量历史；剧情模式传入瘦身后的上一次剧情轮（story_history.last_story_round）。
    思维链按 scene/affinity/turn 真状态条件选（select_chains），尾部注入遵守最严、头部随 system。
    """
    ctx["_preset_sampling"] = {}
    preset_dir = ctx.get("preset_dir") or ""
    preset_name = ctx.get("preset_name") or ""
    if not (preset_dir and preset_name):
        return [], None, False, [], []
    try:
        from app.services import preset_store
        preset = preset_store.read_preset(preset_dir, preset_name)
        if not preset:
            return [], None, False, [], []
        selected_names = [
            str(name).strip() for name in (ctx.get("_selected_persona_names") or [])
            if str(name).strip()
        ]
        history = (ctx.get("history") or []) if history is None else history
        # ST 深度重注入范式：{{lastUserMessage}}=本轮实时输入（未入历史），{{lastCharMessage}}=历史里
        # 最后一条 AI 消息。配套「擦除历史最后一条用户消息 + 在指定深度重注入 {{lastUserMessage}}」越甲。
        last_char = ""
        for h in reversed(history):
            if (h.get("role") or "") == "assistant" and (h.get("content") or "").strip():
                last_char = (h.get("content") or "").strip()
                break
        markers = {
            "char_name": "、".join(selected_names),
            "char_description": (ctx.get("persona") or "").strip(),
            "char_personality": str(ctx.get("_selected_persona_personality") or "").strip(),
            "scenario": str(ctx.get("_selected_persona_scenario") or "").strip(),
            "dialogue_examples": str(ctx.get("_selected_persona_examples") or "").strip(),
            "worldbook": worldbook_text or "",
            "persona": (ctx.get("user_persona") or "").strip(),
            "user_name": (ctx.get("user_name") or "").strip(),
            "last_user_message": (ctx.get("message") or "").strip(),
            "last_char_message": last_char,
        }
        messages = preset_store.assemble_messages(preset, markers, history)
        has_hist = preset_store.has_history_marker(preset)
        chains_tail, chains_head = preset_store.select_chains(
            preset, scene=scene, affinity=affinity, turn=turn)
        # 思维链（含状态栏模板）与其它预设内容一致做宏替换，避免 {{user}}/{{char}} 字面漏进提示词→被模型照抄进正文
        chains_tail = [preset_store.substitute_macros(c, markers) for c in chains_tail]
        chains_head = [preset_store.substitute_macros(c, markers) for c in chains_head]
        params = preset_store.sampling_params(preset)
        ctx["_preset_sampling"] = params
        # 2026-09-04 设置整合：trace 里的 temperature 取 settings 全局（权威源），
        # preset 顶层 sampling 字段保留但仅作旧 settings 迁移期兼容——新链路已迁移完毕。
        # 若 settings 缺该字段，回退 preset 字段。
        _ps = ctx.get("preset_sampling") if isinstance(ctx.get("preset_sampling"), dict) else {}
        _t = _ps.get("temperature")
        if not isinstance(_t, (int, float)) or isinstance(_t, bool):
            _t = params.get("temperature")
        temp = _t
        return (messages, (float(temp) if isinstance(temp, (int, float)) else None),
                has_hist, chains_tail, chains_head)
    except Exception:  # noqa: BLE001
        return [], None, False, [], []


def _tool_on(agent_cfg: dict | None, key: str) -> bool:
    """工具开关：无预设全开（原行为）；有预设按其 tools 配置，缺省 True。"""
    if agent_cfg is None:
        return True
    return ((agent_cfg.get("tools") or {}).get(key, True))


def _has_mcp(agent_cfg: dict | None) -> bool:
    """本轮是否有可用 MCP 外部工具：有预设看其 mcpServerIds 非空；无预设看全局已启用服务器。
    为真才在 supervisor 里放出 tool_agent 分派（无 MCP 时该 route 不激活，与原多 Agent 行为一致）。"""
    try:
        if agent_cfg is not None:
            return bool(agent_cfg.get("mcpServerIds"))
        from app.services import mcp_store
        return bool(mcp_store.enabled_servers())
    except Exception:  # noqa: BLE001
        return False


def _agent_system(ctx: dict, base: str) -> str:
    """按预设/风格/技能拼 system_prompt（与单 agent _build 对齐）：
    自定义预设的 systemPrompt 完全替换人设，memory 作长期记忆，风格模板+技能追加。"""
    cfg = ctx.get("agent_cfg")
    sp = (cfg.get("systemPrompt").strip() if cfg and (cfg.get("systemPrompt") or "").strip() else base)
    if cfg and (cfg.get("memory") or "").strip():
        sp += "\n\n【长期记忆（关于用户/偏好）】\n" + cfg["memory"].strip()
    st = (ctx.get("style_template") or "").strip()
    if st:
        try:
            from app.services.image_prompt_style import guidance_for
            sp += "\n\n【生图提示词写法】" + guidance_for("", ctx.get("gen_model", ""), st)
        except Exception as exc:  # noqa: BLE001 [EXC-留痕] 附加失败不阻断，但 system 缺该段
            _LOG.warning("生图提示词写法指导附加失败（system 缺少该段，写法约束弱化）: %s", exc)
    frags = ctx.get("skill_frags") or []
    if frags:
        sp += "\n\n【用户自定义技能】\n" + "\n".join(f"- {f}" for f in frags)
    return sp


def _temperature(ctx: dict, default: float) -> float:
    cfg = ctx.get("agent_cfg")
    if cfg and isinstance(cfg.get("temperature"), (int, float)):
        return cfg["temperature"]
    return default


_CHARACTER_DEPARTURE = re.compile(
    r"离开|离场|退出|告辞|走远|远去|消失|不在|已经走了|已走|返回(?:自己的|原来的)?(?:房间|住处|领地)",
)


_CHARACTER_RETURN = re.compile(r"回来|回到|返回现场|重新出现|进入|走进|来到|抵达|仍在|还在|留下")


_NEGATED_DEPARTURE = re.compile(r"没有离开|并未离开|未离开|不曾离开|没有走|并未走")
