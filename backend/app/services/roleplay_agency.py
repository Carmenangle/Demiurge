"""剧情能动性子图编排：把 character_state / agency / scene_illustration / scene_renderers
串成 `roleplay_node` 的内部四阶段循环。**唯一碰 LLM 的能动性代码**，故依赖全注入、可用假件单测。

设计见 ARCHITECTURE.md「剧情能动性引擎」支柱 2。四阶段：
- A 世界提案（默认每剧情回合判断一次，可显式关闭）：LLM 提案 → `judge` 纯规则仲裁。
- B 主控叙述（既有那次 LLM，注入 state 块 + 已裁定自主行动 + 要求尾附 <状态更新> JSON）。
- C 状态写回（**搭车解析，0 额外 LLM**）：抽 <status> 快照存文件（抗压缩、显示不剥）
  + 从尾部剥 <状态更新> 小数值 JSON → apply → save。快照下轮由 render_snapshot_injection 重注入。
- D 插画（门控，通常跳过）：好感度跨档/失控/每N段 → build_scene_request → renderer 出图。

依赖方向（importlinter roleplay-agency-stack 合同将强制）：本模块 import 那四个模块 + llm，
它们**不反向 import 本模块**。本模块不 import agent_graph（由 agent_graph 单向调用），故无环。
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from app.services import (
    agency,
    builtin_agents,
    character_state,
    narrative_memory,
    narrative_store,
    scene_illustration,
)
from app.services.scene_illustration import Renderer, SceneRequest

# 好感度是 -100(厌恶)..100(喜爱) 连续标量（字段边界由 character_state 单一属主）。
# 档位/门控默认由 builtin_agents 单一属主（③ 可被用户覆盖），此处仅做别名，保留旧引用不破坏。
DEFAULT_TIERS: list[float] = builtin_agents.DEFAULT_TIERS
AFFINITY_FIELD = character_state.AFFINITY_FIELD  # 复用单一属主，不另定义
GATE_FLOOR = builtin_agents.GATE_FLOOR            # 默认 -100：敌对 NPC 也保留自主性
GATE_BASE_RATE = builtin_agents.GATE_BASE_RATE    # 默认每回合判断；0 才显式关闭

_TAG_OPEN = "<状态更新>"
_TAG_CLOSE = "</状态更新>"
_STATE_BLOCK_RE = re.compile(re.escape(_TAG_OPEN) + r"(.*?)" + re.escape(_TAG_CLOSE), re.DOTALL)

# 显示层状态栏：预设定义字段、AI 每轮吐 <status>…</status>；引擎当不透明整块，
# **不剥、不解析**（留正文供前端正则渲染），只抽出来存快照 + 下轮重注入。取最后一块。
_STATUS_TAG_RE = re.compile(r"<status>([\s\S]*?)</status>", re.IGNORECASE)


def extract_status_snapshot(reply: str) -> str:
    """抽取叙述里最后一个 <status> 块的**内部原文**（不含标签）。无则空串。

    只读不改：<status> 保留在正文（前端正则渲染绿框），这里仅取内容供落盘快照。
    多块取最后一个（叙述可能先复述旧栏再给新栏，末块为最新）。
    """
    matches = _STATUS_TAG_RE.findall(reply or "")
    return matches[-1].strip() if matches else ""


def ensure_status_snapshot(reply: str, previous_snapshot: str) -> str:
    """模型漏掉本轮 <status> 时，把已持久化的上轮战报保留在正文开头。"""
    if extract_status_snapshot(reply) or not (previous_snapshot or "").strip():
        return reply
    return f"<status>\n{previous_snapshot.strip()}\n</status>\n\n{reply.lstrip()}"


@dataclass
class AgencyDeps:
    """子图运行期依赖（全注入 → 测试传假件；生产由 roleplay_node 从 ctx 组装）。"""
    chat_fn: Callable[..., str]        # LLM 调用（世界提案/条目维护用；主控叙述仍由 roleplay_node 直调）
    rng: random.Random                 # 掷骰/门控随机源（测试传固定种子可复现）
    state_base: str                    # character_state 落盘根（<base>/<repo_id>/state.json）
    renderer: Renderer | None = None   # 出图渲染器（None=不出图，插画阶段静默跳过）
    thresholds: list[float] = field(default_factory=lambda: list(DEFAULT_TIERS))
    # ③ 世界 Agent / 裁判可被用户覆盖的参数（默认取 builtin_agents，由 agent_graph 从 ctx.builtin 注入）
    world_system: str = builtin_agents.WORLD_SYSTEM
    world_temperature: float = builtin_agents.WORLD_TEMPERATURE
    gate_floor: float = builtin_agents.GATE_FLOOR
    gate_base_rate: float = builtin_agents.GATE_BASE_RATE
    affinities: dict[str, float] = field(default_factory=dict)
    state_context: str = ""
    # ③ 条目维护 Agent（curator）：默认每轮启用；剧情后 LLM 抽新知识 → index_fn 写库（只增不改）。
    curator_system: str = builtin_agents.CURATOR_SYSTEM
    curator_temperature: float = builtin_agents.CURATOR_TEMPERATURE
    curator_gate: float = 1.0
    index_fn: Callable[[str, str], object] | None = None  # (text, title)→写入 RAG 知识库；None=不写
    worldbook_context: str = ""  # 当前小仓库世界书条目（带 index），仅供受控增改
    worldbook_context_fn: Callable[[str], str] | None = None
    worldbook_fn: Callable[[list[dict[str, Any]]], int] | None = None
    # top_p/max_tokens 透传（None 不含则用模型默认；由 agent_graph 从各 agent 生效值组装）
    world_sampling: dict = field(default_factory=dict)
    curator_sampling: dict = field(default_factory=dict)
    trace_fn: Callable[..., None] | None = None


def _trace(deps: AgencyDeps, event: str, **data: Any) -> None:
    if deps.trace_fn is not None:
        try:
            deps.trace_fn(event, **data)
        except Exception:
            pass


# ── 阶段 B 辅助：搭车指令 + state 注入块 ──

def state_instruction() -> str:
    """附加到主控叙述 system 的搭车指令：要求正文后另起一行输出状态增量 JSON（0 额外 LLM）。"""
    return (
        "\n\n【状态更新规则】在正文之后另起一行，输出本轮剧情导致的角色状态变化，"
        f"格式：{_TAG_OPEN}[{{\"field\":\"数值/好感度\",\"op\":\"add\",\"value\":5,"
        "\"evidence\":\"本轮证据\"}]" + _TAG_CLOSE + "。"
        "单主角时，好感度增减用 field=\"数值/好感度\" op=\"add\"；态度/心情/所在等用 "
        "field=\"叙事/xxx\" op=\"set\"。同场有多名角色时，每个字段必须写成 "
        "field=\"数值/角色名·好感度\" 或 field=\"叙事/角色名·身体状态\"，"
        "用中点明确角色归属，禁止把身体、精神等状态类别拼进角色名。"
        "每条必须带 evidence 引用本轮剧情依据；本轮无变化则输出空数组 []。"
        "这段仅供系统解析，不要在正文里复述。"
    )


def parse_state_block(reply: str) -> tuple[str, list[Any]]:
    """从叙述里剥离 <状态更新> 块，返回（去块后的正文, 原始 delta 列表）。

    块内 JSON 解析失败或缺块 → 返回原文去块 + 空列表（叙述照常，不因解析失败丢内容）。
    """
    m = _STATE_BLOCK_RE.search(reply or "")
    if m:
        payload = m.group(1)
        clean = _STATE_BLOCK_RE.sub("", reply).strip()
    else:
        # Claude 偶尔在完整 JSON 后漏掉闭标签。仅把最后一个开标签到 EOF 当控制块，
        # 防止它泄漏到正文；普通正文里的相似文字不受影响。
        start = (reply or "").rfind(_TAG_OPEN)
        if start < 0:
            return reply, []
        payload = reply[start + len(_TAG_OPEN):]
        clean = reply[:start].strip()
    try:
        raw = json.loads(payload.strip())
    except (json.JSONDecodeError, ValueError):
        return clean, []
    return clean, raw if isinstance(raw, list) else []


# ── 阶段 A：世界提案（门控 LLM）→ 裁判（纯规则）──

# 世界 Agent 默认提示词由 builtin_agents 单一属主（③ 可覆盖）；此别名保留旧引用不破坏。
_WORLD_SYSTEM = builtin_agents.WORLD_SYSTEM


def consult_world(
    deps: AgencyDeps, *, chat_base: str, chat_key: str, chat_model: str,
    core: str, scene: str, affinity: float | None, proxy: str = "",
) -> list[agency.Verdict]:
    """门控通过时唤起世界 Agent 提案并逐条机械仲裁；否则返回空（塌回单次 LLM）。

    affinity 是兼容旧单角色状态的好感度快照；多角色优先用 deps.affinities。失败/空提案/门控关返回 []。
    """
    if not core.strip() or not scene.strip():
        _trace(deps, "agent.skipped", agent="world", reason="missing_context")
        return []
    fallback_affinity = 0.0 if affinity is None else affinity
    gate_affinities = deps.affinities or {"_": fallback_affinity}
    if not agency.should_consult_world(
            gate_affinities, rng=deps.rng, floor=deps.gate_floor, base_rate=deps.gate_base_rate):
        _trace(deps, "agent.skipped", agent="world", reason="gate_not_matched",
               gate_floor=deps.gate_floor, gate_base_rate=deps.gate_base_rate,
               affinities=gate_affinities)
        return []
    try:
        state = f"\n\n【当前动态状态】\n{deps.state_context}" if deps.state_context.strip() else ""
        user = (f"【在场角色 core】\n{core}{state}\n\n【当前场景】\n{scene}\n\n"
                f"【角色好感度】{json.dumps(gate_affinities, ensure_ascii=False)}")
        _trace(deps, "agent.started", agent="world")
        _trace(deps, "model.request", agent="world", model=chat_model,
               messages=[{"role": "system", "content": deps.world_system},
                         {"role": "user", "content": user}])
        raw = deps.chat_fn(chat_base, chat_key, chat_model, deps.world_system, user,
                           temperature=deps.world_temperature, proxy=proxy, **deps.world_sampling)
        _trace(deps, "model.response", agent="world", content=raw or "")
        m = re.search(r"\[[\s\S]*\]", raw or "")
        if not m:
            _trace(deps, "agent.completed", agent="world", proposal_count=0, verdicts=[])
            return []
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        _trace(deps, "agent.error", agent="world", error=str(exc))
        return []
    verdicts: list[agency.Verdict] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        prop = agency.Proposal(
            actor=str(item.get("actor") or "").strip(),
            intent=str(item.get("intent") or "").strip(),
            difficulty=int(item.get("difficulty") or 50),
            min_affinity=float(item.get("min_affinity") or 0),
            basis=str(item.get("basis") or "").strip(),
            goal=str(item.get("goal") or "").strip(),
        )
        actor_affinity = deps.affinities.get(prop.actor, fallback_affinity)
        verdicts.append(agency.judge(prop, actor_affinity, rng=deps.rng))
    _trace(deps, "agent.completed", agent="world", proposal_count=len(verdicts),
           verdicts=[vars(v) for v in verdicts])
    return verdicts


def narrative_directive(verdicts: list[agency.Verdict]) -> str:
    """把有效自主尝试转成主叙事指令；成功落实，失败也必须呈现为未遂。

    intent 由 judge 从 Proposal 复制到 Verdict，此处直接取用；空 intent 落「自主行动」兜底。
    """
    attempted = [v for v in verdicts if v.roll > 0 and v.intent]
    if not attempted:
        return ""
    lines = []
    for verdict in attempted:
        intent = verdict.intent or "自主行动"
        goal = f"；持续目标：{verdict.goal}" if verdict.goal else ""
        if verdict.outcome == agency.OUTCOME_ACCEPT:
            result = f"按{agency.DEGREE_LABEL[verdict.degree]}落实"
        else:
            result = f"已尝试但按{agency.DEGREE_LABEL[verdict.degree]}处理，必须写成未遂或受挫"
        lines.append(f"- {verdict.actor}：{intent}{goal}；{result}")
    return ("\n\n【本轮 NPC 自主目标与行动（不由用户驱动，必须在本轮叙事中体现）】\n"
            + "\n".join(lines))


def agency_lost(verdicts: list[agency.Verdict]) -> bool:
    """是否有配角强得手（大成功/极难/困难成功）→ 用户短期失控，插画阶段当高潮点。
    普通成功(partial)只算基本达成，不触发失控。"""
    strong = {agency.DEGREE_CRIT, agency.DEGREE_HARD, agency.DEGREE_FULL}
    return any(v.outcome == agency.OUTCOME_ACCEPT and v.degree in strong
              for v in verdicts)


# ── 阶段 C：状态写回（搭车解析，0 额外 LLM）──

def writeback(
    deps: AgencyDeps, *, repo_id: str, card_name: str, raw_deltas: list[Any], turn: int,
    snapshot: str = "",
) -> tuple[float | None, float | None]:
    """把搭车解析出的 delta 应用并落盘，并存本轮 <status> 快照。返回（写回前好感度, 写回后好感度）。

    无 repo_id/card_name → 直接读当前值返回（before==after，不触发跨档）。
    snapshot 非空即刷新快照（抗压缩）；delta/快照有一个变化就落盘。
    """
    st = character_state.load_state(deps.state_base, repo_id, card_name)
    before = _affinity(st)
    deltas = character_state.parse_deltas(
        raw_deltas, turn=turn, source=character_state.SRC_AUTO, card_name=card_name,
        existing_state=st,
    )
    dirty = False
    if deltas:
        character_state.apply_deltas(st, deltas)
        dirty = True
    if snapshot.strip():
        st.快照 = character_state.Snapshot(snapshot.strip(), turn)
        dirty = True
    if dirty:
        character_state.save_state(deps.state_base, st)
    after = _affinity(st)
    return before, after


def _affinity(st: character_state.CharacterState) -> float | None:
    nf = st.数值.get(AFFINITY_FIELD)
    return nf.value if nf else None


def _affinities(st: character_state.CharacterState) -> dict[str, float]:
    """读取多角色好感度；`角色名·好感度` 归属到对应 actor。"""
    result: dict[str, float] = {}
    suffix = f"·{AFFINITY_FIELD}"
    for leaf, numeric in st.数值.items():
        if leaf.endswith(suffix):
            actor = leaf[:-len(suffix)].strip()
            if actor:
                result[actor] = numeric.value
    return result


def _narr(st: character_state.CharacterState, leaf: str) -> str:
    """读某叙事字段当前值（供插画取 state 的衣着/所在）。无则空串。"""
    sf = st.叙事.get(leaf)
    return sf.value if sf else ""


# ── 阶段 D：插画（门控，通常跳过）──

def maybe_illustrate(
    deps: AgencyDeps, *, paragraph: str, appearance: str, wardrobe: str, locale: str,
    actors: list[str], before: float | None, after: float | None,
    turn: int, cadence: int, explicit: bool, lost: bool,
    scene: str = "", prompt_override: str = "", character_encounter: bool = False,
) -> dict | None:
    """按规则判该不该配图，命中则组装 SceneRequest 交 renderer 出图。返回 {url,caption,reason} 或 None。

    无 renderer 或触发不命中 → None。出图失败吞掉返回 None（配图是增强，不该阻断叙述）。
    只返回 url+caption，**绝不把图回灌进对话历史**（token 护栏）。
    scene：场景标签（nsfw/climax 触发配图，P2 分类器产出）。
    prompt_override：caller 用当前正文与视觉锚本地组装并清洗的提示词；非空则替代裸拼接。
    """
    if deps.renderer is None:
        return None
    trig = scene_illustration.decide_trigger(
        explicit=explicit, agency_lost=lost,
        tier_before=before, tier_after=after, thresholds=deps.thresholds,
        turn=turn, cadence=cadence, scene=scene,
        character_encounter=character_encounter)
    if not trig.fire:
        return None
    if prompt_override.strip():
        req = SceneRequest(prompt=prompt_override.strip(), actors=list(actors), reason=trig.reason)
    else:
        req = scene_illustration.build_scene_request(
            paragraph=paragraph, appearance=appearance, wardrobe=wardrobe, locale=locale,
            actors=actors, reason=trig.reason)
    if not req.prompt.strip():
        return None
    try:
        url = deps.renderer(req)
    except Exception:  # noqa: BLE001  配图失败不阻断叙述
        return None
    return {"url": url, "caption": f"[{trig.reason}] {req.prompt[:60]}", "reason": trig.reason}


# ── 纪要记忆（Phase C）：召回（0 LLM）+ 门控抽取&压缩（每 N 轮一次额外 LLM）──

def recall_chronicle(deps: AgencyDeps, *, repo_id: str, query: str, k: int = 10,
                     rag_text: str = "", actors: list[str] | None = None) -> str:
    """按检索词召回往事纪要与 RAG 命中，组装为主 Roleplay 请求的候选记忆块。

    本函数 0 LLM，不生成、精选或改写候选；候选与 GrayWill 预设、世界书、对话历史、
    本轮输入合并后，只由主模型一次生成。只读注入不回灌历史（token 护栏）。
    FTS5 trigram 旁路召回，与 Chroma 语义检索（含检索表行）互补。
    actors：当前出场人物，召回排序先人物名相关、再按时间新→旧。
    rag_text：调用方预先从 rag_store 召回的相关条目（知识库 + 检索表行），拼在纪要之后一起注入。
    """
    if not (repo_id and query.strip()):
        return rag_text.strip()
    try:
        # 上下文合同·记忆召回：召回源=纪要表，注入只取简要内容（render_recall 用 overview），
        # 排序先人物名相关、再按时间新→旧，取 Top-k。
        hits = narrative_store.recall(deps.state_base, repo_id, query, k=max(k * 3, 30))
        recent = narrative_store.recent(deps.state_base, repo_id, k=50)
        hits = narrative_memory.select_by_relevance(
            hits, recent, actors=list(actors or []), k=k,
        )
    except Exception:  # noqa: BLE001  召回失败不阻断叙述
        hits = []
    chronicle = narrative_memory.render_recall(hits)
    if rag_text.strip():
        block = "【相关知识/表格条目（按剧情召回，供参考勿逐字复述）】\n" + rag_text.strip()
        return (chronicle + "\n\n" + block).strip() if chronicle else block
    return chronicle


# ── 条目维护 Agent（curator，门控 LLM，只增不改写 RAG 知识库）──

def maybe_curate(
    deps: AgencyDeps, *, window_text: str,
    chat_base: str, chat_key: str, chat_model: str, proxy: str = "",
    events: list | None = None,
) -> int:
    """gate 命中时从本轮剧情抽「值得长期留存的新知识」写入 RAG 知识库（经 index_fn）。返回写入条数。

    gate 关（curator_gate<=0）/无 index_fn/无内容/失败 → 0（不阻断叙述）。默认只增不改。
    events：可选事件收集器，真正触发写库时按 start/ok/fail 追加 RAG 创建状态（供前端弹窗）。
    """
    if deps.curator_gate <= 0 or (deps.index_fn is None and deps.worldbook_fn is None) or not window_text.strip():
        reason = "gate_disabled" if deps.curator_gate <= 0 else (
            "no_writer" if deps.index_fn is None and deps.worldbook_fn is None else "empty_window")
        _trace(deps, "agent.skipped", agent="curator", reason=reason)
        return 0
    if deps.rng.random() >= deps.curator_gate:
        _trace(deps, "agent.skipped", agent="curator", reason="gate_not_matched",
               gate=deps.curator_gate)
        return 0  # gate 未命中：本轮不创建，不打扰用户
    if events is not None:
        events.append({"kind": "curator", "state": "start"})  # 确实要抽取写库了
    try:
        _trace(deps, "agent.started", agent="curator")
        curator_system = deps.curator_system
        worldbook_context = (
            deps.worldbook_context_fn(window_text)
            if deps.worldbook_context_fn is not None else deps.worldbook_context
        )
        if worldbook_context:
            curator_system += "\n\n【当前小仓库世界书条目（index 用于更新）】\n" + worldbook_context
        _trace(deps, "model.request", agent="curator", model=chat_model,
               messages=[{"role": "system", "content": curator_system},
                          {"role": "user", "content": window_text}])
        raw = deps.chat_fn(chat_base, chat_key, chat_model, curator_system,
                           window_text, temperature=deps.curator_temperature, proxy=proxy,
                           **deps.curator_sampling)
        _trace(deps, "model.response", agent="curator", content=raw or "")
        m = re.search(r"\[[\s\S]*\]", raw or "")
        if not m:
            if events is not None:
                events.append({"kind": "curator", "state": "ok", "count": 0})
            _trace(deps, "agent.completed", agent="curator", extracted=[], written=0)
            return 0
        items = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        _trace(deps, "agent.error", agent="curator", error=str(exc))
        if events is not None:
            events.append({"kind": "curator", "state": "fail"})
        return 0
    except Exception as exc:  # noqa: BLE001
        _trace(deps, "agent.error", agent="curator", error=str(exc))
        if events is not None:
            events.append({"kind": "curator", "state": "fail"})
        return 0
    written = 0
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        op = str(it.get("op") or "add").strip()
        if op in ("worldbook_add", "worldbook_update"):
            continue
        if op != "add" or deps.index_fn is None:
            continue
        text = str(it.get("text") or "").strip()
        title = str(it.get("title") or "").strip()
        if not text:
            continue
        try:
            deps.index_fn(text, title)
            written += 1
        except Exception:  # noqa: BLE001  单条写入失败不阻断其余
            continue
    worldbook_written = 0
    if deps.worldbook_fn is not None:
        wb_ops = [it for it in items if isinstance(it, dict)
                  and str(it.get("op") or "").strip() in ("worldbook_add", "worldbook_update")]
        try:
            worldbook_written = deps.worldbook_fn(wb_ops)
            _trace(deps, "worldbook.writeback", ops=wb_ops, applied=worldbook_written)
        except Exception:  # noqa: BLE001  世界书维护失败不阻断正文/RAG
            worldbook_written = 0
            _trace(deps, "worldbook.writeback", ops=wb_ops, applied=0, status="error")
    written += worldbook_written
    if events is not None:
        events.append({"kind": "curator", "state": "ok", "count": written})
    _trace(deps, "agent.completed", agent="curator", extracted=items, written=written,
           worldbook_written=worldbook_written)
    return written


def maybe_summarize(
    deps: AgencyDeps, *, repo_id: str, card_name: str, window_text: str, turn: int,
    chat_base: str, chat_key: str, chat_model: str, proxy: str = "",
    cadence: int = narrative_memory.CADENCE,
    events: list | None = None,
) -> bool:
    """门控命中的回合抽一条独立纪要落盘（搭 1 次额外 LLM）。返回是否落了新纪要。

    - 未到 cadence → 直接 False（多数回合零成本，对治痛点3 等待长）。
    - 抽取失败/空 → False，旧纪要不动（对治痛点2 填表失败丢上下文）。
    - 落盘后推进 last_turn；每个频率区间永久保留一条独立 layer0 纪要。
    events：可选事件收集器，到 cadence 真正抽纪要时按 start/ok/fail 追加状态（供前端弹窗）。
    """
    if not (repo_id and window_text.strip()):
        _trace(deps, "agent.skipped", agent="chronicle", reason="missing_context")
        return False
    try:
        last = narrative_store.get_last_turn(deps.state_base, repo_id, card_name)
        if turn - last > cadence:
            _trace(deps, "agent.skipped", agent="chronicle",
                   reason="manual_backfill_required", last_turn=last,
                   turn=turn, cadence=cadence)
            return False
        if not narrative_memory.should_summarize(last, turn, cadence=cadence):
            _trace(deps, "agent.skipped", agent="chronicle", reason="cadence_not_reached",
                   last_turn=last, turn=turn, cadence=cadence)
            return False  # 未到轮次：本轮不创建纪要，不打扰用户
        if events is not None:
            events.append({"kind": "chronicle", "state": "start"})
        summary_user = narrative_memory.build_summary_user(window_text)
        _trace(deps, "agent.started", agent="chronicle")
        _trace(deps, "model.request", agent="chronicle", model=chat_model,
               messages=[{"role": "system", "content": narrative_memory.SUMMARY_SYSTEM},
                         {"role": "user", "content": summary_user}])
        raw = deps.chat_fn(
            chat_base, chat_key, chat_model,
            narrative_memory.SUMMARY_SYSTEM,
            summary_user,
            temperature=0.3, proxy=proxy)
        _trace(deps, "model.response", agent="chronicle", content=raw or "")
        entry = narrative_memory.parse_rich_summary(
            raw or "", turn_start=last + 1, turn_end=turn,
        )
        # 字数门槛（概览≤30字/正文≤300字）：填表 prompt 已写明前提，模型仍超写时
        # 压缩改写一次（不机械截断）；仍超限视为抽取失败，旧纪要不动。
        if entry is not None and not narrative_memory.chronicle_within_limits(
                entry.overview, entry.text):
            original_facts = list(entry.facts)
            _trace(deps, "agent.compress", agent="chronicle",
                   overview_chars=len(entry.overview), detail_chars=len(entry.text))
            compressed = deps.chat_fn(
                chat_base, chat_key, chat_model,
                narrative_memory.COMPRESS_SYSTEM,
                narrative_memory.build_compress_user(entry.overview, entry.text),
                temperature=0.3, proxy=proxy)
            entry = narrative_memory.parse_rich_summary(
                compressed or "", turn_start=last + 1, turn_end=turn,
            )
            if entry is not None and not entry.facts and original_facts:
                entry.facts = original_facts  # 事实账本素材不因压缩丢失
        if entry is None or not narrative_memory.chronicle_within_limits(
                entry.overview, entry.text):
            if events is not None:
                events.append({"kind": "chronicle", "state": "fail"})
            _trace(deps, "agent.completed", agent="chronicle", written=False,
                   reason="empty_summary_or_over_limit")
            return False
        narrative_store.append(deps.state_base, repo_id, entry)
        if entry.facts:
            from app.services import temporal_fact_store

            fact_count = 0
            for fact in entry.facts:
                try:
                    temporal_fact_store.record(
                        deps.state_base, repo_id,
                        subject=str(fact.get("subject") or ""),
                        predicate=str(fact.get("predicate") or ""),
                        object_=str(fact.get("object") or ""),
                        valid_from_turn=turn,
                        evidence=str(fact.get("evidence") or ""),
                        source="chronicle",
                    )
                    fact_count += 1
                except ValueError:
                    continue
            _trace(deps, "temporal.write", source="chronicle", count=fact_count)
        narrative_store.set_last_turn(deps.state_base, repo_id, card_name, turn)
        if events is not None:
            events.append({"kind": "chronicle", "state": "ok", "count": 1})
        _trace(deps, "rag.write", source="chronicle", content=entry.text,
               overview=entry.overview, keywords=entry.keywords,
               turn_start=last + 1, turn_end=turn, layer=0)
        _trace(deps, "agent.completed", agent="chronicle", written=True,
                content=entry.text, overview=entry.overview, keywords=entry.keywords)
        return True
    except Exception as exc:  # noqa: BLE001  抽取失败不阻断叙述
        _trace(deps, "agent.error", agent="chronicle", error=str(exc))
        if events is not None:
            events.append({"kind": "chronicle", "state": "fail"})
        return False


def _compact_layers(
    deps: AgencyDeps, *, repo_id: str,
    chat_base: str, chat_key: str, chat_model: str, proxy: str = "",
) -> None:
    """把超上限的层压成上一层：吃掉最旧 COMPACT_BATCH 条 → LLM 归并成一条上层纪要 → 删旧插新。

    归并失败（坏 JSON/空）则跳过本次压缩，旧条保留（宁可暂时超上限也不丢事件）。
    """
    for layer in range(narrative_memory.MAX_LAYER):
        n = narrative_store.count(deps.state_base, repo_id, layer=layer)
        if not narrative_memory.should_compact(layer, n):
            continue
        olds = narrative_store.oldest(
            deps.state_base, repo_id, k=narrative_memory.COMPACT_BATCH, layer=layer)
        if len(olds) < 2:
            continue
        compact_user = narrative_memory.build_compact_user(olds)
        _trace(deps, "agent.started", agent="chronicle_compact", layer=layer)
        _trace(deps, "model.request", agent="chronicle_compact", model=chat_model,
               messages=[{"role": "system", "content": narrative_memory.COMPACT_SYSTEM},
                         {"role": "user", "content": compact_user}])
        raw = deps.chat_fn(
            chat_base, chat_key, chat_model,
            narrative_memory.COMPACT_SYSTEM,
            compact_user,
            temperature=0.3, proxy=proxy)
        _trace(deps, "model.response", agent="chronicle_compact", content=raw or "")
        entry = narrative_memory.parse_rich_summary(raw or "")
        # 归并产出同样守字数门槛：超写压缩改写一次，仍超限则跳过本次压缩（旧条保留）
        if entry is not None and not narrative_memory.chronicle_within_limits(
                entry.overview, entry.text):
            _trace(deps, "agent.compress", agent="chronicle_compact",
                   overview_chars=len(entry.overview), detail_chars=len(entry.text))
            compressed = deps.chat_fn(
                chat_base, chat_key, chat_model,
                narrative_memory.COMPRESS_SYSTEM,
                narrative_memory.build_compress_user(entry.overview, entry.text),
                temperature=0.3, proxy=proxy)
            entry = narrative_memory.parse_rich_summary(compressed or "")
        if entry is None or not narrative_memory.chronicle_within_limits(
                entry.overview, entry.text):
            _trace(deps, "agent.completed", agent="chronicle_compact", written=False,
                   reason="empty_summary_or_over_limit", layer=layer)
            continue
        merged = narrative_memory.ChronicleEntry(
            text=entry.text, overview=entry.overview, keywords=entry.keywords,
            turn_start=olds[0].turn_start, turn_end=olds[-1].turn_end,
            layer=layer + 1)
        narrative_store.append(deps.state_base, repo_id, merged)
        narrative_store.delete_rows(deps.state_base, repo_id, [e.rowid for e in olds])
        _trace(deps, "rag.write", source="chronicle_compact", content=merged.text,
               keywords=merged.keywords, turn_start=merged.turn_start, turn_end=merged.turn_end,
               layer=layer + 1, replaced_rowids=[e.rowid for e in olds])
        _trace(deps, "agent.completed", agent="chronicle_compact", written=True,
               layer=layer + 1, content=merged.text, keywords=merged.keywords)
