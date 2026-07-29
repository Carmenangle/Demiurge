"""把「当前色彩约束」机械追加到 AI 编排计划的正向提示词。

与 lora_inject 是同一类问题、同一套保守原则（只改已有的正向提示词 op、不新增 op、
往 summary 追说明），因此直接复用它的图遍历函数，不另写一套。

两处刻意的差异：
1. **追加而非前置。** 触发词决定 LoRA 是否生效，必须靠前；色板只是风格约束，
   前置会把画面主体挤到后面，反而削弱主体描述。
2. **用户自己提了颜色就整体跳过。** 色彩约束是「默认值」性质的偏好，用户在这轮
   明确说了配色（说了色号，或说了「改成蓝色调」之类），就该听他的，
   而不是把两套配色都塞进去打架。
"""
from __future__ import annotations

import re

from app.services.lora_inject import TEXT_WIDGET_NAMES, positive_node_ids

# 用户这轮自己谈到颜色的信号。命中即整体跳过注入。
# 只收「明确在指定配色」的说法，不收「红色头发」这种描述局部物体的 —— 后者
# 与整体色板不冲突。这个取舍偏保守：宁可少注一次，不要覆盖用户的明确意图。
_HEX_IN_TEXT = re.compile(r"#[0-9a-fA-F]{3,6}\b")
_COLOR_INTENT = (
    "配色", "色调", "色板", "调色盘", "palette", "color scheme", "colour scheme",
    "颜色改", "改颜色", "换色", "色彩风格",
)


def _user_specified_colors(scene: str) -> bool:
    low = (scene or "").lower()
    if _HEX_IN_TEXT.search(scene or ""):
        return True
    return any(k in low for k in _COLOR_INTENT)


def _constraint_text(colors: list[str]) -> str:
    """拼成提示词片段。

    带上 `color palette:` 前缀而不是裸列色号 —— 裸色号在提示词里语义不明，
    模型容易当噪声忽略；点明是调色盘时它会真的往那个色系走。
    """
    return "color palette: " + ", ".join(colors)


def inject(plan: dict, nodes: list[dict], scene: str,
           colors: list[str]) -> list[str]:
    """就地改写 plan，把色彩约束追加到正向提示词 op。返回实际注入的色号。"""
    if not colors:
        return []
    if _user_specified_colors(scene):
        return []
    ops = plan.get("ops") or []
    if not isinstance(ops, list):
        return []

    pos_ids = set(positive_node_ids(nodes))
    hit = False
    for op in ops:
        if (op.get("action") != "set_widget"
                or str(op.get("node_id", "")) not in pos_ids
                or op.get("input") not in TEXT_WIDGET_NAMES):
            continue
        text = op.get("value")
        if not isinstance(text, str):
            continue
        # 模型已经把某个色号写进去了 => 这一口不重复加
        if any(c.lower() in text.lower() for c in colors):
            continue
        frag = _constraint_text(colors)
        op["value"] = (text.rstrip().rstrip(",") + ", " + frag) if text.strip() else frag
        hit = True

    if hit:
        summary = str(plan.get("summary") or "")
        plan["summary"] = (
            summary + f"（已按当前色彩约束追加配色：{', '.join(colors)}）").strip()
        return list(colors)
    return []
