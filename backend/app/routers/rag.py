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


@router.post("/index-generation")
def index_generation(req: IndexGenRequest) -> dict[str, object]:
    """生图完成后调用，把这次生成的提示词/标签/图片入仓库知识库。
    重试 3 次：embedding 偶发瞬时失败(ollama 并发/超时)会让「图落盘了但提示词/生成历史没进知识库」
    →资产库内容丢失。工作流批量出图时几张挤一起调 embedding 最易触发，故退避重试兜底。"""
    try:
        rag_store.index_generation_reliable(
            req.thread_id, req.embed_cfg(), req.prompt, req.tags, req.image_url,
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
    try:
        rag_store.seed_system_docs(req.embed_cfg())
    except Exception:
        pass  # 播种失败（如嵌入接口未配）不阻断列表
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


@router.post("/generations")
def list_generations(req: GenerationsRequest) -> dict[str, object]:
    """列出某仓库的生成记录（图片+提示词+标签），供仓库详情页图片网格。"""
    return {"items": rag_store.list_generations(req.repo_id, req.embed_cfg())}


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
