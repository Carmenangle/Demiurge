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
from dataclasses import dataclass

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


# ===== 首帧复用判断（F1，L0 纯启发式，见 docs/PLAN-VIDEO-FIRSTLAST.md 10.4/10.9）=====

# 时间跳跃词：N+1 首段出现即视为「日期已跳」，首帧画面无法复用（强 regenerate 信号）。
# 只收明确的跨日期词；单纯时段词（黄昏/深夜/傍晚）不在此列，避免误判。
_TIME_JUMP_TERMS = (
    "次日", "翌日", "第二天", "隔日", "次日清晨", "翌日清晨", "第二天一早",
    "后来", "转眼", "不久后", "几日后", "数日后", "数月后", "几年后", "多年后",
    "三天后", "一周后", "过了一段", "又过了",
)

# 场景移动/切换词：N+1 首段出现即视为「地点已变」（跨场景，强 regenerate 信号）。
# 只收「跨场景移动」（来到/走进/离开/前往/回到/镜头转到…），不收「场景内微移动」
# （走到/转身/站起/坐下…），避免把同一房间内的位移误判为换场景。
_SCENE_CHANGE_TERMS = (
    "来到", "走进", "踏入", "迈入", "离开", "走出", "退出", "前往", "回到",
    "返回", "赶到", "抵达", "镜头转到", "场景转到", "画面一转", "另一边", "另一处",
)

# 具体地点锚点词（≥2 字）：两段共享则视为「同场景」（reuse 信号）。
# 不用单字（门/窗/灯）避免宽泛误判。
_LOCALE_ANCHOR_TERMS = (
    "面馆", "客栈", "酒馆", "酒吧", "餐厅", "饭店", "咖啡馆", "咖啡店", "茶馆",
    "教室", "卧室", "客厅", "书房", "厨房", "阳台", "天台", "走廊", "楼梯间",
    "医院", "学校", "商场", "公园", "车站", "机场", "码头", "广场", "街角",
    "房间", "屋子", "大殿", "庭院", "花园", "湖畔", "河边", "山脚", "山顶",
    "门口", "街头", "巷口", "楼顶", "楼下", "楼上",
)


@dataclass
class FrameReuseDecision:
    """首帧复用判断结果（L0 三态，见 docs/PLAN-VIDEO-FIRSTLAST.md 10.9）。"""
    decision: str  # "reuse" | "regenerate" | "ambiguous"
    evidence: str  # 判定依据（供 trace）


def judge_frame_reuse(prev_closing: str, curr_opening: str) -> FrameReuseDecision:
    """首帧复用判断（L0 纯启发式）：判断「N+1 首段」与「N 尾端」是否一张图可涵盖。

    返回三态：
    - regenerate：场景/日期明显变化（curr 含时间跳跃或跨场景移动词）→ 独立生成首帧；
    - reuse：两段共享具体地点词且无切换信号 → 首帧复用 N 尾帧图；
    - ambiguous：无强信号 → 交给 L1（<transition> 搭车结果，见 10.2/10.9）。

    保守原则：只在强信号时给确定结论，宁可 ambiguous 不误判。
    """
    prev = (prev_closing or "").strip()
    curr = (curr_opening or "").strip()
    if not prev or not curr:
        return FrameReuseDecision("ambiguous", "empty_input")

    # 1. 场景切换信号（切换优先于共享地点：curr 既有「离开」又有「面馆」时仍应 regenerate）
    for term in _TIME_JUMP_TERMS:
        if term in curr:
            return FrameReuseDecision("regenerate", f"time_jump:{term}")
    for term in _SCENE_CHANGE_TERMS:
        if term in curr:
            return FrameReuseDecision("regenerate", f"scene_change:{term}")

    # 2. 同场景信号：两段共享具体地点词
    for term in _LOCALE_ANCHOR_TERMS:
        if term in prev and term in curr:
            return FrameReuseDecision("reuse", f"shared_locale:{term}")

    # 3. 均不明显 → 交 L1
    return FrameReuseDecision("ambiguous", "no_strong_signal")


def merge_frame_reuse(
    prev_closing: str,
    curr_opening: str,
    transition: str | None,
) -> FrameReuseDecision:
    """首帧复用决策合并（W2，坑B/坑I）。

    L0 三态是「内部决策态」，<transition> 二态（reuse|regenerate）是「LLM 搭车输出的
    最终复用结论」。合并规则（10.2 用户定调）：
    - L0 确定（reuse / regenerate）→ 用 L0，忽略 <transition>；
    - L0 ambiguous → 消费 <transition>（二态）；
    - L0 ambiguous 且 <transition> 缺失/非法 → ambiguous（L1 兜底失败，交前端坑C 前提裁决）。
    """
    l0 = judge_frame_reuse(prev_closing, curr_opening)
    if l0.decision != "ambiguous":
        return l0
    if transition in ("reuse", "regenerate"):
        return FrameReuseDecision(transition, f"l1_transition:{transition}")
    return FrameReuseDecision("ambiguous", "l0_ambiguous_l1_missing")


__all__ = [
    "StoryFrames", "extract_story_frames", "frames_to_desc",
    "FrameReuseDecision", "judge_frame_reuse", "merge_frame_reuse",
]
