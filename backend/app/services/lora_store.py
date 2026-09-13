"""LoRA 触发词的向量库镜像：供用户在知识库里语义检索/浏览。

**这里不是主存。** 主存是 SQLite 的 lora_triggers 表（见 db.py 表注释）——编排注入要按
lora_name 精确查，向量检索会近似匹配到错文件（如 ganyu_v1 / ganyu_v2）。本模块只做镜像，
镜像写失败不影响主流程。

一条 LoRA 一文档，不分块：单条内容就是「文件名 + 触发词 + 备注」，本就很短。
"""
from __future__ import annotations

import logging

from langchain_core.documents import Document

from app.services import rag_backend
from app.services.rag_backend import EmbedConfig

logger = logging.getLogger(__name__)

LORA_TRIGGER_COLLECTION = "lora_triggers_v1"


def _store(cfg: EmbedConfig):
    return rag_backend.store(LORA_TRIGGER_COLLECTION, cfg)


def _usable(cfg: EmbedConfig) -> bool:
    """嵌入配置是否可用。没配就直接跳过镜像 —— 这是常态（用户可能压根不用知识库），
    不该每次保存都抛一屏栈日志。"""
    if cfg.mode == "local":
        return bool(cfg.model_dir.strip())
    return bool(cfg.base_url.strip())


def _doc_text(lora_name: str, triggers: list[str], note: str) -> str:
    """拼给向量库看的正文。带上文件名是为了让用户搜文件名也能命中。"""
    lines = [f"LoRA：{lora_name}"]
    if triggers:
        lines.append("触发词：" + ", ".join(triggers))
    if note.strip():
        lines.append("备注：" + note.strip())
    return "\n".join(lines)


def index_lora(cfg: EmbedConfig, lora_name: str, triggers: list[str],
               note: str = "", source: str = "") -> None:
    """写入或覆盖一条 LoRA 触发词镜像。异常吞掉——镜像失败不该拖垮同步。"""
    if not _usable(cfg):
        return
    document = Document(page_content=_doc_text(lora_name, triggers, note), metadata={
        "kind": "lora_trigger",
        "id": lora_name,
        "lora_name": lora_name,
        "triggers": ",".join(triggers),
        "source": source,
    })
    try:
        store = _store(cfg)
        if store.get(ids=[lora_name]).get("ids"):
            store.update_document(lora_name, document)
        else:
            store.add_documents([document], ids=[lora_name])
    except Exception:
        logger.warning("LoRA 触发词镜像写入失败：%s", lora_name, exc_info=True)


def delete_lora(cfg: EmbedConfig, lora_name: str) -> None:
    """删除一条镜像。同样吞异常。"""
    if not _usable(cfg):
        return
    try:
        _store(cfg).delete(ids=[lora_name])
    except Exception:
        logger.warning("LoRA 触发词镜像删除失败：%s", lora_name, exc_info=True)
