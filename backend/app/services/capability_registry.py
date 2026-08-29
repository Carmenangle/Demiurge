"""能力注册表（Autopilot P0 单一属主）：把「计划可编排」的 services 能力面
变成机器可读清单，供 agent 编排 Demiurge 自己（docs/ROADMAP-AUTOPILOT.md）。

- **显式注册**，不做 AST 自动扫描——description 是人写的中文（做什么+影响什么），
  自动扫只产出垃圾描述；agent 内部协作用的中间服务不进清单。
- handler 只做薄适配：参数透传既有 services 函数，不藏业务；执行面无任意
  shell/MCP 兜底，未知动作拒绝而不是降级执行。
- manifest 由 ``scripts/generate_capability_manifest.py`` 导出（随源码发布），
  ``--check`` 防清单与注册表漂移。
- 首批能力（comfyui 批量模板提交路径）在本模块尾部注册；能力按 category
  分文件注册、导出合并的拆分待扩展批再做。
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field

CATEGORY_COMFYUI = "comfyui"
CATEGORIES = (CATEGORY_COMFYUI, "worldbook", "character", "repo", "rag", "media", "asset")

SIDE_EFFECT_READONLY = "readonly"
SIDE_EFFECT_REVERSIBLE = "reversible"
SIDE_EFFECT_DURABLE = "durable"
SIDE_EFFECT_EXPENSIVE = "expensive"
SIDE_EFFECT_LEVELS = (SIDE_EFFECT_READONLY, SIDE_EFFECT_REVERSIBLE,
                      SIDE_EFFECT_DURABLE, SIDE_EFFECT_EXPENSIVE)

CHANNEL_SYNC = "sync"
CHANNEL_QUEUE = "queue"
CHANNELS = (CHANNEL_SYNC, CHANNEL_QUEUE)

NEEDS_MODELS = ("chat", "image", "video", "audio", "embed")


@dataclass(frozen=True)
class Capability:
    operation: str                      # 动词.宾语，全局唯一（对齐 _EVENT_ACTIONS 风格）
    category: str
    description: str                    # 中文，写给人看：做什么+影响什么
    params_schema: dict = field(default_factory=dict)
    needs_model: str | None = None      # None/chat/image/video/audio/embed
    side_effect_level: str = SIDE_EFFECT_READONLY
    channel: str = CHANNEL_SYNC
    handler: str = ""                   # 执行适配器「模块:函数」

    def to_manifest(self) -> dict:
        return {
            "operation": self.operation,
            "category": self.category,
            "description": self.description,
            "params_schema": self.params_schema,
            "needs_model": self.needs_model,
            "side_effect_level": self.side_effect_level,
            "channel": self.channel,
            "handler": self.handler,
        }


_REGISTRY: dict[str, Capability] = {}


def register(capability: Capability) -> None:
    """显式注册一条能力；operation 重复/字段非法立即抛错（注册期闸门）。"""
    op = capability.operation
    if not op or "." not in op:
        raise ValueError(f"operation 必须是「动词.宾语」形式：{op!r}")
    if op in _REGISTRY:
        raise ValueError(f"operation 重复注册：{op}")
    if capability.category not in CATEGORIES:
        raise ValueError(f"{op}: 未知 category {capability.category!r}")
    if capability.side_effect_level not in SIDE_EFFECT_LEVELS:
        raise ValueError(f"{op}: 未知 side_effect_level {capability.side_effect_level!r}")
    if capability.channel not in CHANNELS:
        raise ValueError(f"{op}: 未知 channel {capability.channel!r}")
    if capability.needs_model is not None and capability.needs_model not in NEEDS_MODELS:
        raise ValueError(f"{op}: 未知 needs_model {capability.needs_model!r}")
    if capability.handler and ":" not in capability.handler:
        raise ValueError(f"{op}: handler 必须是「模块:函数」形式：{capability.handler!r}")
    _REGISTRY[op] = capability


def get(operation: str) -> Capability | None:
    return _REGISTRY.get(operation)


def all_capabilities() -> list[Capability]:
    return [_REGISTRY[op] for op in sorted(_REGISTRY)]


def build_manifest() -> dict:
    """导出 manifest（键序稳定，供 --check 逐字节对比）。"""
    return {
        "version": 1,
        "capabilities": [cap.to_manifest() for cap in all_capabilities()],
    }


def validate_handlers() -> list[str]:
    """逐条 import handler 验证「模块:函数」存在且可调用；返回错误列表（空=通过）。"""
    errors: list[str] = []
    for cap in all_capabilities():
        if not cap.handler:
            errors.append(f"{cap.operation}: 缺少 handler")
            continue
        module_name, _, func_name = cap.handler.partition(":")
        try:
            module = importlib.import_module(module_name)
            func = getattr(module, func_name)
        except (ImportError, AttributeError) as exc:
            errors.append(f"{cap.operation}: handler 不可导入 {cap.handler}（{exc}）")
            continue
        if not callable(func):
            errors.append(f"{cap.operation}: handler 不可调用 {cap.handler}")
    return errors


def with_availability(configured_models: set[str] | frozenset[str]) -> list[dict]:
    """运行时视图：按四类模型三级代理的已配置集合打 available 标记。

    needs_model 为 None 恒可用；未配置的模型打 available:false，agent 计划阶段即见缺口。
    """
    out: list[dict] = []
    for cap in all_capabilities():
        item = cap.to_manifest()
        item["available"] = cap.needs_model is None or cap.needs_model in configured_models
        out.append(item)
    return out


# ── 首批能力：comfyui 批量模板提交路径（P2 端到端首验「N 变体批量出图」）────────

register(Capability(
    operation="workflow.list_templates",
    category=CATEGORY_COMFYUI,
    description="列出全部工作流模板（只读，返回模板元数据与 exposed 字段，不提交任何任务）。",
    params_schema={"type": "object", "properties": {}, "additionalProperties": False},
    needs_model=None,
    side_effect_level=SIDE_EFFECT_READONLY,
    channel=CHANNEL_SYNC,
    handler="app.services.template_store:list_templates",
))

register(Capability(
    operation="workflow.read_exposed_fields",
    category=CATEGORY_COMFYUI,
    description="读取单个模板的 exposed 字段定义（字段名/控件/默认值/绑定），"
                "是编排注入变体值前必查的只读步骤。",
    params_schema={
        "type": "object",
        "properties": {"template_id": {"type": "string"}},
        "required": ["template_id"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_READONLY,
    channel=CHANNEL_SYNC,
    handler="app.services.template_store:get_template",
))

register(Capability(
    operation="workflow.submit_template",
    category=CATEGORY_COMFYUI,
    description="把注入值填进模板并提交一次 ComfyUI 队列任务（烧 GPU，受模型租约约束）。",
    params_schema={
        "type": "object",
        "properties": {
            "template_id": {"type": "string"},
            "values": {"type": "object"},
            "prompt": {"type": "string"},
            "url": {"type": "string"},
            "client_id": {"type": "string"},
        },
        "required": ["template_id", "values", "prompt", "url"],
        "additionalProperties": False,
    },
    needs_model="image",
    side_effect_level=SIDE_EFFECT_EXPENSIVE,
    channel=CHANNEL_QUEUE,
    handler="app.services.workflow_submission:submit_template",
))

register(Capability(
    operation="file.read_text",
    category="repo",
    description="读取一个本地 UTF-8 文本文件并返回内容（只读）。越出作品域的读取路径"
                "会在审批卡上明示，需批准计划后才能执行。",
    params_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
        "required": ["path"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_READONLY,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:read_text_file",
))

register(Capability(
    operation="media.collect_comfy_outputs",
    category="media",
    description="轮询 ComfyUI 历史取回已提交任务的图片，落作品文件夹并注册进资产库"
                "（generation RAG，挂提示词与「委派计划」标签）。写在作品域内。",
    params_schema={
        "type": "object",
        "properties": {
            "prompt_ids": {"type": "array", "items": {"type": "string"}},
            "submit_result": {"type": "object"},
            "comfyui_url": {"type": "string"},
            "output_dir": {"type": "string"},
            "repo_id": {"type": "string"},
            "names": {"type": "array", "items": {"type": "string"}},
            "prompts": {"type": "array", "items": {"type": "string"}},
            "timeout_seconds": {"type": "integer"},
        },
        "required": ["comfyui_url", "output_dir", "repo_id"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_REVERSIBLE,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:collect_comfy_outputs",
))

register(Capability(
    operation="workflow.submit_batch",
    category=CATEGORY_COMFYUI,
    description="对同一模板按变体值列表批量提交 ComfyUI 队列（每个变体一次任务，"
                "单条失败隔离不中断整批；烧 GPU，受每计划配额约束）。",
    params_schema={
        "type": "object",
        "properties": {
            "template_id": {"type": "string"},
            "variants": {"type": "array", "items": {"type": "object"}, "minItems": 1},
            "prompt": {"type": "string"},
            "url": {"type": "string"},
            "client_id": {"type": "string"},
        },
        "required": ["template_id", "variants", "url"],
        "additionalProperties": False,
    },
    needs_model="image",
    side_effect_level=SIDE_EFFECT_EXPENSIVE,
    channel=CHANNEL_QUEUE,
    handler="app.services.capability_handlers:submit_batch",
))
