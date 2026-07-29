"""把用户填写的值 / 提示词注入到已转好的 API 工作流。

纯变换：不接触 ComfyUI、不做 I/O。原地改写 api 的 inputs 并返回缺失的必填输入标签，
路由层据此决定是否 422 拒绝。抽出来后可脱离 live ComfyUI 单测。
"""
from __future__ import annotations

_TEXT_FIELDS = ("text", "string", "prompt", "positive")


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
