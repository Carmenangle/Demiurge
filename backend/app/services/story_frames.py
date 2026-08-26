"""首尾帧双锚点提取（A1，纯函数）。

给 V1.5 首尾帧视频提供真实的 `first_frame_desc` / `last_frame_desc` 来源：
从一条剧情楼层文本里提取「开头画面」（opening）与「结尾画面」（closing）两个锚点，
与 `scene_illustration` 的「单一高潮段」锚点互补——那里取视觉高潮，这里取**段首 + 段尾**，
让 firstlast 模式的首帧、尾帧分别对应楼层起、合。

设计见 `docs/PLAN-VIDEO-FIRSTLAST.md` P1。纯函数边界（对标 scene_illustration）：
不 import agent_graph / image_gen / video_prompt / LLM / 网络，只复用 `image_prompt_extract`
的可见正文还原（visible_narrative_text / restore_jailbreak），可独立单测。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.services import image_prompt_extract

# 段落切分：空行（含单个换行 + 空白）分隔；单换行的正文行不切（保持叙述连贯）。
_PARAGRAPH_SPLIT = re.compile(r"(?:\r?\n){2,}")

# 判定「纯对白段 / 无画面信息」的依据：引号内容占比过高 → 只说话不动/无构图，不宜当画面锚点。
_DIALOGUE_CHARS = frozenset("“”「」『』\"'『』「」")
_QUOTE_PATTERN = re.compile(r"[“”「」『』\"']")

# 画面段特征词（保守）：出现即认为该段有可还原的视觉画面，优先作为锚点。
_VISUAL_TERMS = (
    "门", "窗", "灯", "光", "影", "桌", "椅", "墙", "街", "巷", "楼", "山", "水",
    "树", "雨", "雪", "风", "火", "月", "星", "夜", "晨", "暮", "屋", "店", "馆",
    "站", "坐", "立", "走", "跑", "转身", "回头", "抬眼", "俯身", "抬头", "低头",
    "抬手", "伸手", "握", "抱", "推", "拉", "站", "坐", "躺", "靠", "望", "看",
    "凝", "笑", "泪", "面", "发", "衣", "裙", "袍", "衫", "袖", "眼", "眉", "唇",
)


@dataclass
class StoryFrames:
    """一条楼层文本提取出的首尾帧双锚点。

    opening：楼层开头画面（首帧）。
    closing：楼层结尾画面（尾帧）。
    evidence：用于说明锚点选取依据（单段 / 多段首尾 / 纯对白降级），供 trace。
    """
    opening: str = ""
    closing: str = ""
    evidence: str = ""


def _visible_paragraphs(text: str) -> list[str]:
    """可见正文 → 段落列表（过滤空段、残留标签段）。"""
    visible = image_prompt_extract.visible_narrative_text(text)
    return [
        part.strip()
        for part in _PARAGRAPH_SPLIT.split(visible)
        if part.strip() and not part.lstrip().startswith("<")
    ]


def _dialogue_ratio(paragraph: str) -> float:
    """段落中引号字符占比，近似衡量「纯对白 / 无画面」。"""
    if not paragraph:
        return 0.0
    quotes = len(_QUOTE_PATTERN.findall(paragraph))
    return quotes / max(1, len(paragraph))


def _has_visual(paragraph: str) -> bool:
    """段落是否含可还原视觉画面的特征词（保守启发式）。"""
    return any(term in paragraph for term in _VISUAL_TERMS)


def _pick_visual(paragraphs: list[str]) -> str:
    """从段落列表选「最有画面感」的一段：优先含视觉特征词的，其次非纯对白。"""
    for paragraph in paragraphs:
        if _has_visual(paragraph):
            return paragraph
    # 全是对白：退回对话占比最低的一段（相对最接近叙述）。
    return min(paragraphs, key=_dialogue_ratio) if paragraphs else ""


def extract_story_frames(text: str) -> StoryFrames:
    """提取楼层首尾帧双锚点。

    规则（纯启发式，0 LLM）：
    1. 可见正文按空行切段；空/无正文 → 空 StoryFrames（上层降级，不猜）。
    2. 多段：opening 取首段画面、closing 取末段画面；若首/末段是纯对白，则就近
       向相邻段借一个有画面的段落（保持「开头」「结尾」的位置语义，不跳到中段）。
    3. 单段：opening == closing（整段既是开头也是结尾，firstlast 退化为静止）。
    4. 长度截断：锚点只保留画面描述，过长截尾（首尾帧描述无需整段复述）。
    """
    paragraphs = _visible_paragraphs(text)
    if not paragraphs:
        return StoryFrames()

    if len(paragraphs) == 1:
        opening = closing = paragraphs[0][:500].strip()
        evidence = "single_paragraph"
        return StoryFrames(opening, closing, evidence)

    # 首段画面：若首段纯对白，向后借一段
    opening = paragraphs[0]
    if not _has_visual(opening) and _dialogue_ratio(opening) > 0.25:
        opening = _pick_visual(paragraphs[1:min(len(paragraphs), 4)]) or opening
    # 末段画面：若末段纯对白，向前借一段
    closing = paragraphs[-1]
    if not _has_visual(closing) and _dialogue_ratio(closing) > 0.25:
        closing = _pick_visual(paragraphs[max(0, len(paragraphs) - 4):-1]) or closing

    return StoryFrames(opening[:500].strip(), closing[:500].strip(), "first_last")


def frames_to_desc(frames: StoryFrames) -> dict[str, str]:
    """把 StoryFrames 转成 firstlast 提示词所需的职责描述（first_frame_desc / last_frame_desc）。

    与 video_prompt.build_video_request 的入参对齐：desc 是「职责描述文字」，不是图地址。
    """
    return {
        "first_frame_desc": frames.opening,
        "last_frame_desc": frames.closing,
    }


__all__ = ["StoryFrames", "extract_story_frames", "frames_to_desc"]
