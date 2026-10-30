"""检索召回域（原 agent_graph E 簇，逐字节搬移）。

逐字节自 agent_graph.py 搬移（方案 B 第一步，见 docs/memory/agent-graph-seam-plan-2026-09-16.md）；
行为等价由既有测试网 + 搬移块 sha1 校验保证。
"""

from __future__ import annotations
from app.services.agent_contracts import CtxLike
from collections import OrderedDict
import logging
import threading
from app.services import run_trace

_LOG = logging.getLogger("uvicorn.error")

def _embed_cfg(ctx: CtxLike):
    """从 ctx 取嵌入配置 → EmbedConfig。

    请求里的嵌入配置不完整（手机默认「云端嵌入」未填完、或只在设置里配了本机嵌入）
    ⇒ 退执行器自己的设置（`embed_resolution`）；两者都不可用 → None（无法走语义检索/入库）。
    """
    from app.services import embed_resolution
    from app.services.rag_backend import EmbedConfig

    requested = EmbedConfig(
        (ctx.get("embed_base") or "").strip(), ctx.get("embed_key", ""),
        (ctx.get("embed_model") or "").strip(),
        proxy=ctx.get("embed_proxy", ""),
    )
    return embed_resolution.resolve(requested)


def _history_embed_fn(ctx: dict):
    """P3①（2026-09-06）：历史语义分档的批量嵌入函数（context_gate 可选增强）。

    缺嵌入配置 → None（context_gate 纯词表分档）；返回 callable(list[str]) →
    list[list[float]]。嵌入失败由 context_gate 静默吞掉退回词表，此处不兜异常——
    保证「语义是增强不是依赖」的边界只在一个地方实现。
    """
    cfg = _embed_cfg(ctx)
    if cfg is None:
        return None
    from app.services import rag_backend

    prefix = f"{cfg.mode}|{cfg.embed_model}|"

    def _embed(texts: list[str]) -> list[list[float]]:
        with _history_embed_lock:
            missing = [t for t in texts if prefix + t not in _history_embed_cache]
        if missing:
            vecs = rag_backend.embeddings(cfg).embed_documents(missing)
            with _history_embed_lock:
                for t, v in zip(missing, vecs):
                    _history_embed_cache[prefix + t] = v
                    _history_embed_cache.move_to_end(prefix + t)
                while len(_history_embed_cache) > _HISTORY_EMBED_CACHE_CAP:
                    _history_embed_cache.popitem(last=False)
        with _history_embed_lock:
            return [list(_history_embed_cache[prefix + t]) for t in texts]

    return _embed


def _rag_recall_text(ctx: CtxLike, repo_id: str, query: str, k: int = 6) -> str:
    """从 rag_store 召回本仓库知识库条目 + 检索表行（kind!=generation），拼成候选文本。

    这是"表格+RAG 结合"的读侧接缝：curator 知识与检索表行同库同通道，一起按 query 召回。
    缺嵌入配置/无命中/异常 → 空串（caller 回退纯纪要召回，不阻断）。
    """
    cfg = _embed_cfg(ctx)
    if cfg is None or not (repo_id and query.strip()):
        run_trace.emit(ctx, "rag.retrieve", status="skipped",
                       reason="missing_embedding_config" if cfg is None else "missing_query",
                       query=query)
        return ""
    try:
        from app.services import rag_store
        candidates = rag_store.retrieve_with_trace(
            repo_id, cfg, query, k=max(k * 2, 12), include_system=False)
        hits = [hit for hit in candidates if hit.get("kind") != "table_row"][:k]
    except Exception as exc:  # noqa: BLE001  召回失败不阻断叙述
        run_trace.emit(ctx, "rag.retrieve", status="error", query=query, error=str(exc))
        return ""
    run_trace.emit(ctx, "rag.retrieve", status="ok", query=query, hit_count=len(hits), hits=hits)
    return "\n".join(
        f"- {hit.get('content', '')}" for hit in hits if (hit.get("content") or "").strip()
    )


def _table_recall_text(ctx: CtxLike, repo_id: str, query: str, k: int = 5) -> str:
    """检索表专属读通道；与普通知识 RAG 分池、分配额。"""
    try:
        from app.services import table_store
        tables = table_store.load(ctx.get("output_dir") or "", repo_id)
        rows = table_store.recall_retrieval_rows(tables, query, k=k)
    except Exception as exc:  # noqa: BLE001
        run_trace.emit(ctx, "table.retrieve", status="error", query=query, error=str(exc))
        return ""
    run_trace.emit(ctx, "table.retrieve", status="ok", query=query, hit_count=len(rows), hits=rows)
    return "\n".join(f"- {row}" for row in rows)


def _repo_tables_for_facts(ctx: CtxLike, repo_id: str) -> list:
    """插画视觉事实兜底读通道：整读 通用表（含 重要角色表），不检索、不分配额。

    世界书视觉锚按条目名匹配，剧情常用名/道号可能连不上（星织恋↔角色卡·绮柔）；
    表格按剧情进度维护、姓名与正文一致，作为未命中角色的【外貌】/【穿着】真源。
    读失败静默空表——事实兜底缺失不阻断出图，由 illustration.profile 的
    appearance_missing_actors 告警暴露。
    """
    if not repo_id:
        return []
    try:
        from app.services import table_store
        return table_store.load(ctx.get("output_dir") or "", repo_id)
    except Exception:  # noqa: BLE001
        return []


def _repo_table_absent_actors(ctx: CtxLike, repo_id: str, known: list[str]) -> set:
    """表格在场状态里明确「不在场」的绑定角色——点名提及不等于画面在场。

    2026-09-01 用户实锤：高潮段只有谢若冰（浮花泉封域内），星织恋在封域外石廊
    （表格在场状态=不在场），但正文/状态栏都点名了她 → request_actors 把两人
    都列入，提示词写成 2girls。姓名+在场状态列是剧情进度维护的真源，优先于点名。
    """
    if not repo_id or not known:
        return set()
    try:
        tables = _repo_tables_for_facts(ctx, repo_id)
        absent: set = set()
        known_set = set(known)
        for table in tables:
            if not isinstance(table, dict):
                continue
            cols = table.get("columns") or []
            if "姓名" not in cols or "在场状态" not in cols:
                continue
            name_idx = cols.index("姓名")
            state_idx = cols.index("在场状态")
            for row in (table.get("rows") or []):
                if not isinstance(row, list) or len(row) <= max(name_idx, state_idx):
                    continue
                name = str(row[name_idx]).strip()
                state = str(row[state_idx]).strip()
                if name in known_set and state.startswith("不在场"):
                    absent.add(name)
        return absent
    except Exception:  # noqa: BLE001 - 表格在场状态是兜底约束，读失败不阻断出图
        return set()


def _reindex_retrieval_tables(ctx: CtxLike, repo_id: str, tables: list) -> None:
    """把 mode=retrieval 的表的当前行重灌进 RAG（表格写回后调用）。缺嵌入配置则跳过。"""
    cfg = _embed_cfg(ctx)
    if cfg is None or not repo_id:
        return
    try:
        from app.services import rag_store, table_store
        for t in table_store.retrieval_tables(tables):
            texts = [table_store.row_text(t, r) for r in (t.get("rows") or [])]
            rag_store.index_table_rows(repo_id, cfg, t.get("uid", ""), t.get("name", ""), texts)
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 不阻断叙述，但索引与表格数据会分叉
        _LOG.warning("检索表重灌索引失败（不阻断叙述，但索引与表格数据可能分叉）: %s", exc)


def _curator_index_fn(ctx: CtxLike, repo_id: str):
    """构造条目维护 Agent 的写库闭包 (text,title)→写入本仓库 RAG 知识库。缺 embed 配置 → None（不写）。"""
    cfg = _embed_cfg(ctx)
    if cfg is None or not repo_id:
        return None

    def _index(text: str, title: str):
        from app.services import rag_store
        result = rag_store.index_document(repo_id, cfg, text, title)
        run_trace.emit(ctx, "rag.write", source="curator", title=title, content=text, result=result)
        return result
    return _index


_history_embed_cache: "OrderedDict[str, list[float]]" = OrderedDict()


_history_embed_lock = threading.Lock()


_HISTORY_EMBED_CACHE_CAP = 256
