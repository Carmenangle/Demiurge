"""世界书检索：卡内嵌 character_book 条目按作品分组索引，两段式激活注入剧情扮演。

对标 SillyTavern World Info，但激活用 RAG 语义检索（而非关键词扫描）为主干：
- constant 常驻条目：每轮直接注入，不检索。
- 非常驻条目：索引进独立 collection `worldbook_<repo_id>`（与剧情文本/生图记录物理隔离，
  按作品分组防串设定），按「最近历史+本轮输入」语义 top-k 注入。
- token 预算封顶，超出截断。

依赖 rag_store 之上的 rag_backend/rag_retrieval（编排层），不反向依赖路由。
条目 schema（character_book 数组）：{keys[], content, constant, comment, enabled, ...}。
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from app.services import character_store, rag_backend, rag_retrieval
from app.services.rag_backend import EmbedConfig

_WB_MARK = "worldbook_hash"          # 旧版全量索引哨兵；增量同步时自动清理
_DEFAULT_K = 8                       # 非常驻条目语义检索条数（关键词命中额外叠加，见 assemble）
# 2026-09-04 成本杠杆（L1-B/L3-B 共用单点）：【世界设定（相关条目）】整段注入硬上限。
# 审计实测：单条 roleplay 请求里该段可达 23.6k 字符（≈15k+ token/轮，恒超模型单轮回复量级）。
# 上限 8000 后按优先级只裁末尾语义补充条目；配合 L1 灰魂栈瘦身把 roleplay 请求 43.7k→~28k。
WORLDBOOK_INJECT_MAX_CHARS = 8000
_INDEX_LOCK = threading.Lock()
_INDEXING: set[tuple[str, tuple[str, ...]]] = set()


def _collection(repo_id: str) -> str:
    import re
    rid = re.sub(r"[^a-zA-Z0-9_-]", "_", (repo_id or "home").strip()) or "home"
    return f"worldbook_{rid}"


@dataclass
class Entry:
    content: str
    constant: bool
    comment: str = ""
    keys: list[str] = None  # type: ignore[assignment]
    source_index: int = -1

    def __post_init__(self) -> None:
        if self.keys is None:
            self.keys = []


def parse_entries(book: dict[str, Any] | None) -> list[Entry]:
    """从 character_book 解析出启用的条目。disabled/空内容跳过。"""
    if not book:
        return []
    raw = book.get("entries")
    items = raw.values() if isinstance(raw, dict) else raw  # 兼容对象/数组两种格式
    out: list[Entry] = []
    for source_index, e in enumerate(items or []):
        if not isinstance(e, dict):
            continue
        # enabled 缺省视为 True；disable=True 明确关闭
        if e.get("enabled") is False or e.get("disable") is True:
            continue
        content = (e.get("content") or "").strip()
        if not content:
            continue
        keys = e.get("keys") or e.get("key") or []
        out.append(Entry(
            content=content,
            constant=bool(e.get("constant")),
            comment=(e.get("comment") or "").strip(),
            keys=[str(k) for k in keys if str(k).strip()] if isinstance(keys, list) else [],
            source_index=source_index,
        ))
    return out


def load_entries(character_dir: str, card_name: str) -> list[Entry]:
    """读作品关联卡的 worldbook.json → 条目列表。无卡/无书返回空。"""
    if not (character_dir and card_name):
        return []
    import pathlib
    p = pathlib.Path(character_store.card_dir(character_dir, card_name)) / character_store.WORLDBOOK_FILE
    if not p.is_file():
        return []
    try:
        book = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return parse_entries(book if isinstance(book, dict) else {"entries": book})


def load_standalone_entries(base: str, name: str) -> list[Entry]:
    """读独立世界书（worldbookDir 下的 <name>.json）→ 条目列表。无目录/无书返回空。

    与卡内嵌 load_entries 平行：供仓库绑定的独立世界书注入，二者可合并。
    """
    if not (base and name):
        return []
    try:
        from app.services import worldbook_store
        book = worldbook_store.read_book(base, name)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(book, dict):
        return []
    return parse_entries(book)


def ensure_indexed(repo_id: str, entries: list[Entry], cfg: EmbedConfig) -> bool:
    """增量同步 worldbook_<repo_id> 的非常驻条目索引。

    Curator 经常只修改一个角色条目；不得因整本书 hash 改变而清库重嵌。
    旧版随机 ID 按正文 hash 识别并保留，新增内容才调用嵌入，删除内容只删对应向量。
    """
    retrievable = [e for e in entries if not e.constant]
    try:
        store = rag_backend.store(_collection(repo_id), cfg)
        _existing, missing, stale_ids = _index_delta(store, retrievable)
        from langchain_core.documents import Document
        if missing:
            store.add_documents(
                [Document(page_content=entry.content,
                          metadata={"kind": "worldbook", "comment": entry.comment})
                 for entry in missing],
                ids=[f"wb_{_hid(entry.content)}" for entry in missing],
            )
        if stale_ids:
            store.delete(ids=list(dict.fromkeys(stale_ids)))
        return bool(missing or stale_ids)
    except Exception:  # noqa: BLE001
        return False


def _index_delta(store: Any, entries: list[Entry]) -> tuple[list[str], list[Entry], list[str]]:
    """只读计算已有正文哈希、待嵌入条目和待删除 ID。"""
    retrievable = [entry for entry in entries if not entry.constant]
    desired = {_hid(entry.content): entry for entry in retrievable}
    existing: dict[str, str] = {}
    stale_ids: list[str] = []
    data = store.get()
    for item_id, content, metadata in zip(
        data.get("ids", []) or [],
        data.get("documents", []) or [],
        data.get("metadatas", []) or [],
    ):
        kind = (metadata or {}).get("kind")
        if item_id == _WB_MARK or kind == "_wb_mark":
            stale_ids.append(item_id)
            continue
        if kind != "worldbook" or not (content or "").strip():
            continue
        content_hash = _hid(content)
        if content_hash not in desired or content_hash in existing:
            stale_ids.append(item_id)
            continue
        existing[content_hash] = item_id
    missing = [entry for content_hash, entry in desired.items() if content_hash not in existing]
    return list(existing), missing, stale_ids


def schedule_index(
    repo_id: str,
    entries: list[Entry],
    cfg: EmbedConfig,
    *,
    on_initial: Callable[[int], None] | None = None,
) -> bool:
    """后台增量同步索引；首次确有待嵌入条目时即时通知调用方。"""
    key = (repo_id, rag_backend.embedding_key(cfg))
    with _INDEX_LOCK:
        if key in _INDEXING:
            return False
        _INDEXING.add(key)

    try:
        store = rag_backend.store(_collection(repo_id), cfg)
        existing, missing, stale_ids = _index_delta(store, entries)
    except Exception:  # noqa: BLE001
        with _INDEX_LOCK:
            _INDEXING.discard(key)
        return False
    if not (missing or stale_ids):
        with _INDEX_LOCK:
            _INDEXING.discard(key)
        return False
    if not existing and missing and on_initial is not None:
        on_initial(len(missing))

    def run() -> None:
        try:
            ensure_indexed(repo_id, list(entries), cfg)
        finally:
            with _INDEX_LOCK:
                _INDEXING.discard(key)

    threading.Thread(
        target=run, name=f"worldbook-index-{repo_id[:8]}", daemon=True,
    ).start()
    return True


def _retrieve(repo_id: str, query: str, cfg: EmbedConfig, k: int) -> list[str]:
    """非常驻条目语义检索（dense + BM25 RRF 融合，复用 rag 栈）。"""
    if not query.strip():
        return []
    try:
        store = rag_backend.store(_collection(repo_id), cfg)
        candidate_k = max(k * 4, 12)
        data = store.get()
        # id 统一用内容哈希：dense 排名也按 _hid(content) 记，两路 id 同源 RRF 才能正确去重
        # （曾用 uuid did → 与 dense 的 content-hash 不一致，同条目被计两次，白占 k 槽 → 召回变少）。
        documents = [
            {"id": _hid(doc), "content": doc, "kind": "worldbook", "source": "wb"}
            for did, doc, meta in zip(
                data.get("ids", []) or [], data.get("documents", []) or [],
                data.get("metadatas", []) or [])
            if (meta or {}).get("kind") == "worldbook" and (doc or "").strip()
        ]
        if not documents:
            return []
        rankings: list[tuple[str, list[dict]]] = []
        try:
            vector = rag_backend.embed_query(cfg, query)
            docs = store.similarity_search_by_vector(
                vector, k=candidate_k, filter={"kind": "worldbook"})
            rankings.append(("dense", [
                {"id": _hid(d.page_content), "content": d.page_content,
                 "kind": "worldbook", "source": "wb"} for d in docs]))
        except Exception:  # noqa: BLE001
            pass
        rankings.append(("bm25", rag_retrieval.sparse_rank(query, documents, candidate_k)))
        fused = rag_retrieval.rrf_fuse(rankings, candidate_k)
        return [h["content"] for h in fused[:k]]
    except Exception:  # noqa: BLE001
        return []


def _hid(content: str) -> str:
    return hashlib.sha1(content.encode("utf-8")).hexdigest()


def _keyword_hits(entries: list[Entry], query: str) -> list[str]:
    """SillyTavern 核心机制：非常驻条目的任一 key 出现在 query 里则激活（关键词触发）。

    这是命名实体（角色名/地名/势力名）稳定进注入的保证——语义检索可能因 query 被其它语义带偏而漏掉
    直接点名的条目（如用户说「料理冷倾雪」，「冷倾雪」角色卡必须进）。保序返回命中条目 content。
    """
    if not query.strip():
        return []
    hits: list[str] = []
    for e in entries:
        if e.constant or not e.keys:
            continue
        if any(k and k in query for k in e.keys):
            hits.append(e.content)
    return hits


def _sparse_retrieve(entries: list[Entry], query: str, k: int) -> list[str]:
    documents = [
        {"id": _hid(entry.content), "content": entry.content,
         "kind": "worldbook", "source": "wb"}
        for entry in entries if not entry.constant
    ]
    return [item["content"] for item in rag_retrieval.sparse_rank(query, documents, k)]


@dataclass(frozen=True)
class Selection:
    text: str
    indices: list[int]
    keyword_indices: list[int] = field(default_factory=list)


def keyword_match_indices(entries: list[Entry], query: str) -> list[int]:
    """返回当前 query 精确触发 key 的原始条目 index，不含 constant/语义/BM25。"""
    hits = {_hid(content) for content in _keyword_hits(entries, query)}
    return [
        entry.source_index if entry.source_index >= 0 else position
        for position, entry in enumerate(entries)
        if _hid(entry.content) in hits
    ]


def assemble_selection(repo_id: str, entries: list[Entry], query: str, cfg: EmbedConfig,
                       *, k: int = _DEFAULT_K,
                       max_chars: int = WORLDBOOK_INJECT_MAX_CHARS) -> Selection:
    """组装注入文本，并返回本轮实际进入注入的原始快照条目 index。

    选择性注入，不做 token 预算截断（截断会腰斩机制条目与角色卡，破坏体验）：
      - constant（全局机制 + 系统判定机制条目）：全程恒开，全文注入，永不截断；
      - 关键词命中（key 出现在 query 的命名实体）：本轮直接相关，全量注入；
      - 非常驻语义/BM25 检索：按相关性取 top-k，条数即闸门，不做字数截断。
    max_chars：整段注入硬上限（2026-09-04 成本杠杆 L1-B/L3-B）。条目按「关键词命中 →
    constant → 语义补充」优先级就序，超预算时**只裁末尾最低优先级条目**，并标注省略数；
    机制条目/角色卡永远在序列前部，不受影响。默认上限 WORLDBOOK_INJECT_MAX_CHARS=8000；
    传 None/<=0 关闭上限（旧行为，测试/特殊入口用）。
    """
    indexed = [
        (entry.source_index if entry.source_index >= 0 else position, entry)
        for position, entry in enumerate(entries)
    ]
    by_hash: dict[str, tuple[int, Entry]] = {}
    for index, entry in indexed:
        by_hash.setdefault(_hid(entry.content), (index, entry))

    candidates: list[tuple[int, Entry]] = []
    keyword_hashes = {_hid(text) for text in _keyword_hits(entries, query)}
    keyword_indices = set(keyword_match_indices(entries, query))
    candidates.extend((index, entry) for index, entry in indexed
                      if _hid(entry.content) in keyword_hashes)
    candidates.extend((index, entry) for index, entry in indexed if entry.constant)
    retrieved = [
        *_retrieve(repo_id, query, cfg, k),
        *_sparse_retrieve(entries, query, k),
    ]
    retrieved_hashes: set[str] = set()
    for content in retrieved:
        matched = by_hash.get(_hid(content))
        content_hash = _hid(content)
        if matched is None or content_hash in retrieved_hashes:
            continue
        candidates.append(matched)
        retrieved_hashes.add(content_hash)
        if len(retrieved_hashes) >= k:
            break

    picked_keyword_indices: list[int] = []
    anchor_pairs: list[tuple[str, int]] = []   # keyword 命中 + constant（机制/角色卡）：永不裁
    tail_pairs: list[tuple[str, int]] = []     # 非常驻语义/BM25 补充：预算内衰减
    seen: set[str] = set()
    for index, entry in candidates:
        text = entry.content
        h = _hid(text)
        if h in seen:
            continue
        seen.add(h)
        if index in keyword_indices:
            picked_keyword_indices.append(index)
        is_anchor = index in keyword_indices or entry.constant
        (anchor_pairs if is_anchor else tail_pairs).append((text, index))
    if not anchor_pairs and not tail_pairs:
        return Selection("", [])
    # 整段注入预算（2026-09-04 成本杠杆 L1-B/L3-B）：keyword/constant 锚点段永远全收，
    # 只让语义补充段在预算内衰减；被挤掉的语义条目折叠成标注（不含于 indices/keyword 追踪）。
    kept_text = [text for text, _ in anchor_pairs]
    kept_idx = [index for _, index in anchor_pairs]
    if max_chars and max_chars > 0:
        used = sum(len(text) + 2 for text in kept_text)
        dropped = 0
        for text, index in tail_pairs:
            cost = len(text) + 2
            if used + cost > max_chars:
                dropped += 1
                continue
            kept_text.append(text)
            kept_idx.append(index)
            used += cost
        if dropped:
            kept_text.append(f"…（省略 {dropped} 条，注入预算 {max_chars} 字符内）")
            picked_keyword_indices = [
                i for i in picked_keyword_indices if i in kept_idx]
    else:
        for text, index in tail_pairs:
            kept_text.append(text)
            kept_idx.append(index)
    body = "\n\n".join(f"- {text}" for text in kept_text)
    return Selection(
        f"【世界设定（相关条目）】\n{body}", kept_idx, picked_keyword_indices,
    )


def assemble(repo_id: str, entries: list[Entry], query: str, cfg: EmbedConfig,
             *, k: int = _DEFAULT_K,
             max_chars: int = WORLDBOOK_INJECT_MAX_CHARS) -> str:
    """组装本轮世界书注入文本：constant 全带（不截断）+ 关键词触发命中 + 非常驻语义检索 top-k。

    注入优先级（高→低，去重后按序全收）：
      1) 关键词触发命中（key 出现在 query，ST 核心机制；用户点名的命名实体是本轮最相关）
      2) constant 常驻条目（全局机制 + 系统判定机制：全程恒开）
      3) 语义检索补充（dense+BM25 RRF，按相关性取 top-k）
    max_chars：整段硬上限（默认 8000，超预算裁末尾语义补充并标注，见 assemble_selection）。
    返回可直接拼进 system 的文本；无内容返回空串。
    """
    return assemble_selection(repo_id, entries, query, cfg, k=k, max_chars=max_chars).text
