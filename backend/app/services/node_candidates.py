"""为 AI 搭工作流解析本机节点候选。

RAG 只提供按功能召回的候选；ComfyUI ``object_info`` 是安装状态和节点接口的
唯一事实来源。RAG 为空或暂时不可用时，本模块仍从本机库存生成候选。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.services import comfyui_client, node_index
from app.services.rag_middleware import expand_query


_LOG = logging.getLogger(__name__)

_COMMON_NODES = (
    "CheckpointLoaderSimple", "UNETLoader", "DualCLIPLoader", "CLIPLoader", "VAELoader",
    "CLIPTextEncode", "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage", "LoadImage",
    "VAEEncode",
)

_OPTIONAL_NODES = (
    "DanbooruGalleryNode", "LoraLoaderModelOnly",
    "Any Switch (rgthree)", "Fast Groups Muter (rgthree)", "ImpactConditionalBranch",
    "ImpactSwitch", "llama_cpp_model_loader", "llama_cpp_instruct_adv",
    "llama_cpp_parameters", "QwenTE_ModelLoader", "QwenTE_ImageInfer", "BLIPCaption",
    "DeepDanbooruCaption", "AILab_Florence2", "WD14Tagger|pysssss", "Florence2Run",
)


@dataclass(frozen=True)
class NodeCandidates:
    """一次候选解析的完整结果。"""

    object_info: dict
    packs: list[dict]
    names: list[str]
    named: list[str]


def named_nodes_in_text(text: str, object_info: dict) -> list[str]:
    """找出文本中明确点名且本机真实存在的节点。"""
    low = (text or "").lower()
    if not low:
        return []
    hits: list[str] = []
    for class_type, schema in object_info.items():
        if not isinstance(class_type, str):
            continue
        display_name = str(schema.get("display_name") or "") if isinstance(schema, dict) else ""
        if ((len(class_type) >= 4 and class_type.lower() in low)
                or (len(display_name) >= 4 and display_name.lower() in low)):
            hits.append(class_type)
    return hits


def _query_terms(query: str) -> set[str]:
    terms: set[str] = set()
    for expanded in expand_query(query):
        low = expanded.lower()
        terms.update(token for token in re.findall(r"[a-z0-9_]+", low) if len(token) >= 3)
        for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", low):
            terms.add(phrase)
            terms.update(phrase[i:i + 2] for i in range(len(phrase) - 1))
    return terms


def inventory_candidates(query: str, object_info: dict, limit: int = 24) -> list[str]:
    """按能力词匹配本机库存，不制造或推断节点安装状态。"""
    terms = _query_terms(query)
    scored: list[tuple[float, str]] = []
    for class_type, schema in (object_info or {}).items():
        if not isinstance(class_type, str) or not isinstance(schema, dict):
            continue
        class_name = class_type.lower()
        blob = " ".join((
            class_type,
            str(schema.get("display_name", "")),
            str(schema.get("category", "")),
            str(schema.get("description", "")),
        )).lower()
        score = sum(2.0 if term in class_name else 1.0 for term in terms if term in blob)
        if score:
            scored.append((score, class_type))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in scored[:limit]]


def resolve(cfg, query: str, comfy_url: str, *, k: int = 10) -> NodeCandidates:
    """先读取权威本机库存，再以 RAG 和库存匹配共同准备候选。"""
    object_info = comfyui_client.fetch_object_info(comfy_url)
    try:
        packs = node_index.search(cfg, query, k=k)
    except Exception as exc:  # RAG 降级不能改变本机安装事实
        _LOG.warning("Node RAG unavailable; using object_info inventory: %s", exc)
        packs = []

    rag_names = [
        name
        for pack in packs
        for name in (pack.get("node_names", []) or [])
        if isinstance(name, str)
    ]
    named = named_nodes_in_text(query, object_info)
    ordered = [
        *named,
        *inventory_candidates(query, object_info),
        *_OPTIONAL_NODES,
        *_COMMON_NODES,
        *rag_names,
    ]
    names: list[str] = []
    seen: set[str] = set()
    for name in ordered:
        if name in object_info and name not in seen:
            names.append(name)
            seen.add(name)
    return NodeCandidates(object_info=object_info, packs=packs, names=names, named=named)
