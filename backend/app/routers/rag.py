"""仓库 RAG 知识库路由：生成历史自动入库 + 手动参考资料入库。

接口地址/密钥/模型由前端从「设置 → 对话模型」透传（与 /api/ai/chat 一致）。
"""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import asset_library, asset_store, embed_resolution, rag_store
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
        return embed_resolution.resolve_or(EmbedConfig(
            base_url=self.base_url,
            api_key=self.api_key,
            embed_model=self.embed_model,
            model_dir=self.embed_model_dir,
            reranker_dir=self.reranker_model_dir,
            mode=self.embed_mode,
            proxy=self.proxy_url,
        ))


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
    repo_ids: list[str] = []   # 2026-10-18：非空 ⇒ 多仓库合并分页（资产库聚合页）
    limit: int = 0          # 0=全量（默认，兼容）；>0=每页条数
    before_ts: int = 0      # keyset 游标：只取 created_at 早于此的记录；0=不过滤
    offset: int = 0         # 按偏移取（2026-10-19：分页条要「共 N 页」+ 直达任意页）
    media_type: str = ""    # image/video/audio；空＝不限（2026-10-19 下推，别再前端过滤）


@router.post("/generations")
def list_generations(req: GenerationsRequest) -> dict[str, object]:
    """列出生成记录（图片+提示词+标签），供仓库详情页图片网格。

    读路径策略（主表↔向量库回退）、分页口径（keyset/offset、total 与 items 同口径）
    见 services/asset_library.list_generations_page（2026-10-24 审计中期项下沉）。
    """
    return asset_library.list_generations_page(
        req.repo_id, req.repo_ids, cfg=req.embed_cfg(), limit=req.limit,
        before_ts=req.before_ts, offset=req.offset, media_type=req.media_type,
    )


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
    repo_ids: list[str] = []   # 2026-10-18：非空 ⇒ 多仓库一次跑，聚合成一个总进度


@router.post("/generate-thumbs")
def generate_thumbs(req: GenerateThumbsRequest) -> dict[str, object]:
    """为本仓库历史资产补生成缩略图（P0-2）。只处理本机静态图；远程/裂图跳过。
    幂等可重跑；读路径有 thumb_url_if_ready 兜底发现，无需回写元数据（避免重嵌）。
    2026-10-18：登记为后台任务（jobs），面板显示 X/N 进度。任务循环见
    services/asset_library.generate_thumbs（2026-10-24 审计中期项下沉）。"""
    try:
        return asset_library.generate_thumbs(req.repo_id, req.repo_ids, req.embed_cfg())
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class Wd14TagRequest(EmbedAuth):
    id: str
    repo_id: str = "home"


@router.post("/wd14-tag")
def wd14_tag(req: Wd14TagRequest) -> dict[str, object]:
    """对单条资产跑 WD14 反推打标（P0-3），结果合并进 tags（不动原有标签）。

    仅支持本机留存图片（远程图取不到字节）；模型未安装回 503 并附下载指引。
    打标编排见 services/asset_library.wd14_tag_asset（2026-10-24 审计中期项下沉）。
    """
    try:
        return asset_library.wd14_tag_asset(req.id, req.repo_id, req.embed_cfg())
    except asset_library.TaggerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except asset_library.AssetMissing as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    """按指定打标器对单条资产反推打标（合并进 tags，不动原有）。可插拔，见 tagger_registry。
    打标编排见 services/asset_library.tag_asset_with（2026-10-24 审计中期项下沉）。"""
    try:
        return asset_library.tag_asset_with(req.id, req.repo_id, req.model, req.embed_cfg())
    except asset_library.TaggerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except asset_library.AssetMissing as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class TagTextRequest(EmbedAuth):
    id: str
    repo_id: str = "home"
    text: str = ""
    model: str = ""
    label: str = ""
    save_description: bool = True


@router.post("/tag-text")
def tag_text(req: TagTextRequest) -> dict[str, object]:
    """两段式反推落库：标签行 → 新标签快照；描述段 → 资产描述（重建语义索引）。

    视觉推理由前端发起（wire 透传对话模型配置，模型可以是本地 llama.cpp / 云端），
    后端只做**解析 + 落库**。切分规则单一真源 = services/asset_library.parse_tagged_text，
    落库编排 = save_tag_text（2026-10-24 审计中期项下沉）。
    """
    try:
        return asset_library.save_tag_text(
            req.id, req.repo_id, text=req.text, model=req.model, label=req.label,
            save_description=req.save_description, cfg=req.embed_cfg(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    """为仓库图片建立独立视觉向量索引。

    2026-10-18：登记为**后台任务**（jobs），前端「后台活动」面板轮询 /jobs 显示 X/N 进度条；
    支持 repo_ids 多仓库一次跑（聚合出一个 1/157 的总进度）；分块编码、可协作式取消。
    任务循环见 services/asset_library.index_visual_generations（2026-10-24 审计中期项下沉）。
    """
    try:
        return asset_library.index_visual_generations(req.repo_id, req.repo_ids, req.embed_cfg())
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class _VlmCfg(BaseModel):
    """视觉模型三元组：由前端从设置透传（唯一真源仍是「设置 → 模型 → 视觉模型」）。

    后端刻意不解析 local/remote 两态——此前 activeVlmChat 的代理口径改过一次，
    在此重复实现必然再次漂移。
    """
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    proxy: str = ""


class DescribeMissingRequest(EmbedAuth):
    repo_ids: list[str] = []
    vlm: _VlmCfg = _VlmCfg()
    hint: str = ""
    concurrency: int = 3


@router.post("/describe-missing")
def describe_missing(req: DescribeMissingRequest) -> dict[str, object]:
    """批量补视觉描述（后台任务，立即返回 job_id）。

    2026-10-18：从浏览器端搬到后端。原实现有三个硬伤——
      ① 只覆盖「当前画廊已加载的条目」，全库要靠手动翻页；
      ② 切页/刷新即断；
      ③ 本地 8B VLM 全库跑十几分钟，浏览器端没有任何持久化进度。
    现在登记为 job：GET /jobs 轮询可得 X/N，「后台活动」面板显示进度条，可取消。
    任务循环见 services/asset_library.describe_missing_assets（2026-10-24 审计中期项下沉）。
    """
    if not str(req.vlm.model or "").strip() or not str(req.vlm.base_url or "").strip():
        raise HTTPException(status_code=400, detail="未配置视觉模型（设置 → 模型 → 视觉模型）")
    try:
        return asset_library.describe_missing_assets(
            req.repo_ids or ["home"], req.vlm.model_dump(), req.hint, req.concurrency,
            req.embed_cfg(),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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


@router.post("/prune-orphan-repos")
def prune_orphan_repos(req: EmbedAuth) -> dict[str, object]:
    """孤儿仓库清理：把「有记录但不在注册表、也非合成 repo」的仓库也纳入 prune
    （只删磁盘文件已缺失的裂图记录）。返回 {orphan_repos, removed, per_repo}。"""
    try:
        return rag_store.prune_orphan_repos(req.embed_cfg())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"孤儿仓库清理失败：{e}")


class RepoSyncRequest(EmbedAuth):
    output_dir: str = ""


@router.post("/repo-sync-report")
def repo_sync_report(req: RepoSyncRequest) -> dict[str, object]:
    """仓库同步体检（**只读**）：对账「注册表/磁盘/索引」三方，报告漂移，不改数据。"""
    if not req.output_dir:
        raise HTTPException(status_code=400, detail="缺少 output_dir")
    from app.services import repo_sync

    return repo_sync.repo_sync_report(req.output_dir, req.embed_cfg())


@router.post("/repo-sync-fix")
def repo_sync_fix(req: RepoSyncRequest) -> dict[str, object]:
    """仓库同步修复（保守）：补录未入库文件 + 清理裂图/孤儿。不删磁盘、不覆盖已有索引。"""
    if not req.output_dir:
        raise HTTPException(status_code=400, detail="缺少 output_dir")
    from app.services import repo_sync

    return repo_sync.repo_sync_fix(req.output_dir, req.embed_cfg())


# ===== P1-3 结构化搜索（FTS5 + asset_tags）＋ P1-2 回收站（2026-10-18） =====

class SearchAssetsRequest(EmbedAuth):
    repo_id: str = "home"
    repo_ids: list[str] = []
    q: str = ""
    tags: list[str] = []
    media_type: str = ""
    limit: int = 0
    before_ts: int = 0


@router.post("/search-assets")
def search_assets(req: SearchAssetsRequest) -> dict[str, object]:
    """结构化搜索：FTS5 全文 + asset_tags 精确 AND + LIKE 兜底（中文子串）。

    与 /search-generations（Chroma hybrid 语义检索）**并存**，不是二选一：
    标签/chips/关键词走这里，语义开关走那里。检索与主表未回填时的内存兜底见
    services/asset_library.search_assets_page（2026-10-24 审计中期项下沉）。
    """
    return asset_library.search_assets_page(
        req.repo_id, req.repo_ids, cfg=req.embed_cfg(), q=req.q, tags=req.tags,
        media_type=req.media_type, limit=req.limit, before_ts=req.before_ts,
    )


class AssetIdRequest(EmbedAuth):
    id: str
    repo_id: str = "home"


@router.post("/assets/trash")
def list_trash(req: GenerationsRequest) -> dict[str, object]:
    """回收站列表（软删除记录，按删除时间倒序）。"""
    repo_ids = req.repo_ids or [req.repo_id]
    return {"items": asset_store.list_assets(repo_ids, limit=req.limit, deleted=True)}


@router.post("/assets/restore")
def restore_asset(req: AssetIdRequest) -> dict[str, object]:
    """从回收站恢复：**先重建向量、成功后才清 deleted_at**。

    顺序刻意如此：嵌入失败则保持回收站态（可重试），绝不出现「列表有、语义搜不到」
    的半状态。恢复流程见 services/asset_library.restore_asset（2026-10-24 审计中期项下沉）。
    """
    try:
        return asset_library.restore_asset(req.id, req.repo_id, req.embed_cfg())
    except asset_library.AssetMissing as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/assets/purge")
def purge_asset(req: AssetIdRequest) -> dict[str, object]:
    """彻底删除（只允许回收站内记录；存活记录必须先删除）。"""
    row = asset_store.get_row(req.id)
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if row.get("deleted_at") is None:
        raise HTTPException(status_code=400, detail="该记录不在回收站（请先删除到回收站）")
    asset_store.purge(req.id)
    return {"ok": True}


@router.post("/assets/trash-clear")
def clear_trash(req: GenerationsRequest) -> dict[str, object]:
    """清空回收站（repo_ids 给范围；留空＝全部仓库）。"""
    return {"removed": asset_store.clear_trash(req.repo_ids)}


class TagStatsRequest(EmbedAuth):
    repo_ids: list[str] = ["home"]


@router.post("/tag-stats")
def tag_stats(req: TagStatsRequest) -> dict[str, object]:
    """聚合仓库集合的标签→图片数量（按量降序），供加标签/搜索的输入补全。

    主表已回填 ⇒ SQLite 聚合，否则回退 Chroma 全量扫——回退策略见
    services/asset_library.tag_stats（2026-10-24 审计中期项下沉）。
    """
    return asset_library.tag_stats(req.repo_ids, req.embed_cfg())


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
