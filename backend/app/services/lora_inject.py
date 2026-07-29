"""把 LoRA 触发词机械注入 AI 编排计划的正向提示词。

**为什么不交给大脑写**：触发词必须逐字精确，而 LLM 会把 `mksks style` 这类词翻译成中文、
改写成更通顺的英文、或干脆漏掉 —— 结果 LoRA 不生效，用户却看不出问题（画面照样出，只是
不像）。所以模型只管画面描述，触发词由本模块按文件名精确查表后前置。

保守原则：**只改计划里已有的正向提示词 op，不新增 op。** 用户只说「seed 改成 5」时，
计划里没有提示词 op，此时不该去动他没提的提示词口（对齐 _PORTS_SYSTEM 的「用户没提到的口
不要乱填」）。主流程（用户描述画面 → AI 必然写正向提示词）不受影响。
"""
from __future__ import annotations

# 采样器节点类型关键词：顺它的 positive 输入才能找到正向提示词节点
_SAMPLER_HINTS = ("sampler",)
# 提示词 widget 名。CLIPTextEncode 是 text；部分自定义节点用别名。
# 公开给 palette_inject 复用 —— 两者注入的是同一批正向提示词 op。
TEXT_WIDGET_NAMES = ("text", "text_g", "prompt", "positive")


def _widget_value(node: dict, name: str) -> str:
    for w in node.get("widgets") or []:
        if isinstance(w, dict) and w.get("name") == name:
            v = w.get("value")
            return v if isinstance(v, str) else ""
    return ""


def _is_lora_node(node: dict) -> bool:
    """靠「类型名含 lora + 有 lora_name widget」判定，兼容各种自定义 LoRA 加载器。"""
    ntype = str(node.get("type") or node.get("class_type") or "").lower()
    if "lora" not in ntype:
        return False
    return any(
        isinstance(w, dict) and w.get("name") == "lora_name"
        for w in node.get("widgets") or []
    )


def _effective_lora_name(node: dict, ops: list[dict]) -> str:
    """取该节点最终生效的 lora_name：计划里改了就用新值，否则用画布现值。

    覆盖「换成 xxx 这个 lora」的场景 —— 该注新 LoRA 的触发词，不是旧的。
    """
    nid = str(node.get("id", ""))
    for op in ops:
        if (str(op.get("node_id", "")) == nid
                and op.get("action") == "set_widget"
                and op.get("input") == "lora_name"):
            v = op.get("value")
            if isinstance(v, str) and v.strip():
                return v.strip()
    return _widget_value(node, "lora_name").strip()


def positive_node_ids(nodes: list[dict]) -> list[str]:
    """顺采样器的 positive 输入找正向提示词节点 id（可能有多个采样器）。

    公开给 palette_inject 复用 —— 定位正向提示词的规则只该有一份。
    """
    ids: list[str] = []
    for node in nodes:
        ntype = str(node.get("type") or node.get("class_type") or "").lower()
        if not any(h in ntype for h in _SAMPLER_HINTS):
            continue
        for inp in node.get("inputs") or []:
            if not isinstance(inp, dict) or inp.get("name") != "positive":
                continue
            src = str(inp.get("source_node_id") or "")
            if src and src not in ids:
                ids.append(src)
    return ids


def _already_present(text: str, word: str) -> bool:
    """触发词是否已在文本里（大小写不敏感）。用户自己写了就不重复注入。"""
    return word.lower() in (text or "").lower()


def collect_triggers(nodes: list[dict], ops: list[dict],
                     triggers_map: dict[str, list[str]]) -> list[str]:
    """按节点顺序收集该工作流所有生效 LoRA 的触发词，去重（保序）。

    跳过：bypass 的节点（mode 2=静音 / 4=绕过）、查不到触发词的 LoRA。
    """
    out: list[str] = []
    for node in nodes:
        if not _is_lora_node(node):
            continue
        if node.get("mode") in (2, 4):   # 绕过的 LoRA 没加载，注它的词纯属污染
            continue
        name = _effective_lora_name(node, ops)
        for word in triggers_map.get(name, []):
            if word not in out:
                out.append(word)
    return out


def inject(plan: dict, nodes: list[dict], scene: str,
           triggers_map: dict[str, list[str]]) -> list[str]:
    """就地改写 plan，把触发词前置到正向提示词 op。返回实际注入的词。

    只改计划里已有的正向提示词 op（见模块 docstring 的保守原则）。注入后会在 summary 末尾
    追一句说明 —— 计划要用户确认，得让他看见凭空多出来的词是哪来的。
    """
    ops = plan.get("ops") or []
    if not isinstance(ops, list):
        return []
    words = collect_triggers(nodes, ops, triggers_map)
    if not words:
        return []

    pos_ids = set(positive_node_ids(nodes))
    injected: list[str] = []
    for op in ops:
        if (op.get("action") != "set_widget"
                or str(op.get("node_id", "")) not in pos_ids
                or op.get("input") not in TEXT_WIDGET_NAMES):
            continue
        text = op.get("value")
        if not isinstance(text, str):
            continue
        # 用户在需求里已经点名的、或模型已经写进去的，都不重复注
        todo = [w for w in words
                if not _already_present(scene, w) and not _already_present(text, w)]
        if not todo:
            continue
        op["value"] = ", ".join(todo) + (", " + text if text.strip() else "")
        for w in todo:
            if w not in injected:
                injected.append(w)

    if injected:
        summary = str(plan.get("summary") or "")
        plan["summary"] = (summary + f"（已自动前置 LoRA 触发词：{', '.join(injected)}）").strip()
    return injected
