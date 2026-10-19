"""仓库 RAG 知识库路由：生成历史自动入库 + 手动参考资料入库。

接口地址/密钥/模型由前端从「设置 → 对话模型」透传（与 /api/ai/chat 一致）。
"""
from typing import Literal

import json
import re
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import asset_store, rag_store
from app.services.rag_backend import EmbedConfig

router = APIRouter()


def _asset_db_empty() -> bool:
    """主表尚未回填（0 行）⇒ 读路径回退 Chroma（旧行为）。

    升级瞬间不能让资产列表变空：未跑 scripts/backfill_asset_db.py 时以向量库为准；
    主表异常同样回退（列表绝不 500）。
    """
    try:
        return asset_store.count_assets() == 0
    except Exception:  # noqa: BLE001 [EXC-保留] 主表不可用一律回退（列表绝不 500）
        return True


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
    repo_ids: list[str] = []   # 2026-10-18：非空 ⇒ 多仓库合并分页（资产库聚合页）
    limit: int = 0          # 0=全量（默认，兼容）；>0=每页条数
    before_ts: int = 0      # keyset 游标：只取 created_at 早于此的记录；0=不过滤
    offset: int = 0         # 按偏移取（2026-10-19：分页条要「共 N 页」+ 直达任意页）
    media_type: str = ""    # image/video/audio；空＝不限（2026-10-19 下推，别再前端过滤）


@router.post("/generations")
def list_generations(req: GenerationsRequest) -> dict[str, object]:
    """列出生成记录（图片+提示词+标签），供仓库详情页图片网格。

    limit>0 时为分页（created_at 倒序）：响应带 has_more 与 **total**（命中的总条数）。
    - keyset：把上一页末条 created_at 作为 before_ts 传入（新增图不会打乱已有页）；
    - offset：按偏移取，供「共 N 页 / 直达第 N 页」用（只靠游标前端根本算不出总页数）。
    media_type 下推到 SQL（2026-10-19）：此前媒体筛选只在前端过滤**已加载项**，
    聚合页翻页不会为筛选结果取更多 ⇒ 筛「视频」后滚动一直加载却看不到新内容。
    total 与 items 同口径（都按 media_type 过滤后的计数）。
    limit=0 时全量返回、has_more=False，行为与改造前一致。
    """
    fetch = req.limit + 1 if req.limit > 0 else 0
    media = req.media_type if req.media_type in ("image", "video", "audio") else ""
    repo_ids = req.repo_ids or [req.repo_id]
    items: list[dict] | None = None
    total = 0
    if not _asset_db_empty():
        # P1-1 读路径（2026-10-18）：列表/分页/多仓库走 SQLite 主表（元数据真相），
        # 单查询完成 repo 范围 + 分页 + 媒体类型 —— 不再依赖 Chroma 全量 dump。
        if req.before_ts > 0:
            items = asset_store.list_assets(
                repo_ids, limit=fetch, before_ts=req.before_ts, media_type=media)
        else:
            items = asset_store.list_assets(
                repo_ids, limit=fetch, offset=req.offset, media_type=media)
        try:
            total = asset_store.count_assets(repo_ids, deleted=False, media_type=media)
        except Exception:  # noqa: BLE001 [EXC-保留] 计数失败不该让列表 500（total=0 ⇒ 前端退回虚拟页）
            total = 0
    if items is None:
        # 回退（主表尚未回填）：沿用向量库路径，行为与改造前一致
        if req.repo_ids:
            items = rag_store.list_generations_multi(
                req.repo_ids, req.embed_cfg(), limit=fetch, before_ts=req.before_ts,
            )
        else:
            items = rag_store.list_generations(
                req.repo_id, req.embed_cfg(), limit=fetch, before_ts=req.before_ts,
            )
        total = 0   # 向量库路径不计数 ⇒ 0＝未知，前端退回「虚拟下一页」
    has_more = req.limit > 0 and len(items) > req.limit
    if has_more:
        items = items[:req.limit]
    return {"items": items, "has_more": has_more, "total": total}


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
    2026-10-18：登记为后台任务（jobs），面板显示 X/N 进度。"""
    from app.services import jobs, thumbnail_store

    repo_ids = req.repo_ids or [req.repo_id]
    cfg = req.embed_cfg()
    per_repo_items = {rid: rag_store.list_generations(rid, cfg) for rid in repo_ids}
    total = sum(
        1 for items in per_repo_items.values() for it in items
        if thumbnail_store.local_path_of_url(str(it.get("image_url") or "")) is not None
    )
    job = jobs.create("thumbnails", f"补缩略图（{len(repo_ids)} 仓库）", total)
    try:
        generated = skipped = 0
        for items in per_repo_items.values():
            for item in items:
                if jobs.should_cancel(job.id):
                    break
                path = thumbnail_store.local_path_of_url(str(item.get("image_url") or ""))
                if path is None:          # 远程/裂图：不计入 total，也不 tick
                    skipped += 1
                    continue
                if thumbnail_store.ensure_thumb(path) is None:
                    skipped += 1
                else:
                    generated += 1
                jobs.tick(job.id)
        jobs.finish(job.id, "cancelled" if jobs.should_cancel(job.id) else "done")
        return {"ok": True, "generated": generated, "skipped": skipped, "job_id": job.id}
    except Exception as exc:  # noqa: BLE001
        jobs.finish(job.id, "failed", str(exc))
        raise HTTPException(status_code=500, detail=f"补缩略图失败：{exc}") from exc


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


class TagTextRequest(EmbedAuth):
    id: str
    repo_id: str = "home"
    text: str = ""
    model: str = ""
    label: str = ""
    save_description: bool = True


# 画师标记 `@//画师名/`（ComfyUI「反推动漫」工作流里 DanbooruGallery 的画师串写法）
_ARTIST_MARK = re.compile(r"^@//(.+?)/+$")


def parse_tagged_text(text: str) -> tuple[list[str], str]:
    """两段式反推文本 →（标签列表, 描述段）——与 ComfyUI「反推动漫」工作流同构。

    工作流最终产物（PromptStashPassthrough）＝
        masterpiece, score_9, year 2025, highres, safe, 1girl, liduke, @//liduke/, ...
        <空行>
        A single girl with blonde hair ... against a stark white background.

    解析规则（单一真源，前后端不得各写一份）：
    - 按**空行**切段：第一段是标签行，其余全部拼回＝描述段；
    - 标签按英文逗号/中文逗号/顿号/分号/换行切分，去引号去空白；
    - `@//画师名/` 折成 `画师名`（同一画师裸名已存在则不重复，避免存两份）；
    - 去重保序（大小写不敏感）。
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return [], ""
    parts = re.split(r"\n\s*\n", raw, maxsplit=1)
    head = parts[0]
    description = parts[1].strip() if len(parts) > 1 else ""
    tags: list[str] = []
    seen: set[str] = set()

    def push(value: str) -> None:
        value = value.strip().strip("\"'“”‘’")
        if not value:
            return
        artist = _ARTIST_MARK.match(value)
        if artist:
            value = artist.group(1).strip()
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        tags.append(value)

    for token in re.split(r"[,，、;；\n]+", head):
        push(token)
    # 护栏：模型没按两段式输出（只给了一整句描述、没有逗号）⇒ 不把整句话塞成标签
    if len(tags) == 1 and len(tags[0].split()) >= 8:
        merged = head if not description else f"{head}\n\n{description}"
        return [], merged.strip()
    return tags, description


@router.post("/tag-text")
def tag_text(req: TagTextRequest) -> dict[str, object]:
    """两段式反推落库：标签行 → 新标签快照；描述段 → 资产描述（重建语义索引）。

    视觉推理由前端发起（wire 透传对话模型配置，模型可以是本地 llama.cpp / 云端），
    后端只做**解析 + 落库**，保证切分规则与快照/描述写入只有一份实现。
    """
    tags, description = parse_tagged_text(req.text)
    if not tags:
        raise HTTPException(
            status_code=400,
            detail="没有解析出标签：第一段应为逗号分隔的 tag 行（第二段描述用空行分隔）",
        )
    cfg = req.embed_cfg()
    snap = rag_store.add_tag_snapshot(
        req.id, req.repo_id, cfg, tags, model=req.model, label=req.label,
    )
    if snap is None:
        raise HTTPException(status_code=400, detail="无法写入标签快照（条目被锁定或不存在）")
    saved_description = False
    if req.save_description and description:
        saved_description = rag_store.set_generation_description(
            req.id, req.repo_id, cfg, description,
        )
    return {
        "ok": True,
        "snapshot_id": snap["id"],
        "tags": snap["tags"],
        "total": len(snap["tags"]),
        "description": description if saved_description else "",
        "saved_description": saved_description,
    }


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
    支持 repo_ids 多仓库一次跑（聚合出一个 1/157 的总进度）；分块编码、可协作式取消。"""
    from app.services import jobs, visual_asset_index

    repo_ids = req.repo_ids or [req.repo_id]
    cfg = req.embed_cfg()
    try:
        per_repo_items = {rid: rag_store.list_generations(rid, cfg) for rid in repo_ids}
        total = sum(visual_asset_index.count_indexable(items) for items in per_repo_items.values())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"视觉索引不可用：{exc}") from exc

    job = jobs.create("visual_index", f"视觉索引（{len(repo_ids)} 仓库）", total)
    try:
        indexed = skipped = 0
        for rid, items in per_repo_items.items():
            if jobs.should_cancel(job.id):
                break
            r = visual_asset_index.index_items(
                rid, items, on_progress=lambda n: jobs.tick(job.id, n),
            )
            indexed += int(r.get("indexed") or 0)
            skipped += int(r.get("skipped") or 0)
        jobs.finish(job.id, "cancelled" if jobs.should_cancel(job.id) else "done")
        return {"ok": True, "indexed": indexed, "skipped": skipped, "job_id": job.id}
    except Exception as exc:  # noqa: BLE001
        jobs.finish(job.id, "failed", str(exc))
        raise HTTPException(status_code=503, detail=f"视觉索引不可用：{exc}") from exc


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
    图片从本机磁盘直读（不再绕后端代理 URL → dataURI 那一圈）。
    """
    from app.services import jobs, vlm_batch

    repo_ids = req.repo_ids or ["home"]
    if not str(req.vlm.model or "").strip() or not str(req.vlm.base_url or "").strip():
        raise HTTPException(status_code=400, detail="未配置视觉模型（设置 → 模型 → 视觉模型）")
    cfg = req.embed_cfg()
    try:
        total = len(vlm_batch.missing_description_assets(repo_ids))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"资产库不可用：{exc}") from exc

    job = jobs.create("vlm_describe", f"视觉描述（{len(repo_ids)} 仓库）", total)

    def _run() -> None:
        try:
            vlm_batch.run_describe_missing(
                job.id, repo_ids, cfg, req.vlm.model_dump(), req.hint, req.concurrency,
            )
        except Exception as exc:  # noqa: BLE001 [EXC-保留] 后台线程兜底：线程内异常不能静默吞掉
            jobs.finish(job.id, "failed", str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "job_id": job.id, "total": total}


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


def _row_tags(row: dict) -> list[str]:
    """主表 tags_json → 标签列表（兼容裸逗号串/损坏 JSON）。"""
    raw = row.get("tags_json") or "[]"
    try:
        data = json.loads(raw) if isinstance(raw, str) else list(raw)
        if isinstance(data, list):
            return [str(t) for t in data if str(t).strip()]
    except (ValueError, TypeError):
        pass
    return [t.strip() for t in str(raw).split(",") if t.strip()]


def _fallback_search_chroma(req: SearchAssetsRequest, repo_ids: list[str]) -> list[dict]:
    """主表未回填时的结构化搜索兜底（内存过滤；仅升级过渡期用，与 FTS5 同语义）。"""
    cfg = req.embed_cfg()
    merged: list[dict] = []
    for rid in repo_ids:
        merged.extend(rag_store.list_generations(rid, cfg, before_ts=req.before_ts))
    wanted = [t.strip() for t in req.tags if t.strip()]
    text = (req.q or "").strip().lower()
    out: list[dict] = []
    for it in merged:
        if req.media_type and it.get("mediaType") != req.media_type:
            continue
        if wanted and not all(t in (it.get("tags") or []) for t in wanted):
            continue
        if text:
            hay = "\n".join([
                it.get("prompt") or "", it.get("description") or "",
                ",".join(it.get("tags") or []),
            ]).lower()
            if text not in hay:
                continue
        out.append(it)
    out.sort(key=lambda d: d.get("created_at", 0), reverse=True)
    return out


@router.post("/search-assets")
def search_assets(req: SearchAssetsRequest) -> dict[str, object]:
    """结构化搜索：FTS5 全文 + asset_tags 精确 AND + LIKE 兜底（中文子串）。

    与 /search-generations（Chroma hybrid 语义检索）**并存**，不是二选一：
    标签/chips/关键词走这里，语义开关走那里。
    """
    repo_ids = req.repo_ids or [req.repo_id]
    fetch = req.limit + 1 if req.limit > 0 else 0
    if _asset_db_empty():
        items = _fallback_search_chroma(req, repo_ids)
    else:
        items = asset_store.search_assets(
            repo_ids, q=req.q, tags=req.tags, media_type=req.media_type,
            limit=fetch, before_ts=req.before_ts,
        )
    has_more = req.limit > 0 and len(items) > req.limit
    if has_more:
        items = items[:req.limit]
    return {"items": items, "has_more": has_more}


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
    的半状态。旧版随机 uuid 记录恢复后 id 变为 gen-<sha1>，顺手清掉旧空壳行。
    """
    row = asset_store.get_row(req.id)
    if row is None:
        raise HTTPException(status_code=404, detail="回收站里没有这条记录")
    if row.get("deleted_at") is None:
        return {"ok": True, "already": True, "id": req.id}
    repo_id = row.get("repo_id") or req.repo_id
    cfg = req.embed_cfg()
    if not (cfg.base_url or cfg.model_dir):
        # wire 未带嵌入配置（脚本/后端自发调用）⇒ 读运行态真源，避免重建向量报缺协议
        cfg = rag_store.embed_config_from_state()
    derived_raw = row.get("derived_from") or "[]"
    try:
        derived = json.loads(derived_raw) if isinstance(derived_raw, str) else list(derived_raw)
        if not isinstance(derived, list):
            derived = []
    except (ValueError, TypeError):
        derived = []
    try:
        rag_store.index_generation(
            repo_id, cfg,
            prompt=row.get("prompt") or "",
            tags=",".join(_row_tags(row)),
            image_url=row.get("image_url") or "",
            created_at=int(row.get("created_at") or 0),
            description=row.get("description") or "",
            template_name=row.get("template_name") or "",
            model_name=row.get("model_name") or "",
            lora_names=row.get("lora_names") or "",
            media_type=row.get("media_type") or "image",
            derived_from=derived,
            source=row.get("source") or "",
            source_url=row.get("source_url") or "",
            source_meta=row.get("source_meta") or "",
        )
    except Exception as exc:  # noqa: BLE001 - 重建向量失败：保持回收站态，可重试
        raise HTTPException(status_code=503, detail=f"恢复失败（重建向量出错）：{exc}")
    new_id = rag_store.generation_doc_id(repo_id, row.get("image_url") or "")
    if new_id != req.id:
        asset_store.delete_rows([req.id])
    asset_store.restore(new_id)
    return {"ok": True, "id": new_id}


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

    P1-1：主表已回填 ⇒ SQLite 聚合（asset_tags 索引）；否则回退 Chroma 全量扫。
    """
    if not _asset_db_empty():
        return {"items": asset_store.tag_stats(req.repo_ids)}
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
