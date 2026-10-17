"""仓库 RAG 知识库路由：生成历史自动入库 + 手动参考资料入库。

接口地址/密钥/模型由前端从「设置 → 对话模型」透传（与 /api/ai/chat 一致）。
"""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import rag_store
from app.services.rag_backend import EmbedConfig

router = APIRouter()


class _EmbedFields(BaseModel):
    """三元组 wire 字段的公共基类；embed_cfg() 收成单一属主对象。"""
    base_url: str = ""
    api_key: str = ""
    embed_model: str = "text-embedding-3-small"
    embed_mode: Literal["remote", "local"] = "remote"
    embed_model_dir: str = ""
    reranker_model_dir: str = ""
    proxy_url: str = ""

    def embed_cfg(self) -> EmbedConfig:
        return EmbedConfig(
            base_url=self.base_url,
            api_key=self.api_key,
            embed_model=self.embed_model,
            model_dir=self.embed_model_dir,
            reranker_dir=self.reranker_model_dir,
            mode=self.embed_mode,
            proxy=self.proxy_url,
        )


class IndexGenRequest(_EmbedFields):
    thread_id: str = "home"        # 仓库 id
    prompt: str = ""               # 出图提示词 / 反推描述
    tags: str = ""                 # D站标签（版权/角色/普通/原数据）
    image_url: str = ""            # 结果图地址
    # 2026-10-16 补：生成溯源元数据（可选）。此前该端点不收这三个字段 ⇒ 经此链路
    # 入库的图在资产库详情里「模板 / 模型 / LoRA」恒为空（真机实测 130 条全空）。
    template_name: str = ""        # 工作流模板名（或「云端」标识）
    model_name: str = ""           # 主模型 / UNet / 云端模型名
    lora_names: str = ""           # LoRA 名，逗号分隔


@router.post("/index-generation")
def index_generation(req: IndexGenRequest) -> dict[str, object]:
    """生图完成后调用，把这次生成的提示词/标签/图片入仓库知识库。
    重试 3 次：embedding 偶发瞬时失败(ollama 并发/超时)会让「图落盘了但提示词/生成历史没进知识库」
    →资产库内容丢失。工作流批量出图时几张挤一起调 embedding 最易触发，故退避重试兜底。"""
    try:
        rag_store.index_generation_reliable(
            req.thread_id, req.embed_cfg(), req.prompt, req.tags, req.image_url,
            template_name=req.template_name, model_name=req.model_name,
            lora_names=req.lora_names,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}


class IndexDocRequest(_EmbedFields):
    thread_id: str = "home"
    text: str = ""                 # 参考资料正文
    title: str = ""


@router.post("/index-document")
def index_document(req: IndexDocRequest) -> dict[str, object]:
    """手动上传参考资料入库。返回入库条数。"""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="参考资料内容为空")
    try:
        n = rag_store.index_document(
            req.thread_id, req.embed_cfg(),
            req.text, req.title,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"入库失败：{e}")
    return {"ok": True, "chunks": n}


class ImportDoc(BaseModel):
    text: str = ""
    title: str = ""


class ImportDocsRequest(_EmbedFields):
    thread_id: str = "home"
    docs: list[ImportDoc] = []


@router.post("/import-documents")
def import_documents(req: ImportDocsRequest) -> dict[str, object]:
    """批量导入参考资料（从导出的 JSON 恢复 / 迁移到其它仓库）。返回导入的文档数与入库分块数。"""
    try:
        result = rag_store.import_documents(
            req.thread_id, req.embed_cfg(), [(item.text, item.title) for item in req.docs],
        )
    except rag_store.DocumentImportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, **result}


class RetrieveRequest(_EmbedFields):
    thread_id: str = "home"
    query: str = ""
    k: int = 4


@router.post("/retrieve")
def retrieve(req: RetrieveRequest) -> dict[str, object]:
    """检索本仓库知识库（供调试/前端展示用，对话内部已自动调用）。"""
    hits = rag_store.retrieve(
        req.thread_id, req.embed_cfg(), req.query, req.k,
    )
    return {"items": hits}


class EmbedAuth(_EmbedFields):
    pass


class ListRequest(EmbedAuth):
    repo_id: str = "home"


@router.post("/list")
def list_docs(req: ListRequest) -> dict[str, object]:
    """列出「系统库 + 本仓库库」所有条目（含系统指令，locked 标记）。顺带幂等播种系统指令。"""
    rag_store.seed_system_docs_best_effort(req.embed_cfg())
    return {"items": rag_store.list_docs(req.repo_id, req.embed_cfg())}


class DeleteDocRequest(EmbedAuth):
    id: str
    repo_id: str = "home"
    remove_file: bool = False      # 生成图：同时删除本机留存的图片文件


@router.post("/delete")
def delete_doc(req: DeleteDocRequest) -> dict[str, object]:
    """删除单条；系统指令（locked）拒绝删除。remove_file=True 时连本机图片文件一起删。"""
    ok = rag_store.delete_doc(req.id, req.repo_id, req.embed_cfg(), req.remove_file)
    if not ok:
        raise HTTPException(status_code=403, detail="系统指令条目不可删除")
    return {"ok": True}


class UpdateDocRequest(EmbedAuth):
    id: str
    text: str = ""
    title: str = ""
    repo_id: str = "home"


@router.post("/update")
def update_doc(req: UpdateDocRequest) -> dict[str, object]:
    """编辑单条；系统指令（locked）拒绝修改。"""
    ok = rag_store.update_doc(req.id, req.text, req.repo_id, req.embed_cfg(), req.title)
    if not ok:
        raise HTTPException(status_code=403, detail="内容为空或系统指令条目不可修改")
    return {"ok": True}


@router.post("/seed")
def seed(req: EmbedAuth) -> dict[str, object]:
    """手动触发系统指令播种（幂等）。"""
    n = rag_store.seed_system_docs(req.embed_cfg())
    return {"ok": True, "added": n}


class GenerationsRequest(EmbedAuth):
    repo_id: str = "home"
    limit: int = 0          # 0=全量（默认，兼容）；>0=每页条数
    before_ts: int = 0      # keyset 游标：只取 created_at 早于此的记录；0=不过滤


@router.post("/generations")
def list_generations(req: GenerationsRequest) -> dict[str, object]:
    """列出某仓库的生成记录（图片+提示词+标签），供仓库详情页图片网格。

    limit>0 时为 keyset 分页（created_at 倒序）：响应带 has_more（多取一条判定），
    翻页把上一页末条 created_at 作为 before_ts 传入。limit=0 时全量返回、
    has_more=False，行为与改造前一致。
    """
    fetch = req.limit + 1 if req.limit > 0 else 0
    items = rag_store.list_generations(
        req.repo_id, req.embed_cfg(), limit=fetch, before_ts=req.before_ts,
    )
    has_more = req.limit > 0 and len(items) > req.limit
    if has_more:
        items = items[:req.limit]
    return {"items": items, "has_more": has_more}


class SearchGenerationsRequest(EmbedAuth):
    repo_ids: list[str] = ["home"]
    query: str = ""
    k: int = 32
    output_dir: str = ""


@router.post("/search-generations")
def search_generations(req: SearchGenerationsRequest) -> dict[str, object]:
    """仅搜索资产 generation，不进入剧情知识检索。"""
    items = rag_store.search_generations(req.repo_ids, req.embed_cfg(), req.query, req.k)
    if req.output_dir:
        from app.services import visual_preference

        items = visual_preference.rank(req.output_dir, items)
    return {"items": items}


class GenerateThumbsRequest(EmbedAuth):
    repo_id: str = "home"


@router.post("/generate-thumbs")
def generate_thumbs(req: GenerateThumbsRequest) -> dict[str, object]:
    """为本仓库历史资产补生成缩略图（P0-2）。只处理本机静态图；远程/裂图跳过。
    幂等可重跑；读路径有 thumb_url_if_ready 兜底发现，无需回写元数据（避免重嵌）。"""
    from app.services import thumbnail_store

    items = rag_store.list_generations(req.repo_id, req.embed_cfg())
    generated = skipped = 0
    for item in items:
        path = thumbnail_store.local_path_of_url(str(item.get("image_url") or ""))
        if path is None or thumbnail_store.ensure_thumb(path) is None:
            skipped += 1
        else:
            generated += 1
    return {"ok": True, "generated": generated, "skipped": skipped}


class Wd14TagRequest(EmbedAuth):
    id: str
    repo_id: str = "home"


@router.post("/wd14-tag")
def wd14_tag(req: Wd14TagRequest) -> dict[str, object]:
    """对单条资产跑 WD14 反推打标（P0-3），结果合并进 tags（不动原有标签）。

    仅支持本机留存图片（远程图取不到字节）；模型未安装回 503 并附下载指引。
    """
    from fastapi import HTTPException

    from app.services import thumbnail_store, wd14_tagger

    if not wd14_tagger.model_available():
        raise HTTPException(
            status_code=503,
            detail="WD14 模型未安装：请将 model.onnx 与 selected_tags.csv 放到 "
                   "data/models/wd14-tagger/（HF: SmilingWolf/wd-swinv2-tagger-v3）",
        )
    items = rag_store.list_generations(req.repo_id, req.embed_cfg())
    item = next((i for i in items if i.get("id") == req.id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="资产不存在")
    path = thumbnail_store.local_path_of_url(str(item.get("image_url") or ""))
    if path is None:
        raise HTTPException(status_code=400, detail="仅支持本机留存图片（远程图取不到字节）")
    tags = wd14_tagger.reverse_tags(path)
    existing = list(item.get("tags") or [])
    merged = list(dict.fromkeys([*existing, *tags]))[:32]
    rag_store.set_doc_tags(req.id, req.repo_id, req.embed_cfg(), merged)
    return {"ok": True, "added": [t for t in tags if t not in existing], "total": len(merged)}


class TaggerTagRequest(EmbedAuth):
    id: str
    repo_id: str = "home"
    model: str = ""   # 空＝默认打标器（见 tagger_registry.DEFAULT_ID）


@router.post("/taggers")
def list_taggers() -> dict[str, object]:
    """可用反推打标器列表（内置 WD14 家族 + 丢在 models/taggers/ 下的自定义）。"""
    from app.services import tagger_registry
    return {"items": tagger_registry.list_taggers(), "default": tagger_registry.DEFAULT_ID}


@router.post("/tag")
def tag_generation(req: TaggerTagRequest) -> dict[str, object]:
    """按指定打标器对单条资产反推打标（合并进 tags，不动原有）。可插拔，见 tagger_registry。"""
    from fastapi import HTTPException

    from app.services import tagger_registry, thumbnail_store, wd14_tagger

    spec = tagger_registry.resolve(req.model)
    if not wd14_tagger.model_available(spec.dir):
        raise HTTPException(503, f"打标模型未安装：{spec.name}（请放到 {spec.dir}）")
    items = rag_store.list_generations(req.repo_id, req.embed_cfg())
    item = next((i for i in items if i.get("id") == req.id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="资产不存在")
    path = thumbnail_store.local_path_of_url(str(item.get("image_url") or ""))
    if path is None:
        raise HTTPException(status_code=400, detail="仅支持本机留存图片（远程图取不到字节）")
    tags = [name for name, _score in tagger_registry.predict(req.model, path)]
    snap = rag_store.add_tag_snapshot(req.id, req.repo_id, req.embed_cfg(), tags,
                                      model=spec.id, label=spec.name)
    if snap is None:
        raise HTTPException(status_code=400, detail="无法写入标签快照（条目被锁定或不存在）")
    return {"ok": True, "model": spec.id, "snapshot_id": snap["id"],
            "tags": snap["tags"], "total": len(snap["tags"])}


class TagSnapshotRequest(EmbedAuth):
    id: str
    repo_id: str = "home"
    snapshot_id: str = ""


@router.post("/tag-snapshots")
def tag_snapshots(req: TagSnapshotRequest) -> dict[str, object]:
    """列出某资产的所有标签快照（版本）+ 当前版。"""
    return rag_store.list_tag_snapshots(req.id, req.repo_id, req.embed_cfg())


@router.post("/tag-snapshot/activate")
def tag_snapshot_activate(req: TagSnapshotRequest) -> dict[str, object]:
    """切到某一版标签快照（同步 metadata.tags）。"""
    ok = rag_store.set_active_tag_snapshot(req.id, req.repo_id, req.embed_cfg(), req.snapshot_id)
    return {"ok": bool(ok)}


@router.post("/tag-snapshot/delete")
def tag_snapshot_delete(req: TagSnapshotRequest) -> dict[str, object]:
    """删除某一版标签快照（删当前版时回退到最后一版）。"""
    ok = rag_store.delete_tag_snapshot(req.id, req.repo_id, req.embed_cfg(), req.snapshot_id)
    return {"ok": bool(ok)}


class VisualPreferenceRequest(BaseModel):
    output_dir: str
    repo_id: str
    winner_id: str
    loser_id: str
    reason: str = "other"


@router.post("/visual-preference")
def record_visual_preference(req: VisualPreferenceRequest) -> dict[str, object]:
    from app.services import visual_preference

    try:
        return {"ok": True, **visual_preference.record(
            req.output_dir, req.repo_id, winner_id=req.winner_id,
            loser_id=req.loser_id, reason=req.reason,
        )}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/visual-preference")
def visual_preference_summary(output_dir: str, repo_id: str) -> dict[str, object]:
    from app.services import visual_preference

    return visual_preference.summary(output_dir, repo_id)


class ClearVisualPreferenceRequest(BaseModel):
    output_dir: str
    repo_id: str


@router.post("/visual-preference/clear")
def clear_visual_preference(req: ClearVisualPreferenceRequest) -> dict[str, object]:
    from app.services import visual_preference

    visual_preference.clear(req.output_dir, req.repo_id)
    return {"ok": True}


class SetGenerationDescriptionRequest(EmbedAuth):
    id: str
    repo_id: str = "home"
    description: str = ""


@router.post("/set-generation-description")
def set_generation_description(req: SetGenerationDescriptionRequest) -> dict[str, object]:
    ok = rag_store.set_generation_description(
        req.id, req.repo_id, req.embed_cfg(), req.description,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="资产不存在或描述为空")
    return {"ok": True}


@router.post("/index-visual-generations")
def index_visual_generations(req: GenerationsRequest) -> dict[str, object]:
    """用已安装的视觉嵌入模型为本仓库图片建立独立向量索引。"""
    from app.services import visual_asset_index

    try:
        items = rag_store.list_generations(req.repo_id, req.embed_cfg())
        return {"ok": True, **visual_asset_index.index_items(req.repo_id, items)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"视觉索引不可用：{exc}") from exc


@router.post("/dedup-generations")
def dedup_generations(req: GenerationsRequest) -> dict[str, object]:
    """清理某仓库里重复的生成记录（同一张图多条，历史随机 id 造成）。返回删除条数。"""
    try:
        n = rag_store.dedup_generations(req.repo_id, req.embed_cfg())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"去重失败：{e}")
    return {"ok": True, "removed": n}


@router.post("/prune-generations")
def prune_generations(req: GenerationsRequest) -> dict[str, object]:
    """清理僵尸记录：指向本机留存图但磁盘文件已不存在的条目（手动删文件留下的裂图）。返回删除条数。"""
    try:
        n = rag_store.prune_missing_generations(req.repo_id, req.embed_cfg())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"清理失败：{e}")
    return {"ok": True, "removed": n}


class TagStatsRequest(EmbedAuth):
    repo_ids: list[str] = ["home"]


@router.post("/tag-stats")
def tag_stats(req: TagStatsRequest) -> dict[str, object]:
    """聚合仓库集合的标签→图片数量（按量降序），供加标签/搜索的输入补全。"""
    return {"items": rag_store.tag_stats(req.repo_ids, req.embed_cfg())}


class SetTagsRequest(EmbedAuth):
    id: str
    repo_id: str = "home"
    tags: list[str] = []


@router.post("/set-tags")
def set_tags(req: SetTagsRequest) -> dict[str, object]:
    """覆盖某资产条目的标签（手动增删 / AI 打标落库）。"""
    ok = rag_store.set_doc_tags(req.id, req.repo_id, req.embed_cfg(), req.tags)
    if not ok:
        raise HTTPException(status_code=403, detail="条目不存在或系统条目不可改")
    return {"ok": True}
