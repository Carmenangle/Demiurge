"""解析 ComfyUI workflow JSON，输出节点和字段信息。

支持两种格式：
- API 格式：顶层即节点字典 { "5": {"class_type": ..., "inputs": {...}} }，inputs 是字典
- UI(编辑器导出)格式：{ "nodes": [ {id,type,inputs:[...],widgets_values:[...]} ], ... }
  其中 widgets_values 是无名数组，需按节点类型的 widget 顺序映射出字段名。
"""
from __future__ import annotations

# 常见节点的 widget 字段名顺序（UI 格式 widgets_values 没有名字，靠此表还原）。
# 表外的节点用 widget_0 / widget_1 … 占位。
WIDGET_NAMES: dict[str, list[str]] = {
    "KSampler": ["seed", "control_after_generate", "steps", "cfg", "sampler_name", "scheduler", "denoise"],
    "KSamplerAdvanced": [
        "add_noise", "noise_seed", "control_after_generate", "steps", "cfg",
        "sampler_name", "scheduler", "start_at_step", "end_at_step", "return_with_leftover_noise",
    ],
    "CLIPTextEncode": ["text"],
    "EmptyLatentImage": ["width", "height", "batch_size"],
    "CheckpointLoaderSimple": ["ckpt_name"],
    "VAELoader": ["vae_name"],
    "LoraLoader": ["lora_name", "strength_model", "strength_clip"],
    "LoadImage": ["image", "upload"],
    "SaveImage": ["filename_prefix"],
    "ImageScale": ["upscale_method", "width", "height", "crop"],
    "PrimitiveNode": ["value"],
}

# 中转/纯注释节点：既不参与暴露，运行期也需穿透。convert 在此基础上另加 PrimitiveNode
# （PrimitiveNode 运行期要穿透、但编辑器里可暴露其 value，故差异是有意的，不并进这里）。
PASSTHROUGH_TYPES = {"Note", "MarkdownNote", "Reroute"}

# 纯注释 / 中转节点：不参与暴露
SKIP_TYPES = PASSTHROUGH_TYPES


def _fields_from_api_node(node: dict) -> list[dict]:
    inputs = node.get("inputs", {}) or {}
    fields = []
    for name, value in inputs.items():
        linked = isinstance(value, list)  # ["4", 0] 形式 = 连线
        fields.append(
            {
                "name": name,
                "value": None if linked else value,
                "linked": linked,
                "required": False,  # 不接 /object_info 无法可靠判定，交由用户手动勾选
            }
        )
    return fields


def _fields_from_ui_node(node: dict) -> list[dict]:
    fields: list[dict] = []
    # 1) inputs 数组：有 link 则为连线，无 link 视为可选输入槽（不强制必填）
    for inp in node.get("inputs", []) or []:
        if not isinstance(inp, dict):
            continue
        linked = inp.get("link") is not None
        fields.append(
            {
                "name": inp.get("name", ""),
                "value": None,
                "linked": linked,
                "required": False,
            }
        )
    # 2) widgets_values：按类型表映射出名字
    widgets = node.get("widgets_values")
    if isinstance(widgets, list):
        names = WIDGET_NAMES.get(node.get("type", ""), [])
        for i, value in enumerate(widgets):
            name = names[i] if i < len(names) else f"widget_{i}"
            fields.append(
                {
                    "name": name,
                    "value": value,
                    "linked": False,
                    "required": False,
                }
            )
    return fields


def parse_workflow(workflow: dict) -> list[dict]:
    """返回节点列表，每个节点含可暴露字段及必填标记。"""
    if not isinstance(workflow, dict):
        return []

    result: list[dict] = []

    # UI / 编辑器导出格式：nodes 为列表
    nodes = workflow.get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            ntype = node.get("type", "")
            if ntype in SKIP_TYPES:
                continue  # 注释/中转节点不展示
            meta = node.get("properties", {}) or {}
            title = node.get("title") or meta.get("Node name for S&R", "")
            mode = node.get("mode", 0)  # 0=正常 2=静音 4=绕过
            result.append(
                {
                    "id": str(node.get("id", "")),
                    "class_type": ntype,
                    "title": title,
                    "bypassed": mode in (2, 4),
                    "fields": _fields_from_ui_node(node),
                }
            )
        return result

    # nodes 为字典，或顶层即 API 节点字典
    if isinstance(nodes, dict):
        node_dict = nodes
    else:
        node_dict = {
            k: v for k, v in workflow.items()
            if isinstance(v, dict) and "class_type" in v
        }

    for node_id, node in node_dict.items():
        if not isinstance(node, dict):
            continue
        result.append(
            {
                "id": str(node_id),
                "class_type": node.get("class_type", ""),
                "title": (node.get("_meta") or {}).get("title", ""),
                "bypassed": False,
                "fields": _fields_from_api_node(node),
            }
        )
    return result


# 兼容旧调用
def parse_workflow_nodes(workflow: dict) -> list[dict]:
    return parse_workflow(workflow)
