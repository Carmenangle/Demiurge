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

    def embed_cfg(self) -> EmbedConfig:
        return EmbedConfig(
            base_url=self.base_url,
            api_key=self.api_key,
            embed_model=self.embed_model,
            model_dir=self.embed_model_dir,
            reranker_dir=self.reranker_model_dir,
            mode=self.embed_mode,
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
    import time as _t
    last = None
    for attempt in range(3):
        try:
            rag_store.index_generation(
                req.thread_id, req.embed_cfg(),
                req.prompt, req.tags, req.image_url,
            )
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            last = e
            import logging
            logging.getLogger("uvicorn.error").warning(
                "index-generation 失败(第%d次) repo=%s: %s", attempt + 1, req.thread_id, e)
            if attempt < 2:
                _t.sleep(0.8 * (attempt + 1))
    raise HTTPException(status_code=502, detail=f"入库失败(重试3次)：{last}")


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
