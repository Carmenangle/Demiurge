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

import json
import re
from dataclasses import dataclass, field

CADENCE = 3              # 每 N 个 assistant 回合抽一次纪要（默认对齐剧情表每三条一统计）
LAYER0_CAP = 8          # layer0（细）条数上限，超出把最旧 COMPACT_BATCH 条压成一条 layer1
LAYER1_CAP = 8          # layer1（中）条数上限，超出压成一条 layer2（世界观级大纲）
COMPACT_BATCH = 4       # 每次压缩吃掉的旧条数
MAX_LAYER = 2           # 分层封顶：0 细 / 1 中 / 2 粗，不再往上压
_SUMMARY_MAX = 1200     # 详细纪要软上限；提交主 Agent 时只取短概览，不回灌详情
_OVERVIEW_MAX = 120
_DIALOGUE_MAX = 500
_GRAM_CAP = 48          # trigram 召回查询最多取多少个 3-gram，防 MATCH 串过长


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
    "{\"overview\":\"一句到三句概览，不超过120字\","
    "\"chronicle\":\"详细纪要，完整说明这三轮发生的事情与变化\","
    "\"dialogue\":\"重要对白原文，没有则空串\","
    "\"characters\":[\"实际出场人物\"],\"keywords\":[\"人物\",\"地点\",\"事件\"]}。"
    "只输出 JSON。"
)


def build_summary_user(window_text: str) -> str:
    """抽取 prompt 的 user 部分：喂近 N 轮对话窗口。"""
    return f"【近期剧情片段】\n{window_text}\n\n请压缩成一条事件纪要 JSON。"


def parse_summary(raw: str) -> tuple[str, list[str]]:
    """解析抽取 LLM 的 JSON 产出 → (纪要正文, 关键词列表)。

    坏 JSON / 空 summary → ("", [])（上层据此跳过落盘，旧纪要不动，对治痛点2）。
    """
    m = re.search(r"\{[\s\S]*\}", raw or "")
    if not m:
        return "", []
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return "", []
    if not isinstance(data, dict):
        return "", []
    text = str(data.get("summary") or "").strip()[:_SUMMARY_MAX]
    if not text:
        return "", []
    kws_raw = data.get("keywords")
    keywords: list[str] = []
    if isinstance(kws_raw, list):
        for k in kws_raw:
            s = str(k).strip()
            if s and s not in keywords:
                keywords.append(s)
    return text, keywords[:12]


def parse_rich_summary(raw: str, *, turn_start: int = 0,
                       turn_end: int = 0) -> ChronicleEntry | None:
    """解析丰富纪要；兼容旧 summary 结构。"""
    m = re.search(r"\{[\s\S]*\}", raw or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    overview = str(data.get("overview") or data.get("summary") or "").strip()[:_OVERVIEW_MAX]
    detail = str(data.get("chronicle") or data.get("summary") or overview).strip()[:_SUMMARY_MAX]
    if not (overview and detail):
        return None

    def unique_list(key: str, limit: int) -> list[str]:
        raw_items = data.get(key)
        result: list[str] = []
        if isinstance(raw_items, list):
            for item in raw_items:
                value = str(item).strip()
                if value and value not in result:
                    result.append(value)
        return result[:limit]

    return ChronicleEntry(
        text=detail, overview=overview,
        dialogue=str(data.get("dialogue") or "").strip()[:_DIALOGUE_MAX],
        characters=unique_list("characters", 12), keywords=unique_list("keywords", 16),
        turn_start=turn_start, turn_end=turn_end,
    )


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
    "{\"summary\":\"归并后的梗概\",\"keywords\":[\"关键词\"]}。summary 不超过 120 字。只输出 JSON。"
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
    """
    if not entries:
        return ""
    ordered = sorted(entries, key=lambda e: (e.turn_start, e.turn_end))
    lines = [f"- {e.short_overview()}" for e in ordered]
    return "【前情提要（相关人物最近事件，仅供保持连贯，勿逐字复述）】\n" + "\n".join(lines)


def select_relevant_recent(entries: list[ChronicleEntry], actors: list[str], *,
                           k: int = 10) -> list[ChronicleEntry]:
    """优先当前出场人物相关纪要，再按时间取最近 k 条。"""
    names = {name.strip() for name in actors if name.strip()}
    relevant = [
        entry for entry in entries
        if names.intersection(entry.characters)
        or any(name in entry.body() for name in names)
    ] if names else []
    pool = relevant or entries
    return sorted(pool, key=lambda entry: (entry.turn_end, entry.rowid), reverse=True)[:max(0, k)]
