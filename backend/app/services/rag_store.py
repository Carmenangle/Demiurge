"""仓库 RAG 知识库：Chroma 本地持久化 + OpenAI 兼容嵌入。

按仓库隔离（B方案）：
  - 系统指令独占 global 库：全局共享一份，不随仓库复制（规模做大也不冗余）。
  - 每个仓库内容独占 repo_<id> 库：generation / document 各存各的。
  - 检索、列表都查「系统库 + 当前仓库库」两库再合并。
入库来源两类：
  - generation：每次生图自动入库（提示词 + D站标签 + 反推描述 + 图片URL）
  - document  ：用户手动上传的参考资料
嵌入模型走「设置 → 嵌入模型」配置的 OpenAI 兼容接口（Ollama / OpenAI / 中转通用）。
"""
import hashlib
import re
import time
import uuid
from pathlib import Path

from langchain_core.documents import Document

from app.services import rag_backend, rag_retrieval, reranker
from app.services.rag_backend import EmbedConfig


SYSTEM_COLLECTION = "global"  # 系统指令独占库，全局共享


def _repo_collection(repo_id: str) -> str:
    """仓库内容库名：repo_<规范化id>。

    Chroma collection 名限制：3-63 字符、首尾为字母数字、仅 [a-zA-Z0-9._-]。
    repo_id 多为 UUID（含连字符，合法），但 home/空 等需兜底。非法字符替换为下划线。
    """
    rid = re.sub(r"[^a-zA-Z0-9_-]", "_", (repo_id or "home").strip()) or "home"
    return f"repo_{rid}"


def _store(collection: str, cfg: EmbedConfig):
    return rag_backend.store(collection, cfg)


def _auto_tags(prompt: str, tags: str) -> list[str]:
    """从 D站标签串 + 提示词里提取结构化标签（去重保序，过滤元信息垃圾）。
    - D站 tags：按分隔符拆分，原样保留。
    - 提示词：常含 API 调用元信息（如 'Model: gpt-image-2'、'Task ID: xxx'、
      'GET /v1/...'、URL、'Total Tokens: 3615'），这些不是画面标签，必须滤掉；
      只切真正的提示词短语。中文自然语言无分隔符时不硬切，留给「AI 打标」。
    """
    # 元信息行/词的特征：含这些前缀键、或含 URL/路径/HTTP 动词 → 丢弃
    META_KEYS = ("model", "task id", "image url", "total tokens", "aspect ratio",
                 "resolution", "quality", "input images", "output", "prompt",
                 "seed", "steps", "cfg", "sampler", "scheduler")

    def is_junk(t: str) -> bool:
        low = t.lower().strip()
        if not low:
            return True
        if "http" in low or "/" in t or "{" in t or "}" in t:  # URL/路径/占位
            return True
        if re.match(r"^(get|post|put|delete|patch)\b", low):    # HTTP 动词行
            return True
        # 纯 "键: 值" 元信息（键在 META_KEYS 内）
        if ":" in t:
            key = t.split(":", 1)[0].strip().lower()
            if key in META_KEYS:
                return True
        if low in META_KEYS:                                   # 裸键名
            return True
        if re.match(r"^[\d\s.x*]+$", t):                       # 纯数字/尺寸
            return True
        return False

    out: list[str] = []
    seen: set[str] = set()
    for raw in [tags or "", prompt or ""]:
        for piece in re.split(r"[,，;；\n|]+", raw):
            t = piece.strip()
            if not t or len(t) > 40 or t in seen or is_junk(t):
                continue
            seen.add(t)
            out.append(t)
    return out


def index_generation(repo_id: str, cfg: EmbedConfig,
                     prompt: str, tags: str = "", image_url: str = "") -> None:
    """生图完成后入库：提示词 + 标签合为一条文档，附图片URL元数据。写入本仓库库。

    只要有图片就入库——智能体出图常无提示词文本，此时用占位文本嵌入，
    避免因文本为空而整张图被丢弃（资产库会漏图）。
    标签同时结构化存入 metadata.tags（逗号串），供资产库标签展示/搜索/增删。
    """
    text = "\n".join(t for t in [prompt, tags] if t and t.strip())
    if not text.strip() and not (image_url or "").strip():
        return  # 既无文本也无图，无意义
    embed_text = text if text.strip() else "生成图片"  # 无提示词时用占位文本嵌入
    tag_list = _auto_tags(prompt, tags)
    store = _store(_repo_collection(repo_id), cfg)
    # 幂等 id：同一仓库同一张图用确定性 id，Chroma 相同 id 覆盖而非新增。
    # 这是防重复的最后一道闸——前端/后端/后台线程任何一条路径重复入库都不会产生多条
    # （前端去重只在内存 ref，切仓库/刷新后失效，故必须在入库层兜底）。
    # 无 image_url（纯文本记录）才回退随机 id。
    img = (image_url or "").strip()
    doc_id = ("gen-" + hashlib.sha1(f"{repo_id}|{img}".encode("utf-8")).hexdigest()
              if img else str(uuid.uuid4()))
    store.add_documents([
        Document(page_content=embed_text,
                 metadata={"kind": "generation", "image_url": image_url or "",
                           "repo_id": repo_id or "", "tags": ",".join(tag_list),
                           # 权威排序键：入库毫秒时间戳。前端据此从新到旧，不再依赖文件名编号
                           # （文件改名/删图都不影响排序）。历史记录无此字段时前端回退到文件名。
                           "created_at": int(time.time() * 1000)})
    ], ids=[doc_id])


def index_document(repo_id: str, cfg: EmbedConfig,
                   text: str, title: str = "") -> int:
    """手动参考资料入库，按段落粗切分。写入本仓库库。返回入库条数。"""
    if not text.strip():
        return 0
    chunks = [c.strip() for c in re.split(r"\n\s*\n", text) if c.strip()]
    if not chunks:
        return 0
    store = _store(_repo_collection(repo_id), cfg)
    store.add_documents([
        Document(page_content=c, metadata={"kind": "document", "title": title or "",
                                           "repo_id": repo_id or ""})
        for c in chunks
    ], ids=[str(uuid.uuid4()) for _ in chunks])
    return len(chunks)


def _context_id(source: str, content: str, metadata: dict) -> str:
    raw = f"{source}|{metadata.get('kind', '')}|{metadata.get('title', '')}|{content}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _context_item(source: str, content: str, metadata: dict) -> dict:
    return {
        "id": _context_id(source, content, metadata),
        "content": content,
        "title": metadata.get("title", ""),
        "kind": metadata.get("kind", "document"),
        "source": source,
    }


def _context_documents(collection: str, source: str, cfg: EmbedConfig) -> list[dict]:
    try:
        data = _store(collection, cfg).get()
    except Exception:
        return []
    documents = data.get("documents", []) or []
    metadatas = data.get("metadatas", []) or []
    out = []
    for index, content in enumerate(documents):
        metadata = (metadatas[index] if index < len(metadatas) else {}) or {}
        if metadata.get("kind") == "generation":
            continue
        out.append(_context_item(source, content or "", metadata))
    return out


def retrieve_with_trace(repo_id: str, cfg: EmbedConfig,
                        query: str, k: int = 4) -> list[dict]:
    """普通知识库 Hybrid 检索，返回可供评估追踪的结构化结果。"""
    if not query.strip():
        return []
    candidate_k = max(k * 4, 12)
    sources = [
        (SYSTEM_COLLECTION, "system"),
        (_repo_collection(repo_id), f"repo:{repo_id or 'home'}"),
    ]
    rankings: list[tuple[str, list[dict]]] = []
    documents: list[dict] = []
    try:
        vector = rag_backend.embed_query(cfg, query)
    except Exception:
        vector = None
    for collection, source in sources:
        documents.extend(_context_documents(collection, source, cfg))
        if vector is None:
            continue
        try:
            docs = _store(collection, cfg).similarity_search_by_vector(
                vector, k=candidate_k, filter={"kind": {"$ne": "generation"}},
            )
            dense = [
                _context_item(source, doc.page_content, doc.metadata or {}) for doc in docs
            ]
        except Exception:
            dense = []
        rankings.append((f"dense:{source}", dense))
    rankings.append(("bm25", rag_retrieval.sparse_rank(query, documents, candidate_k)))
    fused = rag_retrieval.rrf_fuse(rankings, candidate_k)
    if not rag_retrieval.needs_rerank(fused):
        return fused[:k]
    reranked = reranker.rerank(
        query, fused, cfg.reranker_dir, k,
        instruction="Rank knowledge-base passages by how directly they answer the user's question.",
    )
    return reranked or fused[:k]


def retrieve(repo_id: str, cfg: EmbedConfig,
             query: str, k: int = 4) -> list[str]:
    """检索与 query 相关的片段（系统库 + 本仓库库合并），返回文本列表。
    只检索 kind != generation 的条目：单次出图的提示词/反推无复用价值，
    会污染对话检索；它们仍留在库里供资产库展示，只是不喂给 AI 检索。
    可复用知识（角色固定特征等）请走知识库手动录入（kind=document）。
    """
    return [hit["content"] for hit in retrieve_with_trace(repo_id, cfg, query, k)]


def _dump(collection: str, cfg: EmbedConfig) -> list[dict]:
    """导出单个 collection 的全部条目为统一 dict 列表。"""
    try:
        store = _store(collection, cfg)
        data = store.get()  # {ids, documents, metadatas}
    except Exception:
        return []
    ids = data.get("ids", []) or []
    docs = data.get("documents", []) or []
    metas = data.get("metadatas", []) or []
    out: list[dict] = []
    for i, _id in enumerate(ids):
        meta = metas[i] or {}
        out.append({
            "id": _id,
            "content": docs[i] if i < len(docs) else "",
            "kind": meta.get("kind", "document"),
            "title": meta.get("title", ""),
            "locked": bool(meta.get("locked", False)),
            "image_url": meta.get("image_url", ""),
            "tags": meta.get("tags", ""),
            "created_at": int(meta.get("created_at", 0) or 0),
        })
    return out


def list_docs(repo_id: str, cfg: EmbedConfig) -> list[dict]:
    """列出「系统库 + 本仓库库」所有条目。返回 [{id, content, kind, title, locked, image_url}]。"""
    out = _dump(SYSTEM_COLLECTION, cfg)
    out += _dump(_repo_collection(repo_id), cfg)
    # 系统条目排前面
    out.sort(key=lambda d: (0 if d["locked"] else 1))
    return out


def list_generations(repo_id: str, cfg: EmbedConfig) -> list[dict]:
    """列出某仓库的生成记录（kind=generation）。

    返回 [{id, prompt, image_url, tags}]，供仓库详情页图片网格展示。tags 为字符串列表。
    """
    out: list[dict] = []
    for d in _dump(_repo_collection(repo_id), cfg):
        if d["kind"] != "generation" or not d["image_url"]:
            continue
        prompt = d["content"] if d["content"] != "生成图片" else ""  # 还原占位为空
        tags = [t for t in (d.get("tags", "") or "").split(",") if t.strip()]
        out.append({"id": d["id"], "prompt": prompt, "image_url": d["image_url"],
                    "tags": tags, "created_at": d.get("created_at", 0)})
    return out


def tag_stats(repo_ids: list[str], cfg: EmbedConfig) -> list[dict]:
    """聚合指定仓库集合里所有生成图的标签→图片数量，按数量降序。
    供资产库/加标签处的输入补全：输入前缀即可提示匹配标签 + 图片数。
    这是对现有 generation.metadata.tags 的实时聚合，无需额外统计库。"""
    from collections import Counter
    cnt: Counter = Counter()
    seen: set[str] = set()  # 按 doc id 去重（同图可能在多仓库集合里）
    for rid in repo_ids:
        for d in _dump(_repo_collection(rid), cfg):
            if d["kind"] != "generation" or not d["image_url"] or d["id"] in seen:
                continue
            seen.add(d["id"])
            for t in (d.get("tags", "") or "").split(","):
                t = t.strip()
                if t:
                    cnt[t] += 1
    return [{"tag": t, "count": n} for t, n in cnt.most_common()]


def set_doc_tags(doc_id: str, repo_id: str, cfg: EmbedConfig,
                 tags: list[str]) -> bool:
    """覆盖某条目的结构化标签 metadata.tags。供手动增删 / AI 打标落库。
    locked 系统条目拒绝；条目不存在返回 False。"""
    store, meta = _find_store(doc_id, repo_id, cfg)
    if store is None or meta.get("locked"):
        return False
    clean = [t.strip() for t in tags if t and t.strip()]
    meta["tags"] = ",".join(dict.fromkeys(clean))  # 去重保序
    got = store.get(ids=[doc_id])
    content = (got.get("documents") or [""])[0] or ""  # 保持原文档内容不变
    store.update_document(doc_id, Document(page_content=content, metadata=meta))
    return True


def _find_store(doc_id: str, repo_id: str, cfg: EmbedConfig):
    """在「系统库 + 本仓库库」中定位 doc_id 所在库。返回 (Chroma, meta) 或 (None, None)。"""
    for coll in (SYSTEM_COLLECTION, _repo_collection(repo_id)):
        store = _store(coll, cfg)
        try:
            got = store.get(ids=[doc_id])
        except Exception:
            continue
        if got.get("ids"):
            metas = got.get("metadatas", []) or []
            return store, (metas[0] if metas else {}) or {}
    return None, None


def _remove_local_image(image_url: str) -> None:
    """若 image_url 指向本机留存原图（local-view?path=...），删除该文件。其它地址忽略。"""
    if not image_url or "local-view" not in image_url:
        return
    try:
        from urllib.parse import urlparse, parse_qs, unquote
        q = parse_qs(urlparse(image_url).query)
        path = (q.get("path") or [""])[0]
        if not path:
            return
        p = Path(unquote(path))
        if p.is_file():
            p.unlink()
    except Exception:
        pass  # 删文件失败不阻断条目删除


def delete_doc(doc_id: str, repo_id: str, cfg: EmbedConfig,
               remove_file: bool = False) -> bool:
    """删除单条。locked（系统指令）条目拒绝删除，返回 False。

    remove_file=True 且该条目是生成图（image_url 指向本机留存）时，一并删除本机图片文件。
    """
    store, meta = _find_store(doc_id, repo_id, cfg)
    if store is None:
        return False
    if meta.get("locked"):
        return False  # 系统条目不可删
    if remove_file and meta.get("kind") == "generation":
        _remove_local_image(meta.get("image_url", ""))
    store.delete(ids=[doc_id])
    return True


def dedup_generations(repo_id: str, cfg: EmbedConfig) -> int:
    """清理某仓库里重复的生成记录（同一 image_url 多条，历史随机 id 造成）。
    每个 image_url 只保留一条（优先保留有 prompt/tags 的），删除其余重复条目。
    只删库条目、不碰图片文件（重复条目指向同一张图）。返回删除条数。"""
    store = _store(_repo_collection(repo_id), cfg)
    by_url: dict[str, list[dict]] = {}
    for d in _dump(_repo_collection(repo_id), cfg):
        if d.get("kind") != "generation":
            continue
        url = (d.get("image_url") or "").strip()
        if not url:
            continue
        by_url.setdefault(url, []).append(d)
    removed = 0
    for url, docs in by_url.items():
        if len(docs) <= 1:
            continue
        # 保留信息最全的一条：优先有 prompt(content!='生成图片')，其次有 tags
        docs.sort(key=lambda d: (d.get("content", "") == "生成图片",
                                 not (d.get("tags", "") or "").strip()))
        for d in docs[1:]:
            try:
                store.delete(ids=[d["id"]])
                removed += 1
            except Exception:
                pass
    return removed


def _local_path_of(image_url: str) -> Path | None:
    """从 local-view?path=... 解出本机磁盘路径；非本机留存图返回 None。"""
    if not image_url or "local-view" not in image_url:
        return None
    try:
        from urllib.parse import urlparse, parse_qs, unquote
        path = (parse_qs(urlparse(image_url).query).get("path") or [""])[0]
        return Path(unquote(path)) if path else None
    except Exception:
        return None


def prune_missing_generations(repo_id: str, cfg: EmbedConfig) -> int:
    """清理「僵尸记录」：指向本机留存图、但磁盘文件已不存在的 generation 条目
    （多因用户手动删磁盘文件、未走应用删除按钮，留下裂图记录）。返回删除条数。
    只删本机留存且确实缺失的；外部 URL 图(无 local-view)无法判定存在性，一律保留。"""
    store = _store(_repo_collection(repo_id), cfg)
    removed = 0
    for d in _dump(_repo_collection(repo_id), cfg):
        if d.get("kind") != "generation":
            continue
        p = _local_path_of((d.get("image_url") or "").strip())
        if p is None or p.is_file():
            continue  # 非本机图 或 文件仍在 → 保留
        try:
            store.delete(ids=[d["id"]])
            removed += 1
        except Exception:
            pass
    return removed


def update_doc(doc_id: str, text: str, repo_id: str, cfg: EmbedConfig,
               title: str = "") -> bool:
    """编辑单条内容。locked 条目拒绝修改，返回 False。"""
    if not text.strip():
        return False
    store, meta = _find_store(doc_id, repo_id, cfg)
    if store is None or meta.get("locked"):
        return False  # 不存在或系统条目不可改
    meta["title"] = title or meta.get("title", "")
    store.update_document(doc_id, Document(page_content=text, metadata=meta))
    return True


# 系统指令功能说明（不可删改），用固定 id 幂等播种。
# seed_system_docs 会自动删除本字典之外的旧 sys- 条目，故可自由增删 id。
_SYSTEM_DOCS = {
    "sys-agent": "本工具默认是图像智能体：直接用自然语言说需求即可，无需记指令。"
    "想生图就描述画面（如「画一个雪山下的湖泊，黄昏」）；想反推/改图就贴上图片并说要求"
    "（如「参考这张图，改成赛博朋克风再生成」）；也可直接问绘画相关问题。智能体自行"
    "决定反推、生图、联网找灵感还是直接回答，并自动调用对话模型与生图模型。"
    "先在「设置」里配好对话模型和生图模型的 API 即可开始。",

    "sys-image": "生图与反推：设置→生图模型配好 API 后，描述画面智能体即调它出图；"
    "上传图片并提要求，智能体会先反推图片提示词再按需改图生成。"
    "底部可切换对话模型、生图模型，以及出图的比例（如 16:9）和分辨率档（1k/2k/4k）。"
    "生成的图自动留存到设置的输出目录，全分辨率原图保存，清理 ComfyUI 输出后仍可查看和「再改进」。",

    "sys-find": "联网找灵感：输入 /find 主题（如 /find 哥特萝莉裙），或直接说「帮我找找XX的参考」，"
    "工具会联网搜索并提炼成一张「灵感卡」，展示提示词与来源，点卡片右下角可把提示词插入对话继续生图。"
    "注意：访问外网需在设置里打开代理开关并填代理地址，否则搜索会失败。",

    "sys-knowledge": "知识库（每个仓库独立）：点顶部「知识库」录入角色设定、画风说明等参考资料，"
    "之后该仓库的对话会自动检索这些资料作答（RAG）。适合放需要反复复用的固定设定，"
    "让 AI 记住角色长相、世界观等，不必每次重复描述。",

    "sys-assets": "资产库与仓库：每次生成的图自动入当前仓库的资产库，可在仓库详情页看图片网格。"
    "资产支持标签管理——AI 自动切词打标，也可手动增删标签、用多个关键词组合搜索（AND）。"
    "仓库之间内容隔离，切换仓库互不干扰，可按仓库名检索。",

    "sys-queue": "生成进行中还能继续发消息：新消息进入输入框上方的队列，当前这轮结束后自动按序发出。"
    "队列里每条可删除，或点「引导」立即打断当前生成并让 AI 结合已生成内容续写（=合并）。"
    "生成过程已与界面解耦：切换仓库、刷新页面都不会丢，后台跑完会自动补回结果。",

    "sys-w": "指令 /w：选择工作流模板（专业控制，给需要画布级精调的场景）。输入 /w 弹出模板列表，"
    "或 /w 模板名 直接选；选中后在对话流插入工作流卡片，可在真实 ComfyUI 画布里调参，"
    "点「选择完毕」确认。日常生图用自然语言交给智能体即可，无需走工作流。",

    "sys-s": "指令 /s：启动已确认的工作流出图。先用 /w 选模板并在画布里调好点「选择完毕」，"
    "再输入 /s 提交到 ComfyUI 出图。需要 ComfyUI 已启动（未启动时若在设置填了 ComfyUI 目录会尝试自动拉起）。",

    "sys-a": "指令 /a：AI 编排工作流输入口。当对话里有配置了「替换输入/输出节点」的工作流卡时，"
    "直接用自然语言说需求，AI 会判断是否为编排意图并规划各输入口填什么，生成计划卡供你确认后写入画布；"
    "也可点工作流卡上的「AI 编排」按钮，或手动输入 /a 模板名 需求。确认后直接 /s 出图。",

    "sys-models": "模型下载：顶部「模型下载」可从 HuggingFace / Civitai 下载模型到 ComfyUI 的 models 目录，"
    "下载后 ComfyUI 自动识别。ComfyUI 节点面板（对话页顶部按钮）可打开原生 ComfyUI 界面做复杂节点操作。",
}


def seed_system_docs(cfg: EmbedConfig) -> int:
    """把系统指令功能说明播种进系统库（kind=system, locked=true）。

    内容幂等：固定 id；不存在则新增，已存在但内容变化则覆盖更新（改文档自动同步）。
    返回本次新增或更新的条数。需要可用嵌入接口。
    """
    store = _store(SYSTEM_COLLECTION, cfg)
    try:
        got = store.get()
        ids_all = got.get("ids", []) or []
        old = {i: (got.get("documents") or [])[n] for n, i in enumerate(ids_all)}
        metas_all = {i: (got.get("metadatas") or [])[n] or {} for n, i in enumerate(ids_all)}
    except Exception:
        old, metas_all = {}, {}
    meta = {"kind": "system", "locked": True, "title": "系统指令"}
    changed = 0
    for sid, text in _SYSTEM_DOCS.items():
        if sid not in old:
            store.add_documents([Document(page_content=text, metadata=meta)], ids=[sid])
            changed += 1
        elif old[sid] != text:
            store.update_document(sid, Document(page_content=text, metadata=meta))
            changed += 1
    # 清理已废弃的旧系统条目（id 以 sys- 开头但已不在字典中，如旧的 sys-p/sys-r/sys-g）
    stale = [i for i in old if i.startswith("sys-") and i not in _SYSTEM_DOCS]
    if stale:
        store.delete(ids=stale)
        changed += len(stale)
    # 脏数据自查：系统库应只含 system 条目；清掉历史混入的非 system 孤儿
    # （如早期版本误入 global 的 generation/document），避免它对所有仓库可见。
    dirty = [i for i in ids_all
             if i not in _SYSTEM_DOCS and (metas_all.get(i, {}).get("kind") != "system")]
    if dirty:
        store.delete(ids=dirty)
        changed += len(dirty)
    return changed
