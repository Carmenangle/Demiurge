"""AI 搭工作流的「骨架底座」库：给 AI 一个已验证正确的工作流做起点，避免从零硬连乱挑节点。

两类来源合并供给：
1. 内置精简骨架(_BUILTINS)：文生图/图生图/反推三个最常用底座，API prompt 格式，
   全用 ComfyUI 标准核心节点，不依赖冷门自定义套件，保证任何环境都能校验通过。
2. 用户工作流文件夹(settings 的默认工作流目录)：下载的模板、用户自己的工作流都在这里，
   扫 .json 当骨架候选（含 209 节点的反推动漫这类真实大图）。

AI 搭建空画布时先按需求选一个骨架 load 进画布，再走增量模块引擎改。
骨架只读，AI 的改动经 build/save 新建文件，绝不覆盖原骨架文件。
"""
from __future__ import annotations

import json
from pathlib import Path


# —— 内置精简骨架（API prompt 格式：{node_id: {class_type, inputs}}）——
# 全部用标准核心节点：CheckpointLoaderSimple / CLIPTextEncode / KSampler / VAEDecode /
# EmptyLatentImage / SaveImage / LoadImage / VAEEncode。ckpt_name 等 combo 值留空占位，
# load 进画布后由用户/AI 按本机实际模型填（校验器会用真实候选纠正）。

_TXT2IMG: dict = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ""}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "masterpiece, best quality", "clip": ["1", 1]}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "worst quality, low quality", "clip": ["1", 1]}},
    "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
    "5": {"class_type": "KSampler", "inputs": {
        "seed": 0, "steps": 25, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
        "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}},
    "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
    "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI", "images": ["6", 0]}},
}

_IMG2IMG: dict = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ""}},
    "2": {"class_type": "LoadImage", "inputs": {"image": ""}},
    "3": {"class_type": "VAEEncode", "inputs": {"pixels": ["2", 0], "vae": ["1", 2]}},
    "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "masterpiece, best quality", "clip": ["1", 1]}},
    "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "worst quality, low quality", "clip": ["1", 1]}},
    "6": {"class_type": "KSampler", "inputs": {
        "seed": 0, "steps": 25, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 0.6,
        "model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["3", 0]}},
    "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
    "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI", "images": ["7", 0]}},
}

# 分离式加载文生图：现代模型(ANIMA/Flux/SD3 等)用 UNETLoader 从 diffusion_models 加载主模型，
# CLIP、VAE 各自单独加载，不是 checkpoint 一体化。unet_name/clip_name/vae_name/type 留空占位，
# load 后按本机实际模型填（校验器会用真实候选纠正；DualCLIPLoader 的 type 选 sdxl/flux/sd3 等）。
_TXT2IMG_UNET: dict = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "", "weight_dtype": "default"}},
    "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "", "clip_name2": "", "type": ""}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": ""}},
    "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "masterpiece, best quality", "clip": ["2", 0]}},
    "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "worst quality, low quality", "clip": ["2", 0]}},
    "6": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
    "7": {"class_type": "KSampler", "inputs": {
        "seed": 0, "steps": 25, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
        "model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0]}},
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI", "images": ["8", 0]}},
}

# 分离式加载图生图：同上分离加载 + LoadImage/VAEEncode 提供初始 Latent，denoise 0.6。
_IMG2IMG_UNET: dict = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "", "weight_dtype": "default"}},
    "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "", "clip_name2": "", "type": ""}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": ""}},
    "4": {"class_type": "LoadImage", "inputs": {"image": ""}},
    "5": {"class_type": "VAEEncode", "inputs": {"pixels": ["4", 0], "vae": ["3", 0]}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "masterpiece, best quality", "clip": ["2", 0]}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "worst quality, low quality", "clip": ["2", 0]}},
    "8": {"class_type": "KSampler", "inputs": {
        "seed": 0, "steps": 25, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 0.6,
        "model": ["1", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
    "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
    "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI", "images": ["9", 0]}},
}

# 反推：看图出提示词。用核心 CLIP 视觉相关节点难以保证通用，这里给最小可跑骨架——
# 加载图 + 预览，反推节点(llama/wd14 等)因环境差异不写死，交给 AI 增量加。
_INTERROGATE: dict = {
    "1": {"class_type": "LoadImage", "inputs": {"image": ""}},
    "2": {"class_type": "PreviewImage", "inputs": {"images": ["1", 0]}},
}

_BUILTINS: list[dict] = [
    {"id": "builtin-txt2img", "name": "文生图 · Checkpoint 一体式",
     "desc": "CheckpointLoaderSimple 一体加载(SD1.5/SDXL/Pony/Illustrious 等) → 正负提示词 → 空 Latent → KSampler → VAEDecode → 保存。",
     "kind": "文生图", "graph": _TXT2IMG},
    {"id": "builtin-txt2img-unet", "name": "文生图 · UNET 分离式（ANIMA/Flux/SD3）",
     "desc": "UNETLoader 从 diffusion_models 加载主模型 + DualCLIPLoader + VAELoader 分离加载。ANIMA、Flux、SD3 这类现代模型走这个。",
     "kind": "文生图", "graph": _TXT2IMG_UNET},
    {"id": "builtin-img2img", "name": "图生图 · Checkpoint 一体式",
     "desc": "Checkpoint 一体加载 + LoadImage + VAEEncode 提供初始 Latent，denoise 0.6 做图生图。",
     "kind": "图生图", "graph": _IMG2IMG},
    {"id": "builtin-img2img-unet", "name": "图生图 · UNET 分离式（ANIMA/Flux/SD3）",
     "desc": "UNETLoader + DualCLIPLoader + VAELoader 分离加载 + LoadImage/VAEEncode 初始 Latent，denoise 0.6。现代模型用这个。",
     "kind": "图生图", "graph": _IMG2IMG_UNET},
    {"id": "builtin-interrogate", "name": "反推起步骨架（加载图+预览）",
     "desc": "加载图片并预览的最小起点，反推节点(llama/wd14)按本机环境增量添加。",
     "kind": "反推", "graph": _INTERROGATE},
]


def _node_count(graph: dict) -> int:
    """API 格式或 UI 格式都能数节点。"""
    if not isinstance(graph, dict):
        return 0
    nodes = graph.get("nodes")
    if isinstance(nodes, list):  # UI 格式
        return len(nodes)
    return len(graph)  # API 格式：顶层就是 {id: node}


def list_skeletons(workflow_dir: str = "") -> list[dict]:
    """列出全部骨架候选：内置 + 用户工作流文件夹里的 .json。
    返回 [{id, name, desc, kind, source, node_count, path}]。
    内置的 path 为空、source=builtin；文件的 source=file、id 用相对路径。"""
    out: list[dict] = []
    for b in _BUILTINS:
        out.append({
            "id": b["id"], "name": b["name"], "desc": b["desc"], "kind": b["kind"],
            "source": "builtin", "node_count": _node_count(b["graph"]), "path": "",
        })
    base = Path(workflow_dir) if workflow_dir else None
    if base and base.exists() and base.is_dir():
        for p in sorted(base.rglob("*.json")):
            if not p.is_file():
                continue
            try:
                g = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                continue
            rel = str(p.relative_to(base))
            out.append({
                "id": f"file:{rel}", "name": p.stem, "desc": f"来自工作流文件夹：{rel}",
                "kind": "文件", "source": "file", "node_count": _node_count(g), "path": str(p),
            })
    return out


def get_skeleton_graph(skeleton_id: str, workflow_dir: str = "") -> dict | None:
    """按 id 取骨架的 graph（内置直接返回；file: 前缀读磁盘）。不存在返回 None。
    只读——绝不修改原文件。"""
    for b in _BUILTINS:
        if b["id"] == skeleton_id:
            return b["graph"]
    if skeleton_id.startswith("file:") and workflow_dir:
        rel = skeleton_id[len("file:"):]
        p = (Path(workflow_dir) / rel).resolve()
        base = Path(workflow_dir).resolve()
        # 防目录穿越：解析后必须仍在 workflow_dir 内
        if base not in p.parents and p != base:
            return None
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return None
    return None
