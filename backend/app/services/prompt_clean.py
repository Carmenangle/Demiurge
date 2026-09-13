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


def sanitize_table_text(value: str) -> str:
    """表格 / 状态文本落库前的破甲还原（2026-09-13 用户实锤）。

    用户报角色状态表「依据」出现 `@为@争@被@插@彻@底@撕@碎@体@面@…` 逐字分隔：
    模型按防拦截预设写正文，evidence / ops 引用正文时把拆字标记一并带出。

    表格文本**不参与网关检测**，落库必须是可读原文 ⇒ 提取后机械过滤一遍。
    与 `restore_jailbreak` 同一套规则（保持一致，避免第二份正则漂移）；
    `<status>` 状态栏快照**相反**——那是前端正则渲染要用的真身，不清洗。
    """
    return restore_jailbreak(value or "")


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


# ── 正文元话语泄漏清洗（2026-09-13 用户实锤）─────────────────────────
# 模型把「输出纪律」清单抄进了正文：首行「质量检查：满足1000-8000字；状态栏更新收养线
# 与目的地；」、末行「【系统提示：以下为推演输出尾部标注，非正文内容】」。
# 只删**整行**元话语；行内正常出现的同名词（正文对话里说「质量检查进行得如何？」）不动。
# 调用方只应对 <content> 正文调用——<think> 里的「质量检查」是预设思维链9 的设计产物
# （不是泄漏），<status> 是前端正则渲染要用的真身。
META_LEAK_LINE_LIMIT = 4       # 一次最多剥这么多行，超过即视为异常（宁留残留不误删）
META_LEAK_KEEP_MIN = 80        # 剥完正文不得短于该长度

_META_KEYWORDS = (
    "质量检查", "系统提示", "系统说明", "输出纪律", "输出结构",
    "字数要求", "字数统计", "状态栏更新", "尾部标注", "元话语",
)
# 行首标签式：`质量检查：…` / `- 系统提示:…` / `## 字数要求：…`
_META_LEAD_RE = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:#{1,6}\s*)?[【\[]?\s*(?:"
    + "|".join(_META_KEYWORDS)
    + r")\s*[】\]]?\s*[：:]"
)
# 整行【…】包裹且内含「系统提示/系统说明」= 模型自造的伪系统声明
_META_BRACKET_RE = re.compile(r"^\s*【[^】]{0,80}】\s*$")
_META_BRACKET_TOKEN_RE = re.compile(r"(?:系统提示|系统说明)")


def is_meta_leak_line(line: str) -> bool:
    """该行是否为被抄进正文的元话语（整行判定）。"""
    stripped = (line or "").strip()
    if not stripped:
        return False
    if _META_LEAD_RE.match(stripped):
        return True
    return bool(_META_BRACKET_RE.match(stripped) and _META_BRACKET_TOKEN_RE.search(stripped))


def strip_meta_leak(body: str) -> tuple[str, list[str]]:
    """剥掉正文里被模型抄进来的「输出纪律」元话语整行，返回（清洗后正文, 被删的行）。

    安全护栏（任一越界即整体放弃、原样返回）：一次最多删 META_LEAK_LINE_LIMIT 行；
    被删字符不得超过正文的 15%（且不超过 120 字）；剥完正文不得短于 META_LEAK_KEEP_MIN。
    宁可留一点残留，也不误删正文。
    """
    if not body:
        return body, []
    kept: list[str] = []
    removed: list[str] = []
    for line in body.split("\n"):
        if is_meta_leak_line(line):
            removed.append(line.strip())
        else:
            kept.append(line)
    if not removed:
        return body, []
    removed_chars = sum(len(x) for x in removed)
    if (len(removed) > META_LEAK_LINE_LIMIT
            or removed_chars > max(120, int(0.15 * len(body)))):
        return body, []
    cleaned = "\n".join(kept)
    if len(cleaned.strip()) < META_LEAK_KEEP_MIN:
        return body, []
    return cleaned, removed
