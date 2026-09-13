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
from pathlib import Path
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
    uid: str = ""           # 条目稳定身份键（原书 id/uid；curator ops 不改）

    def __post_init__(self) -> None:
        if self.keys is None:
            self.keys = []


def parse_entries(book: dict[str, Any] | None) -> list[Entry]:
    """从 character_book 解析出启用的条目。disabled/空内容跳过。

    uid 取 `id`（V2 卡数组）→ `uid` 字段 → 容器键名（ST 对象形式 keyed-by-uid）逐级回退；
    它是会话稳定序列的记序依据（见 entry_identity），必须跨轮不变。
    """
    if not book:
        return []
    raw = book.get("entries")
    pairs: list[tuple[str, Any]] = (
        [(str(k), v) for k, v in raw.items()] if isinstance(raw, dict)
        else [("", v) for v in (raw or [])]
    )
    out: list[Entry] = []
    for source_index, (key_uid, e) in enumerate(pairs):
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
            uid=str(e.get("id") or e.get("uid") or key_uid or "").strip(),
        ))
    return out


def entry_identity(entry: Entry) -> str:
    """条目**跨轮稳定**的身份键：id/uid → comment → 内容 hash（逐级回退）。

    会话稳定序列（assemble_selection_stable）必须按身份键而非内容 hash 记序：curator 的
    worldbook_update 会改写条目正文（底座保留、末尾【剧情进展·动态】区变化，见
    worldbook_store.apply_repo_ops），内容 hash 一变，旧槽位就解析不到 → 整条被丢弃 →
    其后条目全部左移 → 前缀在该处之后整段作废（2026-09-12 实测跨轮 LCP 只剩 9.0%）。
    id/uid 与 comment 均不随 curator ops 变更，故可作稳定标识。
    """
    if entry.uid:
        return f"i:{entry.uid}"
    if entry.comment:
        return f"c:{entry.comment}"
    return f"h:{_hid(entry.content)}"


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
    dropped: int = 0          # 因预算未注入的条目数（稳定序列用；0 = 无裁剪）
    # ⚠ 2026-09-12：召回段与锚点段**必须分开发给调用方**。锚点段（constant/关键词命中）跨轮
    # 稳定、留在世界书块（history 之前）；召回段每轮按相关度重排，若留在世界书块内，则它一变
    # 就开始的整段（全部历史 + 其后全部预设片段）每轮作废——真机实测 LCP 因此从 78% 掉到 62%。
    # 调用方须把 recall_text 放进**尾部动态块**（history 之后），两段各自到位。
    recall_text: str = ""
    recall_indices: list[int] = field(default_factory=list)
    # ⚠ 2026-09-13 动态区外置：`dynamic_text` 也必须进**尾部动态块**。含两类内容：
    # ① 锚点条目末尾的【剧情进展·动态】区（curator 每轮可能更新——留在锚点段会把分叉点
    #    拉回世界书块中部，其后的历史与预设片段整段作废，真机三次实测 cached=0 同因）；
    # ② 会话**中途**首次激活的锚点条目全文（curator 新增条目等——若追加进锚点段，分叉点
    #    =锚点段末尾，仍 < 缓存粒度）。锚点段因此会话内**双冻结**（条目集+内容快照），
    #    curator 的一切产出改道尾部动态块，前缀分叉点回到历史追加点（≫ 缓存粒度）。
    dynamic_text: str = ""


def keyword_match_indices(entries: list[Entry], query: str) -> list[int]:
    """返回当前 query 精确触发 key 的原始条目 index，不含 constant/语义/BM25。"""
    hits = {_hid(content) for content in _keyword_hits(entries, query)}
    return [
        entry.source_index if entry.source_index >= 0 else position
        for position, entry in enumerate(entries)
        if _hid(entry.content) in hits
    ]


def _candidate_entries(
    repo_id: str, entries: list[Entry], query: str, cfg: EmbedConfig, k: int,
) -> tuple[list[tuple[int, Entry]], set[int]]:
    """本轮候选条目（keyword 命中 → constant → 语义/BM25 top-k），保序去重。

    返回 (candidates, keyword_indices)：candidates 元素为 (原始 index, Entry)。
    这是 assemble_selection / assemble_selection_stable 共用的**条目选择**规则；
    两者只在**排列与裁剪**上不同（见各自 docstring）。
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
    return candidates, keyword_indices


# ── 会话内稳定序列（2026-09-12，P4-roleplay 配套）──────────────────────────
# 目标：让注入的世界书条目集在**会话内单调追加**、顺序为「首次激活顺序」，
# 从而跨轮保持逐字节前缀稳定（上游 prompt 前缀缓存的前提）。
# 现状 assemble_selection 每轮按 keyword→constant→语义 重排：条目集一抖动整块重写，
# 位于其后的历史与尾部合同全部不可复用（实测跨轮 LCP 只有 7.6%）。
# 代价（明示）：条目顺序不再按本轮相关度排列；预算打满后新的语义补充条目进不来
# （keyword 点名与 constant 机制条目仍必进——它们是锚点，永不裁剪）。
_SESSION_SEQ: dict[str, list[str]] = {}      # session_key -> [entry_identity]（首次激活顺序）
_SESSION_ANCHOR: dict[str, set[str]] = {}    # session_key -> {entry_identity}（锚点，永不裁）
# ⚠ 2026-09-13 动态区外置：锚点段「双冻结」的两块配套状态。
# _SESSION_CONTENT：identity -> 首次激活时的**静态底座**快照（剥掉【剧情进展·动态】区）。
#   锚点段永远用快照渲染——curator 之后的 update 只会改条目末尾动态区
#   （worldbook_store._merge_character_dynamic 保底座），底座快照保证锚点段逐字节不变。
# _SESSION_LATE：会话**中途**才首次激活的锚点条目（curator 新增/新命中）——永不进锚点段
#   （追加入段会把分叉点拉到锚点段末尾，仍够不到缓存粒度），永远走尾部动态块。
_SESSION_CONTENT: dict[str, dict[str, str]] = {}
_SESSION_LATE: dict[str, set[str]] = {}
_SESSION_LOCK = threading.Lock()
_SESSION_MAX_ENTRIES = 400                   # 防御：单会话序列条数上限
_SESSION_MAX_SESSIONS = 128                  # 防御：驻留会话数上限

# 与 worldbook_store._merge_character_dynamic 的唯一动态区标记同口径。
DYNAMIC_MARKER = "【剧情进展·动态】"


def _split_dynamic(content: str) -> tuple[str, str]:
    """把条目正文拆成（静态底座, 动态区）。

    与 curator 写回（worldbook_store._merge_character_dynamic：底座逐字节保留 + 末尾唯一
    【剧情进展·动态】区）同口径。无标记时动态区为空串。
    """
    if DYNAMIC_MARKER in content:
        base, dyn = content.split(DYNAMIC_MARKER, 1)
        return base.rstrip(), dyn.strip()
    return content, ""


def _session_file_read(state_path: str | None) -> dict[str, Any] | None:
    """读会话序列持久化文件（不存在/损坏返回 None）。路径由调用方注入（agent_graph）。"""
    if not state_path:
        return None
    try:
        data = json.loads(Path(state_path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _session_file_write(state_path: str | None, state: dict[str, Any]) -> None:
    """会话序列落盘（序列/底座快照/late 集）。失败静默——缓存优化不因 IO 故障阻断生成。"""
    if not state_path:
        return
    try:
        p = Path(state_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def reset_session(session_key: str, state_path: str | None = None) -> None:
    """清空某会话的世界书稳定序列（换书 / 用户手动重置 / 测试用）。

    传入 state_path 时同步删除持久化文件，否则下次组装会从盘上恢复旧序列。
    """
    with _SESSION_LOCK:
        _SESSION_SEQ.pop(session_key, None)
        _SESSION_ANCHOR.pop(session_key, None)
        _SESSION_CONTENT.pop(session_key, None)
        _SESSION_LATE.pop(session_key, None)
    if state_path:
        try:
            Path(state_path).unlink(missing_ok=True)
        except OSError:
            pass


def assemble_selection_stable(
    repo_id: str, entries: list[Entry], query: str, cfg: EmbedConfig, *,
    session_key: str, k: int = _DEFAULT_K,
    max_chars: int = WORLDBOOK_INJECT_MAX_CHARS,
    state_path: str | None = None,
) -> Selection:
    """会话内稳定版 assemble_selection：按「首次激活顺序」单调追加，跨轮前缀只增不改。

    与 assemble_selection 的**条目选择规则完全一致**（本轮 keyword 命中 → constant →
    语义/BM25 top-k），差异只在两处：
      1. **排列**改为首次激活顺序：条目一旦进入序列，其相对位置永不变；新激活条目一律
         追加到序列**末尾**（append-only）。顺序不再随本轮相关度重排。
      2. **裁剪**只在序列尾部**连续**进行，保留部分恒等于序列的一个前缀。

    序列用 entry_identity（id/uid → comment → 内容 hash）记序，**不是内容 hash**：curator
    会改写条目正文（底座保留、末尾动态区变化），按内容 hash 记序会让该槽位解析不到而整条
    丢弃、其后条目全部左移，前缀在该处之后整段作废（2026-09-12 实测跨轮 LCP 只剩 9.0%）。

    锚点轨（keyword 命中 / constant）：按首次激活顺序稳定排列、**永不裁** → 与静态段一起进前缀缓存。
    召回轨（语义/BM25 top-k）：**不记入稳定序列**，每轮按相关度重排、排在锚点段之后、吃 max_chars
    预算 —— 保持时效性（这部分每轮变化，本就不在前缀缓存内）。

    ⚠ 2026-09-12 分轨：此前两轨共用一条序列 + 一个预算，而本书 constant 锚点 13 条合计
    21,936 字符已是预算 8,000 的 2.74 倍、锚点又不参与预算 ⇒ `used > max_chars` ⇒ 语义段
    第一条就被 break ⇒ 召回事实上全废（离线 dropped 逐轮 7→13→17→20→24），且注入集在
    「最后一个锚点入场后」被冻结（真机 [7] 轮 3/4/5 = 37,793 逐字节不变）。分轨后
    **max_chars 语义明确为「召回段上限」**。

    session_key：会话标识（作品级，见 agent_graph._worldbook_session_key）。
    无 session_key 时调用方应退回 assemble_selection（无状态、每轮重排）。

    ⚠ 2026-09-12 返回值分两段（见 Selection.recall_text）：`text` = 锚点段，`recall_text` =
    召回段。调用方必须把 recall_text 放进**尾部动态块**（history 之后）。早期实现把两段拼在
    一起放进世界书块（history 之前）⇒ 召回段每轮变 ⇒ 其后的历史与全部预设片段每轮作废，
    真机相邻轮字节 LCP 78% → 62%（数据表召回原本就在尾部动态块，世界书召回应对齐同一位置）。

    ⚠ 2026-09-13 动态区外置（三段返回）：`dynamic_text` 也进尾部动态块。锚点段升级为
    **双冻结**：
      1. 内容冻结——锚点条目用首次激活时的**静态底座快照**渲染（_SESSION_CONTENT）；curator
         的 update 只改条目末尾【剧情进展·动态】区（worldbook_store._merge_character_dynamic
         保底座），动态区改道 dynamic_text，锚点段逐字节不变。真机三次实测 cached=0 的分叉
         点全在世界书锚点块内（curator 写回），本条消灭该分叉源。
      2. 集合冻结——会话**中途**首次激活的锚点条目（curator worldbook_add 新条目、新 keyword
         命中）登记进序列但走 dynamic_text（_SESSION_LATE），不追加进锚点段文本；否则分叉点
         =锚点段末尾（≈15k token），仍够不到 16,384 的缓存粒度。
    `state_path` 提供时序列/快照/late 集落盘、内存 miss 时恢复：重启后锚点段不重排，前缀
    跨重启延续（否则重启即 miss 一轮且服务端旧前缀作废）。
    明示代价：锚点条目的**底座**被用户手动编辑后，会话内不反映（仍显示首次激活快照），
    重开会话生效；动态内容不受影响（动态区每轮取条目最新值）。
    """
    candidates, keyword_indices = _candidate_entries(repo_id, entries, query, cfg, k)
    if not candidates:
        return Selection("", [])
    # 统一 index 口径（与 _candidate_entries 一致：source_index 缺失时回退位置序号），
    # 全量 entries 兜底解析旧序列里的条目，避免本轮未命中的已注入条目被丢掉。
    # ⚠ 2026-09-12：解析键由「内容 hash」改为**稳定身份键**（entry_identity，id/uid 优先）。
    # 内容 hash 一旦被 curator 改正文就打乱：旧 hash 解析不到 → 整条丢弃 → 其后条目左移
    # → 前缀在该处之后整段作废。身份键不随 ops 变更，内容改写后条目仍在原位。
    indexed_map: dict[str, tuple[int, Entry]] = {}
    for position, entry in enumerate(entries):
        index = entry.source_index if entry.source_index >= 0 else position
        indexed_map.setdefault(entry_identity(entry), (index, entry))
    for index, entry in candidates:
        indexed_map.setdefault(entry_identity(entry), (index, entry))

    anchor_pairs = [(i, e) for i, e in candidates if i in keyword_indices or e.constant]
    recall_pairs = [(i, e) for i, e in candidates
                    if not (i in keyword_indices or e.constant)]

    with _SESSION_LOCK:
        if len(_SESSION_SEQ) >= _SESSION_MAX_SESSIONS and session_key not in _SESSION_SEQ:
            for stale in list(_SESSION_SEQ)[: max(1, _SESSION_MAX_SESSIONS // 2)]:
                _SESSION_SEQ.pop(stale, None)
                _SESSION_ANCHOR.pop(stale, None)
                _SESSION_CONTENT.pop(stale, None)
                _SESSION_LATE.pop(stale, None)
        # 内存 miss 时从持久化恢复（重启后锚点段不重排，前缀跨重启延续）。
        if session_key not in _SESSION_SEQ:
            restored = _session_file_read(state_path)
            if restored:
                seq_restored = [str(x) for x in restored.get("seq") or []]
                content_restored = {
                    str(k): str(v) for k, v in (restored.get("content") or {}).items()
                }
                late_restored = {str(x) for x in restored.get("late") or []}
                if seq_restored:
                    _SESSION_SEQ[session_key] = seq_restored
                    _SESSION_CONTENT[session_key] = content_restored
                    _SESSION_LATE[session_key] = late_restored
        seq = _SESSION_SEQ.setdefault(session_key, [])
        anchors = _SESSION_ANCHOR.setdefault(session_key, set())
        contents = _SESSION_CONTENT.setdefault(session_key, {})
        late = _SESSION_LATE.setdefault(session_key, set())
        # 序列**只登记锚点**（keyword 命中 / constant）：召回段每轮可变，登记进去会把它
        # 拖成「冻结集」并让序列无限膨胀（真机 38 条里 24 条永远进不来）。
        known = set(seq)
        initial_build = not known      # 首次构建：本轮激活的锚点全部进锚点段（会话基线）
        new_anchor_pairs: list[tuple[int, Entry]] = []   # 中途激活 → 记账 + 走动态块
        for _index, entry in anchor_pairs:
            identity = entry_identity(entry)
            anchors.add(identity)
            if identity in known:
                continue
            seq.append(identity)
            known.add(identity)
            if len(seq) > _SESSION_MAX_ENTRIES:
                seq.pop(0)
            if initial_build:
                late.discard(identity)
            else:
                late.add(identity)
                new_anchor_pairs.append((_index, entry))
        # 底座快照补记：锚点条目首次见到时剥出静态底座（后续 curator 只改末尾动态区，
        # 快照保证锚点段逐字节不变）。
        for _index, entry in anchor_pairs:
            identity = entry_identity(entry)
            if identity not in contents:
                contents[identity] = _split_dynamic(entry.content)[0]
        snapshot = list(seq)
        late_snapshot = set(late)
        state_to_save: dict[str, Any] | None = (
            {"seq": list(seq), "content": dict(contents), "late": sorted(late)}
            if state_path else None
        )

    # 按序列顺序解析锚点条目（全量 entries 优先，保证本轮未命中的旧锚点也在场）。
    # ⚠ 2026-09-13 动态区外置：锚点段渲染用**底座快照**；条目当前正文的动态区（可能已被
    # curator 更新）收集进动态块，取**最新值**——动态块在 history 之后，变化不伤前缀。
    resolved: list[tuple[int, str]] = []
    dynamic_items: list[str] = []
    late_indices: list[int] = []
    seen: set[str] = set()
    for identity in snapshot:
        if identity in seen:
            continue
        hit = indexed_map.get(identity)
        if hit is None:
            continue          # 条目已从世界书删除 → 跳过（前缀在此处缩一次）
        seen.add(identity)
        index, entry = hit
        if identity in late_snapshot:
            # 会话中途激活的锚点：永远走动态块（追加入锚点段会破坏其前缀）。
            # 新登记的中途锚点也在 snapshot 里（登记发生在 snapshot=list(seq) 之前），
            # 此处统一处理，无需另开循环。
            dynamic_items.append(entry.content)
            if index >= 0:
                late_indices.append(index)
            continue
        base = contents.get(identity)
        if base is None:      # 防御：快照缺失（异常恢复）→ 现场补记
            base, _dyn = _split_dynamic(entry.content)
        else:
            _dyn = _split_dynamic(entry.content)[1]
        resolved.append((index, base))
        if _dyn:
            dynamic_items.append(
                f"【剧情进展·动态·{entry.comment or identity}】\n{_dyn}")
    if state_to_save is not None:
        _session_file_write(state_path, state_to_save)

    # 召回段：按本轮相关度排列（candidates 保序：语义 top-k → BM25 top-k），排在锚点段之后。
    # 独立预算 ⇒ 锚点再大也挤不掉它；每轮可换 ⇒ 召回保持时效性。
    recall_texts: list[str] = []
    recall_indices: list[int] = []
    dropped = 0
    used = 0
    for index, entry in recall_pairs:
        identity = entry_identity(entry)
        if identity in seen:
            continue
        cost = len(entry.content) + 2
        if max_chars and max_chars > 0 and used + cost > max_chars:
            dropped += 1
            continue
        seen.add(identity)
        used += cost
        recall_texts.append(entry.content)
        recall_indices.append(index)
    if not resolved and not recall_texts:
        return Selection("", [])

    # 刻意**不加**「省略 N 条」标注（dropped>0 时）：标注的有无与条数会随预算和序列长度
    # 逐轮变化，一旦写进文本就破坏「保留部分恒为前缀」。省略数改走 Selection.dropped
    # 由调用方落 trace（世界书条目对模型是独立事实块，缺省提示无实质信息增量）。
    kept_indices = [index for index, _text in resolved if index >= 0]
    kept_indices += [index for index in late_indices if index not in kept_indices]
    kept_indices += recall_indices
    picked_keyword = [index for index, _text in resolved if index in keyword_indices]
    # 三段分开发：锚点段留世界书块（history 之前、跨轮双冻结）；召回段与动态段都由调用方
    # 放尾部动态块。锚点段内任何变化（召回混入/动态区/中途新条目）都会把其后的历史与
    # 全部预设片段的前缀打掉（真机三次 cached=0 的分叉点全在锚点块内）。
    anchor_body = "\n\n".join(f"- {text}" for _text_index, text in resolved)
    head = f"【世界设定（相关条目）】\n{anchor_body}" if anchor_body else ""
    recall_body = "\n\n".join(f"- {text}" for text in recall_texts)
    recall_seg = f"【世界设定·本轮召回（每轮重排）】\n{recall_body}" if recall_body else ""
    dynamic_seg = (
        "【世界设定·动态更新（随剧情推进）】\n" + "\n\n".join(f"- {item}" for item in dynamic_items)
        if dynamic_items else ""
    )
    return Selection(
        head, kept_indices, picked_keyword, dropped, recall_seg, recall_indices,
        dynamic_seg,
    )


def assemble_selection(repo_id: str, entries: list[Entry], query: str, cfg: EmbedConfig,
                       *, k: int = _DEFAULT_K,
                       max_chars: int = WORLDBOOK_INJECT_MAX_CHARS) -> Selection:
    """组装注入文本，并返回本轮实际进入注入的原始快照条目 index。

    选择性注入，不做 token 预算截断（截断会腰斩机制条目与角色卡，破坏体验）：
      - constant（全局机制 + 系统判定机制条目）：全程恒开，全文注入，永不截断；
      - 关键词命中（key 出现在 query 的命名实体）：本轮直接相关，全量注入；
      - 非常驻语义/BM25 检索：按相关性取 top-k，条数即闸门，不做字数截断。
    max_chars：**召回补充段（语义/BM25 top-k）的上限**（2026-09-04 成本杠杆 L1-B/L3-B；
    2026-09-12 明确口径）。条目按「关键词命中 → constant → 语义补充」优先级就序，超上限时
    **只裁末尾最低优先级条目**，并标注省略数；keyword 命中与机制条目/角色卡是锚点，
    **不参与该预算、永远全收**。默认上限 WORLDBOOK_INJECT_MAX_CHARS=8000；
    传 None/<=0 关闭上限（旧行为，测试/特殊入口用）。

    ⚠ 2026-09-12：此前预算从 `sum(锚点)` 起算，本书 constant 锚点 13 条 21,936 字符恒 ≫ 8,000
    ⇒ 语义段第一条就被 break ⇒ 召回全废。现与 assemble_selection_stable 同口径：预算只作用于
    召回段（锚点段不计入）。
    """
    candidates, keyword_indices = _candidate_entries(repo_id, entries, query, cfg, k)

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
    # 锚点段永远全收、不计入预算；只让语义补充段在预算内衰减，被挤掉的折叠成标注。
    kept_text = [text for text, _ in anchor_pairs]
    kept_idx = [index for _, index in anchor_pairs]
    if max_chars and max_chars > 0:
        used = 0                              # 2026-09-12：预算只作用于召回段
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
