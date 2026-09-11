"""转场判定提取（纯逻辑：内嵌 transition 块指令 + 剥离解析）。

设计对齐 audio_dialogue_extract：0 I/O、0 LLM（LLM 调用在主 Roleplay 同轮完成）。
核心立场：
- <transition> 是「首帧复用判断」的 L1 搭车块：主模型在生成正文的同一次调用里
  默认输出「当前楼层开头 vs 上一楼层结尾」是否同一场景的判定（reuse|regenerate）。
- 它是「提前准备好的 L1」，不是「事后补判」——正文落地后不再读文本复核
  （防拦截文本再次读取可能被拦截/读不准，见 docs/PLAN-VIDEO-FIRSTLAST.md 10.2）。
- 剥离只是把已搭车产出的枚举值抽出来，不重发预设、不做专用片段。

依赖方向：只 import 标准库，可独立单测（不 import agent_graph / LLM / 网络）。
"""
from __future__ import annotations

import re

# 转场判定枚举：reuse（首帧复用上一楼层尾帧图）| regenerate（独立生成首帧）。
REUSE = "reuse"
REGENERATE = "regenerate"
_TRANSITION_VALUES = frozenset({REUSE, REGENERATE})

# 双闭合正则（前后空白容错）+ 开尾截断正则（模型只开不闭时，把 <transition> 起截断，
# 避免把后续正文吞掉）——对齐 audio_dialogue_extract 的 _AUDIO_RE / _AUDIO_OPEN_TAIL_RE。
_TRANSITION_RE = re.compile(r"\s*<transition>\s*([\s\S]*?)\s*</transition>\s*", re.I)
_TRANSITION_OPEN_TAIL_RE = re.compile(r"\s*<transition>\s*[\s\S]*\Z", re.I)

_INLINE_TRANSITION_INSTRUCTION = (
    "\n\n【转场判定】正文仍按剧情自然推进，并把完整可见正文放在 <content>...</content> 中；"
    "think、状态块、表格块、illustration 块、audio 块、transition 块都不计入正文。"
    "在全部正文与状态块之后追加一个内部块，不得省略。"
    "判断「本轮正文的开头画面」与「上一轮对话的结尾画面」是否构成「一张图可涵盖」的关系："
    "- 若两者仍在同一场景（构图、场景、站位无显著变化）→ 输出 reuse；"
    "- 若发生地点切换、时间跳跃、构图或站位明显变化 → 输出 regenerate。"
    "只依据剧情事实判断，不得为迁就画面篡改判定。只允许以下格式：\n"
    "<transition>reuse</transition> 或 <transition>regenerate</transition>"
)


def build_inline_transition_instruction() -> str:
    """返回主 Roleplay 同次生成使用的转场判定契约。"""
    return _INLINE_TRANSITION_INSTRUCTION


def extract_transition(reply: str) -> tuple[str, str | None]:
    """从主生成回复剥离并校验转场判定；返回 (去块正文, decision)。

    decision ∈ {"reuse", "regenerate"}；漏块 / 值非法 / 只开不闭 → (去块正文, None)，
    不抛错（L1 缺失时回退 L0 结论，见 docs/PLAN-VIDEO-FIRSTLAST.md 10.2）。
    """
    source = reply or ""
    clean = _TRANSITION_RE.sub("", source)
    unterminated = _TRANSITION_OPEN_TAIL_RE.search(clean)
    if unterminated:
        # 模型只开不闭：截断开尾，避免把正文吞掉。
        clean = clean[:unterminated.start()].rstrip()
        return clean, None
    clean = clean.rstrip()
    matches = list(_TRANSITION_RE.finditer(source))
    if not matches:
        return clean, None
    value = matches[-1].group(1).strip().lower()
    if value not in _TRANSITION_VALUES:
        return clean, None
    return clean, value
