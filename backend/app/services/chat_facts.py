"""正常对话的长期事实记忆（M3，2026-09-06 用户定案）。

复用 P3② 指认式原语（检索→指认→收口/re-affirm），把非剧情对话线程里「值得长期
记住的事实」自动落进独立的时序事实库，下轮对话注入 system——「记忆自动收敛、
注入总是当前为真」，且与剧情事实库物理隔离。

边界（用户定案 + 成本先例）：
- 落盘 `<DATA_DIR>/chat_facts/<thread_id>/temporal_facts.db`，repo_id=thread_id，
  与作品事实库（output_dir）互不污染；
- 门控：每 CADENCE=4 回合抽一次（对齐 curator 成本杠杆 L3-A：逐轮跑纯烧钱）；
- 只挂 answer 路由（roleplay 有自己的纪要/curator 链）；
- NSFW/拦截/失败 → 静默跳过（不阻断正文；最坏=不收敛，绝不产生错误记忆）；
- 无 DELETE：消失写成新事实；指认式 UPDATE 同 P3②（降级普通 ADD）。
"""
from __future__ import annotations

from app.config import DATA_DIR
from app.services import narrative_memory, temporal_fact_store

CADENCE = 4              # 每 N 回合抽一次
MAX_KNOWN_FACTS = 40     # 喂给抽取模型的现存事实清单上限（同 P3②）
MAX_WINDOW_CHARS = 6000  # 抽取窗口字符上限（防长对话撑爆 prompt）

EXTRACT_SYSTEM = (
    "你是对话记忆员。从这段日常对话里挑出【值得长期记住的事实】——用户的稳定偏好、"
    "自我介绍信息、项目/作品的持久设定、明确承诺或决定。不记寒暄、情绪、一次性问答。"
    "输出 JSON：{\"facts\":[{\"subject\":\"实体\",\"predicate\":\"属性/关系\","
    "\"object\":\"值\",\"evidence\":\"对话原话片段\","
    "\"supersedes_id\":\"<现存事实清单里被本条更新的 id 前缀，没有则空串>\"}]}。"
    "facts 可为空数组（没有值得记的就空着）。只允许引用清单里真实存在的 id，绝不编造。"
    "只输出 JSON。"
)


def facts_base() -> str:
    """正常对话事实库落盘根：`<DATA_DIR>/chat_facts/`。"""
    return str(DATA_DIR / "chat_facts")


def render_facts_block(thread_id: str, turn: int, *, limit: int = 20) -> str:
    """注入块：当前有效事实（M2 同款 recency-first，预算压力下最新事实存活）。
    无库/无事实/异常 → 空串（记忆缺失不阻断对话）。"""
    try:
        facts = temporal_fact_store.as_of(
            facts_base(), thread_id, turn, recency_first=True)[:limit]
    except Exception:  # noqa: BLE001 - 记忆注入永不阻断对话
        return ""
    lines = [
        f"- {f.get('subject', '')}｜{f.get('predicate', '')}｜{f.get('object', '')}"
        for f in facts if f.get("subject") and f.get("predicate") and f.get("object")
    ]
    if not lines:
        return ""
    return ("【已记住的长期事实（来自往期对话，供保持连贯；与当前发言冲突时以当前发言为准）】\n"
            + "\n".join(lines))


def maybe_extract(chat_fn, thread_id: str, *, window_text: str, turn: int,
                  chat_base: str, chat_key: str, chat_model: str,
                  proxy: str = "", cadence: int = CADENCE) -> bool:
    """门控抽取：到 cadence 的回合才搭一次额外 LLM。返回是否落了新事实。

    抽取失败/解析失败/无事实 → False 静默跳过（旧记忆不动，同纪要抽取防线）。
    """
    if not (thread_id and window_text.strip()):
        return False
    if cadence < 1 or turn % cadence != 0:
        return False
    try:
        known = temporal_fact_store.as_of(
            facts_base(), thread_id, turn, recency_first=True)[:MAX_KNOWN_FACTS]
        user = narrative_memory.build_summary_user(
            window_text[-MAX_WINDOW_CHARS:], known)
        raw = chat_fn(chat_base, chat_key, chat_model, EXTRACT_SYSTEM, user,
                      temperature=0.2, proxy=proxy)
        payload = narrative_memory.structured_output.parse_model(
            raw or "", narrative_memory.RichChronicle)
        written = 0
        for fact in payload.facts[:12]:
            ref = str(fact.supersedes_id or "").strip()
            supersedes = (temporal_fact_store.resolve_supersedes(
                facts_base(), thread_id, ref) if ref else None)
            try:
                temporal_fact_store.record(
                    facts_base(), thread_id,
                    subject=fact.subject.strip(), predicate=fact.predicate.strip(),
                    object_=fact.object.strip(), valid_from_turn=turn,
                    evidence=fact.evidence.strip(), source="chat",
                    supersedes_id=supersedes,
                )
                written += 1
            except ValueError as exc:
                if supersedes is None:
                    continue  # 非法字段/角色状态谓词越界：跳过该条
                # 指认失败 → 降级普通 ADD（信息不丢，矛盾交诊断）
                if "supersedes" in str(exc):
                    try:
                        temporal_fact_store.record(
                            facts_base(), thread_id,
                            subject=fact.subject.strip(), predicate=fact.predicate.strip(),
                            object_=fact.object.strip(), valid_from_turn=turn,
                            evidence=fact.evidence.strip(), source="chat",
                        )
                        written += 1
                    except ValueError:
                        continue
        return written > 0
    except Exception:  # noqa: BLE001 - 抽取失败静默跳过，绝不阻断对话
        return False
