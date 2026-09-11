"""某个加载器 widget 上「合法的模型文件」清单，以及对 AI 计划的校验。

**为什么不需要自己给模型分类。** checkpoint / lora / unet / vae 该怎么归类、
取舍哪些目录 —— 这个问题不用回答，因为 ComfyUI 的 /object_info 已经按节点给了答案：
每个加载器的模型名 widget 直接带着该位置合法的文件枚举。所以正确的提问不是
「这个模型属于哪一类」，而是「这个节点上什么文件合法」。不用扫盘。

拿它来做两件事：
1. 给前端出可选项（用户从合法值里挑，不用手打）；
2. 校验 AI 计划里的模型名 —— 不在枚举里就拦掉，避免把编出来的文件名写进画布，
   那会让工作流跑到一半才报 "value not in list"。
"""
from __future__ import annotations

from app.services.comfyui_client import fetch_object_info

# 模型名 widget 的名字。判定「这个 widget 装的是模型文件」用它。
MODEL_WIDGETS = frozenset({
    "ckpt_name", "unet_name", "lora_name", "vae_name", "clip_name",
    "model_name", "gguf_name", "diffusion_model", "control_net_name",
    "style_model_name", "upscale_model_name",
})


def options_for(comfy_url: str, class_type: str, widget: str) -> list[str]:
    """某节点某 widget 的合法取值列表。取不到返回 []（调用方据此放行，不误拦）。"""
    try:
        info = fetch_object_info(comfy_url, class_type)
    except Exception:
        return []
    schema = info.get(class_type) if isinstance(info, dict) else None
    if not isinstance(schema, dict):
        return []
    spec = ((schema.get("input") or {}).get("required") or {}).get(widget)
    if spec is None:
        spec = ((schema.get("input") or {}).get("optional") or {}).get(widget)
    # 枚举的形状是 [[选项...], {配置}]；非枚举（FLOAT/INT 等）第 0 项是类型名字符串
    if isinstance(spec, list) and spec and isinstance(spec[0], list):
        return [str(x) for x in spec[0]]
    return []


def _widget_ops(plan: dict) -> list[dict]:
    ops = plan.get("ops")
    if not isinstance(ops, list):
        return []
    return [op for op in ops
            if isinstance(op, dict) and op.get("action") == "set_widget"
            and str(op.get("input") or "") in MODEL_WIDGETS]


def validate_plan(plan: dict, nodes: list[dict], comfy_url: str) -> list[str]:
    """就地剔掉计划里模型名非法的 op，返回被剔掉项的说明。

    只在能拿到枚举时才拦 —— ComfyUI 没跑或查不到 schema 时一律放行，
    否则会把本来正确的操作也拦掉。
    """
    ops = _widget_ops(plan)
    if not ops:
        return []
    by_id = {str(n.get("id")): n for n in nodes if isinstance(n, dict)}
    dropped: list[str] = []
    bad_ops: list[dict] = []
    for op in ops:
        node = by_id.get(str(op.get("node_id")))
        if not node:
            continue
        class_type = str(node.get("type") or node.get("class_type") or "")
        widget = str(op.get("input") or "")
        value = op.get("value")
        if not class_type or not isinstance(value, str) or not value.strip():
            continue
        allowed = options_for(comfy_url, class_type, widget)
        if not allowed:
            continue                      # 查不到枚举 → 放行
        if value in allowed:
            continue
        # 大小写/路径分隔符差异不算错，按规范化再比一次
        norm = {a.replace("\\", "/").lower(): a for a in allowed}
        fixed = norm.get(value.replace("\\", "/").lower())
        if fixed:
            op["value"] = fixed           # 修正成枚举里的原样写法
            continue
        bad_ops.append(op)
        dropped.append(
            f"{class_type}.{widget} = 「{value}」不是这个位置的合法模型文件"
            f"（该位置有 {len(allowed)} 个可选值）")
    if bad_ops:
        plan["ops"] = [op for op in plan["ops"] if op not in bad_ops]
        summary = str(plan.get("summary") or "")
        plan["summary"] = (
            summary + "（已移除 " + str(len(bad_ops)) + " 个模型名无效的操作："
            + "；".join(dropped) + "。请从下拉可选值里挑，或确认文件已放进对应目录）").strip()
    return dropped
