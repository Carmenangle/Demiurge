"""叙事纪要记忆·纯逻辑（0 I/O 0 LLM 全单测）：Phase C「表格记忆」的事件叙事支路。

设计见 ARCHITECTURE.md「剧情能动性引擎」支柱 1 的分工与「叙事 vs 角色条目」定论：
- 角色条目（`character_state`）记「结构化活状态」——好感度数值+态度/心情/所在，每轮读进上下文、确定性可写。
- 纪要（本模块 + `narrative_store`）记「事件叙事」——AM 码大纲，**只增不改**，按相关性 recall，
  绝不全量回灌。解原酒馆三痛点：①token 高→纪要只增量抽取不重填整表；②填表失败丢上下文→
  抽取失败跳过、旧纪要不动；③等待长→抽取按 N 轮门控搭一次额外 LLM，多数回合零成本。

本模块只管**纯逻辑**：门控判定 / 抽取&压缩 prompt / LLM 产出解析 / 分层晋升判定 /
中文无分词的 trigram 召回查询构造。I/O（SQLite FTS5 落盘+召回）在 `narrative_store`，
它 import 本模块的 `ChronicleEntry` 类型；本模块**不反向 import**（narrative-memory-purity 合同强制）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services import structured_output
from app.services.structured_contracts import RichChronicle

CADENCE = 3              # 每 N 个 assistant 回合抽一次纪要（默认对齐剧情表每三条一统计）
LAYER0_CAP = 8          # layer0（细）条数上限，超出把最旧 COMPACT_BATCH 条压成一条 layer1
LAYER1_CAP = 8          # layer1（中）条数上限，超出压成一条 layer2（世界观级大纲）
COMPACT_BATCH = 4       # 每次压缩吃掉的旧条数
MAX_LAYER = 2           # 分层封顶：0 细 / 1 中 / 2 粗，不再往上压
_SUMMARY_MAX = 300      # 详细纪要软上限（用户定稿：300 字内）；提交主 Agent 时只取短概览，不回灌详情
_OVERVIEW_MAX = 30      # 简要概览软上限（用户定稿：30 字内）
_DIALOGUE_MAX = 500
_GRAM_CAP = 48          # trigram 召回查询最多取多少个 3-gram，防 MATCH 串过长
RECENCY_HALF_LIFE_TURNS = 30.0  # P2 召回 recency 半衰期（回合）：每过 30 回合时间权重减半
LAYER_IMPORTANCE_STEP = 0.25    # P2 重要性加成：每升一层（细→中→粗）权重 +0.25，layer2 = 1.5
_RECALL_RELEVANCE_BOOST = 2.0   # P2 人物命中乘法加成：保持「人物相关优先」合同（组内仍按加权分排序）


@dataclass
class ChronicleEntry:
    """一条事件纪要（AM 码大纲）。只增不改，按相关性召回。

    layer: 0 细（每 CADENCE 轮一条）/ 1 中（压缩）/ 2 粗（世界观级）。
    turn_start/turn_end: 覆盖的剧情回合区间，供渲染时标时序。
    keywords: 抽取出的关键词，并进 body 一起 trigram 索引，提升召回命中。
    """
    text: str
    turn_start: int = 0
    turn_end: int = 0
    layer: int = 0
    keywords: list[str] = field(default_factory=list)
    rowid: int = 0         # 由 store 落盘后回填，纯逻辑构造时为 0
    overview: str = ""     # 给主 Roleplay 注入的短概览
    dialogue: str = ""     # 本段关键对白（资产表展示，不进主上下文）
    characters: list[str] = field(default_factory=list)
    facts: list[dict[str, str]] = field(default_factory=list)

    def card_id(self) -> str:
        """稳定展示编号：T<层级>-<落盘序号>，不随回合区间或内容编辑变化。"""
        return f"T{self.layer + 1}-{self.rowid}" if self.rowid > 0 else f"T{self.layer + 1}-new"

    def body(self) -> str:
        """trigram 索引正文 = 纪要正文 + 关键词（关键词重复进正文，加权召回）。"""
        extras = " ".join([self.overview, *self.characters, *self.keywords]).strip()
        return (self.text + " " + extras).strip() if extras else self.text

    def short_overview(self) -> str:
        return self.overview.strip() or self.text.strip()[:_OVERVIEW_MAX]


def should_summarize(last_summarized_turn: int, current_turn: int, *,
                     cadence: int = CADENCE) -> bool:
    """是否到了抽纪要的回合：距上次抽取已过 ≥cadence 个回合。

    首次（last=0）也要等攒够 cadence 个回合才抽，避免开局稀薄内容硬抽。
    """
    if cadence < 1:
        return False
    return current_turn - last_summarized_turn >= cadence


# ── 抽取（阶段：搭 B 之后一次额外 LLM，仅在门控命中的回合）──

SUMMARY_SYSTEM = (
    "你是剧情纪要员。把给定的最近三轮角色扮演整理成一条内容充实的事件纪要。"
    "保留关键事件、因果、人物行动、关系与局势变化，不写状态数值。输出 JSON："
    "{\"overview\":\"一句概览，不超过30字\","
    "\"chronicle\":\"详细纪要，完整说明这三轮发生的事情与变化，不超过300字\","
    "\"dialogue\":\"重要对白原文，没有则空串\","
    "\"characters\":[\"实际出场人物\"],\"keywords\":[\"人物\",\"地点\",\"事件\"],"
    "\"facts\":[{\"subject\":\"实体\",\"predicate\":\"关系/世界属性\","
    "\"object\":\"值\",\"evidence\":\"剧情中的直接证据\"}]}。"
    "facts 只写可长期查询的世界/实体事实，不写好感、态度、心情、所在、身体或衣着状态；"
    "没有则空数组。"
        "若本次剧情使某条既有事实过期（同一实体同一属性出现新状态），给该条 fact 附"
    " \"supersedes_id\":\"<现存事实清单里的 id 前缀>\"——旧事实会收口但时间线保留；"
    "只允许引用清单里真实存在的 id，没有对应项或不确定就留空串，绝不编造；"
    "实体已消亡也写成一条新事实（如 状态=已覆灭），不存在删除。"
"只输出 JSON。"
)


def build_summary_user(window_text: str, known_facts: list[dict] | None = None) -> str:
    """抽取 prompt 的 user 部分：近 N 轮对话窗口 + 现存事实清单（P3② 指认式替代）。

    known_facts 是当前有效事实（temporal_fact_store.as_of 产物，超量由调用方截断）；
    每条渲染为 `#id前12位 subject·predicate=object（第X回合起）`，供模型在 supersedes_id
    里指认引用。清单为空/None 不输出该段。
    """
    parts = [f"【近期剧情片段】\n{window_text}"]
    if known_facts:
        items = [
            f"#{str(f.get('id') or '')[:12]} {f.get('subject')}·{f.get('predicate')}"
            f"={f.get('object')}（第{f.get('valid_from_turn')}回合起）"
            for f in known_facts if f.get("id")
        ]
        if items:
            parts.append("【现存事实（当前有效；本次剧情使其过期的，在新事实 supersedes_id 引用其 id 前缀）】\n"
                         + "\n".join(items))
    return "\n".join(parts) + "\n\n请压缩成一条事件纪要 JSON。"


def parse_rich_summary(raw: str, *, turn_start: int = 0,
                       turn_end: int = 0) -> ChronicleEntry | None:
    """解析丰富纪要；兼容旧 summary 结构。如实解析，不机械截断（字数门槛见 chronicle_within_limits）。"""
    try:
        payload = structured_output.parse_model(raw, RichChronicle)
    except structured_output.StructuredOutputError:
        return None
    overview = (payload.overview or payload.summary).strip()
    detail = (payload.chronicle or payload.summary or overview).strip()
    if not (overview and detail):
        return None

    def unique_list(raw_items: list[str], limit: int) -> list[str]:
        result: list[str] = []
        for item in raw_items:
            value = str(item).strip()
            if value and value not in result:
                result.append(value)
        return result[:limit]

    return ChronicleEntry(
        text=detail, overview=overview,
        dialogue=payload.dialogue.strip()[:_DIALOGUE_MAX],
        characters=unique_list(payload.characters, 12), keywords=unique_list(payload.keywords, 16),
        facts=[fact.model_dump() for fact in payload.facts[:12]],
        turn_start=turn_start, turn_end=turn_end,
    )


# ── 字数门槛（用户定稿：概览≤30字、详细纪要≤300字）：prompt 写明前提，超写压缩改写，不机械截断 ──

def chronicle_within_limits(overview: str, detail: str) -> bool:
    """纪要落盘硬门槛：概览/正文必须落在字数上限内。

    超限时调用方必须先走 LLM 压缩改写（COMPRESS_SYSTEM），仍超限则拒绝落盘；
    任何环节都不得机械截断（截断会留下半句话，破坏纪要可读性）。
    """
    return len(overview.strip()) <= _OVERVIEW_MAX and len(detail.strip()) <= _SUMMARY_MAX


COMPRESS_SYSTEM = (
    "你是剧情纪要压缩员。把给定的超字数纪要改写到上限内："
    "overview 一句概览不超过30字；chronicle 详细纪要不超过300字。"
    "保留关键事件、因果、人物行动与局势变化，删细节不删脉络。输出 JSON："
    "{\"overview\":\"…\",\"chronicle\":\"…\",\"dialogue\":\"重要对白原文，没有则空串\","
    "\"characters\":[\"实际出场人物\"],\"keywords\":[\"人物\",\"地点\",\"事件\"],\"facts\":[]}。"
    "只输出 JSON。"
)


def build_compress_user(overview: str, detail: str) -> str:
    """压缩 prompt 的 user 部分：给出超限原文与当前字数。"""
    return (f"【超限纪要】\noverview（现{len(overview.strip())}字）：{overview.strip()}\n"
            f"chronicle（现{len(detail.strip())}字）：{detail.strip()}\n\n"
            "请压缩到字数上限内，输出纪要 JSON。")


# ── 分层压缩（把旧的细纪要压成粗纪要，防无限膨胀）──

def should_compact(layer: int, count: int) -> bool:
    """某层条数超上限即需压缩到上一层。layer ≥ MAX_LAYER 不再压。"""
    if layer >= MAX_LAYER:
        return False
    cap = LAYER0_CAP if layer == 0 else LAYER1_CAP
    return count > cap


COMPACT_SYSTEM = (
    "你是剧情纪要归并员。把给定的多条同层事件纪要归并成**一条**更粗的上层纪要："
    "保留主要事件脉络与关系变化，丢弃细节。输出 JSON："
    "{\"overview\":\"一句话概览，不超过30字\","
    "\"summary\":\"归并后的梗概，不超过300字\",\"keywords\":[\"关键词\"]}。只输出 JSON。"
)


def build_compact_user(entries: list[ChronicleEntry]) -> str:
    """压缩 prompt 的 user 部分：把待归并的多条纪要按序列出。"""
    lines = [f"- {e.text}" for e in entries]
    return "【待归并纪要】\n" + "\n".join(lines) + "\n\n请归并成一条上层纪要 JSON。"


# ── trigram 召回查询（中文无分词：取 3-gram 的 OR）──

def to_trigram_query(text: str) -> str:
    """把召回查询文本切成 3-gram 并组成 FTS5 MATCH 串（`"gram" OR "gram" …`）。

    trigram 分词器按 3 字滑窗匹配子串，语言无关、免中文分词依赖。空/过短 → 空串（上层跳过召回）。
    每个 gram 用双引号包成短语并去掉内部引号，避免 FTS5 语法注入/报错。
    """
    s = re.sub(r"\s+", "", text or "")
    if len(s) < 3:
        return ""
    grams: list[str] = []
    seen: set[str] = set()
    for i in range(len(s) - 2):
        g = s[i:i + 3].replace('"', "")
        if g and g not in seen:
            seen.add(g)
            grams.append(g)
        if len(grams) >= _GRAM_CAP:
            break
    if not grams:
        return ""
    return " OR ".join(f'"{g}"' for g in grams)


def render_recall(entries: list[ChronicleEntry]) -> str:
    """把召回到的纪要组装成注入块（供 roleplay 主控叙述参考往事）。空 → 空串。

    只读注入，不回灌进对话历史（token 护栏，同插画 url 处理）。按回合升序排，标时序。
    只注入简要内容（overview ≤30 字，落盘时已由字数门槛保证），注入侧不再截断。
    """
    if not entries:
        return ""
    ordered = sorted(entries, key=lambda e: (e.turn_start, e.turn_end))
    lines = [f"- {e.short_overview()}" for e in ordered]
    return "【前情提要（相关人物最近事件，仅供保持连贯，勿逐字复述）】\n" + "\n".join(lines)


def recency_weight(turn_end: int, reference_turn: int, *,
                   half_life: float = RECENCY_HALF_LIFE_TURNS) -> float:
    """回合差 → 时间衰减权重（(0,1]，P2 2026-09-06）。

    gap=0 → 1.0；每过 half_life 个回合权重减半（指数衰减：可预测、零依赖、纯函数）。
    时间基准用回合数而非墙钟——纪要只存 turn 区间与 rowid，没有时间戳。
    """
    if half_life <= 0:
        return 1.0
    gap = max(0, reference_turn - turn_end)
    return 0.5 ** (gap / half_life)


def recall_score(entry: ChronicleEntry, *, reference_turn: int,
                 actors: list[str] | None = None) -> float:
    """召回打分（P2 2026-09-06）：人物相关 × 重要性(layer) × 时效衰减。

    - importance：layer 越粗（世界观级归并）越重要——它是多条细节的压缩载体；
    - recency：指数衰减钳制重要性——重要性只能兜住「不太旧」的条目
      （半衰期 30 回合下，重要性 1.5 最多翻盘约 17.5 回合的回合差）；
    - 人物命中乘 2.0：保持既有「人物相关优先」合同。
    """
    importance = 1.0 + LAYER_IMPORTANCE_STEP * max(0, min(entry.layer, MAX_LAYER))
    names = {name.strip() for name in (actors or []) if name.strip()}
    relevance = 1.0
    if names and (names.intersection(entry.characters)
                  or any(name in entry.body() for name in names)):
        relevance = _RECALL_RELEVANCE_BOOST
    return relevance * importance * recency_weight(entry.turn_end, reference_turn)


def select_by_relevance(hits: list[ChronicleEntry], recent: list[ChronicleEntry], *,
                        actors: list[str] | None = None, k: int = 10) -> list[ChronicleEntry]:
    """按加权分取 Top-k 纪要（P2 2026-09-06）：人物名相关优先（乘 2.0 加成），
    组内按 重要性(layer) × 时效衰减 排序——不再是无衰减的纯时间序；
    同分并列回退 (turn_end, rowid) 降序。人物相关组与非相关组都不超过 k 条。

    hits（FTS bm25 召回）与 recent（最近纪要）合并去重后作为候选池；
    reference_turn 取候选池最大 turn_end（相对衰减，无需调用方传当前回合）。
    """
    names = {name.strip() for name in (actors or []) if name.strip()}
    pool: list[ChronicleEntry] = []
    seen: set[int] = set()
    for entry in [*hits, *recent]:
        if entry.rowid in seen:
            continue
        seen.add(entry.rowid)
        pool.append(entry)
    reference_turn = max((entry.turn_end for entry in pool), default=0)
    ordered = sorted(
        pool,
        key=lambda entry: (-recall_score(entry, reference_turn=reference_turn, actors=actors),
                           -entry.turn_end, -entry.rowid),
    )
    if names:
        relevant_ids = set()
        relevant = []
        for entry in ordered:
            if names.intersection(entry.characters) or any(
                    name in entry.body() for name in names):
                relevant.append(entry)
                relevant_ids.add(entry.rowid)
        ordered = relevant + [entry for entry in ordered if entry.rowid not in relevant_ids]
    return ordered[:max(0, k)]
