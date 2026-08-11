"""把用户填写的值 / 提示词注入到已转好的 API 工作流。

纯变换：不接触 ComfyUI、不做 I/O。原地改写 api 的 inputs 并返回缺失的必填输入标签，
路由层据此决定是否 422 拒绝。抽出来后可脱离 live ComfyUI 单测。
"""
from __future__ import annotations

from copy import deepcopy

_TEXT_FIELDS = ("text", "string", "prompt", "positive")
_LORA_LOADERS = {"LoraLoader", "LoraLoaderModelOnly"}


def disable_all_loras(api: dict) -> None:
    """无 LoRA 模式保留图结构，但令所有标准 LoRA 加载器不产生影响。"""
    for node in api.values():
        if not isinstance(node, dict) or node.get("class_type") not in _LORA_LOADERS:
            continue
        inputs = node.setdefault("inputs", {})
        inputs["strength_model"] = 0
        if node.get("class_type") == "LoraLoader":
            inputs["strength_clip"] = 0


def inject_lora_stack(api: dict, anchor_node_id: str, loras: list[dict]) -> bool:
    """把 LoRA 列表串到模板现有加载器后，并把原下游改接到链尾。"""
    stack = [item for item in loras if str(item.get("name") or "").strip()]
    anchor_id = str(anchor_node_id or "")
    anchor = api.get(anchor_id)
    if not stack or not isinstance(anchor, dict):
        return False
    class_type = str(anchor.get("class_type") or "")
    if class_type not in _LORA_LOADERS:
        return False
    anchor_inputs = anchor.setdefault("inputs", {})

    def apply_values(inputs: dict, item: dict) -> None:
        weight = float(item.get("weight", 0.8))
        inputs["lora_name"] = str(item["name"])
        inputs["strength_model"] = weight
        if class_type == "LoraLoader":
            inputs["strength_clip"] = weight

    apply_values(anchor_inputs, stack[0])
    numeric_ids = [int(node_id) for node_id in api if str(node_id).isdigit()]
    next_id = max(numeric_ids, default=0) + 1
    chain_ids = {anchor_id}
    previous_id = anchor_id
    for item in stack[1:]:
        while str(next_id) in api:
            next_id += 1
        node_id = str(next_id)
        next_id += 1
        inputs = deepcopy(anchor_inputs)
        inputs["model"] = [previous_id, 0]
        if class_type == "LoraLoader":
            inputs["clip"] = [previous_id, 1]
        apply_values(inputs, item)
        api[node_id] = {"class_type": class_type, "inputs": inputs}
        chain_ids.add(node_id)
        previous_id = node_id
    if previous_id == anchor_id:
        return True
    for node_id, node in api.items():
        if node_id in chain_ids or not isinstance(node, dict):
            continue
        for key, value in (node.get("inputs") or {}).items():
            if isinstance(value, list) and len(value) == 2 and str(value[0]) == anchor_id:
                output_index = value[1]
                if output_index == 0 or (class_type == "LoraLoader" and output_index == 1):
                    node["inputs"][key] = [previous_id, output_index]
    return True


def inject_template_values(
    api: dict,
    exposed: list[dict],
    values: dict,
    prompt: str = "",
    prompt_node_id: str = "",
) -> list[str]:
    """套用暴露字段的用户值，并可选把 prompt 注入到指定节点的文本字段。

    - 仅覆盖模板暴露的字段（node_id.field）。
    - 输入型（control == "image"）为空 → 记入 missing。
    - prompt 非空且 prompt_node_id 命中 → 写首个常见文本字段，否则首个字符串字段。

    原地修改 api，返回 missing（缺失必填项的标签列表）。
    """
    exposed_keys = {f"{f['node_id']}.{f['field']}" for f in exposed}
    missing: list[str] = []
    for f in exposed:
        key = f"{f['node_id']}.{f['field']}"
        node_id, field = f["node_id"], f["field"]
        val = values.get(key)
        # 输入型（图片）为空 → 缺失，拒绝启动
        if f.get("control") == "image" and (val is None or val == ""):
            missing.append(f.get("label") or field)
            continue
        if val is None or key not in exposed_keys:
            continue
        if node_id in api:
            api[node_id].setdefault("inputs", {})[field] = val

    pid = str(prompt_node_id or "")
    if prompt and pid and pid in api:
        inp = api[pid].setdefault("inputs", {})
        target = next((k for k in _TEXT_FIELDS if k in inp), None)
        if target is None:
            target = next((k for k, v in inp.items() if isinstance(v, str)), None)
        if target is not None:
            inp[target] = prompt

    return missing
