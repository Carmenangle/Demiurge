"""Typed ComfyUI Graph Compiler：AI 输出意图，Compiler 确定性编排。

设计原则：模型只输出工作流意图与阶段计划；确定性 Compiler 根据节点知识库
选节点、连端口、填默认参数；提交前做类型/模型族/输入完整性/输出可达性/
显存预算检查；无法编译时返回具体缺口（而非让模型重猜整张图）。

用法示例：
    result = WorkflowCompiler.compile(
        need="生成一张写实人像，用 SDXL",
        current_graph={},          # 可选：已有骨架
        object_info=object_info,   # ComfyUI object_info
        node_index_cfg=cfg,        # node_index EmbedConfig
        chat_fn=llm.chat,
        model_base_url=...,
    )
    if result.gaps:
        # 按 gap 逐项修复，不重猜整图
        for gap in result.gaps:
            if gap.kind == "model_family":
                suggest = node_index.suggest_alternatives(cfg, gap.missing_names, object_info)
                ...
    else:
        graph = result.graph  # 可直接提交
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from app.services import node_index as _node_index
from app.services import workflow_graph_rules as _wgr

if TYPE_CHECKING:
    from app.services.rag_backend import EmbedConfig

logger = logging.getLogger(__name__)

# ── 类型别名 ──────────────────────────────────────────────────────────────────

JSON = dict | list | str | int | float | bool | None

# ── 编译结果 ─────────────────────────────────────────────────────────────────

# pylint: disable=too-many-instance-attributes


class CompilerGaps:
    """不可自动弥合的缺口（需用户/AI 决策）。"""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        node_id: str = "",
        missing_names: list[str] | None = None,
        missing_types: list[str] | None = None,
        suggestion: str = "",
    ) -> None:
        self.kind = kind          # model_not_found | port_unreachable | vram_exceeded | type_mismatch | graph_empty
        self.message = message    # 人类可读描述
        self.node_id = node_id    # 关联节点（如有）
        self.missing_names = missing_names or []
        self.missing_types = missing_types or []
        self.suggestion = suggestion  # 建议动作


class CompilerWarnings:
    """可记录但不阻断编译的问题。"""

    def __init__(
        self,
        issues: list[str] | None = None,
        filled_combos: int = 0,
        split_nodes: list[str] | None = None,
        audit_issues: list[str] | None = None,
    ) -> None:
        self.issues = issues or []           # audit_graph 返回的结构问题
        self.filled_combos = filled_combos   # fill_combo_defaults 改动数
        self.split_nodes = split_nodes or [] # 被拆掉的缺失节点 class_type
        self.audit_issues = audit_issues or []  # audit 零碎问题


class CompilerResult:
    """编译结果：成功时含可提交图，失败时含具体缺口。"""

    def __init__(
        self,
        graph: JSON | None = None,
        *,
        gaps: list[CompilerGaps] | None = None,
        warnings: CompilerWarnings | None = None,
        vram_mib: int = 0,
        compile_ms: int = 0,
    ) -> None:
        self.graph = graph               # 成功时为 API 格式 graph；失败时 None
        self.gaps = gaps or []           # 不可自动弥合的缺口
        self.warnings = warnings or CompilerWarnings()
        self.vram_mib = vram_mib         # 估算显存消耗（Mib）
        self.compile_ms = compile_ms     # 编译耗时（毫秒）
        self.ok = len(gaps or []) == 0 and graph is not None


# ── 显存估算 ─────────────────────────────────────────────────────────────────

# 各节点类典型显存占用（单位 Mib；实际取决于参数规模，此处为保守估算）
# 只估算大显存节点：加载器、采样器、VAE、解码器
_VRAM_ESTIMATE: dict[str, int] = {
    # 加载器
    "CheckpointLoader": 7000,
    "CheckpointLoaderSimple": 7000,
    "UNETLoader": 5000,
    "CLIPLoader": 500,
    "DualCLIPLoader": 1000,
    "VAELoader": 800,
    "LoraLoader": 100,
    "LoraLoaderModelOnly": 3000,   # UNET 部分
    # 采样器
    "KSampler": 0,
    "KSamplerAdvanced": 0,
    "SamplerCustom": 0,
    "ModelSamplingContinuous": 0,
    "ModelSamplingDiscrete": 0,
    "CFGNorm": 0,
    # VAE
    "VAEDecode": 2000,
    "VAEDecodeUsingTiles": 2500,
    "VAEEncode": 500,
    "VAEEncodeUsingTiles": 800,
    # 图像 I/O
    "SaveImage": 100,
    "PreviewImage": 100,
    "LoadImage": 50,
    # ControlNet
    "ControlNetApply": 1500,
    "ControlNetApplyAdvanced": 1500,
    # IPAdapter
    "ImageUpscaleWithModel": 2000,
    "IPAdapterApply": 2000,
}


def estimate_vram(graph: JSON, object_info: dict) -> int:
    """估算工作流显存占用（Mib）。只累计大显存节点，同类节点取最大。"""
    if not isinstance(graph, dict):
        return 0
    seen_kinds: dict[str, int] = {}
    total = 0
    for nid, node in graph.items():
        if not isinstance(node, dict):
            continue
        ct = str(node.get("class_type", ""))
        mem = _VRAM_ESTIMATE.get(ct, 0)
        # 同类节点只算一次（多个 LoRA 加载器时取最大）
        if ct not in seen_kinds or seen_kinds[ct] < mem:
            if seen_kinds.get(ct, 0) < mem:
                total = total - seen_kinds.get(ct, 0) + mem
            seen_kinds[ct] = mem
    return total


# ── 模型族兼容性 ─────────────────────────────────────────────────────────────

# 主流 UNet/CLIP/VAE 三件套对应关系（启发式，参照 ComfyUI 社区约定）
_UNET_FAMILY: dict[str, set[str]] = {
    "sd15": {
        "stable-diffusion", "sd15", "v1", "sd-v1",
        "sd-xl", "sdxl", "sdxl-base", "sd-xl-base",
    },
    "sd3": {
        "stable-diffusion-3", "sd3", "sd3-medium", "sd3.5",
    },
    "flux": {"flux", "flux1", "flux-dev", "flux-schnell"},
    "pony": {"pony", "ponysd", "animagine", "animaginexl"},
}

_CLIP_FAMILY: dict[str, set[str]] = {
    "sd15": {"clip", "sd15-clip", "openai/clip-vit"},
    "sdxl": {"clip_l", "clip_g", "xl", "sdxl-clip"},
    "flux": {"t5", "t5xxl", "gepush"},
    "pony": {"pony-clip", "lllyasviel/clip"},
}

_VAE_FAMILY: dict[str, set[str]] = {
    "sd15": {"vae", "sd15-vae", "ema-vae"},
    "sdxl": {"vae", "sdxl-vae", "sdxl-vae-ft-ema"},
    "flux": {"vae", "ae", "flux-vae"},
    "pony": {"vae", "pony-vae"},
}


def infer_model_family(node: dict, object_info: dict) -> str | None:
    """从加载器节点的 widget 值推断模型族（sd15/sdxl/flux/ Pony 等）。"""
    ct = node.get("class_type", "")
    inp = node.get("inputs", {}) or {}
    if ct in ("CheckpointLoader", "CheckpointLoaderSimple", "UNETLoader"):
        for field in ("ckpt_name", "unet_name", "model_name"):
            val = inp.get(field, "")
            if val:
                val_lower = str(val).lower()
                for fam, markers in _UNET_FAMILY.items():
                    if any(m in val_lower for m in markers):
                        return fam
    return None


def check_model_family_consistency(graph: JSON, object_info: dict) -> list[CompilerGaps]:
    """检查模型族一致性：Checkpoint/UNET/CLIP/VAE 是否配套。"""
    if not isinstance(graph, dict):
        return []
    # 找各加载器节点
    loaders: dict[str, dict] = {}  # role -> node
    for nid, node in graph.items():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        if ct in ("CheckpointLoader", "CheckpointLoaderSimple"):
            loaders["checkpoint"] = node
        elif ct == "UNETLoader":
            loaders["unet"] = node
        elif ct in ("DualCLIPLoader", "CLIPLoader"):
            loaders.setdefault("clip", node)
        elif ct == "VAELoader":
            loaders["vae"] = node

    if len(loaders) < 2:
        return []  # 只有一个加载器时无法判断一致性

    gaps: list[CompilerGaps] = []
    # 如果同时存在 CheckpointLoader 和 UNETLoader/CLIPLoader，可能是不配套的分离式加载
    has_checkpoint = "checkpoint" in loaders
    has_unet = "unet" in loaders
    has_clip = "clip" in loaders
    has_vae = "vae" in loaders

    # 分离式加载（UNETLoader+DualCLIPLoader）必须有配套的 VAE（同一族）
    if has_unet and has_vae:
        # 检查 VAE 是否与 UNET 同族（简单启发：均含 "sdxl" 或均含 "flux" 等）
        unet_val = str(loaders["unet"].get("inputs", {}).get("unet_name", "")).lower()
        vae_val = str(loaders["vae"].get("inputs", {}).get("vae_name", "")).lower()
        for marker in ("sdxl", "flux", "sd3", "pony", "animagine"):
            in_unet = marker in unet_val
            in_vae = marker in vae_val
            if in_unet and not in_vae:
                gaps.append(CompilerGaps(
                    kind="model_family",
                    message=f"UNET 与 VAE 模型族不匹配：UNET 含「{marker}」，VAE 不含。建议换配套 VAE。",
                    node_id="vae",
                    suggestion=f"查找本机含「{marker}」的 VAE，或在 object_info 中确认 VAE 族别。",
                ))
                break

    # CLIP 与 UNET/Checkpoint 配套检查
    if has_clip and (has_unet or has_checkpoint):
        clip_inputs = loaders.get("clip", {}).get("inputs", {}) or {}
        clip_val = str(
            clip_inputs.get("clip_name1", "") or
            clip_inputs.get("clip_name", "") or
            clip_inputs.get("clip_name2", "")
        ).lower()
        target_val = str(
            loaders.get("unet", {}).get("inputs", {}).get("unet_name", "") or
            loaders.get("checkpoint", {}).get("inputs", {}).get("ckpt_name", "")
        ).lower()
        for marker in ("sdxl", "flux", "t5", " Pony"):
            if marker in target_val and marker not in clip_val:
                gaps.append(CompilerGaps(
                    kind="model_family",
                    message=f"CLIP 与模型不配套：主模型含「{marker}」，CLIP 不含。建议用配套 CLIP。",
                    node_id="clip",
                    suggestion=f"确认本机 clip_name 配套「{marker}」模型。",
                ))
                break

    return gaps


# ── 核心 Compiler ─────────────────────────────────────────────────────────────


class WorkflowCompiler:
    """确定性工作流编译器。给定意图 + 节点知识库，输出可提交图或结构化缺口。"""

    def __init__(
        self,
        object_info: dict,
        node_index_cfg: EmbedConfig | None,
    ) -> None:
        self.object_info = object_info
        self.node_index_cfg = node_index_cfg

    def compile(self, need: str, current_graph: JSON = None,
                stage_plan: str = "") -> CompilerResult:
        """编译入口。参数：

        need: AI 描述的工作流意图（自然语言）
        current_graph: 可选，已有骨架图（增量模式）
        stage_plan: 可选，AI 给出的阶段计划（如 "1. 加载SDXL模型 2. 用LoRA微调 3. 采样"）
        """
        import time
        t0 = time.monotonic_ns()
        graph = self._init_graph(current_graph)
        gaps: list[CompilerGaps] = []

        # 1. 清理缺失节点（AI 可能编了不存在的节点）
        clean, missing_types = _wgr.split_missing_nodes(graph, self.object_info)
        if missing_types:
            # 检索平替，写进缺口建议供用户/AI 决策
            alts: dict[str, list[str]] = {}
            if self.node_index_cfg:
                alts = _node_index.suggest_alternatives(
                    self.node_index_cfg,
                    missing_types,
                    self.object_info,
                    k=4,
                )
            alt_text = "; ".join(
                f"{name} → {', '.join(cands[:3])}" if cands else f"{name} → (无平替)"
                for name, cands in alts.items()
            )
            gaps.append(CompilerGaps(
                kind="model_not_found",
                message=f"以下节点类型本机未安装：{missing_types}",
                missing_names=missing_types,
                suggestion=alt_text or "请安装对应节点包，或改用本机已有节点。",
            ))
        graph = clean

        # 2. 填充 combo 默认值（近似纠正）
        filled = _wgr.fill_combo_defaults(graph, self.object_info)

        # 3. 硬校验：类型/存在性/必填连线口
        hard_errors = _wgr.validate_graph(graph, self.object_info)
        if hard_errors:
            for err in hard_errors:
                # 提取节点 ID（格式 "节点 XXXX(...) 缺必填输入口 YYYY"）
                nid_m = re.search(r"节点\s+(\S+)", err)
                nid = nid_m.group(1) if nid_m else ""
                gaps.append(CompilerGaps(
                    kind="type_mismatch" if "类型不匹配" in err else "port_unreachable",
                    message=err,
                    node_id=nid,
                    suggestion="参考 object_info 中该节点的 required_links，补充缺失连线或 widget 值。",
                ))

        # 4. 模型族一致性
        gaps.extend(check_model_family_consistency(graph, self.object_info))

        # 5. 显存估算
        vram = estimate_vram(graph, self.object_info)
        # 当前 model_lease 持有量作为参考（仅报告，不强制阻断）
        # actual_free 由 model_lease.status() 实时获取，此处只做估算提示

        # 6. 结构审核（警告级，不阻断）
        audit_issues = _wgr.audit_graph(graph, self.object_info)
        reach_warns = _wgr.reachability_warnings(graph, self.object_info)

        compile_ms = int((time.monotonic_ns() - t0) / 1_000_000)
        warnings = CompilerWarnings(
            issues=reach_warns,
            filled_combos=filled,
            split_nodes=missing_types,
            audit_issues=audit_issues,
        )

        if gaps:
            return CompilerResult(
                graph=None,
                gaps=gaps,
                warnings=warnings,
                vram_mib=vram,
                compile_ms=compile_ms,
            )

        # 7. 输出节点可达性终检
        if not _has_output_reachable(graph, self.object_info):
            gaps.append(CompilerGaps(
                kind="graph_empty",
                message="工作流无法抵达任何输出节点，请确认至少有一个 SaveImage/PreviewImage。",
                suggestion="在图中加入 SaveImage 或 PreviewImage，并确保其输入连线到上游节点。",
            ))
            return CompilerResult(
                graph=None,
                gaps=gaps,
                warnings=warnings,
                vram_mib=vram,
                compile_ms=compile_ms,
            )

        return CompilerResult(
            graph=graph,
            gaps=[],
            warnings=warnings,
            vram_mib=vram,
            compile_ms=compile_ms,
        )

    def _init_graph(self, current: JSON) -> dict:
        """标准化输入图为 API 格式。"""
        if isinstance(current, dict) and current:
            return current
        return {}

    def interface_sheet(self, node_names: list[str], max_nodes: int = 60,
                       priority: set | None = None) -> str:
        """生成节点接口速查表（供 AI 后续修复缺口用）。"""
        return _wgr.interface_sheet(node_names, self.object_info, max_nodes, priority)


# ── 辅助 ─────────────────────────────────────────────────────────────────────


def _has_output_reachable(graph: dict, object_info: dict) -> bool:
    """至少有一个输出节点可达。"""
    sinks = {
        nid for nid, node in graph.items()
        if object_info.get(node.get("class_type", ""), {}).get("output_node")
    }
    if not sinks:
        return False
    # 反向 BFS
    upstream: dict[str, set[str]] = {nid: set() for nid in graph}
    for nid, node in graph.items():
        for val in (node.get("inputs", {}) or {}).values():
            if isinstance(val, list) and len(val) == 2:
                up = str(val[0])
                if up in upstream:
                    upstream[nid].add(up)
    reachable = set(sinks)
    stack = list(sinks)
    while stack:
        cur = stack.pop()
        for up in upstream.get(cur, ()):
            if up not in reachable:
                reachable.add(up)
                stack.append(up)
    return bool(reachable)


# ── 便捷入口 ─────────────────────────────────────────────────────────────────


def compile(need: str, object_info: dict,
           node_index_cfg: EmbedConfig | None = None,
           current_graph: JSON = None) -> CompilerResult:
    """一行编译入口。"""
    compiler = WorkflowCompiler(object_info, node_index_cfg)
    return compiler.compile(need, current_graph)


# ── gap → 人类可读摘要（供前端展示） ─────────────────────────────────────────


def format_gaps(gaps: list[CompilerGaps]) -> str:
    """把缺口列表格式化为人类可读摘要。"""
    if not gaps:
        return "编译通过，无缺口。"
    lines = [f"共 {len(gaps)} 个缺口："]
    for i, g in enumerate(gaps, 1):
        lines.append(f"{i}. [{g.kind}] {g.message}")
        if g.suggestion:
            lines.append(f"   → {g.suggestion}")
    return "\n".join(lines)
