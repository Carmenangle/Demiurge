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
import logging
import re
import time
import uuid
import json
from pathlib import Path

import httpx
from langchain_core.documents import Document

from app.services import (
    asset_store, comfyui_client, rag_backend, rag_retrieval, reranker, thumbnail_store,
)
from app.services.rag_backend import EmbedConfig


SYSTEM_COLLECTION = "global"  # 系统指令独占库，全局共享
# 上网素材合成仓库（P2-1 完整刀）：_web_materials/ 在 outputDir 根、全局共享、
# 不属于任何真实仓库 ⇒ 素材资产记录统一落这个作用域（Chroma 库名 repo_web_materials）。
# ⚠ 它只是 RAG 集合作用域，不进仓库注册表/仓库选择器。
WEB_MATERIALS_REPO_ID = "web-materials"
_log = logging.getLogger("uvicorn.error")


def _asset_db_delete(ids: list[str]) -> None:
    """主表对账硬删（prune/dedup：破损与重复记录不进回收站）。失败只留痕。"""
    try:
        asset_store.delete_rows(ids)
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 主表对账失败不阻断向量侧清理
        _log.warning("资产主表硬删失败 ids=%s: %s", ids, exc)


def _sync_asset_tags(doc_id: str, tags: list[str]) -> None:
    """主表标签对账（快照新增/切换/删除后调用）。

    SQLite 主表是**列表与 FTS5 搜索的真源**：只改 Chroma 元数据的话，刷新后
    列表还显示旧标签（2026-10-18 用户实测快照切换后标签回退）。失败只留痕。
    """
    try:
        asset_store.sync_tags(doc_id, [str(t) for t in tags if str(t).strip()])
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 主表同步失败不阻断向量写
        _log.warning("主表标签同步失败 id=%s: %s", doc_id, exc)


class DocumentImportError(RuntimeError):
    def __init__(self, imported: int, cause: Exception):
        self.imported = imported
        super().__init__(f"导入失败（已入 {imported} 篇）：{cause}")


def _repo_collection(repo_id: str) -> str:
    """仓库内容库名：repo_<规范化id>。

    Chroma collection 名限制：3-63 字符、首尾为字母数字、仅 [a-zA-Z0-9._-]。
    repo_id 多为 UUID（含连字符，合法），但 home/空 等需兜底。非法字符替换为下划线。    """
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


def generation_doc_id(repo_id: str, image_url: str) -> str:
    """生成/素材资产记录的 doc_id（单一公式，index 与 delete 共用）。

    有图 = gen-<sha1(repo_id|image_url)>：同一张图重复入库天然 upsert（防双写/去重）；
    无图 = 随机 uuid。⚠ 公式原内联在 index_generation，抽取须逐字等价
    （img 先 strip、repo_id 原样拼接）。
    """
    img = (image_url or "").strip()
    return ("gen-" + hashlib.sha1(f"{repo_id}|{img}".encode("utf-8")).hexdigest()
            if img else str(uuid.uuid4()))


def index_generation(repo_id: str, cfg: EmbedConfig,
                     prompt: str, tags: str = "", image_url: str = "",
                     created_at: int | None = None, description: str = "",
                     template_name: str = "", model_name: str = "",
                     lora_names: str = "", media_type: str = "image",
                     derived_from: list | None = None,
                     source: str = "", source_url: str = "",
                     source_meta: str = "") -> None:
    """生图完成后入库：提示词 + 标签合为一条文档，附图片URL元数据。写入本仓库库。

    只要有图片就入库——智能体出图常无提示词文本，此时用占位文本嵌入，
    避免因文本为空而整张图被丢弃（资产库会漏图）。
    标签同时结构化存入 metadata.tags（逗号串），供资产库标签展示/搜索/增删。
    template_name / model_name / lora_names 为工作流生成元数据（逗号分隔），
    供画布卡片展示「用哪个模板/主模型/LoRA 生成的」。
    media_type = image|video（默认 image）；derived_from 为派生链弱引用
    （[{asset_id|media_slot_ref, turn_id, kind}]，M2.1 只读展示不级联重建）。
    source / source_url / source_meta（P2-1 素材链专用）：素材资产记录带来源
    （渠道/来源页/作者/评级/搜索词 JSON）。默认空串 ⇒ **不写这三个键**，
    既有生成链与历史记录形状逐字不变。
    """
    text = "\n".join(t for t in [prompt, tags, description] if t and t.strip())
    if not text.strip() and not (image_url or "").strip():
        return  # 既无文本也无图，无意义
    embed_text = text if text.strip() else ("生成视频" if media_type == "video" else "生成图片")
    tag_list = _auto_tags(prompt, tags)
    store = _store(_repo_collection(repo_id), cfg)
    img = (image_url or "").strip()
    doc_id = generation_doc_id(repo_id, img)
    metadata = {"kind": "generation", "image_url": image_url or "",
                "repo_id": repo_id or "", "tags": ",".join(tag_list),
                "prompt": prompt or "", "description": description or "",
                "template_name": template_name or "",
                "model_name": model_name or "",
                "lora_names": lora_names or "",
                "media_type": media_type,
                # P0-2：本机静态图同步生成缩略图（.thumbs/ 派生层）；
                # 远程图/视频/失败一律空串，前端回落原图，不阻断入库
                "thumb_url": thumbnail_store.thumb_url_for(image_url),
                "derived_from": json.dumps(derived_from or [],
                                           ensure_ascii=False),
                "created_at": (created_at if created_at is not None
                               else int(time.time() * 1000))}
    # P2-1：素材来源信息仅素材链传；默认空 ⇒ 不写键（回归锁见
    # tests/test_web_material_assets.py::test_index_generation_默认路径不写_source三键）
    if source:
        metadata["source"] = source
    if source_url:
        metadata["source_url"] = source_url
    if source_meta:
        metadata["source_meta"] = source_meta
    store.add_documents([
        Document(page_content=embed_text, metadata=metadata)
    ], ids=[doc_id])
    # P1-1 双写（2026-10-18）：元数据真相落 SQLite 主表。失败只留痕——
    # 向量已入库，主表可由 scripts/backfill_asset_db.py 幂等补齐。
    try:
        asset_store.upsert_from_generation(repo_id, metadata, doc_id)
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 主表双写失败不阻断入库
        _log.warning("资产主表双写失败 id=%s: %s", doc_id, exc)


def index_generation_reliable(
    repo_id: str,
    cfg: EmbedConfig,
    prompt: str,
    tags: str = "",
    image_url: str = "",
    *,
    template_name: str = "",
    model_name: str = "",
    lora_names: str = "",
    attempts: int = 3,
    sleep_fn=time.sleep,
) -> int:
    """可靠入库事务；重试策略由 RAG Module 统一拥有，路由不参与编排。

    template_name / model_name / lora_names：工作流或云端生成的溯源元数据
    （2026-10-16 补：此前这条链路只收 prompt/tags/image_url ⇒ 资产库详情的
    「模板 / 模型 / LoRA」恒空。默认空串，既有调用行为不变）。
    """
    last: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            index_generation(repo_id, cfg, prompt, tags, image_url,
                             template_name=template_name, model_name=model_name,
                             lora_names=lora_names)
            return attempt + 1
        except Exception as exc:  # noqa: BLE001 - 嵌入后端错误类型不统一
            last = exc
            _log.warning("index-generation 失败(第%d次) repo=%s: %s", attempt + 1, repo_id, exc)
            if attempt + 1 < max(1, attempts):
                sleep_fn(0.8 * (attempt + 1))
    raise RuntimeError(f"入库失败(重试{max(1, attempts)}次)：{last}") from last


def embed_config_from_state() -> EmbedConfig:
    """后端自发入库链的嵌入配置：读 user_state.json 的 settings.embedModel。

    为什么不用前端 wire 透传：/rag/* 端点由前端传三元组，但「保存素材即入库」是
    后端自发链（image_store 保存成功后直接调），没有请求上下文带 embed 字段
    ⇒ 与 capability_handlers._embed_config_from_state 同源读运行态真源。
    ⚠ 已知局限：settings.embedModel.proxyUrl 是前端运行时解析值、通常不持久化
    ⇒ 需要代理的远程嵌入端点这里取不到代理（capability_handlers 同样局限）。
    """
    from app.config import DATA_DIR

    try:
        st = json.loads((DATA_DIR / "user_state.json").read_text(encoding="utf-8"))
        embed = (st.get("settings") or {}).get("embedModel") or {}
        if not isinstance(embed, dict):
            embed = {}
    except (OSError, json.JSONDecodeError):
        embed = {}
    return EmbedConfig(
        base_url=str(embed.get("baseUrl") or ""),
        api_key=str(embed.get("apiKey") or ""),
        embed_model=str(embed.get("modelName") or ""),
        model_dir=str(embed.get("modelDir") or ""),
        reranker_dir=str(embed.get("rerankerDir") or ""),
        mode=str(embed.get("mode") or "remote"),
        proxy=str(embed.get("proxyUrl") or ""),
    )


def index_web_material_asset(image_url: str, provenance: dict,
                             created_at: int | None = None) -> bool:
    """上网素材落盘后入库（P2-1 完整刀「入库即资产记录」）。

    provenance：image_store 写的 _web_materials/.provenance.json 条目
    {source_url, query, provider, title, downloaded_at, size, detected_ext,
     tags, author, rating}。
    - 作用域 = WEB_MATERIALS_REPO_ID（合成仓库；素材全局共享，不属任何真实仓库）；
    - source = provider（danbooru/pixiv 原名，其余归一 "web"，枚举不无限膨胀）；
    - tags 原站标签走 _auto_tags 同款过滤；**title 作 prompt**（进嵌入文本）；
      2026-10-18 修正：此前 title 塞 description ⇒ 素材的「视觉描述」字段其实是标题
      （用户实测「素材只有标签没有描述」），描述字段留空等 VLM 生成。
    - source_meta = JSON（作者/评级/搜索词/渠道/大小/下载时间）。

    失败只 warning 返回 False，**绝不抛**：保存已成功，入库失败不该让用户看到
    「保存失败」（与 capability_handlers 采集链的失败语义一致）。
    """
    if not (image_url or "").strip():
        return False
    prov = provenance if isinstance(provenance, dict) else {}
    provider = str(prov.get("provider") or "").strip()
    source = provider if provider in ("danbooru", "pixiv") else "web"
    tags = ",".join(str(t) for t in (prov.get("tags") or []) if str(t).strip())
    source_meta = json.dumps({
        "author": str(prov.get("author") or ""),
        "rating": str(prov.get("rating") or ""),
        "query": str(prov.get("query") or ""),
        "provider": provider,
        "size": prov.get("size") or 0,
        "downloaded_at": str(prov.get("downloaded_at") or ""),
    }, ensure_ascii=False)
    try:
        index_generation(
            WEB_MATERIALS_REPO_ID, embed_config_from_state(),
            prompt=str(prov.get("title") or ""), tags=tags, image_url=image_url,
            description="",
            source=source, source_url=str(prov.get("source_url") or ""),
            source_meta=source_meta, created_at=created_at,
        )
        return True
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 入库失败不丢文件（保存已成功）
        _log.warning("上网素材入库失败（文件已保存，资产库将缺席该条）: %s", exc)
        return False


def delete_web_material_asset(image_url: str) -> bool:
    """上网素材删除后同步删资产记录（P2-1 完整刀）。

    文件已由 image_store.delete_web_material 删除 ⇒ 这里只删库条目
    （remove_file=False）。从未入库过的素材静默返回 False。
    """
    if not (image_url or "").strip():
        return False
    try:
        return delete_doc(generation_doc_id(WEB_MATERIALS_REPO_ID, image_url),
                          WEB_MATERIALS_REPO_ID, embed_config_from_state())
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 删记录失败不阻断删文件
        _log.warning("上网素材资产记录删除失败（文件已删，资产库可能留幽灵条目）: %s", exc)
        return False


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


def import_documents(
    repo_id: str,
    cfg: EmbedConfig,
    documents: list[tuple[str, str]],
) -> dict[str, int]:
    """批量导入并保持既有的“报告已成功篇数”语义。"""
    imported = 0
    chunks = 0
    for text, title in documents:
        if not text.strip():
            continue
        try:
            count = index_document(repo_id, cfg, text, title)
        except Exception as exc:  # noqa: BLE001
            raise DocumentImportError(imported, exc) from exc
        if count:
            imported += 1
            chunks += count
    return {"documents": imported, "chunks": chunks}


def index_table_rows(repo_id: str, cfg: EmbedConfig,
                     table_uid: str, table_name: str, row_texts: list[str]) -> int:
    """把某张检索表的当前所有行索引进本仓库库（kind=table_row）。先清该表旧行再重灌，保证与表一致。

    表增删改后整表重灌（行数通常不大）：先删该 table_uid 下全部旧 table_row 条目，再按当前行重新入库。
    行文本由 table_store.row_text 渲染；id 用 (table_uid, 序号) 确定性生成，同表重灌相同 id 覆盖。
    与 curator 知识（kind=document）同库，一起被 retrieve 召回 → 真·表格+RAG 结合。返回入库行数。
    """
    if not (repo_id and table_uid):
        return 0
    store = _store(_repo_collection(repo_id), cfg)
    # 清旧行：查该表已有 table_row 条目并删除（行删除/缩减时不留孤儿）
    try:
        got = store.get(where={"table_uid": table_uid})
        old_ids = got.get("ids", []) or []
        if old_ids:
            store.delete(ids=old_ids)
    except Exception as exc:  # noqa: BLE001 - 清旧行失败会留孤儿索引，必须留痕
        _log.warning("表格旧行索引清理失败 table=%s: %s", table_uid, exc)
    texts = [t for t in row_texts if t and t.strip()]
    if not texts:
        return 0
    docs = [
        Document(page_content=t,
                 metadata={"kind": "table_row", "title": table_name or "",
                           "table_uid": table_uid, "repo_id": repo_id or ""})
        for t in texts
    ]
    ids = [f"trow-{table_uid}-{i}" for i in range(len(texts))]
    store.add_documents(docs, ids=ids)
    return len(texts)


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
                        query: str, k: int = 4, *, include_system: bool = True) -> list[dict]:
    """Hybrid 检索，返回可供评估追踪的结构化结果。

    普通知识问答默认合并系统库；剧情链路传 include_system=False，只召回当前作品，
    避免节点帮助文档污染角色记忆。
    """
    if not query.strip():
        return []
    candidate_k = max(k * 4, 12)
    sources = [(_repo_collection(repo_id), f"repo:{repo_id or 'home'}")]
    if include_system:
        sources.insert(0, (SYSTEM_COLLECTION, "system"))
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
             query: str, k: int = 4, *, include_system: bool = True) -> list[str]:
    """检索与 query 相关的片段（系统库 + 本仓库库合并），返回文本列表。
    只检索 kind != generation 的条目：单次出图的提示词/反推无复用价值，
    会污染对话检索；它们仍留在库里供资产库展示，只是不喂给 AI 检索。
    可复用知识（角色固定特征等）请走知识库手动录入（kind=document）。
    """
    return [
        hit["content"]
        for hit in retrieve_with_trace(repo_id, cfg, query, k, include_system=include_system)
    ]


def _parse_derived_from(raw: str) -> list[dict]:
    """解析 metadata 里 JSON 序列化的 derived_from；损坏/空返回 []。"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def _parse_source_meta(raw: str) -> dict:
    """解析 metadata 里 JSON 序列化的 source_meta（素材来源：作者/评级/搜索词
    等）；损坏/空返回 {}（P2-1，读侧投影给前端详情面板）。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _fresh_thumb_url(stored: str, image_url: str) -> str:
    """缩略图 URL 取用（2026-10-18）：元数据 thumb_url 若指向**已不存在文件** ⇒
    丢弃（防网格渲染破图），回退「只查不生成」的派生发现（历史补生成后无需重嵌
    元数据也能生效）。真实文件存在则原样返回（不因探测抖动换 URL）。"""
    thumb = (stored or "").strip()
    if thumb and thumbnail_store.local_path_of_url(thumb) is None:
        thumb = ""
    return thumb or thumbnail_store.thumb_url_if_ready(image_url)


def _dump(collection: str, cfg: EmbedConfig, where: dict | None = None) -> list[dict]:
    """导出单个 collection 的条目为统一 dict 列表。

    where：Chroma metadata 过滤（None=不过滤，行为与改造前一致）；
    供 list_generations 的 keyset 分页把 created_at 游标下推（P0-1）。
    """
    try:
        store = _store(collection, cfg)
        data = store.get(where=where)  # {ids, documents, metadatas}
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
            "prompt": meta.get("prompt", ""),
            "description": meta.get("description", ""),
            "repo_id": meta.get("repo_id", ""),
            "template_name": meta.get("template_name", ""),
            "model_name": meta.get("model_name", ""),
            "lora_names": meta.get("lora_names", ""),
            "media_type": meta.get("media_type", "image"),
            "thumb_url": meta.get("thumb_url", ""),
            "derived_from": meta.get("derived_from", ""),
            "created_at": int(meta.get("created_at", 0) or 0),
            "tag_snapshots": meta.get("tag_snapshots", ""),
            "active_tag_snapshot": meta.get("active_tag_snapshot", ""),
            "source": meta.get("source", ""),
            "source_url": meta.get("source_url", ""),
            "source_meta": meta.get("source_meta", ""),
        })
    return out


def list_docs(repo_id: str, cfg: EmbedConfig) -> list[dict]:
    """列出「系统库 + 本仓库库」所有条目。返回 [{id, content, kind, title, locked, image_url}]。"""
    out = _dump(SYSTEM_COLLECTION, cfg)
    out += _dump(_repo_collection(repo_id), cfg)
    # 系统条目排前面
    out.sort(key=lambda d: (0 if d["locked"] else 1))
    return out


def list_generations(repo_id: str, cfg: EmbedConfig, *, limit: int = 0,
                     before_ts: int = 0) -> list[dict]:
    """列出某仓库的生成记录（kind=generation）。

    返回 [{id, prompt, image_url, tags, templateName, modelName, loraNames, ...}]，
    供仓库详情页图片网格展示。字段用 camelCase，与前端 GenLike 契约一致，
    否则画布详情面板的「模板/模型/LoRA」取不到值。

    P0-1 keyset 分页（created_at 倒序）：
    - before_ts > 0：只返回 created_at < before_ts 的记录（游标下推 Chroma where）；
    - limit > 0：排序后截前 limit 条（调用方按「多取一条」判 has_more）。
    两者都为 0 时**逐字返回全量原顺序**（默认路径与改造前一致，既有调用方无感）。
    """
    where = ({"kind": "generation", "created_at": {"$lt": before_ts}}
             if before_ts > 0 else None)
    out: list[dict] = []
    for d in _dump(_repo_collection(repo_id), cfg, where=where):
        if d["kind"] != "generation" or not d["image_url"]:
            continue
        legacy = d["content"] if d["content"] != "生成图片" else ""
        prompt = d.get("prompt", "") or legacy
        tags = [t for t in (d.get("tags", "") or "").split(",") if t.strip()]
        out.append({"id": d["id"], "repo_id": repo_id, "prompt": prompt,
                    "description": d.get("description", ""),
                    "image_url": d["image_url"], "tags": tags,
                    "templateName": d.get("template_name", ""),
                    "modelName": d.get("model_name", ""),
                    # lora_names 在 metadata 存为逗号分隔字符串，读时拆为数组与前端 GenLike 类型一致
                    "loraNames": [s for s in (d.get("lora_names", "") or "").split(",") if s.strip()],
                    "mediaType": d.get("media_type", "image"),
                    # P0-2：优先用入库时写入的 thumb_url；历史记录没有时读路径兜底发现
                    # （`/rag/generate-thumbs` 补生成后不重嵌元数据也能生效）
                    # 2026-10-18：元数据 thumb_url 可能**陈旧**（.thumbs 被清/迁移后文件
                    # 已不在）⇒ 指向失效文件时必须丢弃，否则网格直接渲染破图（prune 只看
                    # 原图，救不了这个场景）。
                    "thumbUrl": _fresh_thumb_url(d.get("thumb_url", ""), d["image_url"]),
                    "derivedFrom": _parse_derived_from(d.get("derived_from", "")),
                    # P2-1：素材资产来源（generation 链为空串/{}，前端 `|| ""` 兼容历史记录）
                    "source": d.get("source", ""),
                    "sourceUrl": d.get("source_url", ""),
                    "sourceMeta": _parse_source_meta(d.get("source_meta", "")),
                    "created_at": d.get("created_at", 0)})
    if limit <= 0 and before_ts <= 0:
        return out  # 默认路径：原顺序原样返回
    out.sort(key=lambda d: d.get("created_at", 0), reverse=True)
    return out[:limit] if limit > 0 else out


def _generation_search_item(item: dict) -> dict:
    content = "\n".join(value for value in [
        item.get("prompt", ""), " ".join(item.get("tags", [])),
        item.get("description", ""),
    ] if value)
    # 标题：描述优先（生成图的语义摘要），回退 prompt（素材的 title 存在 prompt 里，
    # 2026-10-18 起描述字段只放 VLM 视觉描述）
    return {**item, "content": content, "title": item.get("description") or item.get("prompt") or ""}


def search_generations(repo_ids: list[str], cfg: EmbedConfig,
                       query: str, k: int = 32) -> list[dict]:
    """generation 专用 Hybrid 检索；与剧情 retrieve 隔离。"""
    if not query.strip():
        return []
    cap = max(1, min(int(k), 100))
    candidate_k = max(cap * 4, 24)
    documents = [
        _generation_search_item(item)
        for repo_id in dict.fromkeys(repo_ids)
        for item in list_generations(repo_id, cfg)
    ]
    by_image = {
        (str(item.get("repo_id") or ""), str(item.get("image_url") or "")): item
        for item in documents
    }
    rankings: list[tuple[str, list[dict]]] = []
    try:
        vector = rag_backend.embed_query(cfg, query)
    except Exception:
        vector = None
    if vector is not None:
        for repo_id in dict.fromkeys(repo_ids):
            try:
                dense_docs = _store(_repo_collection(repo_id), cfg).similarity_search_by_vector(
                    vector, k=candidate_k, filter={"kind": "generation"},
                )
            except Exception:
                dense_docs = []
            dense = []
            for doc in dense_docs:
                item = by_image.get((repo_id, str((doc.metadata or {}).get("image_url") or "")))
                if item is not None:
                    dense.append(item)
            rankings.append((f"dense:repo:{repo_id}", dense))
    rankings.append(("bm25", rag_retrieval.sparse_rank(query, documents, candidate_k)))
    try:
        from app.services import visual_asset_index

        visual_ids = visual_asset_index.search(repo_ids, query, candidate_k)
        by_id = {str(item.get("id") or ""): item for item in documents}
        rankings.append(("visual", [by_id[item_id] for item_id in visual_ids if item_id in by_id]))
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 视觉检索失败不阻断，但检索融合缺视觉一路
        _log.warning("视觉资产检索失败（维持文本语义搜索，但融合结果缺视觉一路）: %s", exc)
    fused = rag_retrieval.rrf_fuse(rankings, candidate_k)
    if rag_retrieval.needs_rerank(fused):
        reranked = reranker.rerank(
            query, fused, cfg.reranker_dir, cap,
            instruction="Rank generated images by how well their prompt, tags and description match the query.",
        )
        fused = reranked or fused
    return [{key: value for key, value in item.items() if key not in {"content", "title"}}
            for item in fused[:cap]]


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
    content = (got.get("documents") or [""])[0] or ""
    if meta.get("kind") == "generation":
        content = "\n".join(value for value in [
            str(meta.get("prompt") or ""), meta["tags"],
            str(meta.get("description") or ""),
        ] if value) or "生成图片"
    store.update_document(doc_id, Document(page_content=content, metadata=meta))
    # P1 主表对账：标签改动同步（asset_tags 精确过滤 + FTS 重建）
    if meta.get("kind") == "generation":
        try:
            asset_store.sync_tags(doc_id, list(dict.fromkeys(clean)))
        except Exception as exc:  # noqa: BLE001 [EXC-留痕] 主表同步失败不阻断向量写
            _log.warning("主表标签同步失败 id=%s: %s", doc_id, exc)
    return True


# ===== 标签快照（版本化反推打标，2026-10-17 用户定案：不合并、按快照切换） =====

def _parse_tag_snapshots(meta: dict) -> list[dict]:
    raw = meta.get("tag_snapshots") or ""
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _content_for(meta: dict) -> str:
    if meta.get("kind") == "generation":
        return "\n".join(v for v in [
            str(meta.get("prompt") or ""), str(meta.get("tags") or ""),
            str(meta.get("description") or ""),
        ] if v) or "生成图片"
    return str(meta.get("tags") or "")


def add_tag_snapshot(doc_id: str, repo_id: str, cfg: EmbedConfig,
                     tags: list[str], *, model: str = "", label: str = "") -> dict | None:
    """新增一版标签快照并设为当前（**不合并旧标签**，旧版保留可切回）。返回快照 dict。"""
    store, meta = _find_store(doc_id, repo_id, cfg)
    if store is None or meta.get("locked"):
        return None
    clean = list(dict.fromkeys(t.strip() for t in tags if t and t.strip()))
    snaps = _parse_tag_snapshots(meta)
    snap = {"id": uuid.uuid4().hex[:12], "tags": clean, "model": model,
            "label": label or "", "at": int(time.time())}
    snaps.append(snap)
    meta["tag_snapshots"] = json.dumps(snaps, ensure_ascii=False)
    meta["active_tag_snapshot"] = snap["id"]
    meta["tags"] = ",".join(clean)
    store.update_document(doc_id, Document(page_content=_content_for(meta), metadata=meta))
    # P1 主表对账（2026-10-18）：快照操作也必须同步 SQLite，否则列表/搜索读主表会看到旧标签
    _sync_asset_tags(doc_id, clean)
    return snap


def list_tag_snapshots(doc_id: str, repo_id: str, cfg: EmbedConfig) -> dict:
    store, meta = _find_store(doc_id, repo_id, cfg)
    if store is None:
        return {"snapshots": [], "active": ""}
    return {"snapshots": _parse_tag_snapshots(meta),
            "active": str(meta.get("active_tag_snapshot") or "")}


def set_active_tag_snapshot(doc_id: str, repo_id: str, cfg: EmbedConfig,
                            snapshot_id: str) -> bool:
    """切到某一版标签快照（把 metadata.tags 同步成该版）。不存在返回 False。"""
    store, meta = _find_store(doc_id, repo_id, cfg)
    if store is None or meta.get("locked"):
        return False
    snap = next((s for s in _parse_tag_snapshots(meta) if str(s.get("id")) == str(snapshot_id)), None)
    if snap is None:
        return False
    meta["active_tag_snapshot"] = str(snapshot_id)
    meta["tags"] = ",".join(snap.get("tags") or [])
    store.update_document(doc_id, Document(page_content=_content_for(meta), metadata=meta))
    _sync_asset_tags(doc_id, list(snap.get("tags") or []))   # P1 主表对账
    return True


def delete_tag_snapshot(doc_id: str, repo_id: str, cfg: EmbedConfig,
                        snapshot_id: str) -> bool:
    """删一版快照；删的是当前版时回退到最后一版（全删则清空 tags）。"""
    store, meta = _find_store(doc_id, repo_id, cfg)
    if store is None or meta.get("locked"):
        return False
    snaps = _parse_tag_snapshots(meta)
    remaining = [s for s in snaps if str(s.get("id")) != str(snapshot_id)]
    if len(remaining) == len(snaps):
        return False
    meta["tag_snapshots"] = json.dumps(remaining, ensure_ascii=False)
    if str(meta.get("active_tag_snapshot") or "") == str(snapshot_id):
        if remaining:
            last = remaining[-1]
            meta["active_tag_snapshot"] = str(last.get("id"))
            meta["tags"] = ",".join(last.get("tags") or [])
        else:
            meta["active_tag_snapshot"] = ""
            meta["tags"] = ""
    store.update_document(doc_id, Document(page_content=_content_for(meta), metadata=meta))
    _sync_asset_tags(doc_id, [t for t in (meta.get("tags") or "").split(",") if t.strip()])
    return True


def set_generation_description(doc_id: str, repo_id: str, cfg: EmbedConfig,
                               description: str) -> bool:
    """保存 VLM 描述并重建该 generation 的文本向量；不写聊天历史。"""
    value = (description or "").strip()
    if not value:
        return False
    store, meta = _find_store(doc_id, repo_id, cfg)
    if store is None or meta.get("kind") != "generation":
        return False
    got = store.get(ids=[doc_id])
    legacy = (got.get("documents") or [""])[0] or ""
    prompt = str(meta.get("prompt") or "")
    if not prompt and legacy != "生成图片":
        prompt = legacy
    meta["prompt"] = prompt
    meta["description"] = value
    content = "\n".join(part for part in [prompt, str(meta.get("tags") or ""), value] if part)
    store.update_document(doc_id, Document(page_content=content, metadata=meta))
    # P1 主表对账：描述同步（FTS 索引由触发器重建）
    try:
        asset_store.sync_description(doc_id, value)
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 主表同步失败不阻断向量写
        _log.warning("主表描述同步失败 id=%s: %s", doc_id, exc)
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
        thumbnail_store.remove_thumb(p)  # 顺手清派生缩略图（防 .thumbs/ 留孤儿）
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 删原图失败不阻断条目删除，但磁盘残留孤儿图
        _log.warning("删除本机留存原图失败（条目照常删除，但磁盘残留孤儿文件）: %s", exc)


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
    # P1-2 回收站（2026-10-18）：向量立即删（语义检索不再命中），元数据主表置
    # deleted_at（可恢复）。先 upsert 再软删 ⇒ 即使这条尚未回填过也能进回收站。
    try:
        asset_store.upsert_from_generation(repo_id, meta, doc_id)
        asset_store.soft_delete(doc_id)
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 回收站记账失败不阻断删除
        _log.warning("回收站记账失败 id=%s: %s", doc_id, exc)
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
                _asset_db_delete([d["id"]])   # 主表对账：去重硬删（不进回收站）
                removed += 1
            except Exception as exc:  # noqa: BLE001 - 去重删除失败留痕：重复条目会残留
                _log.warning("生成图去重删除失败 id=%s url=%s: %s", d.get("id"), url, exc)
    return removed


def local_path_of(image_url: str) -> Path | None:
    """从 local-view?path=... 解出本机磁盘路径；非本机留存图返回 None。

    2026-10-17：媒体产出已按类型迁入 <repo>/pictures|videos|audios/，但老索引仍记
    <repo>/<name> ⇒ 原路径不存在时在同级类型夹里找同名文件（否则 prune 会误判丢失、删记录）。
    """
    if not image_url or "local-view" not in image_url:
        return None
    try:
        from urllib.parse import urlparse, parse_qs, unquote
        path = (parse_qs(urlparse(image_url).query).get("path") or [""])[0]
        if not path:
            return None
        p = Path(unquote(path))
        if p.exists():
            return p
        for _sub in ("pictures", "videos", "audios"):
            alt = p.parent / _sub / p.name
            if alt.exists():
                return alt
        return p
    except Exception:
        return None


def _remote_view_target(image_url: str) -> dict | None:
    """解析 legacy remote-view 直链（/api/comfyui/view?filename=...&url=<comfyui>）。

    早期生成未落盘留存时，image_url 直接指向 ComfyUI output 代理；ComfyUI 清理输出后
    这类条目就成裂图。返回 {filename, type, subfolder, url}；非该形态返回 None。
    """
    if not image_url or "/api/comfyui/view?" not in image_url:
        return None
    try:
        from urllib.parse import urlparse, parse_qs

        query = parse_qs(urlparse(image_url).query)
        filename = (query.get("filename") or [""])[0]
        if not filename:
            return None
        return {
            "filename": filename,
            "type": (query.get("type") or ["output"])[0],
            "subfolder": (query.get("subfolder") or [""])[0],
            "url": (query.get("url") or [""])[0],
        }
    except Exception:
        return None


def _probe_external_image(url: str, timeout: float = 8) -> str:
    """外部 http(s) 图存在性探测（裂图清理用，2026-10-18）。

    返回三态 "ok"/"missing"/"unreachable"：
    - HEAD 优先；404/410 ⇒ missing（云端产物过期/删除的典型形态）；
    - 2xx ⇒ ok；405/501（CDN 不支持 HEAD）⇒ 用 Range GET 复核一次；
    - 其余状态、非 http(s) 形态、网络/TLS 异常 ⇒ unreachable（调用方按保留处理，
      绝不因探测失败误删真源）。
    ⚠ trust_env=False：不走环境代理（后端探活按直连判定；用户环境需代理时
    探活失败只会导致保留，不会误删）。
    """
    if not re.match(r"^https?://", url or "", re.IGNORECASE):
        return "unreachable"
    try:
        with httpx.Client(trust_env=False, follow_redirects=True, timeout=timeout) as client:
            resp = client.head(url)
            status = resp.status_code
            if status in (404, 410):
                return "missing"
            if 200 <= status < 300:
                return "ok"
            if status in (405, 501):
                resp = client.get(url, headers={"Range": "bytes=0-0"})
                status = resp.status_code
                if status in (404, 410):
                    return "missing"
                return "ok" if 200 <= status < 400 else "unreachable"
            return "unreachable"
    except Exception:  # noqa: BLE001 - 探活失败一律视为无法判定（保留）
        return "unreachable"


def list_generations_multi(repo_ids: list[str], cfg: EmbedConfig, *, limit: int = 0,
                           before_ts: int = 0) -> list[dict]:
    """多仓库合并列表（P0-1 keyset 分页的聚合页扩展，2026-10-18）。

    资产库页聚合全部仓库：此前只能「全量 dump 后前端切片」，千图库首屏越来越慢。
    这里每库按同一游标各取 limit 条 → 按 created_at 倒序归并 → 截 limit。
    全局 top-limit 必在各库 top-limit 的并集内 ⇒ 游标翻页不重不漏。
    limit=0 = 全量合并（旧行为，兼容升级全量/搜索路径）；按 id 去重（防御性）。
    """
    merged: list[dict] = []
    for rid in repo_ids:
        merged.extend(list_generations(rid, cfg, limit=limit, before_ts=before_ts))
    merged.sort(key=lambda d: d.get("created_at", 0), reverse=True)
    out: list[dict] = []
    seen: set[str] = set()
    for d in merged:
        if d["id"] in seen:
            continue
        seen.add(d["id"])
        out.append(d)
    return out[:limit] if limit > 0 else out


def prune_missing_generations(repo_id: str, cfg: EmbedConfig) -> int:
    """清理「僵尸记录」：指向本机留存图、但磁盘文件已不存在的 generation 条目
    （多因用户手动删磁盘文件、未走应用删除按钮，留下裂图记录）。返回删除条数。
    只删确实缺失的：local-view 条目按磁盘文件存在性判定；legacy remote-view 直链
    向 ComfyUI 探测，仅在其明确 404 时删。外部 URL 图与 ComfyUI 未起/探测异常
    （无法判定存在性）一律保留，防止误删真源。"""
    store = _store(_repo_collection(repo_id), cfg)
    removed = 0
    for d in _dump(_repo_collection(repo_id), cfg):
        if d.get("kind") != "generation":
            continue
        image_url = (d.get("image_url") or "").strip()
        missing = False
        p = local_path_of(image_url)
        if p is not None:
            missing = not p.is_file()
        else:
            target = _remote_view_target(image_url)
            if target is not None:
                missing = comfyui_client.probe_view(
                    target["url"], target["filename"],
                    target["type"], target["subfolder"],
                ) == "missing"
            else:
                # 2026-10-18（用户实测：清理裂图后 aiproxy 云端死链仍在）：外部 http(s)
                # 直链此前一律「无法判定→保留」⇒ 断链永远清不掉。改为显式探活：
                # 404/410 判缺失；200/网络异常仍保留（防误删）。
                missing = _probe_external_image(image_url) == "missing"
        if not missing:
            continue  # 文件仍在 / 无法判定（外链、ComfyUI 未起）→ 保留
        if p is not None:
            thumbnail_store.remove_thumb(p)  # 裂图记录清除时顺手清派生缩略图
        try:
            store.delete(ids=[d["id"]])
            _asset_db_delete([d["id"]])   # 主表对账：裂图硬删（不进回收站）
            removed += 1
        except Exception as exc:  # noqa: BLE001 - 失效清理失败留痕：幽灵条目会残留
            _log.warning("失效生成图清理失败 id=%s: %s", d.get("id"), exc)
    return removed


# 保留/合成 repo_id：不参与孤儿判定（素材合成仓库 + 默认 home）。
_RESERVED_REPO_IDS = {WEB_MATERIALS_REPO_ID, "home"}


def _registered_repo_ids() -> set[str]:
    """已注册仓库 id 集合（user_state.repos，前端仓库注册表的后端镜像）。
    读失败/无文件返回空集——空集会让「所有有记录的仓库」都算孤儿，过于激进，
    故读取异常时保守返回空集并留痕，由调用方决定是否继续。"""
    try:
        from app.config import DATA_DIR

        p = DATA_DIR / "user_state.json"
        if not p.is_file():
            return set()
        st = json.loads(p.read_text(encoding="utf-8"))
        return {str(r.get("id")) for r in (st.get("repos") or []) if r.get("id")}
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 读注册表失败不炸，返回空集
        _log.warning("读注册仓库表失败: %s", exc)
        return set()


def prune_orphan_repos(cfg: EmbedConfig) -> dict:
    """孤儿仓库清理（2026-10-18）：把「有资产记录、但不在注册表、也非合成 repo」的仓库
    也纳入 prune。此前 prune 只跑前端传来的注册仓库 ⇒ 孤儿仓库（删了文件夹/丢了注册）
    的裂图记录永远清不掉、在搜索里冒幽灵。

    对每个孤儿跑 prune_missing_generations——**只删磁盘文件已缺失的记录**；文件仍在的
    保留（仓库可能只是暂时未注册，防止误删真源）。返回 {orphan_repos, removed, per_repo}。
    """
    registered = _registered_repo_ids()
    conn = asset_store.get_connection()
    try:
        # 只算**在线**记录（deleted_at is null）——回收站软删行不掺和，避免把
        # 回收站里的行误判成孤儿、清掉用户本想保留/待还原的记录。
        rows = conn.execute(
            "select distinct repo_id from assets where deleted_at is null"
        ).fetchall()
    finally:
        conn.close()
    all_ids = {str(r["repo_id"]) for r in rows if r["repo_id"]}
    orphans = sorted(all_ids - registered - _RESERVED_REPO_IDS)
    per_repo: dict[str, int] = {}
    total = 0
    for rid in orphans:
        try:
            n = prune_missing_generations(rid, cfg)
        except Exception as exc:  # noqa: BLE001 [EXC-留痕] 单个孤儿失败不阻断其余
            _log.warning("孤儿仓库清理失败 repo=%s: %s", rid, exc)
            n = 0
        per_repo[rid] = n
        total += n
    return {"orphan_repos": orphans, "removed": total, "per_repo": per_repo}


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


def seed_system_docs_best_effort(cfg: EmbedConfig) -> int:
    """播种系统指令的 best-effort 包装：失败**只留痕不外抛**（供 /rag/list 顺带播种用）。

    与 `seed_system_docs` 的分工：手动 `/rag/seed` 要让用户看见失败（保持外抛）；列表接口
    的顺带播种只是尽力而为 —— 失败时列表照常返回，但**必须留痕**：系统指令静默缺索引
    等于检索质量无声退化（与审计 §4-C4 同档）。
    """
    try:
        return seed_system_docs(cfg)
    except Exception as exc:  # [EXC-留痕] 播种失败不阻断列表，但失败=系统指令静默缺索引
        _log.warning("播种系统指令失败（列表照常返回，检索可能缺系统指令）: %s", exc)
        return 0
