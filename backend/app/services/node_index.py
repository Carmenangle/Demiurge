"""ComfyUI 节点知识库：扫描已装节点 → 按包(python_module)归拢 → 存进 RAG。

一个 python_module(=一个 GitHub 项目/custom_nodes 目录) = 一条知识点。
内容优先取 object_info 自带 description(省 token)，供 AI 搭工作流时语义检索。
增量同步：对比当前 object_info 的包与已入库的，只处理新增/变更。
同步在后台线程跑并实时上报进度（done/total/当前包名），前端轮询 /nodes/sync-progress 显示。
"""
import re
import threading

from app.services import comfyui_client as _cc
from app.services import node_store as _rag
from app.services import reranker as _reranker
from app.services.rag_middleware import expand_query as expand_query, plan_queries
from app.services.rag_backend import EmbedConfig


# 同步进度（进程内单例，同一时刻只允许一个同步任务）：
#   running 是否在跑，done/total 已处理/总包数，current 当前包名，
#   synced/skipped 结果计数，error 失败信息，finished 是否已结束。
_PROGRESS: dict = {"running": False, "done": 0, "total": 0, "current": "",
                   "synced": 0, "skipped": 0, "error": "", "finished": False}
_LOCK = threading.Lock()


def _search_fused(groups: list[list[dict]], k: int, *,
                  weights: list[float] | None = None,
                  preserve_top: list[bool] | None = None) -> list[dict]:
    """多路结果用 RRF 融合，再做轻量 MMR 去重。

    Cross-Encoder 在 search() 中对最终候选做精排；这里的 MMR 先减少
    同一节点包重复占位，保证不同查询分支都有机会进入精排池。
    """
    scores: dict[str, float] = {}
    by_id: dict[str, dict] = {}
    branch_weights = weights or [1.0] * len(groups)
    preserved = preserve_top or [False] * len(groups)
    pinned_groups: list[list[str]] = []

    def merge_hit(target: dict, incoming: dict) -> None:
        if "node_names" in target or "node_names" in incoming:
            names = list(target.get("node_names", []) or [])
            known = set(names)
            for name in incoming.get("node_names", []) or []:
                if name not in known:
                    names.append(name)
                    known.add(name)
            target["node_names"] = names
        content = str(incoming.get("content", "") or "")
        if content and content not in str(target.get("content", "") or ""):
            target["content"] = f"{target.get('content', '')}\n\n{content}".strip()

    for group_index, hits in enumerate(groups):
        weight = branch_weights[group_index] if group_index < len(branch_weights) else 1.0
        group_pins: list[str] = []
        for rank, hit in enumerate(hits):
            ident = str(hit.get("id") or hit.get("python_module") or hit.get("title") or rank)
            scores[ident] = scores.get(ident, 0.0) + weight / (60 + rank)
            if ident in by_id:
                merge_hit(by_id[ident], hit)
            else:
                by_id[ident] = dict(hit)
                if "node_names" in hit:
                    by_id[ident]["node_names"] = list(hit.get("node_names", []) or [])
            if group_index < len(preserved) and preserved[group_index] and rank < 4:
                group_pins.append(ident)
        if group_pins:
            pinned_groups.append(group_pins)
    if not scores:
        return []
    pinned: list[str] = []
    for rank in range(max((len(group) for group in pinned_groups), default=0)):
        for group in pinned_groups:
            if rank < len(group) and group[rank] not in pinned:
                pinned.append(group[rank])
    max_score = max(scores.values()) or 1.0

    def tokens(hit: dict) -> set[str]:
        text = " ".join([
            str(hit.get("title", "")), str(hit.get("content", "")),
            " ".join(str(n) for n in hit.get("node_names", []) or []),
        ]).lower()
        return set(re.findall(r"[a-z0-9_]+|[一-鿿]", text))

    token_sets = {ident: tokens(hit) for ident, hit in by_id.items()}
    remaining = set(scores)
    selected: list[str] = pinned[:k]
    remaining.difference_update(selected)
    while remaining and len(selected) < k:
        best_id = None
        best_value = float("-inf")
        for ident in sorted(remaining):
            relevance = scores[ident] / max_score
            diversity_penalty = 0.0
            current_tokens = token_sets[ident]
            if selected and current_tokens:
                overlaps = []
                for chosen in selected:
                    chosen_tokens = token_sets[chosen]
                    union = current_tokens | chosen_tokens
                    overlaps.append(len(current_tokens & chosen_tokens) / len(union) if union else 0.0)
                diversity_penalty = max(overlaps, default=0.0)
            value = 0.78 * relevance - 0.22 * diversity_penalty
            if value > best_value:
                best_id, best_value = ident, value
        if best_id is None:
            break
        selected.append(best_id)
        remaining.remove(best_id)
    return [
        {**by_id[ident], "_rag_pinned": ident in pinned}
        for ident in selected
    ]


def _prioritize_named_nodes(hits: list[dict], query: str) -> list[dict]:
    """能力查询中，优先包含查询明确点名节点的候选，原排名作为次序。"""
    query_text = re.sub(r"[^a-z0-9一-鿿]+", " ", query.casefold()).strip()

    def exact_count(hit: dict) -> int:
        return sum(
            1 for name in (hit.get("node_names", []) or [])
            if len(str(name)) >= 3
            and re.sub(r"[^a-z0-9一-鿿]+", " ", str(name).casefold()).strip() in query_text
        )

    return [hit for _, hit in sorted(
        enumerate(hits), key=lambda pair: (-exact_count(pair[1]), pair[0]),
    )]


def _set_progress(**kw) -> None:
    with _LOCK:
        _PROGRESS.update(kw)


def sync_progress() -> dict:
    """当前同步进度快照，供前端轮询。"""
    with _LOCK:
        return dict(_PROGRESS)


def _pack_id(python_module: str) -> str:
    """稳定包 id：去掉 custom_nodes. 前缀，内置节点归到 'core'。"""
    pm = python_module or ""
    if pm.startswith("custom_nodes."):
        return pm[len("custom_nodes."):]
    return pm or "core"


def _group_by_pack(object_info: dict) -> dict[str, dict]:
    """把 {节点名: schema} 按 python_module 归拢成 {pack_id: {title, module, nodes:[...]}}。"""
    packs: dict[str, dict] = {}
    for name, info in object_info.items():
        pm = info.get("python_module", "") or "nodes"
        pid = _pack_id(pm)
        pack = packs.setdefault(pid, {
            "title": pid, "python_module": pm, "nodes": [], "categories": set(),
        })
        pack["nodes"].append({
            "name": name,
            "display": info.get("display_name", "") or name,
            "desc": (info.get("description", "") or "").strip(),
            "category": info.get("category", "") or "",
        })
        top = (info.get("category", "") or "").split("/")[0]
        if top:
            pack["categories"].add(top)
    return packs


def _pack_content(pack: dict) -> str:
    """把一个包的所有节点汇总成一段知识点文本(name·display·desc·category)。"""
    lines = [f"节点包：{pack['title']}（来源 {pack['python_module']}）"]
    for n in pack["nodes"]:
        lines.append(_node_line(n))
    return "\n".join(lines)


def _node_line(node: dict) -> str:
    seg = f"- {node['name']}"
    if node["display"] and node["display"] != node["name"]:
        seg += f"（{node['display']}）"
    if node["category"]:
        seg += f" [{node['category']}]"
    if node["desc"]:
        seg += f"：{node['desc']}"
    return seg


def _pack_chunks(pack: dict, max_nodes: int = 12) -> list[dict]:
    """按顶层 category 分组并限量切块，避免大插件包稀释单个节点语义。"""
    groups: dict[str, list[dict]] = {}
    for node in pack["nodes"]:
        category = (node.get("category") or "uncategorized").split("/", 1)[0]
        groups.setdefault(category, []).append(node)

    chunks: list[dict] = []
    for category, nodes in groups.items():
        for offset in range(0, len(nodes), max_nodes):
            batch = nodes[offset:offset + max_nodes]
            header = f"节点包：{pack['title']}；能力分类：{category}（来源 {pack['python_module']}）"
            chunks.append({
                "content": "\n".join([header, *(_node_line(node) for node in batch)]),
                "node_names": [node["name"] for node in batch],
                "categories": [category],
            })
    return chunks


def start_sync(comfy_url: str, cfg: EmbedConfig, full: bool = False) -> dict:
    """启动后台同步。先同步取 object_info（未运行立刻抛 ComfyError 让前端报错），
    再开线程逐包处理并上报进度。返回 {total_packs}（总包数，前端拿去显示 x/total）。
    已有任务在跑时拒绝重复启动。"""
    with _LOCK:
        if _PROGRESS["running"]:
            return {"total_packs": _PROGRESS["total"], "already_running": True}

    object_info = _cc.fetch_object_info(comfy_url, force=True)  # 同步必须读取最新安装清单
    packs = _group_by_pack(object_info)
    total = len(packs)
    _set_progress(running=True, done=0, total=total, current="",
                  synced=0, skipped=0, error="", finished=False)

    def _run():
        try:
            _do_sync(packs, cfg, full)
            _set_progress(running=False, finished=True, current="")
        except Exception as e:  # noqa: BLE001 后台线程兜底，错误进度里报
            _set_progress(running=False, finished=True, error=str(e), current="")

    threading.Thread(target=_run, daemon=True).start()
    return {"total_packs": total, "already_running": False}


def _do_sync(packs: dict[str, dict], cfg: EmbedConfig, full: bool) -> None:
    """实际逐包入库，每包更新进度。供后台线程调用。"""
    existing = {p["id"]: p for p in _rag.list_node_packs(cfg)}
    for stale_pack_id in existing.keys() - packs.keys():
        _rag.delete_node_pack(cfg, stale_pack_id)
        existing.pop(stale_pack_id, None)
    synced = 0
    skipped = 0
    done = 0
    for pid, pack in packs.items():
        _set_progress(current=pid)
        node_names = [n["name"] for n in pack["nodes"]]
        prev = existing.get(pid)
        unchanged = prev and set(prev.get("node_names", [])) == set(node_names)
        chunks_ready = unchanged and _rag.node_chunks_ready(cfg, pid)
        manual = bool(prev and prev.get("content_source") == "manual")
        manual_detail = _rag.get_node_pack(cfg, pid) if manual else None
        # 包与分块都完整才跳过；旧版本只有包文档时自动补建分块。
        if not full and unchanged and chunks_ready:
            skipped += 1
        else:
            if full or not unchanged:
                content = (
                    str(manual_detail.get("content", ""))
                    if manual_detail else _pack_content(pack)
                )
                _rag.index_node_pack(
                    cfg, pack_id=pid, title=pack["title"], content=content,
                    node_names=node_names, categories=sorted(pack["categories"]),
                    python_module=pack["python_module"],
                    content_source="manual" if manual_detail else "auto",
                )
            chunks = (
                [{
                    "content": str(manual_detail.get("content", "")),
                    "node_names": node_names,
                    "categories": sorted(pack["categories"]),
                }]
                if manual_detail else _pack_chunks(pack)
            )
            _rag.index_node_chunks(
                cfg, pack_id=pid, title=pack["title"], chunks=chunks,
                python_module=pack["python_module"],
                content_source="manual" if manual_detail else "auto",
            )
            synced += 1
        done += 1
        _set_progress(done=done, synced=synced, skipped=skipped)


def search(cfg: EmbedConfig, need: str, k: int = 8) -> list[dict]:
    """多路召回、RRF/MMR 融合，再用 Cross-Encoder 一次性精排。"""
    candidate_k = max(k * 4, 24)
    branches = plan_queries(need)
    dense_index = next(
        (index for index, branch in enumerate(branches) if branch.kind == "subquery"),
        0,
    )
    try:
        raw_groups = _rag.search_node_packs_many(
            cfg, [branch.query for branch in branches], k=candidate_k,
            dense_indexes={dense_index},
        )
    except Exception:
        raw_groups = [[] for _ in branches]
    raw_groups = [*raw_groups, *([[]] * max(0, len(branches) - len(raw_groups)))]
    groups = [
        _prioritize_named_nodes(hits, branch.query)
        if branch.kind == "capability" else hits
        for branch, hits in zip(branches, raw_groups)
    ]
    fused = _search_fused(
        groups, candidate_k,
        weights=[branch.weight for branch in branches],
        preserve_top=[branch.preserve_top for branch in branches],
    )
    pinned = [item for item in fused if item.get("_rag_pinned")]
    rerankable = [item for item in fused if not item.get("_rag_pinned")]
    rerank_k = min(max(k - len(pinned), 0), 4)
    reranked = _reranker.rerank(
        need, rerankable, getattr(cfg, "reranker_dir", ""), rerank_k,
    ) if rerank_k else []
    if not reranked:
        combined = fused[:k]
        return [
            {key: value for key, value in item.items() if key != "_rag_pinned"}
            for item in combined
        ]
    reranked_ids = {
        str(item.get("id") or item.get("python_module") or item.get("title") or "")
        for item in reranked
    }
    tail = [
        item for item in rerankable
        if str(item.get("id") or item.get("python_module") or item.get("title") or "")
        not in reranked_ids
    ]
    combined = [*pinned, *reranked, *tail][:k]
    return [
        {key: value for key, value in item.items() if key != "_rag_pinned"}
        for item in combined
    ]


def suggest_alternatives(cfg: EmbedConfig, missing_names: list[str],
                         object_info_keys: set | dict, k: int = 6) -> dict[str, list[str]]:
    """为「本机没装的节点」检索本机已装的同类平替（纯检索，不调对话模型，不会卡）。

    做法：用缺失节点名(拆词做 query)检索知识库→在命中包的 node_names 里挑**本机确实存在**
    (在 object_info_keys 里)的同类节点作为平替候选。返回 {缺失节点名: [平替1, 平替2, ...]}。
    治「AI 想用某能力但编了本机没有的节点」——给它本机真实的同类替代，而不是硬编白名单。"""
    out: dict[str, list[str]] = {}
    for miss in missing_names:
        # 缺失名拆词并做能力扩展；候选仍必须来自 object_info。
        q = " ".join(re.split(r"[_\-.|\s]+", miss))
        packs = search(cfg, q, k=k)
        alts: list[str] = []
        scored: list[tuple[float, str]] = []
        for p in packs:
            for n in p.get("node_names", []):
                if n not in object_info_keys or n == miss or n in alts:
                    continue
                schema = object_info_keys.get(n, {}) if isinstance(object_info_keys, dict) else {}
                blob = f"{n} {schema.get('display_name', '')} {schema.get('category', '')}".lower()
                miss_tokens = set(re.findall(r"[a-z0-9]+|[一-鿿]", q.lower()))
                overlap = sum(1 for token in miss_tokens if token and token in blob)
                scored.append((float(overlap), n))
                alts.append(n)
        # object_info_keys 旧调用仍传 set；保留检索顺序作为无 schema 时的兜底。
        if scored:
            scored.sort(key=lambda item: (-item[0], item[1]))
            alts = [n for _, n in scored]
        out[miss] = alts[:8]
    return out


def stats(cfg: EmbedConfig) -> dict:
    """知识库现状：包数 + 节点总数。"""
    packs = _rag.list_node_packs(cfg)
    return {
        "packs": len(packs),
        "nodes": sum(len(p.get("node_names", [])) for p in packs),
    }


def list_packs(cfg: EmbedConfig) -> list[dict]:
    """列出全部节点包（id/标题/节点数/来源），供管理页展示。按标题排序。"""
    packs = _rag.list_node_packs(cfg)
    packs.sort(key=lambda p: p.get("title", "").lower())
    return [{
        "id": p["id"], "title": p["title"],
        "node_count": len(p.get("node_names", [])),
        "python_module": p.get("python_module", ""),
    } for p in packs]


def get_pack(cfg: EmbedConfig, pack_id: str) -> dict | None:
    """读单个包完整内容（含用途正文 content），供查看/编辑。"""
    return _rag.get_node_pack(cfg, pack_id)


def update_pack_content(cfg: EmbedConfig, pack_id: str, content: str) -> bool:
    """人工改某包的用途正文并重嵌入。包不存在返回 False。"""
    return _rag.update_node_pack_content(cfg, pack_id, content)
