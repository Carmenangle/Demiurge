"""剧情对白音频化提取（纯逻辑：内嵌 audio 块指令 + 解析 + 情感向量校验）。

设计对齐 image_prompt_extract：0 I/O、0 LLM（LLM 调用在主 Roleplay 同轮完成）。
核心立场：
- 只配音**对话台词**（人物说的话），旁白 / 叙述 / 动作描写一律忽略。
- 情感不是从单句读出：由主 Roleplay 在**理解本轮上下文**后，同轮输出每句台词
  对应的 8 维情感向量（IndexTTS-2.5 契约），所以对味。
- 提取只是结构化分析已生成的正文，不重发预设、不做专用片段。

依赖方向：只 import 标准库 + image_prompt_extract（同为纯逻辑），可独立单测。
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable

from app.services import image_prompt_extract

# IndexTTS-2.5 情感向量 8 维（小写 key，前端注入时映射到 Happy/Angry/... 字段）。
EMOTION_KEYS = ("happy", "angry", "sad", "fear", "hate", "low", "surprise", "neutral")
DEFAULT_EMOTION = {key: 0.0 for key in EMOTION_KEYS}
DEFAULT_EMOTION["neutral"] = 1.0

_AUDIO_RE = re.compile(r"\s*<audio>\s*([\s\S]*?)\s*</audio>\s*", re.I)
_AUDIO_OPEN_TAIL_RE = re.compile(r"\s*<audio>\s*[\s\S]*\Z", re.I)
_THINK_RE = re.compile(r"\s*<think\b[^>]*>[\s\S]*?</think>\s*", re.I)
_MAX_LINES = 64

# 台词引述前缀：正文里「角色名：台词」或「角色名说：台词」的机械降级用。
# 引号可选（「你走开」/ 你走开。 都认）；取到行尾，再剥首尾引号与标点。
_SPEAKER_PREFIX_RE = re.compile(r"^\s*([^\s：:，,、]{1,24})\s*[：:]\s*([^\n]{1,200})")
_QUOTE_EDGE_RE = re.compile(r"^[「『\"“]|[」』\"”。！？!?\.]+$")

_INLINE_AUDIO_INSTRUCTION = (
    "\n\n【剧情对白配音计划】正文仍按剧情自然推进，并把完整可见正文放在 <content>...</content> 中；"
    "think、状态块、表格块、illustration 块、audio 块都不计入正文。"
    "在全部正文与状态块之后追加一个内部块，不得省略。"
    "只提取**人物说出口的对话台词**，逐句列出；旁白、叙述、环境/动作/心理描写一律不提取。"
    "speaker 必须逐字使用本轮在场角色的名字；text 必须逐字摘录该角色说出的台词原文"
    "（去掉『他说』『低声道』等引述词，只保留引号内的原话，保留防拦截标记原样）。"
    "同一角色连续说的多句话，若语气/情绪一致可合并为一行，情绪变化处断开为多行。"
    "emotion 为该句台词在当前上下文中说话者的真实情绪，8 个维度（happy/angry/sad/fear/"
    "hate/low/surprise/neutral）各给 0~1 的小数，代表混合强度；不要 one-hot，"
    "拿不准的维度给 0，中性陈述给 neutral=1。必须依据上下文角色处境与情绪判断，"
    "不要只看字面语气词。只允许以下格式：\n"
    '<audio>{"lines":[{"speaker":"角色名","text":"台词原文",'
    '"emotion":{"happy":0,"angry":0.8,"sad":0.1,"fear":0,"hate":0,"low":0,"surprise":0,"neutral":0.1}}]}</audio>'
)


def build_inline_audio_instruction() -> str:
    """返回主 Roleplay 同次生成使用的对白配音计划契约。"""
    return _INLINE_AUDIO_INSTRUCTION


def _repair_json_controls(payload: str) -> str:
    """复用插画解析的 JSON 控制字符修复（模型在 JSON 内插 think/续写控制文本）。"""
    return image_prompt_extract._repair_json_string_controls(payload)


def normalize_emotion(raw: object) -> dict[str, float]:
    """校验 8 维情感向量：0~1 截断、缺失补 0；全 0 / 无效 → 回退 Neutral=1。

    兜底语义：模型没给情感或解析失败时，用中性向量（对齐用户「兜底默认 Neutral=1」）。
    """
    out: dict[str, float] = dict(DEFAULT_EMOTION)
    if not isinstance(raw, dict):
        return dict(DEFAULT_EMOTION)
    any_valid = False
    for key in EMOTION_KEYS:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            clamped = max(0.0, min(1.0, float(value)))
            out[key] = clamped
            if clamped > 0:
                any_valid = True
    if not any_valid:
        return dict(DEFAULT_EMOTION)
    return out


def _extract_lines_from_raw(raw: object) -> list[dict]:
    """从解析出的 audio JSON 提取并规范台词列表。"""
    lines: list[dict] = []
    if not isinstance(raw, dict):
        return lines
    raw_lines = raw.get("lines")
    if not isinstance(raw_lines, list):
        return lines
    for item in raw_lines[:_MAX_LINES]:
        if not isinstance(item, dict):
            continue
        speaker = image_prompt_extract.restore_jailbreak(str(item.get("speaker") or "")).strip()
        text = image_prompt_extract.restore_jailbreak(str(item.get("text") or "")).strip()
        if not speaker or not text:
            continue
        lines.append({
            "speaker": speaker,
            "text": text,
            "emotion": normalize_emotion(item.get("emotion")),
        })
    return lines


def extract_audio_dialogue(
    reply: str,
    block_filter: Callable[[str], str] | None = None,
) -> tuple[str, dict]:
    """从主生成回复剥离并校验对白配音计划；返回 (去块正文, audio_plan)。

    audio_plan = {"lines": [{"speaker","text","emotion":{...}}]}；无有效台词时 lines 为空。
    """
    source = reply or ""
    matches = list(_AUDIO_RE.finditer(source))
    clean = _AUDIO_RE.sub("", source)
    unterminated = _AUDIO_OPEN_TAIL_RE.search(clean)
    if unterminated:
        clean = clean[:unterminated.start()].rstrip()
        return clean, {}
    clean = clean.rstrip()
    if not matches:
        return clean, {}
    try:
        payload = matches[-1].group(1)
        payload = _THINK_RE.sub(" ", payload)
        if block_filter is not None:
            payload = block_filter(payload)
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError:
            raw = json.loads(_repair_json_controls(payload))
    except (json.JSONDecodeError, TypeError, ValueError):
        return clean, {}
    lines = _extract_lines_from_raw(raw)
    if not lines:
        return clean, {}
    return clean, {"lines": lines}


def build_fallback_dialogue(visible_story: str, card_names: list[str]) -> list[dict]:
    """降级：模型违约漏掉 audio 块时，从正文机械提取「角色名：台词」段落。

    只兜底保住链路（对齐生图缺失计划兜底），不追求完整语义；已知角色名优先。
    """
    known = {name.strip() for name in (card_names or []) if name and name.strip()}
    lines: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for line in (visible_story or "").splitlines():
        match = _SPEAKER_PREFIX_RE.match(line)
        if not match:
            continue
        speaker = match.group(1).strip()
        text = _QUOTE_EDGE_RE.sub("", match.group(2).strip()).strip()
        if known and speaker not in known:
            continue
        key = (speaker, text)
        if key in seen or len(text) < 1:
            continue
        seen.add(key)
        lines.append({"speaker": speaker, "text": text, "emotion": dict(DEFAULT_EMOTION)})
        if len(lines) >= _MAX_LINES:
            break
    return lines
