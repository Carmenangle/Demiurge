"""通用提示词清洗规则（共享，图像/视频/音频等一切提示词共用）。

用途：把「剧情正文 → 最终交给生成模型的提示词」之间的清洗链抽成一份**共享规则**，
让所有模态（图像 / 视频 / 未来音频等）都用同一套机制，避免各自为政、重复实现、
浪费上下文。规则文档见 `docs/PROMPT-CLEANING-RULES.md`（单一事实来源）。

清洗链（按序）：
1. 破甲还原：去掉 @()@ 系防拦截拆字标记（_MARKER_RE，对齐用户正则 $1$2$3）。
2. 可见正文：剥掉 <think>/<content>/<status> 等控制块，只留用户可见剧情
   （visible_narrative_text 已在 image_prompt_extract，本模块只负责可复用兜底）。
3. 客观提取：从叙述性文本提取「角色外貌/服装/场景/动作」等客观画面事实
   （各模态自行做结构化提取，本模块提供纯函数清洗兜底）。
4. 拼装：按目标语言（booru 串 / 自然语言 / H3 叙事）拼装成最终提示词。

纯函数边界：0 I/O、0 LLM、不 import agent_graph / image_gen / video_prompt，
可独立单测。图像生成的 IMAGE_PROMPT 清洗规则被删除后，图像提示词仍由本模块
的共享规则庇护防拦截（回归测试 `test_prompt_clean.py` 保证）。
"""
from __future__ import annotations

import re

# 破甲标记还原：对齐用户给的正则 /@\(([^()]*)\)(?=@)|@\(([^()]*)\)|\(([^()]*)\)@|@/g → $1$2$3
# 覆盖 @(x)@ 包裹式；剩余裸 @ 直接删。i/<i> 等分隔符另由用户在 IMAGE_PROMPT 正则里配（此处只兜底 @ 系）。
_MARKER_RE = re.compile(r"@\(([^()]*)\)(?=@)|@\(([^()]*)\)|\(([^()]*)\)@|@")


def restore_jailbreak_with_offsets(text: str) -> tuple[str, list[int]]:
    """还原破甲标记，并返回每个可见字符在原文中的结束偏移。"""
    if not text:
        return text, []
    visible: list[str] = []
    offsets: list[int] = []
    cursor = 0
    for match in _MARKER_RE.finditer(text):
        for index in range(cursor, match.start()):
            visible.append(text[index])
            offsets.append(index + 1)
        group_index = next((i for i in (1, 2, 3) if match.group(i) is not None), None)
        if group_index is not None:
            value = match.group(group_index) or ""
            group_start = match.start(group_index)
            for index, char in enumerate(value):
                visible.append(char)
                offsets.append(group_start + index + 1)
        elif offsets:
            offsets[-1] = match.end()
        cursor = match.end()
    for index in range(cursor, len(text)):
        visible.append(text[index])
        offsets.append(index + 1)
    return "".join(visible), offsets


def restore_jailbreak(text: str) -> str:
    """还原 @()@ 系破甲标记为正常文字（防拦截用的拆字/包裹去掉，保留原义）。"""
    return restore_jailbreak_with_offsets(text)[0]


# 拒答句式：模型拒答文本泄漏进提示词时的识别规则（生图与视频提示词链共用）。
REFUSAL_RE = re.compile(
    r"\bI\s+(?:can't|cannot|can not|won't|will not)\s+"
    r"(?:help|assist|comply|generate|create|produce|write|transform|provide|fulfill)\b|"
    r"无法(?:协助|帮助|满足)|不能(?:协助|帮助|满足)",
    re.I,
)


def strip_refusal_suffix(raw: str) -> str:
    """保留拒答前已经合规的提示词，只裁掉模型追加的拒答说明。"""
    text = restore_jailbreak(raw or "")
    match = REFUSAL_RE.search(text)
    if not match:
        return text
    line_start = text.rfind("\n", 0, match.start()) + 1
    prefix = text[line_start:match.start()]
    cut = line_start if re.search(
        r"此请求|该请求|抱歉|sorry|I(?:'m| am) Claude Code", prefix, re.I,
    ) else match.start()
    return text[:cut].rstrip(" ,，;；\r\n")


# scene_spec 中需要做破甲还原的文本字段（各模态 spec 共用的清洗面）。
_SPEC_TEXT_FIELDS = (
    "narrative", "appearance", "wardrobe", "locale", "camera", "composition",
    "art_direction", "negative_prompt", "protected_narrative", "draft_prompt",
    "profile_prompt", "first_frame_desc", "last_frame_desc", "prev_tail_desc",
)


def clean_spec_text_fields(spec: dict) -> dict:
    """对 scene_spec 的文本字段统一做破甲还原（视频/图像提示词组装前的共享兜底）。

    无论上游（agent_graph / 前端）是否已还原，组装前统一还原一遍，
    避免 @(x)@ 残留进最终提示词（防拦截第一层，规则见 PROMPT-CLEANING-RULES.md）。
    """
    cleaned = dict(spec or {})
    for key in _SPEC_TEXT_FIELDS:
        val = cleaned.get(key)
        if isinstance(val, str) and val:
            cleaned[key] = restore_jailbreak(val)
    return cleaned
