"""剧情插画纯逻辑：触发判定 + 组装 SceneRequest + renderer 注册表（0 I/O、0 LLM，全单测）。

设计见 ARCHITECTURE.md「剧情能动性引擎」支柱 3 + 「明确不做的」。核心立场：
**触发是状态驱动的规则，不是导演 Agent**——好感度跨档 / user_agency 失控 / 每 N 段兜底 / 用户显式。
本模块只判「该不该出图」和「出图 prompt 是什么」，**不拥有出图管线**：真正生成由 renderer
插件（ComfyUI 复用 workflow_submission，云侧复用 image_gen）承接，本模块只持有注册表（纯 dict）。

依赖方向（importlinter scene-illustration-purity 合同将强制）：本模块吃传入的**标量快照**
（好感度前后值、段落文本、core 外观、state 衣着/场景字段），跨档判定复用 `agency.crossed_tier`，
**不 import character_state / agent_graph / workflow_submission / image_gen**，因此可独立单测。
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from typing import Callable

from app.services import agency, image_prompt_extract

# 目标渲染格式（renderer 注册表的 key）
FMT_COMFY = "comfyui"
FMT_GPT_IMAGE = "gpt-image"

# 触发原因（可观测 trace 用；优先级由高到低）
TRIGGER_EXPLICIT = "用户显式"
TRIGGER_AGENCY_LOST = "主导权失控"
TRIGGER_CROSS_TIER = "好感度跨档"
TRIGGER_CADENCE = "每N段兜底"
TRIGGER_SCENE = "高潮场景"
TRIGGER_ENCOUNTER = "新角色登场"

# 触发出图的场景标签（scene_classify 的标签子集：高潮/情色是天然出图点）
_TRIGGER_SCENES = ("nsfw", "climax")
_ANCHOR_TRAILING_PUNCTUATION = frozenset("。！？!?…」』”’")
_FALLBACK_ANCHOR_TERMS = (
    (8, ("高潮", "绝顶", "射精", "决战", "最终战", "生死关头", "关键转折")),
    # 2026-09-18 走查实锤（nsfw_test.txt）：旧表只认「射精」这类**结果**词，而真实插入动作
    # （捅到了底 / 挺腰 / 推到底）全不在表 ⇒ 锚点落到收尾结果句，画面丢掉插入那一刻。
    # 与结果词同权，靠 fallback_illustration_anchor 的 (score, -index) 排序「同分取更早」
    # 优先落回动作瞬间。
    (8, ("捅到了底", "捅到底", "推到底", "一插到底", "顶到底", "挺腰", "贯穿到")),
    (5, ("剧烈", "颤抖", "痉挛", "抽插", "插入")),
    (6, ("仰卧", "压上肩", "腿架", "面对面", "火车便当", "69式", "骑乘位")),
    (5, ("失去平衡", "跪倒", "跌倒", "倒下")),
    (5, ("放下", "放在", "搁在", "递出", "取出", "留下")),
    (4, ("俯身", "抬手", "抬起", "平视", "对视", "凝视", "注视", "转身", "回眸")),
    (3, ("悬在", "伸手", "抓住", "拔剑", "跃起", "跪下", "拥入")),
    (3, ("喘息", "呻吟", "汗", "拥抱", "亲吻")),
    (3, ("匣子", "信物", "徽章")),
    (2, ("光", "影", "火焰", "雷", "鲜血", "长发", "眼眸", "瞳", "衣摆")),
)
# 2026-09-18 走查实锤（simple_test.txt）：「消失」是本表里唯一被大量用作**修辞**的词
# （关系消失 / 议论声消失 / 希望消失），把纯议论段刷成 +5 ⇒ 锚点跑到开篇（offset=72）。
# 它作为视觉动作的用法已被「淡出 / 隐没」等更具体的词覆盖，故退出该表。
_STATE_CHANGE_ACTION_TERMS = (
    "写下", "书写", "下令", "下达", "对折", "折叠", "化作", "变成",
    "飞出", "穿出", "送出", "发送", "掷出", "射出",
)
_STATIC_RESOLUTION_TERMS = (
    "靠回", "靠着", "静坐", "沉默", "目光", "凝视", "嘴角", "浅笑", "微笑", "余韵",
    "那旗", "旗仍然", "旗还在", "没倒", "没有倒下",
)
_OUTCOME_TERMS = (
    "开口", "说出", "答应", "承认", "拒绝", "交出", "取出", "打开", "开启", "碎裂",
    "停下", "求饶", "妥协", "命令", "回应", "结果", "终于",
)
# 「高潮结果」的体液名词族（`_VISIBLE_BODY_TERMS` 体液子集，**只收名词**）：判据锚点句自带这些词
# ⇔ 体液已经落在角色身上 ⇔ 这一帧本身就是**完成时**（2026-09-24 用户定案：「颜射完不就是完成时」）。
# ⚠ 刻意**不收**「射在／射完」这类动词：它们会命中**台词**（真机语料 `「等他射在你里头的时候，
#   会想我的。」`），把一句只有声音、没有画面的对白误升格成高潮结果帧。
_CLIMAX_RESULT_TERMS = ("精液", "白浊", "淫液", "爱液", "黏液")
# 「画面可见的躯体事实」：体液 + 裸露/身体部位。与 `_ACTION_CONTEXT_TERMS`（动作）、
# `_OUTCOME_TERMS`（结局）**正交** —— 这些是「看得见、但不是动作」的表层事实；
# 画面缺了它，就只剩「谁 + 什么姿态」，剧情状态（体液 / 裸露 / 身体状态）整体丢失。
# 用途＝`_visual_action_context` 的「上下文事实增益」分支（2026-09-19 用户判定
# nsfw_test.txt 单人段「状态丢失」后新增）：锚点句是**定格结果句**（句内无动作/
# 高潮/结果词，且含主体名 ⇒ 分支①②③全不命中）时，若**前文回扫窗口补得上**这类
# 事实则采用窗口；补不上（纯心理 / 议论段）则维持锚点单句 ⇒ 静态文戏零回归。
_VISIBLE_BODY_TERMS = (
    # 体液
    "精液", "淫液", "爱液", "黏液", "白浊", "汗水", "汗珠", "血迹",
    # 裸露 / 身体部位
    "赤裸", "全裸", "半裸", "一丝不挂", "乳房", "乳头", "小穴", "後庭", "后庭",
    "肛门", "肉棒", "阴茎", "性器", "臀部", "屁股", "大腿", "股间",
)
# 「定格句后向补叙收编」分支（2026-09-19，用户判定 L2 画面跑偏）的两个量：紧随段必须是
# **状态补叙**——不是台词（带引号）、不开启新动作/结局、不是场景边界、且是补叙量级的短段。
# 刻意**不用** `_VISIBLE_BODY_TERMS`（躯体词表对 SFW 场景不适用，且范围锁要求它单一使用点）。
_DIALOGUE_MARKS = ("\"", "“", "『", "「")
_FOLLOWUP_MAX_CHARS = 40


def _anchor_score(paragraph: str) -> int:
    visible = image_prompt_extract.restore_jailbreak(paragraph)
    score = sum(
        weight for weight, words in _FALLBACK_ANCHOR_TERMS
        if any(word in visible for word in words)
    )
    score += 5 * sum(word in visible for word in _STATE_CHANGE_ACTION_TERMS)
    if any(word in visible for word in _STATIC_RESOLUTION_TERMS):
        score -= 3
    if any(word in visible for word in ("前两次", "之前", "回想", "曾经")):
        score -= 4
    return score


@dataclass
class Trigger:
    """一次触发判定结果。fire=False 时 reason 为空串。"""
    fire: bool
    reason: str = ""


@dataclass
class SceneRequest:
    """组装好的插画请求，交给 renderer 套格式出图。本模块只造它、不执行它。"""
    prompt: str               # 场景描述（段落动作 + core外观 + state衣着/场景 拼成）
    actors: list[str] = field(default_factory=list)  # 在场角色名
    reason: str = ""          # 触发原因（trace）
    fmt: str = FMT_COMFY      # 目标渲染格式


def decide_trigger(
    *,
    explicit: bool = False,
    agency_lost: bool = False,
    tier_before: float | None = None,
    tier_after: float | None = None,
    thresholds: list[float] | None = None,
    turn: int = 0,
    cadence: int = 0,
    scene: str = "",
    character_encounter: bool = False,
) -> Trigger:
    """判本段该不该配图（纯规则，命中优先级最高的一条即返回）。

    优先级：显式 > 失控 > 高潮场景 > 跨档 > 每N段兜底。
    - explicit：用户本回合显式要图。
    - agency_lost：本回合世界 Agent 提案得手、用户短期失去主导（如被下药）——高潮点。
    - scene：场景分类判为 nsfw/climax（P2 场景分类器产出）——天然出图点。
    - 跨档：好感度从 tier_before 到 tier_after 越过档位边界（复用 `agency.crossed_tier`）。
    - 每N段：cadence>0 且 turn>0 且 turn % cadence == 0 时兜底出一张。
    全不命中 → fire=False。
    """
    if explicit:
        return Trigger(True, TRIGGER_EXPLICIT)
    if character_encounter:
        return Trigger(True, TRIGGER_ENCOUNTER)
    if agency_lost:
        return Trigger(True, TRIGGER_AGENCY_LOST)
    if scene in _TRIGGER_SCENES:
        return Trigger(True, TRIGGER_SCENE)
    if (tier_before is not None and tier_after is not None and thresholds
            and agency.crossed_tier(tier_before, tier_after, thresholds)):
        return Trigger(True, TRIGGER_CROSS_TIER)
    if cadence > 0 and turn > 0 and turn % cadence == 0:
        return Trigger(True, TRIGGER_CADENCE)
    return Trigger(False, "")


def encounter_illustration_context(
    text: str,
) -> tuple[str, str, list[str], dict[str, str]]:
    """提取新角色登场的插入锚点、完整视觉上下文、角色名与结构化事实。"""
    empty: tuple[str, str, list[str], dict[str, str]] = ("", "", [], {})
    source = re.sub(
        r"<think\b[^>]*>[\s\S]*?</think>\s*|<think\b[^>]*>[\s\S]*$",
        "", text or "", flags=re.I,
    )
    content = re.search(r"<content\b[^>]*>(.*?)</content>", source, re.I | re.S)
    body = content.group(1) if content else source
    encounter = re.search(r"<encounter\b[^>]*>(.*?)</encounter>", body, re.I | re.S)
    if not encounter:
        return empty
    facts = {
        match.group(1).strip().lower(): match.group(2).strip()
        for match in re.finditer(
            r"^\s*\[([A-Z_]+)\]\s*([^\r\n]+)", encounter.group(1), re.I | re.M,
        )
        if match.group(2).strip()
    }
    actor = re.split(r"[（(]", facts.get("who", ""), maxsplit=1)[0].strip()
    if not actor:
        return empty

    def visible_paragraphs(value: str) -> list[str]:
        return [
            part.strip()
            for part in re.split(r"(?:\r?\n){2,}", value)
            if part.strip() and not part.lstrip().startswith("<")
        ]

    before = visible_paragraphs(body[:encounter.start()])
    after = visible_paragraphs(body[encounter.end():])
    anchor = after[0] if after else (before[-1] if before else "")
    if not anchor:
        return empty
    fact_text = "；".join(
        f"{key.upper()}：{value}" for key, value in facts.items()
    )
    narrative_parts = [
        part.strip()
        for part in [before[-1] if before else "", fact_text, *after[:3]]
        if part.strip()
    ]
    narrative = "\n\n".join(narrative_parts)[:2500]
    return anchor, narrative, [actor], facts


def build_scene_request(
    *,
    paragraph: str,
    appearance: str = "",
    wardrobe: str = "",
    locale: str = "",
    actors: list[str] | None = None,
    reason: str = "",
    fmt: str = FMT_COMFY,
) -> SceneRequest:
    """从「段落动作 + core外观 + state衣着/场景」组装 SceneRequest（纯字符串拼装）。

    - paragraph：本段叙述里的动作/构图（临时，来自当前输出，不入 state）。
    - appearance：core 外观（视觉锚，来自卡文件，永不自动更）。
    - wardrobe/locale：state 的衣着/所在（随剧情走的动态字段）。
    非空片段按「动作 → 外观 → 衣着 → 场景」顺序用「，」连接；全空则 prompt 为空串。
    """
    parts = [s.strip() for s in (paragraph, appearance, wardrobe, locale) if s and s.strip()]
    return SceneRequest(
        prompt="，".join(parts),
        actors=list(actors or []),
        reason=reason,
        fmt=fmt,
    )


# 画幅判定词表（2026-10-19 重构）。分组按「景别 → 空间 → 姿态 → 人数」的优先级，
# 每组内最具体的先判：面部特写不该被「站立」抢走，全景也不该被「两人」抢走。
_ASPECT_CLOSE = ("面部特写", "脸部特写", "眼睛特写", "紧凑特写", "头像", "大头")
_ASPECT_WIDE = ("全景", "远景", "广角", "大远景")
_ASPECT_FULL = ("全身", "整个人", "换装", "展示身材", "展示服装")
_ASPECT_MEDIUM = ("半身", "上半身", "近景")
_ASPECT_STAND = ("站立", "站在", "跪", "跃起", "向上扬起", "纵向")
_ASPECT_SPACE_WIDE = ("战场", "群山", "天际线", "辽阔", "横跨", "旷野", "海面")
_ASPECT_SPACE_ROOM = ("建筑", "街道", "室内", "大厅", "广场", "走廊", "厅堂")
_ASPECT_POSE_HORIZONTAL = ("躺", "横卧", "长榻", "床上", "横向", "铺开")
_ASPECT_VERTICAL_TOWER = ("高塔", "坠落", "俯冲", "向上延伸", "垂直", "纵深高耸")
_ASPECT_MULTI = ("两人", "多人", "对峙", "隔着", "并肩", "围坐")


def infer_aspect_ratio(text: str, actors: list[str] | None = None) -> str:
    """主计划缺失时按**镜头尺度**选择画幅（2026-10-19 重构，原为关键词平铺短路）。

    为什么改成景别优先：画幅不只裁边，它**强制改变主体占画面的比例**——
    同一句提示词，1:1 出中景/特写，16:9 时模型必须补环境、主体被动变小。
    所以「景别」才是第一判据；人数只是「需要横向并置」的补充信号。

    旧实现的坑（用户实锤：最近 87 次提交里 79 次是 4:3）：
    `actor_count >= 2 or 两人/多人/对峙/…` 排在第二，几乎吃掉所有双人场景——
    但「两人并肩站立」该给 9:16/2:3、「两人对峙于辽阔战场」该给 16:9，
    全被压成中景双人的 4:3。
    """
    source = image_prompt_extract.restore_jailbreak(text or "")
    actor_count = len(list(dict.fromkeys(actors or [])))

    # ① 景别：直接决定主体在画面里的占比
    if any(word in source for word in _ASPECT_CLOSE):
        return "1:1"
    if any(word in source for word in _ASPECT_WIDE):
        return "16:9"
    if any(word in source for word in _ASPECT_FULL):
        return "9:16"
    if any(word in source for word in _ASPECT_MEDIUM):
        return "3:4"
    if any(word in source for word in _ASPECT_STAND):
        return "2:3"
    # ② 空间：宽度用来承载环境信息
    if any(word in source for word in _ASPECT_SPACE_WIDE):
        return "16:9"
    if any(word in source for word in _ASPECT_SPACE_ROOM):
        return "16:9"
    # ③ 姿态：横向铺陈与垂直纵深
    if any(word in source for word in _ASPECT_POSE_HORIZONTAL):
        return "3:2"
    if any(word in source for word in _ASPECT_VERTICAL_TOWER):
        return "9:16"
    # ④ 人数：**仅作补充**——多人才需要横向并置，且必须没有更强景别信号
    if actor_count >= 2 or any(word in source for word in _ASPECT_MULTI):
        return "4:3"
    return "3:4"


def fallback_illustration_anchor(text: str) -> str:
    """插画计划缺失时选出视觉高潮段，返回原文锚点。"""
    source = text or ""
    content = re.search(r"<content\b[^>]*>(.*?)</content>", source, re.I | re.S)
    if content:
        body = content.group(1)
    else:
        body, _ = image_prompt_extract.extract_illustration_plan(source)
        body = re.sub(r"<think\b[^>]*>[\s\S]*?</think>\s*", "", body, flags=re.I)
        body = re.sub(r"<(?:status|状态更新|表格更新)\b[\s\S]*$", "", body, flags=re.I)
    candidates = [part.strip() for part in re.split(r"(?:\r?\n){2,}", body) if part.strip()]
    scored: list[tuple[int, int, str]] = []
    for index, paragraph in enumerate(candidates):
        score = _anchor_score(paragraph)
        if score:
            scored.append((score, -index, paragraph))
    if not scored:
        return candidates[0] if candidates else ""
    # 2026-09-01 用户偏好：最高分段落落在正文末尾 20% 时，若存在分差 <=2 的前置段落，
    # 优先选前置——视觉高潮不该总落在收尾段（用户实锤：图又回到底部）。
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)  # 分高优先、同分取更早(-index)
    best_score = scored[0][0]
    best_index = -scored[0][1]
    chosen = scored[0][2]
    tail_threshold = max(1, int(len(candidates) * 0.8))
    if best_index >= tail_threshold:
        for score, neg_index, paragraph in scored:
            idx = -neg_index
            if idx < best_index and score >= best_score - 2:
                chosen = paragraph
                break
    return chosen


def first_frame_anchor_offset(text: str) -> int:
    """首尾帧模式的首帧锚点：正文第一段末尾。

    首帧画面是楼层开篇时刻，位置应在正文开始的第一段后——「视觉高潮纠偏」
    （resolve_illustration_anchor / illustration_anchor_offset 的末段兜底）是为主图
    设计的，会把开篇静态铺垫改判到高潮段，导致首帧落到中央/末尾
    （2026-08-29 用户验收问题①）。段落边界与 complete_sentence 语义对齐主函数。
    """
    source = text or ""
    content = re.search(r"<content\b[^>]*>(.*?)</content>", source, re.I | re.S)
    if content:
        start, end = content.start(1), content.end(1)
    else:
        tail = re.search(r"(?:\r?\n){2,}\s*<(?:status|状态更新|表格更新)\b", source, re.I)
        start, end = 0, tail.start() if tail else len(source)
    body = source[start:end]
    for match in re.finditer(r"\S(?:.*?\S)?(?=(?:\r?\n){2,}|\s*\Z)", body, re.S):
        if match.group(0).lstrip().startswith("<"):
            continue
        offset = start + match.end()
        while offset < end and source[offset] in _ANCHOR_TRAILING_PUNCTUATION:
            offset += 1
        return offset
    return end


def resolve_illustration_anchor(text: str, requested_anchor: str = "") -> str:
    """纠正模型误选的静态收束或低强度结尾钩子，保证插画仍落在视觉高潮。"""
    requested = image_prompt_extract.restore_jailbreak(requested_anchor or "").strip()
    fallback = image_prompt_extract.restore_jailbreak(
        fallback_illustration_anchor(text),
    ).strip()
    if not requested or not fallback:
        return requested or fallback
    requested_excerpt = illustration_scene_excerpt(text, requested)
    fallback_score = _anchor_score(fallback)
    requested_score = _anchor_score(requested)
    crosses_scene_boundary = _anchors_cross_scene_boundary(text, fallback, requested)
    requested_is_action_outcome = (
        any(word in requested for word in _OUTCOME_TERMS)
        and sum(word in requested_excerpt for word in _ACTION_CONTEXT_TERMS) >= 2
    )
    # 高潮结果帧（2026-09-24 用户定案）：判据挑中的这一句本身在写**体液已经落在角色身上**
    # 时，这一帧就是「完成时」——它既不是「静态收束」也不是「低强度结尾钩子」，而是视觉
    # 高潮本身，仲裁机不得推翻它。
    # 真机（轮 `9cbce6c528cd`）：判据挑中「她仰面躺在枕上，那股白浊顺着人中往下淌…」
    # （`_anchor_score`=**0**），被兜底段「亥时的第二记钟磬…把剑谱**搁在**案角…借窗纸**光**」
    # （=**7**，全靠「搁在」+5／「光」+2 两个陈设词）以 7 ≥ 0+3 推翻 ⇒ 交付稿从用户要的
    # 「那张满是白浊的脸＋还压在她颊边蹭的肉棒」变成「她一个人侧卧、背对镜头、
    # 外半边让得更敞」。用户口径：颜射完即完成时。
    # ⚠ 两道收紧，缺一不可：
    #   ① 兜底段**本身也是**高潮结果帧时不动 —— 两帧同等，保留旧行为（语料实测可少 53 帧翻转）；
    #   ② 仍要求 `requested_excerpt` 命中 ≥2 个动作语境词（与上面那条豁免同形，防孤立体液词）。
    # ⚠ **不动 3 分门槛、不动 `_FALLBACK_ANCHOR_TERMS` 打分表**（红线）。
    requested_is_climax_result = (
        any(word in requested for word in _CLIMAX_RESULT_TERMS)
        and not any(word in fallback for word in _CLIMAX_RESULT_TERMS)
        and sum(word in requested_excerpt for word in _ACTION_CONTEXT_TERMS) >= 2
    )
    if (not crosses_scene_boundary
            and not requested_is_action_outcome and not requested_is_climax_result
            and fallback != requested_excerpt
            and fallback_score >= requested_score + 3):
        return fallback
    visual_action_terms = (*_STATE_CHANGE_ACTION_TERMS, *_POSE_CONTEXT_TERMS, "高潮", "痉挛", "抽插", "插入")
    fallback_has_action = any(word in fallback for word in visual_action_terms)
    requested_has_action = any(word in requested for word in visual_action_terms)
    requested_is_static = any(word in requested for word in _STATIC_RESOLUTION_TERMS)
    # ② 也要给同一条豁免：颜射定格句常带 `_STATIC_RESOLUTION_TERMS` 里的「嘴角」（「白浊挂在
    # 嘴角」），而句内没有任何姿态/动作词 ⇒ 上面那个 `requested_has_action` 兜不住，只能靠本豁免。
    if (not crosses_scene_boundary
            and not requested_is_action_outcome and not requested_is_climax_result
            and fallback_has_action and requested_is_static and not requested_has_action):
        return fallback
    return requested


def _anchors_cross_scene_boundary(text: str, first: str, second: str) -> bool:
    """显式计划位于时间跳转后的新场景时，禁止拿旧时段高潮覆盖。"""
    visible = image_prompt_extract.restore_jailbreak(text or "")
    left = visible.find(first)
    right = visible.find(second)
    if left < 0 or right < 0 or left == right:
        return False
    between = visible[min(left, right):max(left, right)]
    return bool(re.search(
        r"(?:^|\n)\s*(?:---+|——+|\*\s*\*\s*\*)\s*(?:\n|$)", between,
    ))


def illustration_scene_excerpt(
    text: str, requested_anchor: str = "", *, subjects: list[str] | None = None,
) -> str:
    """提取锚点所在高潮段作为 Profile 内容真源，避免重传整篇回复。

    提取路径三级（顺序固定，**不可互换**）：
      ① `needle in paragraph` 逐字命中 ⇒ 该段即锚点段；
      ② **段落相似度兜底**（`_ANCHOR_NEEDLE_MATCH_RATIO`，与判据层回退同源）⇒ 取最相似段；
         没有它，「判据输出被改写 ⇒ 判据层回退成真实段落 ⇒ 取材层又拿改写原文去比」这条
         链路会在第二段就断掉（改写文与真实段落相似度常低于逐字命中阈值）—— 2026-09-18 实锤；
      ③ 词表档锚点 → 末段。`subjects` 只影响①/②选出的**那一段**怎么扩窗，不影响选址。

    `subjects` ＝本帧主体名集合（判据档用判据 `subjects`；词表档用在场角色全集）。
    锚点句**不含其中任何名字** ⇒ 该句自身不可能是画面（台词 / 内心独白 / 拟声），
    向前取到场景边界窗口；`subjects=None / []` ⇒ 该分支不触发（A′ 零回归前提）。
    真源：`docs/illustration-window-handoff-2026-09-18.md` §7.3。
    """
    source = text or ""
    content = re.search(r"<content\b[^>]*>(.*?)</content>", source, re.I | re.S)
    body = content.group(1) if content else re.sub(
        r"(?:\r?\n){2,}\s*<(?:status|状态更新|表格更新)\b[\s\S]*$", "", source,
        flags=re.I,
    )
    visible = image_prompt_extract.restore_jailbreak(body)
    paragraphs = [
        part.strip() for part in re.split(r"(?:\r?\n){2,}", visible)
        if part.strip() and not part.lstrip().startswith("<")
        and not _is_non_narrative_placeholder(part)
    ]
    if not paragraphs:
        return visible.strip()[-2500:]
    needle = image_prompt_extract.restore_jailbreak(requested_anchor or "").strip()
    if needle:
        for index, paragraph in enumerate(paragraphs):
            if needle in paragraph:
                return _visual_action_context(paragraphs, index, subjects=subjects)
        normalized_needle = re.sub(r"\s+", "", needle)
        scored = [
            (
                SequenceMatcher(
                    None,
                    _ANCHOR_MATCH_STRIP_RE.sub("", normalized_needle),
                    _ANCHOR_MATCH_STRIP_RE.sub("", paragraph),
                ).ratio(),
                index,
            )
            for index, paragraph in enumerate(paragraphs)
        ]
        best_score, best_index = max(scored, key=lambda item: (item[0], -item[1]))
        if best_score < _ANCHOR_NEEDLE_MATCH_RATIO:
            # 相似度也够不上 ⇒ 不当「最相似」硬凑（那会把图插到无关段落），
            # 交给词表档兜底：宁可退回词表，也不认一个没证据的段落。
            fallback = image_prompt_extract.restore_jailbreak(
                fallback_illustration_anchor(source),
            ).strip()
            return (fallback or paragraphs[-1])[-2500:]
        return _visual_action_context(
            paragraphs, best_index, subjects=subjects,
        )
    fallback = image_prompt_extract.restore_jailbreak(
        fallback_illustration_anchor(source),
    ).strip()
    return (fallback or paragraphs[-1])[-2500:]


_POSE_CONTEXT_TERMS = (
    "仰卧", "躺", "趴", "跪", "俯身", "弯腰", "转向", "看向", "骑乘", "面对面", "背对",
    "侧卧", "双腿", "大腿", "腿压", "腿架", "弯曲", "蜷着", "分开", "打开",
    "肩膀", "抱离地面", "悬空", "锁链", "镣铐", "手腕", "玉碾", "牢门开",
    "火车便当", "69式",
)

_ACTION_CONTEXT_TERMS = (
    *_POSE_CONTEXT_TERMS,
    "抓", "握", "扣", "掐", "卡住", "压向", "推入", "推进", "进入",
    "插入", "抽插", "交媾", "做爱", "性交", "高潮", "痉挛", "颤抖",
    "取出", "放在", "磨", "摩擦", "撤", "抽回", "收回", "追过去", "往上顶",
)


# ═══════════════════════════════════════════════════════════════════════════════
# 「取最近时点」（2026-09-24 第 56 刀 · 用户定案）
#
# 用户原话：「有时候你看上下文实际会发现**高潮点片段的那个位置有不同的时点**，
#   前一段描述**肉棒在腰部**，下一段就描述**插入嘴巴**了，而我的建议就是
#   **高潮点选在离它最近的一个时点为准，不要两个都出现**，
#   这样会生成**多个器官肢体冲突**」。
#
# 真机形态（零 LLM 复算，`_tmp/probe_tp_conflict.py` → `_tmp/tp_conflict.txt`）：
#   turn `f926775566b0` 窗口 = 段 23..42，锚点段 42（「让它悬在她**唇边**…拇指压住下唇」），
#   而**前驱** 39/40/41 是「贴她**大腿外侧**」→「贴她**小腹、腰侧**」→「从她**小腹**一路往上拖，
#   过了**乳沟**」⇒ 一整条**向上攀爬**的路径，与锚点的「唇边」**不同时点**。
#   ⇒ 同一帧里同时出现「腰部接触」与「口部接触」＝ 用户说的**多个器官肢体冲突**。
#
# 判据（确定性）：把每段映射到**接触点高度带**；锚点段的带一旦确定，
#   **继续向前只能收同带或更高带**（沿路径继续上行），遇到**更低带**立即截断
#   —— 更低带＝更早的时点，应当丢弃。
# ⚠ 保守取向（宁漏勿错）：
#   ① 只对**近身接触帧**（锚点段自身带非空）生效 —— 风景/文戏帧锚点带为空 ⇒ 整条不介入，
#      逐字节等于旧行为；
#   ② 带的判定只认**部位词**（不含单字「面」，避免「布面/两面」误命中）；
#   ③ **同一段**里 F 与 L 并存（口交＋抬腿的定格）⇒ 该段**带保持不清** ⇒ 不截断（保真优先）；
#   ④ 截断只**丢更早的低带段**、从不丢锚点段，也不动 `_subject_scoped_backfill` 的方向。
# ═══════════════════════════════════════════════════════════════════════════════

# 接触点**高度带**（只认部位名词；⚠ 刻意**不含**单字「面」——真机
# 「案角压纸的镇石」「布面」「两面」会误命中，与 `_VIEW_FACE_CONTACT_RE` 同源的教训）。
# ⚠ 「膝/跪/骑/坐」这类**姿势**词不是接触点 ⇒ 不进表（真机段 42「膝跪在被里不压她，
#   让它悬在她**唇边**」里「膝」只是姿势、接触点在唇 ⇒ 必须判成 F，不能判成混合）。
# ⚠ 上半身带**不只「脸」**：胸/乳/肩/肋同属**腰线以上**，必须一并收进
#   （真机踩过：漏了「乳沟/乳尖/胸部」⇒ 攀爬链 40/41 被判成纯下半身、窗口塌成锚点单段）。
#   与 `_VIEW_FACE_CONTACT_RE`（取景收束那张表）**同源同理**。
# ⚠ **高频词陷阱（真机零 LLM 逐段复算，`turn 1339a9a2b586`）——裸单字一律不收**：
#   ·「头」→ 狐头/尽头/抬头/心头/石头                                  （命中 4 段，全是噪声）
#   ·「人中」→「两**人中**间那道被缝」（＝人/中间）                      （命中 1 段，纯误伤）
#   ·「口」→ 门口/出口；「牙」→ 月牙                                    （改收 口鼻/口腔/嘴角/牙龈）
#   ·「腹」→ 一腹；「脚」→ 脚步；「足」→ 满足/不足；「阴」→ 阴影/阴冷/阴天
#   ·「股」→ 一**股**雌香（**量词**！这是最严重的一条）
#   ⇒ 全部改成**双字/限定**写法，宁可漏（宁缺勿猜）。
_TP_FACE_RE = re.compile(
    r"(?:嘴|唇角|嘴角|唇|口鼻|口腔|舌|脸颊|侧脸|脸|鼻梁|鼻尖|鼻|下颌|下巴|"
    r"颈窝|脖子|颈部|颈|锁骨|胸|乳沟|乳尖|乳头|乳房|乳|肩|肋|咽喉|喉咙|喉|"
    r"颧|腮|鬓|牙龈|齿|眼罩)",
)
_TP_LOWER_RE = re.compile(
    r"(?:下体|私处|阴部|阴唇|阴阜|阴道|阴蒂|下阴|阴户|小穴|穴口|后庭|"
    r"臀部|臀|屁股|股间|股沟|大腿|小腿|膝弯|腿|"
    r"脚踝|脚背|脚趾|脚心|脚掌|双足|足尖|"
    r"小腹|腰腹|腹部|腹股沟|腰|髋|胯|裆)",
)
# ⚠ **器官名（肉棒/阳具/龟头/柱身）刻意不进表**：它们是**器官**、不是**高度** ——
#   一个段里出现器官不等于「接触点在腰以下」。真机踩过：把「肉棒」放进下半身带后，
#   整条攀爬链**几乎每段都同时命中上/下带**判成 `?` ⇒ 判据整体失效、
#   `turn 1339a9a2b586` 的窗口该收的 2500 字**一个字都没收**（应该收到 22 段）。
#   器官的**位置**由同段的高度名词（腰/腹/腿/唇/胸）表达，那才是带。
_TP_BAND_FACE = "F"
_TP_BAND_LOWER = "L"
_TP_BAND_UNCLEAR = "?"
_TP_BAND_NONE = "."

# 接触动作信号（第 56 刀）：**只有身体名词不算接触**——真机门禁实锤两例：
#   ·「散乱的华袍残片**垂在腰侧**」＝衣着位置，不是接触点；
#   ·「她侧卧在石板上，双手仍被锁链**拷在腰前**，**双腿弯曲分开**」＝体位，不是接触点。
# 若不加本闸，这两段会被判成「下半身时点」而**误截断**，既有的
# `test_高潮重定向保留被叙述桥段隔开的连续复合动作链` 与
# `test_时间跳转后的末尾揭示锚点不得被前一时段高潮覆盖` 当场翻红。
# ⚠ 刻意**不**复用 `_ACTION_CONTEXT_TERMS`：那张表含「转向/看向/分开/弯曲」等
#   **非接触**姿态词，用它当接触闸等于没闸。
_TP_CONTACT_RE = re.compile(
    r"(?:贴|抵|压|蹭|磨|含|插|送|顶|托|拨|扣|握|抓|搁|悬|凑|挤|舔|吮|吻|"
    r"擦|拖|滑|拢|揽|搂|伸|探|碰到|触|塞|吞|舐|咬|叼|抽送|抽插|推进|推入|进入)",
)


def _timepoint_band(paragraph: str) -> str:
    """单段的接触点**高度带**：`F`（腰线以上：口/面/颈/胸）／`L`（腰以下）／
    `?`（两者并存、带不清）／`.`（**非接触段** ⇒ 桥接，保留且不截断）。

    ⚠ 先过 `_TP_CONTACT_RE` 这道**接触闸**：没有接触动作的段一律判 `.`
    （身体名词可能只是衣着/体位描述）。见上方常量注释的两条真机实锤。
    """
    if not _TP_CONTACT_RE.search(paragraph or ""):
        return _TP_BAND_NONE
    face = bool(_TP_FACE_RE.search(paragraph or ""))
    lower = bool(_TP_LOWER_RE.search(paragraph or ""))
    if face and lower:
        return _TP_BAND_UNCLEAR
    if face:
        return _TP_BAND_FACE
    if lower:
        return _TP_BAND_LOWER
    # 有接触动作但段内没有高度带名词（例：「他俯身贴近她。」）⇒ 高度未知 ⇒ 判桥接。
    return _TP_BAND_NONE


def _timepoint_band_of_anchor(paragraph: str) -> str:
    """锚点段的带：`?` 与 `.` 一律**视为不介入**。

    ⚠ 真机实测（`_tmp/probe_tp_ab.py`）：三帧的锚点段**全部**含姿势词
    （段 42「**膝跪在被里**不压她，让它悬在她唇边」＝ F 接触 ＋ L 姿势）
    ⇒ 若把「同段并存」判成 `?` 并据此放弃，本机制会**整体空转**。
    ⇒ 锚点带改用**接触点优先**：先看 F、再看 L（口/面接触优先于腰下——因为
      「悬在她唇边」这种句子的**动作焦点**在面，膝盖只是跪姿）。
    返回 `F`／`L`／`""`（空＝不介入）。
    """
    if _TP_FACE_RE.search(paragraph or ""):
        return _TP_BAND_FACE
    if _TP_LOWER_RE.search(paragraph or ""):
        return _TP_BAND_LOWER
    return ""


def _tp_has_band(paragraph: str, band: str) -> bool:
    """该段是否含指定高度带（`F`／`L`）。⚠ `?`（同段并存）**同时**含 `F` 与 `L`。

    不能写成 `band in "?"`——字符串包含判定对 `?` 恒为假，会把并存段误判成「不含」
    而触发多余的截断（真机踩过：攀爬链 40/41 被判成更早时点、窗口塌成锚点单段）。
    """
    if band == _TP_BAND_FACE:
        return bool(_TP_FACE_RE.search(paragraph or ""))
    if band == _TP_BAND_LOWER:
        return bool(_TP_LOWER_RE.search(paragraph or ""))
    return False


def _nearest_timepoint_start(paragraphs: list[str], index: int, window_start: int) -> int:
    """把取材窗口起点从 `window_start` 收到**离锚点最近的那个时点**（第 56 刀）。

    **语义：找「本时点段的起点」**——不是「只留同带段」。
    锚点段所在的那一小段连续动作（同一个接触高度带）从哪开始，窗口就从哪开始；
    再往前的段属于**上一个时点**（另一处接触点）⇒ 丢弃。

    锚点带为空（无接触证据）⇒ **原样返回** `window_start`（不介入，零回归）。
    否则从锚点段向前逐段看：
      · 段带**空**（`.`）⇒ 桥接/非接触段 ⇒ 收进，继续向上；
      · 段带**不含锚点带**（锚点 F 而该段只有 L）⇒ **上一个时点** ⇒ **在它的后一段截断**；
      · 段带**含锚点带** ⇒ 同一时点 ⇒ 收进，继续向上。

    ⚠ 与「只留同带」的区别（真机 `turn f926775566b0`）：攀爬路径的段是
      `L`（大腿）→ `FL`（小腹/胸）→ `F`（乳沟）→ `FL`（唇边，锚点）——
      它们**逐段递增**、本就是**同一条动作链**，全部保留才对；
      真正该丢的是更早那条**不同**的接触（例：先「贴大腿外侧」一长段、中间隔一段，
      再跳到唇边）。本实现只做「跨带下降」那一次截断，因此攀爬链被完整保留。
    返回新的窗口起点（不截断则逐字返回 `window_start`）。
    """
    anchor_band = _timepoint_band_of_anchor(paragraphs[index]) if 0 <= index < len(paragraphs) else ""
    if not anchor_band:
        return window_start
    start = window_start
    for previous_index in range(index - 1, window_start - 1, -1):
        band = _timepoint_band(paragraphs[previous_index])
        if band == _TP_BAND_NONE:
            # 无接触部位词 ⇒ **桥接段**（环境/反应/姿势）⇒ 保留，且**不**中断上溯。
            # ⚠ 必须同步推进 `start`：只 `continue` 不改 `start` 会把尾部桥接段丢掉
            #   （真机门禁实锤：`test_高潮重定向保留体位动作链…` 里首段「她仰卧在冰冷石板上。」
            #   是**体位**事实、不是更早接触时点，却被误判成窗口外）。
            start = previous_index
            continue
        if _tp_has_band(paragraphs[previous_index], anchor_band):
            # 同一时点（含 `?` 并存段）⇒ 收进窗口，继续向上。
            start = previous_index
            continue
        # 该段不含锚点带（＝更早的时点）⇒ 从它的**后一段**起算，并停止上溯。
        start = previous_index + 1
        break
    return start

def _is_action_context_boundary(paragraph: str) -> bool:
    if re.fullmatch(r"\s*(?:---+|\*\s*\*\s*\*)\s*", paragraph):
        return True
    if len(paragraph) <= 60 and re.match(
        r"^\s*[^，。！？!?]{1,24}(?:转身)?(?:离开|离场|退场|走出|退出)", paragraph,
    ):
        return True
    return bool(re.match(
        r"^\s*(?:与此同时|另一边|另一处|镜头转到|场景转到|"
        r"(?:随后|然后|最终|这时)?\s*(?:他|她|他们|她们|众人|两人)"
        r"\s*(?:转身)?(?:离开|离场|退场|走出|退出))",
        paragraph,
    ))


_NON_NARRATIVE_PLACEHOLDER_RE = re.compile(
    r"\s*(?:"
    r"(?:图片|图像|插画|插图|配图|封面|头像|视频|音频|文件|附件)?"
    r"(?:加载|生成|处理|渲染|上传|下载)(?:中|失败|完成|成功)"
    r"|loading"
    r")[.。…·\s!！?？~～]*",
    re.I,
)


def _is_non_narrative_placeholder(paragraph: str) -> bool:
    """UI 占位串（「图片加载中...」等）不是叙事，必须挡在取材窗口之外。

    2026-09-18 真机实锤（`backend/_probe_anchor_chain_samples.json`）：L3 有一轮判据
    **直接把它选成锚点**（段 222），另一轮扩窗把它带进窗口末尾。设计稿 §6.5 早已把
    「非叙事占位串无机械闸」记为遗留项，此处收口。**整段**是占位串才算，叙事里正常
    出现「图片加载」不受影响。
    """
    return bool(_NON_NARRATIVE_PLACEHOLDER_RE.fullmatch(paragraph or ""))


def _scene_boundary_start(paragraphs: list[str], index: int) -> int:
    """从 `index` 向前回退到**场景边界之后**的第一段（沿用生产 40 段上限）。

    原属分支 ①/② 的扩窗逻辑，逐字搬移为公用（纯搬移、行为不变）——A′ 的锚点档扩窗
    与高潮档走同一把尺，两处口径必须同源。
    """
    start = max(0, index - 40)
    for previous_index in range(index - 1, start - 1, -1):
        if _is_action_context_boundary(paragraphs[previous_index]):
            start = previous_index + 1
            break
    return start


def _action_context_scan_start(paragraphs: list[str], index: int) -> int:
    """从 `index` 向前回扫**动作链**起点（沿用生产口径：≤13 段、最多跨 2 段中性间隔）。

    原为 `_visual_action_context` 末条分支的内联循环；「上下文事实增益」分支要用同一把尺
    （两处口径必须同源），故逐字搬移为公用函数——**纯搬移，行为不变**。
    与 `_scene_boundary_start`（回退到场景边界）是两把不同的尺，按分支取用。
    """
    start = index
    neutral_gap = 0
    for previous_index in range(index - 1, max(-1, index - 13), -1):
        previous = paragraphs[previous_index]
        if _is_action_context_boundary(previous):
            break
        if any(term in previous for term in _ACTION_CONTEXT_TERMS):
            start = previous_index
            neutral_gap = 0
            continue
        neutral_gap += 1
        if neutral_gap > 2:
            break
        start = previous_index
    return start


# 定格句窗口的**事实下限**（2026-09-20 第 15 刀）。真机实锤：L1 窗口 28 字（`屁股翘得更高了。`
# ／`“哈啊……♡”`／`清夏偷瞄了我一眼。`）、L2 窗口 20 字，两者**都没有任何环境事实**
# ⇒ 三档只能整段编造场景（`bed`／`bookshelf`／`bedside lamp`／`candlelight`，原文全无）。
# 【事实边界】在「邻域本来就没有画面事实」时无从约束，故必须在**取材层**补足。
_MIN_WINDOW_CHARS = 120
_BACKFILL_MAX_SCAN = 8


def _subject_scoped_backfill(
    paragraphs: list[str], window_start: int, index: int, subjects: list[str] | None,
    anchor_text: str = "",
) -> int:
    """窗口低于事实下限时**向前补足同主体事实**，返回新的窗口起点（不扩则原样返回）。

    只收**点名了「锚点句里已出现的本帧主体」**的段落——这是「同主体」的**保守充分条件**。
    ⚠ 不能放宽成「点名了 `subjects` 里任何名字」：**词表档的 `subjects` 是「在场全集」**，
    那样等于「谁在场都算同主体」，会把旁观者段一并收进来（门禁实锤：词表档测试里
    「明澜把双手贴到墙上」被误收）。锚点句里出现过的主体才是这一帧的主语候选。
    遇到不含本帧主体名的段落立即停 ⇒ 不会卷入他人：L2 前邻域紧贴**罗瑟琳**心理段
    （「这个念头让罗瑟琳感到无比厌恶…」），实测第 1 段即停 ⇒ 窗口逐字维持旧值（零回归）。
    纯台词段（引号）跳过但不中止：它画不出来、又最可能带他人名，却是同一动作链里的常见间隔
    （L1 的 `“哈啊……♡”` 就夹在躯体事实与锚点之间）。
    """
    if not subjects:
        return window_start
    scoped = [name for name in subjects if name and name in (anchor_text or "")]
    if not scoped:
        return window_start
    start = window_start
    for offset in range(1, _BACKFILL_MAX_SCAN + 1):
        candidate = window_start - offset
        if candidate < 0:
            break
        paragraph = paragraphs[candidate]
        if any(mark in paragraph for mark in _DIALOGUE_MARKS):
            continue
        if not any(name in paragraph for name in scoped):
            break
        start = candidate
        if len("\n\n".join(paragraphs[start:index + 1])) >= _MIN_WINDOW_CHARS:
            break
    return start


def _visual_action_context(
    paragraphs: list[str], index: int, *, subjects: list[str] | None = None,
) -> str:
    """保留高潮前连续动作链；允许两段环境/反应桥接，禁止稀疏抽样丢动作。

    `subjects`（本帧主体名集合）只作用于 A′ 扩窗分支：锚点句**不含其中任何名字** ⇒ 该句自身
    不可能是画面（台词 / 内心独白 / 拟声）⇒ 向前取到场景边界窗口。`subjects=None/[]`
    时逐字维持旧行为。真源：`docs/illustration-window-handoff-2026-09-18.md` §7.3。

    「上下文事实增益」分支（2026-09-19 新增，用户判定 nsfw_test.txt 单人段「状态丢失」）：
    锚点句是**定格结果句**时（句内无动作/高潮/结果词、且**含**主体名 ⇒ 前三条分支全不命中），
    窗口原会退化成锚点单句，体液/身体状态整体丢失——实锤：「浑身无力的清夏靠在公告栏上。」
    ⇒ 窗口 14 字，而「我把肉棒抽了出来」「精液吧嗒吧嗒地滴落」「從小穴里吐出精液的样子」
    都在锚点前 1–4 段，全在窗外。此分支向前回扫（与末条分支同尺 `_action_context_scan_start`），
    **仅当窗口相对锚点句新增 `_VISIBLE_BODY_TERMS`** 时采用窗口；纯心理 / 议论段
    （回扫窗口只有「或许我一直如此孤独」这类）不带新增事实 ⇒ 维持单句，静态文戏零回归。

    「定格句后向补叙收编」分支（2026-09-19 新增，用户判定 L2 画面跑偏）：事实增益只认
    躯体事实 ⇒ SFW 定格句（「塞蕾娜正微笑着。」）**前文**没有躯体词、**后文**才有状态
    补叙时，窗口仍退化成单句（真机：锚点 8 字 + 紧随「一副心满意足的模样。」才是本主体
    的画面事实）。而此类句子的前邻域常是**另一主体**的心理/动作段（罗瑟琳段紧贴锚点，
    零 LLM 普查实锤），任何向前回扫的连续窗口都必然卷入他人 ⇒ 方向转向**后向**：
    紧随段是同主体状态    补叙（无动作/结局词、非台词、非场景边界、≤`_FOLLOWUP_MAX_CHARS`
    字）才收编，**只收紧邻一段**（最小可预期；多段收集等真机证据再议）。`subjects` 为空
    （词表档）时不触发，逐字维持旧行为。**只服务低信息量定格句**：锚点段已自带
    `_VISIBLE_BODY_TERMS` 躯体事实时画面信息量足够 ⇒ 不收（门禁实锤：否则 NSFW 动作帧
    会把无关环境静物「桌上的茶杯还冒着热气。」误收进窗口）。已知上界：无法判定紧随段
    主语是否属于其他在场者（在场全集不进本函数）——中文叙事惯例下定格句的紧随短段多为
    同主体状态补叙，台词段（最可能带他人名）已被引号条件排除。
    """
    # ⚠ 分支逻辑（含「锚点句自身含动作词/高潮词/结果词/主体名」那四条判据）
    #   全部搬进 `_visual_action_window_range`，本函数只做「算区间 → 取最近时点 → 落文本」。
    start, end, cap = _visual_action_window_range(paragraphs, index, subjects=subjects)
    # ⚠ 2026-09-24 第 56 刀「取最近时点」＝**单一收口**：所有分支算完窗口后，
    #   在这里统一按「离锚点最近的那个时点」收一次。压在收口处而不是逐分支接线，
    #   是因为逐分支接线漏过 6 条返回路径中的 4 条（真机门禁实锤：既有的
    #   「高潮重定向保留体位动作链」「时间跳转后的末尾揭示锚点」三条用例当场翻红）。
    start = _nearest_timepoint_start(paragraphs, index, start)
    return "\n\n".join(paragraphs[start:end + 1])[-cap:]


def _visual_action_window_range(
    paragraphs: list[str], index: int, *, subjects: list[str] | None = None,
) -> tuple[int, int, int]:
    """算出取材窗口的**段区间** `(start, end, cap)`——`cap` 是字符截断上限。

    本函数是 `_visual_action_context` 的**纯区间**形态：把原先分散在 6 条 `return` 上的
    `"\\n\\n".join(paragraphs[a:b])[-N:]` 统一成「返回下标 + 上限」，
    好让「取最近时点」（第 56 刀）等**收口型**收束能在**唯一一处**施加。
    ⇒ 语义与分支优先级**逐字未改**（纯搬移）。
    """
    selected = paragraphs[index]
    # 2026-09-18 走查实锤（nsfw_test.txt）：该结果句常被单独成段（实测 26 字），而扩展条件
    # 原不含「射精」⇒ 片段退化成单段，提示词编译拿不到任何动作链，无从贴合剧情。
    # 「射精」与「高潮」同属动作链末端结果，一并向前取回。
    if any(term in selected for term in ("高潮", "绝顶", "痉挛", "颤抖", "临界", "射精")):
        # 高潮结果通常位于动作链末端；向前取到真实场景边界，而不是只保留最后两段反应。
        return (_scene_boundary_start(paragraphs, index), index, 5000)
    if not any(term in selected for term in _ACTION_CONTEXT_TERMS):
        if any(term in selected for term in _OUTCOME_TERMS):
            # 结果句是前方动作链的结局；从当前场景边界连续取到结果句，
            # 保留中间反应与关键物件，不做会丢事实的稀疏抽样。
            return (_scene_boundary_start(paragraphs, index), index, 5000)
        if subjects and not any(name in selected for name in subjects):
            # A′ 取材（2026-09-18 用户拍板走 A′、否决 B）：锚点句不含本帧任何主体名
            # ⇒ 它不可能是画面，改成向前取到场景边界窗口。
            # ⚠ 已知边界（不是缺陷，是刻意的上界）：锚点句**含部分**主体名时不扩窗
            #   （如「她只掰开了一边」只提到两名主体之一）。按 §8.3，「多人同框全都在
            #   窗口内」的上界是**选点**，选点归判据 LLM；本机制只向前取材，故不额外扩窗。
            return (_scene_boundary_start(paragraphs, index), index, 5000)
        window_start = _action_context_scan_start(paragraphs, index)
        if window_start < index:
            window = "\n\n".join(paragraphs[window_start:index + 1])
            if any(
                term in window and term not in selected
                for term in _VISIBLE_BODY_TERMS
            ):
                start = _subject_scoped_backfill(paragraphs, window_start, index, subjects, selected)
                return (start, index, 2500)
        # 「定格句后向补叙收编」：事实增益（只认躯体事实）未采用时，向后看一段——
        # 紧随段是同主体状态补叙才收编。到达此处时 subjects 非空 ⇒ 锚点必含主体名
        # （不含主体名的已被 A′ 分支接走），故 subjects 为空（词表档）必不触发。
        # 前置「锚点段不含躯体事实」＝本分支只服务**低信息量定格句**（「塞蕾娜正微笑着。」）；
        # 锚点段已自带小穴/后庭等可见事实时画面信息量足够，后向短段更可能是无关环境静物
        # （全量门禁实锤：「桌上的茶杯还冒着热气。」被误收进 NSFW 动作帧）⇒ 不收。
        if subjects and not any(
            t in selected for t in _VISIBLE_BODY_TERMS
        ):
            follow = paragraphs[index + 1] if index + 1 < len(paragraphs) else ""
            if (follow and not _is_action_context_boundary(follow)
                    and not any(term in follow for term in _ACTION_CONTEXT_TERMS)
                    and not any(term in follow for term in _OUTCOME_TERMS)
                    and not any(mark in follow for mark in _DIALOGUE_MARKS)
                    and len(follow) <= _FOLLOWUP_MAX_CHARS):
                start = _subject_scoped_backfill(paragraphs, index, index, subjects, selected)
                return (start, index + 1, 2500)
        start = _subject_scoped_backfill(paragraphs, index, index, subjects, selected)
        return (start, index, 2500)
    # 末条分支：动作链回扫。
    return (_action_context_scan_start(paragraphs, index), index, 2500)


def protected_illustration_scene_excerpt(text: str, visible_excerpt: str) -> str:
    """找回与可见高潮段对应的原始防拦截正文，供独立 Profile 沿用当前预设。"""
    body = image_prompt_extract.protected_narrative_text(text)
    paragraphs = [
        part.strip() for part in re.split(r"(?:\r?\n){2,}", body)
        if part.strip() and not part.lstrip().startswith("<")
        and not _is_non_narrative_placeholder(part)
    ]
    if not paragraphs:
        return body[-2500:]
    visible_parts = [
        part.strip() for part in re.split(r"(?:\r?\n){2,}", visible_excerpt or "")
        if part.strip()
    ]
    matched_indices: list[int] = []
    search_from = 0
    for visible_part in visible_parts:
        target_part = re.sub(r"\s+", "", visible_part)
        for index in range(search_from, len(paragraphs)):
            restored = re.sub(
                r"\s+", "", image_prompt_extract.restore_jailbreak(paragraphs[index]),
            )
            if target_part == restored or target_part in restored or restored in target_part:
                matched_indices.append(index)
                search_from = index + 1
                break
    if matched_indices:
        return "\n\n".join(paragraphs[matched_indices[0]:matched_indices[-1] + 1])[-2500:]
    target = re.sub(r"\s+", "", visible_excerpt or "")
    for paragraph in paragraphs:
        if target and target in re.sub(
            r"\s+", "", image_prompt_extract.restore_jailbreak(paragraph),
        ):
            return paragraph[-2500:]
    best = max(
        paragraphs,
        key=lambda paragraph: SequenceMatcher(
            None, target,
            re.sub(r"\s+", "", image_prompt_extract.restore_jailbreak(paragraph)),
        ).ratio(),
    )
    return best[-2500:]


def snap_offset_to_paragraph_end(text: str, offset: int | None) -> int | None:
    """把「锚点句尾」偏移吸附到**所在段落的末尾**（第 58 刀，2026-09-24 用户口径）。

    用户原话：「首先,插入到段落下方,不是段落中间」—— 锚点句常在段落中间，按句尾插入
    会把一张图**切进段落中间**。吸附到段落边界（2+ 换行）后，图落在整段之下。
    `offset=None`（锚点定位失败）原样返回 None；越界钳到文本长度。
    """
    if offset is None:
        return None
    source = text or ""
    if offset < 0:
        return 0
    if offset >= len(source):
        return len(source)
    m = re.search(r"(?:\r?\n){2,}", source[offset:])
    return offset + m.start() if m else len(source)


def illustration_anchor_offset(text: str, requested_anchor: str = "") -> int | None:
    """优先取主生成指定高潮段；指定锚点无效时失败关闭，避免图片落到末尾。"""
    source = text or ""
    content = re.search(r"<content\b[^>]*>(.*?)</content>", source, re.I | re.S)
    if content:
        start, end = content.start(1), content.end(1)
    else:
        tail = re.search(r"(?:\r?\n){2,}\s*<(?:status|状态更新|表格更新)\b", source, re.I)
        start, end = 0, tail.start() if tail else len(source)
    needle = (requested_anchor or "").strip()

    def complete_sentence(offset: int) -> int:
        while offset < end and source[offset] in _ANCHOR_TRAILING_PUNCTUATION:
            offset += 1
        return offset

    if needle:
        found = source.rfind(needle, start, end)
        if found >= start:
            return complete_sentence(found + len(needle))
        visible, offsets = image_prompt_extract.restore_jailbreak_with_offsets(source[start:end])
        normalized_needle = image_prompt_extract.restore_jailbreak(needle)
        normalized_found = visible.rfind(normalized_needle)
        normalized_end = normalized_found + len(normalized_needle)
        if normalized_found >= 0 and normalized_end > 0:
            return complete_sentence(start + offsets[normalized_end - 1])
        def normalized(value: str) -> str:
            return re.sub(r"[\s，。！？!?、；;：:…'\"“”‘’（）()]", "", value)

        normalized_needle = normalized(normalized_needle)
        best: tuple[float, re.Match[str]] | None = None
        for paragraph in re.finditer(r"\S(?:.*?\S)?(?=(?:\r?\n){2,}|\s*\Z)", visible, re.S):
            if paragraph.group(0).lstrip().startswith("<"):
                continue
            paragraph_text = normalized(paragraph.group(0))
            if not normalized_needle or not paragraph_text:
                continue
            match = SequenceMatcher(None, normalized_needle, paragraph_text).find_longest_match()
            score = match.size / len(normalized_needle)
            if score >= 0.6 and (best is None or score > best[0]):
                best = (score, paragraph)
        if best is not None:
            paragraph_end = best[1].end()
            return complete_sentence(start + offsets[paragraph_end - 1])
        # 锚点模糊匹配也失败 → 视觉高潮评分选段，选不中才返回 None
        fallback = fallback_illustration_anchor(source)
        if fallback:
            found = source.rfind(fallback, start, end)
            if found >= start:
                return complete_sentence(found + len(fallback))
        return None
    body = source[start:end]
    matches = list(re.finditer(r"\S(?:.*?\S)?(?=(?:\r?\n){2,}|\s*\Z)", body, re.S))
    candidates = [match for match in matches if not match.group(0).lstrip().startswith("<")]
    # 2026-09-01 用户实锤：无锚点直接取末段 = 图插到底部。改评分选段，
    # 评分选不中才退回首段（而非末尾收束段）。
    fallback = fallback_illustration_anchor(source)
    if fallback:
        found = source.rfind(fallback, start, end)
        if found >= start:
            return complete_sentence(found + len(fallback))
    chosen = candidates[0] if candidates else (matches[0] if matches else None)
    if chosen is None:
        return end
    anchor = start + chosen.end()
    while anchor > start and source[anchor - 1].isspace():
        anchor -= 1
    return anchor


# ── 判据档：高潮点定位判据（用户态 prompt）的**输出解析与 evidence 校验** ──────────
# 分工（2026-09-18）：判据正文属主是 illustration_prompt_store（用户可改，只开放
# 「想让模型怎么想」）；**「程序怎么读」在本节**——字段名、宽容解析、逐字落文校验，
# 程序侧固定拼接、用户改不到（一改就是全链静默失败）。

# 单条锚点的字段。⚠ 2026-09-24 **第 57 刀**新增 `score`（多批生图的排序分）。
# `ANCHOR_INTENT_FIELDS` 保持为**单条**字段集：老代码与测试按「逐字段出现在合同正文里」
# 校验它（`test_判据合同…`）⇒ 加字段必须同时把该字段写进合同文本，否则那条锁会红。
ANCHOR_INTENT_FIELDS = ("anchor", "composition", "subjects", "reason", "score")
# 顶层键：判据一次输出**一整个列表**（多批生图的地基，第 57 刀）。
ANCHOR_INTENTS_KEY = "anchors"

# 只读输出合同：由 anchor_intent_system() 拼在用户判据之后，前端只读展示。
# 字段名从 ANCHOR_INTENT_FIELDS 生成，防两处漂移。
#
# ⚠ 2026-09-24 **第 57 刀**：合同由**单锚点**改为**锚点数组**（多批生图的前置）。
#   两条零回归锁：
#     ① 纯声明、不含实现细节；`viewpoint` 等既有条款一律不动；
#     ② **解析层同时兼容旧单条形态**（见 `parse_anchor_intents`）⇒ 用户手上那份旧判据
#        即使仍按单锚点输出，也逐字节维持旧行为（A/B 只改「一次问几个」）。
#   成本：合同变长约 120 字，但判据只调一次（**不增加调用次数** —— 一次问清全部锚点）。
ANCHOR_INTENT_CONTRACT = (
    "字段固定为 " + "、".join(ANCHOR_INTENT_FIELDS) + "，只输出严格 JSON，"
    "不要 markdown 代码块、不要任何解释文字，字符串一律用英文半角双引号：\n"
    '{"anchors": [{"anchor": "...", "composition": "...", "subjects": ["..."], '
    '"reason": "...", "score": 0}]}\n'
    "· anchors：本段剧情里**所有**值得配图的时刻，按它们在正文里出现的**先后**排列"
    "（**不要**按分数排序）；一个可画的时刻都没有就输出空数组 [];\n"
    "· anchor：原文中逐字出现的一句话（含标点、不含换行），必须能在正文里原样找到——"
    "归一化比对时忽略空白与各类引号，但**不做模糊匹配**，找不到即**该条**作废"
    "（其余条目照常采用）；\n"
    "· composition：特写 / 中景 / 全身 / 双人同框 / 多人同框 / 远景；\n"
    "· subjects：锚点那一刻画面上应出现的主要角色名；\n"
    "· reason：一句话说明为什么选这里（不超过 40 字）；\n"
    "· score：0–100 的整数，这一帧对读者的价值有多大（越大越该出图）。"
    "同一段剧情里各锚点的分数必须**彼此可比**——不要都给同一个数，否则无法排序。\n"
    "（以上字段名与 anchor 校验规则是程序解析合同，不由判据决定；判定不合规时本次锚点"
    "回退中文词表，不会中断出图。）"
)

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>[\s\S]*?</think>", re.I)
_ANCHOR_INTENT_JSON_RE = re.compile(r"\{[\s\S]*\}")
# 第 57 刀：判据可能直接吐**裸数组**。⚠ 不能用 `\{[\s\S]*\}` 加 `\[[\s\S]*\]` 各试一遍 ——
# 贪婪的 `\{…\}` 会把裸数组里**第一个对象**当成整块（真机测试实锤：`[{"a":1},{"b":2}]`
# 只解析出 1 条）。⇒ 取**起点更靠前**的那个容器，才能正确切出外层。
_ANCHOR_INTENT_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")
_ANCHOR_INTENT_FULLWIDTH_QUOTES = str.maketrans({"\u201c": '"', "\u201d": '"'})
# evidence 归一化比对：忽略空白与中英标点/引号。刻意是 `illustration_anchor_offset`
# 那套的**超集**（多收半角 `, .`）——模型逐字引用时经常把全角顿号/句号换成半角，
# 而定位插入点与判定 evidence 的容错诉求相反：前者宁可模糊也要给出位置，后者必须
# 逐字认账，所以这里放宽容错面、但绝不放宽到模糊匹配。
_ANCHOR_MATCH_STRIP_RE = re.compile(r"[\s，。,.！？!?、；;：:…'\"“”‘’（）()]")

# 锚点「够不够像」的统一阈值：判据层回退（`_anchor_intent_recover`）与取材层选址
# （`illustration_scene_excerpt` 的相似度兜底）**共用同一个数**——两处口径必须同源，
# 否则会出现「判据层说找到了、取材层换一段」的分叉。定在 0.6 是实测：真实造词锚点
# 与目标真实段落 ratio≈0.727（L3 留样），而无关段落 ≤0.26（同上），阈值落中间有余量。
# 与 `illustration_anchor_offset` 的段落兜底同值（那里用的是 longest-match 口径，更严）。
_ANCHOR_NEEDLE_MATCH_RATIO = 0.6


def anchor_intent_system(rubric: str) -> str:
    """拼一次判据调用的 system：用户判据 + 只读输出合同（顺序不可颠倒）。"""
    judgment = (rubric or "").strip()
    if not judgment:
        return ""
    return judgment + "\n\n【输出格式（不可更改）】\n" + ANCHOR_INTENT_CONTRACT


def _anchor_intent_field(payload: str, key: str) -> str:
    """从（可能被全角引号破坏的）JSON 文本里逐字段兜底抓一个字符串字段。"""
    match = re.search(
        rf'["“”]?{key}["“”]?\s*[:：]\s*["“](?P<value>[^"“”]*?)["”]', payload,
    )
    return match.group("value").strip() if match else ""


def _anchor_intent_subjects(payload: str) -> list[str]:
    match = re.search(r'["“”]?subjects["“”]?\s*[:：]\s*\[(?P<value>[^\]]*)\]', payload)
    if not match:
        return []
    return [
        item.strip()
        for item in re.findall(r'["“]([^"“”]+)["”]', match.group("value"))
        if item.strip()
    ]


def _normalize_anchor_intent(value: dict) -> dict:
    subjects = value.get("subjects")
    return {
        "anchor": str(value.get("anchor") or "").strip(),
        "composition": str(value.get("composition") or "").strip(),
        "reason": str(value.get("reason") or "").strip(),
        "subjects": (
            [str(item).strip() for item in subjects if str(item).strip()]
            if isinstance(subjects, list) else []
        ),
        # 第 57 刀（多批生图）：排序分。⚠ **缺失/非法一律记 0，不判死**——
        # 用户手改的旧判据（按单锚点输出、没有 score）必须继续可用（零回归锁）。
        "score": _anchor_intent_score(value.get("score")),
    }


def _anchor_intent_score(value: object) -> int:
    """把判据给的 `score` 收敛成整数；缺失/非数 ⇒ 0（**不判死**，旧判据零回归）。"""
    if value is None or isinstance(value, bool):
        # ⚠ `bool` 必须先挡：它是 `int` 的子类，`True` 否则会被读成 1。
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            return int(float(text))
        except ValueError:
            return 0
    return 0


def _anchor_intent_json_block(text: str) -> str:
    """切出判据输出里的**最外层** JSON 容器（对象或数组），找不到 ⇒ 原样返回。

    ⚠ 取**起点更靠前**的那个容器，不是「先试对象、失败再试数组」：
    贪婪 `\\{…\\}` 会把裸数组里第一个对象当成整块（真机测试实锤只解析出 1 条）。
    """
    array_match = _ANCHOR_INTENT_JSON_ARRAY_RE.search(text)
    object_match = _ANCHOR_INTENT_JSON_RE.search(text)
    if array_match and (not object_match or array_match.start() < object_match.start()):
        return array_match.group(0)
    if object_match:
        return object_match.group(0)
    return text


def parse_anchor_intents(raw: str) -> list[dict]:
    """宽容解析判据输出为**锚点列表**（第 57 刀 · 多批生图的地基）。

    兼容三种形态（真机都可能出现，缺一即静默退化到词表档）：
      ① **新形态** `{"anchors": [ {...}, ... ]}` —— 合同规定的形态；
      ② **旧形态** `{"anchor": ...}`（单条）⇒ 包装成**单元素列表**、`score=0`
         ⇒ **老判据逐字节维持旧行为**（零回归锁，与 `parse_anchor_intent` 同一口径）；
      ③ 裸数组 `[ {...}, ... ]`。
    逐条剔掉「anchor 为空」的项（合同允许某条 evidence 不过时只废该条）；
    整表为空、或三级解析全失败 ⇒ `[]`（调用方退回词表档）。
    """
    text = _THINK_BLOCK_RE.sub(" ", raw or "").strip()
    text = re.sub(r"^```(?:json|text)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text).strip()
    payload = _anchor_intent_json_block(text)
    for candidate in (payload, payload.translate(_ANCHOR_INTENT_FULLWIDTH_QUOTES)):
        try:
            value = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and isinstance(value.get(ANCHOR_INTENTS_KEY), list):
            # ① 新形态：显式列表 ⇒ 剔掉「没给出 anchor」的项（合同允许单条作废）。
            return [
                _normalize_anchor_intent(item)
                for item in value[ANCHOR_INTENTS_KEY]
                if isinstance(item, dict) and str(item.get("anchor") or "").strip()
            ]
        if isinstance(value, list):
            # ③ 裸数组：同上。
            return [
                _normalize_anchor_intent(item)
                for item in value
                if isinstance(item, dict) and str(item.get("anchor") or "").strip()
            ]
        if isinstance(value, dict):
            # ② **旧形态（单条）**：⚠ 逐字保留改造前行为 —— 即使四个字段全空也返回该条
            #   （旧实现就是 `return _normalize_anchor_intent(value)`）。
            #   调用方 `resolve_anchor_intent` 会因 `anchor` 为空而走「无锚点」分支，
            #   但 `detail` 非空这件事实会进 trace（`invalid` 的 `parsed=True`）⇒ 不可丢。
            return [_normalize_anchor_intent(value)]
        return []
    # 三级兜底：逐字段正则抓取（沿用旧实现，只服务**单条**形态 ——
    # 多锚点形态在两级 `loads` 都失败时不做正则硬凑，宁可退回词表档）。
    fields = {
        "anchor": _anchor_intent_field(payload, "anchor"),
        "composition": _anchor_intent_field(payload, "composition"),
        "reason": _anchor_intent_field(payload, "reason"),
        "subjects": _anchor_intent_subjects(payload),
        "score": _anchor_intent_score(_anchor_intent_field(payload, "score")),
    }
    if not (fields["anchor"] or fields["composition"] or fields["subjects"]):
        return []
    return [fields]


def select_anchor_intents(items: list[dict], count: int) -> list[dict]:
    """按**分数降序**取前 `count` 条；同分取**正文更早**（保序稳定，第 57 刀）。

    `count <= 0` ⇒ 全部（「上限不限制」的口径：数量 ≥ 锚点数就是全部出图）。
    ⚠ **本函数不做 0 分过滤**：旧单锚点形态被 `parse_anchor_intents` 有意包装成
    `score=0`（零回归锁）⇒ 「显式 0 分不生成」是**多批选择层**的规则，
    落在 agent_agency 的调用侧（对判据新形态的多锚点列表过滤），不落在这里。
    ⚠ 返回**按正文原序**排列（图要跟剧情位置走，不是按分数排）——
       本函数只决定「选哪几条」，不决定「摆哪几处（`offset`）」。
    """
    if not items:
        return []
    if count <= 0 or count >= len(items):
        return list(items)
    ranked = sorted(
        enumerate(items),
        key=lambda pair: (-int(pair[1].get("score") or 0), pair[0]),
    )
    chosen = {index for index, _ in ranked[:count]}
    return [item for index, item in enumerate(items) if index in chosen]


def dedupe_anchor_intents(items: list[dict], text: str) -> list[dict]:
    """把**同一处高潮**的重复锚点合并成一条（第 57 刀 · 多批生图去重）。

    判据一次列出「所有值得配图的时刻」后，常会把**同一次接触**拆成几条
    （前一段「在腰间」、后一段「插进嘴里」——正是用户 56 刀点名的那种时点错位）。
    若不去重，多批生图会把**同一处**画好几张、位置还挤在一起。

    **判据（用户定案，2026-09-24）：高度带 + 相邻段落。**
      · 同一条锚点的段落由 `anchor` 逐字在正文里定位（归一化后比对，忽略空白与标点）；
      · 两条锚点**高度带相同**（复用 56 刀 `_timepoint_band`：`F`／`L`）**且**其段落
        **相邻或重叠**（候选间隔 ≤ `_ANCHOR_DEDUPE_MAX_GAP` 段）⇒ 判为**同一处**，合并；
      · 合并后**保留 `score` 更高**的那条；同分保留**正文更早**的那条（保序稳定）；
      · 高度带为空（`.`／`?`，无接触证据）**不参与合并**——没证据就不猜，宁可多出一张；
      · 段落定位失败（`anchor` 找不到或在原文重复出现）**不参与合并**（同上，宁缺勿猜）。

    ⚠ **不做传递闭包**：只与「已保留的最近一条」比。§ 逐条比 ⇒ a~b~c 不会把 a 与 c 并掉，
      避免一次长链误吞整段剧情的锚点。
    返回**保持正文原序**的列表（图要跟剧情位置走，不按分数排）。
    """
    if len(items) <= 1:
        return list(items)
    # 稳健性：调用方理论上只传 `_normalize_anchor_intent` 的产物，但本函数是**公开面**，
    # 非字典项一律原样保留、不参与合并（绝不因脏项抛异常）。
    items = [item if isinstance(item, dict) else {"anchor": "", "score": 0} for item in items]
    paragraphs = [part for part in _ANCHOR_PARAGRAPH_SPLIT_RE.split(text or "") if part.strip()]
    located = [_locate_anchor_paragraph(paragraphs, str(item.get("anchor") or "")) for item in items]
    bands = [
        _timepoint_band(paragraphs[where]) if where is not None else ""
        for where in located
    ]
    kept: list[dict] = []
    kept_where: list[int | None] = []
    kept_band: list[str] = []
    for item, where, band in zip(items, located, bands):
        merge_into = _dedupe_merge_target(
            where=where, band=band, kept_where=kept_where, kept_band=kept_band,
        )
        if merge_into is None:
            kept.append(item)
            kept_where.append(where)          # None ⇒ 永不参与后续合并
            kept_band.append(band)
            continue
        # 同一处 ⇒ 分数高者留下；同分保留**已保留的**（＝正文更早）。
        if int(item.get("score") or 0) > int(kept[merge_into].get("score") or 0):
            kept[merge_into] = item
            kept_where[merge_into] = where
            kept_band[merge_into] = band
    return kept


# 判为「同一处高潮」的最大**段落间隔**（§ 与 `_ANCHOR_DEDUPE_MAX_GAP` 同名的局部副本）。
# 1 表示「相邻或重叠」（判据的 `0 <= 段落号差 - 1 <= gap`）；留 1 段余量不更宽。
_ANCHOR_DEDUPE_MAX_GAP = 1


def _dedupe_merge_target(
    *, where: int | None, band: str,
    kept_where: list[int | None], kept_band: list[str],
) -> int | None:
    """该锚点该并进哪条已保留的（无 ⇒ `None`）。规则见 `dedupe_anchor_intents`。"""
    if where is None or band not in (_TP_BAND_FACE, _TP_BAND_LOWER):
        return None
    for index in range(len(kept_where) - 1, -1, -1):
        other_where = kept_where[index]
        if other_where is None or kept_band[index] != band:
            continue
        if abs(where - other_where) <= _ANCHOR_DEDUPE_MAX_GAP:
            return index
        # 已保留的是正文更早的 ⇒ 更早的都对不上时不必再往前找（后方的只会更远）。
        if other_where < where:
            return None
    return None


def _locate_anchor_paragraph(paragraphs: list[str], anchor: str) -> int | None:
    """`anchor` 落在第几段（0 起）。**唯一命中**才返回段号，否则 `None`。

    用与 `anchor_evidence_in_text` 同源的归一化（`_ANCHOR_MATCH_STRIP_RE`）：
    忽略空白与标点，但不做模糊匹配。
    ⚠ 只在**唯一**命中时定位：同一句话在正文里出现两次 ⇒ 段位有歧义 ⇒ 不参与去重
      （宁缺勿猜——并错一处比多出一张图的代价大）。
    """
    needle = _ANCHOR_MATCH_STRIP_RE.sub("", str(anchor or "").strip())
    if not needle:
        return None
    hits = [
        index for index, paragraph in enumerate(paragraphs)
        if needle and needle in _ANCHOR_MATCH_STRIP_RE.sub("", paragraph)
    ]
    return hits[0] if len(hits) == 1 else None


def parse_anchor_intent(raw: str) -> dict | None:
    """宽容解析判据输出，返回**单条**锚点（第 57 刀后＝「分最高」的那条）。

    保留本函数是**兼容老调用方与既有测试**（`resolve_anchor_intent` 也走它）：
      ① 旧形态（单条 `{"anchor": …}`）⇒ 只有一个元素 ⇒ **逐字节等于改造前**（零回归锁）；
      ② 新形态（数组）⇒ 取 `score` 最大者、同分取**正文更早**者
         —— 即「出图数 = 1」时的口径（用户定案：数量 < 高潮点数 ⇒ 按分从高到低）。
    空/无效 ⇒ `None`（调用方退回词表档，与改造前一致）。
    """
    items = parse_anchor_intents(raw)
    if not items:
        return None
    picked = select_anchor_intents(items, 1)
    return picked[0] if picked else None


def anchor_evidence_in_text(text: str, anchor: str) -> bool:
    """判据 anchor 的 evidence 校验：归一化后必须逐字落在正文。

    `text` 传**已剥离控制块的可见正文**（调用方负责，见 `_wb_comfy_scene_target` 的
    `clean`），故此处不重复抽 body。刻意不做模糊匹配——模糊匹配会让「模型编一句
    看起来像原文的话」也通过，判定档就失去意义（对照 `illustration_anchor_offset`
    是给"定位插入点"用的，那里模糊匹配是合理兜底，这里不是）。
    """
    visible = image_prompt_extract.restore_jailbreak(text or "")
    needle = image_prompt_extract.restore_jailbreak(anchor or "").strip()
    if not needle or not visible:
        return False
    if needle in visible:
        return True
    return (
        _ANCHOR_MATCH_STRIP_RE.sub("", needle)
        in _ANCHOR_MATCH_STRIP_RE.sub("", visible)
    )


_ANCHOR_PARAGRAPH_SPLIT_RE = re.compile(r"(?:\r?\n)+")


def _anchor_intent_recover(text: str, anchor: str) -> str:
    """锚点句逐字不在正文时，回退到**最相似的真实段落**（保留模型的位置意图）。

    真机实证（2026-09-18，生产模型 `deepseek-v4-flash-0731` P 档 3 次采样）：模型**想对了
    但写了原文没有的句子**——①「两人都用双手大大掰开了自己的臀缝，把小穴和后庭一同献了
    上来。」原文无此句；②「**两人**同时献上小穴和后庭…」原文是「**两侧**同时献上…」。
    旧行为是逐字闸判无效 ⇒ 整轮退回中文词表档 ⇒ 落到靠后的**单人插入戏**（1/3 失效，
    正是用户抱怨的「高潮点总是靠后的单人戏段」）。

    **为什么保留位置意图是安全的**：锚点句本身**不作为画面来源**——画面始终来自取材窗口
    （`illustration_scene_excerpt`）。锚点只回答「插在哪附近」。所以模型那句被改写的话，
    其**位置意图**仍然有效，只需换成一个**逐字可查证的真实段落**。

    **为什么不放行幻觉**：回退结果必须是**原文里的完整段落**（逐字可定位），且相似度
    达不到 `_ANCHOR_NEEDLE_MATCH_RATIO` 就返回空串 ⇒ 调用方照旧走词表档。
    `anchor_evidence_in_text` 仍是**审查函数**（结论不变），本函数是它下方的一层补救。

    **通用性**：只看相似度，不依赖任何场景词、不区分单人/多人（特征测试各锁一条）。
    """
    visible = image_prompt_extract.restore_jailbreak(text or "")
    needle = image_prompt_extract.restore_jailbreak(anchor or "").strip()
    if not visible or not needle:
        return ""
    normalized_needle = _ANCHOR_MATCH_STRIP_RE.sub("", needle)
    if not normalized_needle:
        return ""
    best: tuple[float, str] | None = None
    for raw_part in _ANCHOR_PARAGRAPH_SPLIT_RE.split(visible):
        part = raw_part.strip()
        if not part or part.lstrip().startswith("<"):
            continue
        normalized_part = _ANCHOR_MATCH_STRIP_RE.sub("", part)
        if not normalized_part:
            continue
        score = SequenceMatcher(None, normalized_needle, normalized_part).ratio()
        if score >= _ANCHOR_NEEDLE_MATCH_RATIO and (best is None or score > best[0]):
            best = (score, part)
    return best[1] if best else ""


def _anchor_intent_first_paragraph(anchor: str) -> str:
    """把跨段锚点收敛到**首个非空段**——锚点必须是能定位的一段连续原文。

    2026-09-18 三段固定文本实锤（simple_test）：「塞蕾娜正微笑着。\\n\\n一副心满意足的模样。」
    —— evidence 校验因为**归一化剥空白**而放行，但下游 `illustration_anchor_offset` 是
    「破甲还原后逐字匹配，命中不了才退回评分兜底」，跨段串命中不了 ⇒ 退回中文词表同款评分
    ⇒ 插入点被拉到正文 45% 处，而模型认出的那句话其实在 99.6% 处 ⇒ **判据白判**。
    收敛到首段后逐字可定位，且结果仍是「已通过 evidence 校验那串」的子串（校验结论不变）。
    """
    for part in _ANCHOR_PARAGRAPH_SPLIT_RE.split(anchor or ""):
        stripped = part.strip()
        if stripped:
            return stripped
    return ""


def resolve_anchor_intent(text: str, raw: str) -> tuple[str, dict]:
    """判据链收口：解析 → 段内收敛 → evidence 校验 →（造词时）相似段落回退 → (anchor, 明细)。

    校验不过时**先尝试回退**（`_anchor_intent_recover`：把模型的位置意图落回一个逐字可查证
    的真实段落，2026-09-18 第六刀），回退也拿不到才返回 `("", 明细或 {})` 走词表兜底。
    明细里的 `anchor` 是**实际采用**的锚点；跨段被收敛时另带 `anchor_raw` 保留模型原话（Trace 用），
    造词回退时另带 `anchor_recovered` 标记（True）以示「这句是系统回退来的、不是模型原话」。
    """
    parsed = parse_anchor_intent(raw)
    if not parsed:
        return "", {}
    raw_anchor = parsed["anchor"]
    anchor = _anchor_intent_first_paragraph(raw_anchor)
    if anchor != (raw_anchor or "").strip():
        parsed["anchor_raw"] = raw_anchor
    if not anchor:
        return "", parsed
    if not anchor_evidence_in_text(text, anchor):
        # 造词回退：模型位置意图保留，锚点换成最相似的真实段落（通用规则，见函数 docstring）。
        recovered = _anchor_intent_recover(text, anchor)
        if not recovered:
            return "", parsed
        parsed.setdefault("anchor_raw", raw_anchor)
        parsed["anchor_recovered"] = True
        anchor = recovered
    parsed["anchor"] = anchor
    return anchor, parsed


# 叙事者/主角别名（与 `character_state._PROTAGONIST_OWNERS` **同源**，2026-09-01 用户定案：
# 「第一人称的『我』不是剧情角色」）。本模块按分层约定**不 import character_state**
# （见模块 docstring）⇒ 用等值锁测试防漂移（`test_叙事者别名集与角色状态表同源`）。
# 这些别名常被 LLM 当角色名写进判据 `subjects` ⇒ 会被当成画面里的一个人。
_NARRATOR_ALIASES = frozenset({
    "主角", "我", "我们", "你", "您", "玩家", "宿主", "用户", "user", "player",
})
_FIRST_PERSON_RE = re.compile(r"我(?:们)?")


VIEWPOINT_MODES = ("first_person", "third_person")


_THIRD_PERSON_RE = re.compile(r"他(?:们)?|她(?:们)?|它(?:们)?")


def _viewpoint_of_paragraph(para: str, known: list[str] | None) -> str:
    """单段判定：`first_person` / `third_person` / `""`（**无信号**）。

    ⚠ 2026-09-20 真机实锤的坑：只判「锚点段有没有『我』」会把第一人称叙事的帧判成第三人称
    （三份语料全是第一人称，但判据抽到的锚点是拟声词/心理句/触感句，句中都没有「我」）。
    ⇒ 改成**有第三人称正向证据才算第三人称**（第三人称代词、或角色表点名），否则**无信号**，
    由调用方落到「默认第一人称」（用户 2026-09-20 定案）。
    """
    if _FIRST_PERSON_RE.search(para):
        return "first_person"
    if _THIRD_PERSON_RE.search(para):
        return "third_person"
    if any(name and name in para for name in (known or [])):
        return "third_person"
    return ""


def _frame_viewpoint_auto(
    anchor: str, narrative: str, known: list[str] | None = None,
) -> tuple[str, str]:
    """自动判定 + 判定依据（`anchor` / `narrative` / `default`）。"""
    para = _anchor_intent_first_paragraph(
        image_prompt_extract.restore_jailbreak(anchor or ""),
    )
    if para:
        # 锚点段就是这一帧。第三人称叙事里出现的「我」多半是对白 ⇒ 先看锚点段。
        mode = _viewpoint_of_paragraph(para, known)
        if mode:
            return mode, "anchor"
    fallback = _anchor_intent_first_paragraph(
        image_prompt_extract.restore_jailbreak(narrative or ""),
    )
    if fallback:
        mode = _viewpoint_of_paragraph(fallback, known)
        if mode:
            return mode, "narrative"
    # 2026-09-20 用户定案：自动判定拿不到信号时**默认第一人称**。
    return "first_person", "default"


def frame_viewpoint(
    anchor: str, narrative: str = "", *, override: str = "", known: list[str] | None = None,
) -> str:
    """画面人称（生图模式）：`first_person`（叙事者不占画面）/ `third_person`（主人公算一个）。

    2026-09-19 用户定案「人称口径」的机械等价物，2026-09-20 显式化为一等字段。
    三层（2026-09-20 用户定案「默认第一人称 + 手选优先」）：
    1. `override` 是合法档位 ⇒ **手选优先**（用户明确指定就听用户的）；
    2. 锚点段含第一人称标记 ⇒ `first_person`；锚点段取不到 ⇒ 退回正文首段；
    3. 两者都取不到（无信号）⇒ **默认第一人称**。
    ⚠ 只按**锚点段**判，不拿全文判：第三人称叙事里出现的「我」多半是对白。
    ⚠ 段内判定是「**有第三人称正向证据才算第三人称**」（第三人称代词 / `known` 角色表点名），
    否则无信号 ⇒ 默认第一人称。真机实锤：只判「锚点段有没有我」会把第一人称叙事的帧判成第三人称。
    """
    if override in VIEWPOINT_MODES:
        return override
    return _frame_viewpoint_auto(anchor, narrative, known)[0]


def frame_viewpoint_detail(
    anchor: str, narrative: str = "", *, override: str = "", known: list[str] | None = None,
) -> dict:
    """`frame_viewpoint` 的**可审计**版本：返回判定结果、依据、以及手选与自动的冲突。

    冲突留痕（用户 2026-09-20 定案「手选优先 + 冲突留痕」）：`conflict` 为真表示
    用户手选与自动判定不一致，实际采用了手选值。
    """
    auto, source = _frame_viewpoint_auto(anchor, narrative, known)
    manual = override if override in VIEWPOINT_MODES else ""
    return {
        "viewpoint": manual or auto,
        "auto": auto,
        "source": "override" if manual else source,
        "conflict": bool(manual and manual != auto),
    }


@dataclass
class ViewpointScope:
    """人称单一真源（第 51 刀）：算一次、写 `scene_spec`、6 处消费点一律消费，禁止各自重判。

    `mode` ＝ 画面人称（`first_person`/`third_person`）；`narrator` ＝ 叙事者姓名；
    `narrator_source` ＝ 姓名来路（`user_name`/`protagonist_table`/`worldbook`/`""`）；
    `detail` ＝ `frame_viewpoint_detail` 原样（供审计与 `illustration.request` 补记）。
    """

    mode: str = ""
    narrator: str = ""
    narrator_source: str = ""
    detail: dict = field(default_factory=dict)


def viewpoint_scope(
    anchor: str, known: list[str], *,
    override: str = "", narrator: str = "", narrator_source: str = "",
) -> ViewpointScope:
    """人称单一真源接缝（第 51 刀）。

    `mode` 由 `frame_viewpoint_detail` 算一次（手选优先 + 自动档，冲突留痕）；
    `narrator`／`narrator_source` 由上游三段读取（`worldbook_store.repo_protagonist_name`）
    算好传进来。下游 6 处消费点一律消费本结果，**不得再调 `frame_viewpoint` 自判**（＝D1）。
    """
    detail = frame_viewpoint_detail(anchor, known=known, override=override)
    return ViewpointScope(
        mode=detail.get("viewpoint") or "",
        narrator=str(narrator or "").strip(),
        narrator_source=str(narrator_source or "").strip(),
        detail=detail,
    )


def filter_anchor_subjects(
    subjects: list[str], anchor: str, known: list[str], *, narrator: str = "",
    viewpoint: str = "",
) -> list[str]:
    """判据档 subjects 机械后滤——「人称口径」（2026-09-19 用户定案）的机械等价物。

    第一人称 ⇒ 叙事者「我」不占画面；第三人称 ⇒ 主人公算。一律按**画面主体数**判。
    改判据 rubric 措辞已到天花板（v12/v13/v12.1 三轮真机实验：加措辞≈0 收益甚至污染
    L2）⇒ 走机械路径。三步**固定顺序**：

    1. **名字吸附**：判据漏写后缀/用简称时（真机实锤：L3 输出「泰宇」、角色表是
       「泰宇老师」），若该名是角色表某名字的子串且**唯一**命中 ⇒ 吸附为全名。
       只做**补全方向**（不变短——反向会把判据给的全名截断，信息单向丢失）；
       exact 命中先行短路；命中多于一个（歧义）⇒ 保留原样不猜。
    2. **滤叙事者/主角**：叙事者不占画面。必须在收敛**之前**滤——否则锚点句里
       第一人称的「我」字会把 subjects 收敛成只剩「我」。
       ⚠ 2026-09-20 收紧（用户实锤「因为有主角被误判为多人」）：原先只滤**字面**「我」，
       叙事者被写成别名（`你/玩家/主角/宿主…`）或**本名**时剔不掉 ⇒ 被算成画面里的一个人
       ⇒ 多人契约误触发。现在滤 `_NARRATOR_ALIASES` 全集；`narrator`（主角本名，
       调用方传 `ctx["user_name"]`）**只在第一人称**下滤——第三人称叙事里主人公算一个。
       ⚠ **第 42 刀·B（2026-09-22）**：上述「滤主角本名」依赖 `narrator` 非空，而 `narrator`
       来自用户设置的人设名 ⇒ **设置为空时整条判据空转**（真机事故：手选第一人称，判据
       `subjects=["洛渊","沈映澜"]` 里叙事者洛渊没被剔 ⇒ 契约按两人成稿 ⇒ 主人公入画）。
       故第一人称下**再收一道**：把 ∉ `known`（＝已配置角色卡，画面主体的身份真源）的名字
       一律剔出画面主体；**剔空则回退原名单**（不剔），`known` 为空则空转。
    3. **锚点句点名收敛**：判据锚点是定格句、画面主体就在句里点名（九格
       `_chain_full.json` 实测）。锚点段（破甲还原后取首段，与仲裁机同口径）与
       subjects 交集非空 ⇒ 收敛为交集（L1 叙事者自动出局 / L2 特写帧不卷他人）；
       交集为空 ⇒ 不动（L3 真多人帧，锚点用「两人」代称时不点名 ⇒ 保留全部）。

    `known` 为空时①自然空转；`subjects` 为空（词表档）原样返回 ⇒ 零回归前提。
    `narrator` 缺省空串 ⇒ 逐字节等于旧行为（锁测试）。
    `viewpoint`（第 51 刀）缺省空串 ⇒ 退回内部 `frame_viewpoint` 自判（零回归旧行为）；
    传 `first_person` / `third_person` 时**直接消费上游单一真源**、不再自判（修 D1）；
    值已含手选优先（上游 `frame_viewpoint_detail` 已把手选 override 合并进 mode）。
    """
    names = [str(s).strip() for s in (subjects or []) if str(s).strip()]
    if not names:
        return []
    known_names = [str(k).strip() for k in (known or []) if str(k).strip()]
    known_set = set(known_names)
    resolved: list[str] = []
    for name in names:
        if name in known_set:
            resolved.append(name)  # 已是角色表全名，精确短路
            continue
        hits = [k for k in known_names if name in k]  # 只补全（name ⊂ 全名）
        resolved.append(hits[0] if len(hits) == 1 else name)
    # 第 51 刀（2026-09-22 用户定案「人称单一真源」）：`viewpoint` 由上游唯一接缝
    # `viewpoint_scope` 算好传入，本函数**不再调 `frame_viewpoint` 自判**（修 D1）。
    # `viewpoint` 缺省空串 ⇒ 退回内部 `frame_viewpoint` 自判（零回归旧行为）。
    first_person = (
        (viewpoint == "first_person")
        if str(viewpoint or "").strip()
        else frame_viewpoint(anchor, known=known_names) == "first_person"
    )
    drop = set(_NARRATOR_ALIASES)
    if first_person:
        protagonist = str(narrator).strip()
        if protagonist:
            drop.add(protagonist)
    resolved = [name for name in resolved if name not in drop]
    resolved = list(dict.fromkeys(resolved))  # 吸附后可能撞名，去重保序
    # 第 42 刀·B（2026-09-22 用户实锤「第一人称下主人公仍在画面内」）：叙事者还会以**本名**
    # 出现在判据 `subjects` 里，而他**不是已配置角色卡**（本名来自用户人设，不在 `known`）
    # ⇒ 第 37 刀那条「滤主角本名」在 `narrator` 为空时抓不到他（`user_name` 未配置即空）。
    # 画面主体的身份真源是**已配置角色卡**：第一人称下 ∉ `known` 的名字一律不占画面主体，
    # 否则他会占掉一个主体名额 ⇒ 多人契约误触发 ⇒ 模型照着「两人」把主人公画进画面
    # （真机事故：女主服装被绑到主人公身上、女主变裸体）。
    # ⚠ 剔空则**整体回退**（不剔）：某帧画面里真的没有角色卡人物时，不得把主体数算成 0。
    # ⚠ `known` 为空 ⇒ 整段空转（老调用方零回归）。
    if first_person and known_set:
        known_only = [name for name in resolved if name in known_set]
        if known_only:
            resolved = known_only
    if not resolved:
        return []
    anchor_para = _anchor_intent_first_paragraph(
        image_prompt_extract.restore_jailbreak(anchor or ""),
    )
    if anchor_para:
        named = [name for name in resolved if name in anchor_para]
        if named:
            return named
    return resolved


# ── renderer 注册表：纯 dict，注册/查询无 I/O；concrete 渲染器（有 I/O）在别处注册进来 ──

# ── 角色事实三源融合（第 36 刀 · 2026-09-21 用户定案）─────────────────────────
# 用户口径：**剧情表现 > 表格补充 > 角色条目** —— 剧情＝实际最新、表格＝上轮信息、
# 条目＝最开始设定；三者是**融合**而不是二选一（重复或冲突的内容本身就是剧情演进：
# 丝袜被撕开／兔女郎装版／婚纱装）。本模块只做「**表格补充 ＋ 角色条目**」这一层的融合；
# 剧情那一层在 `image_prompt_profiles._evidenced_visual_facts`／`visual_facts`，不在此处。
# 零 LLM、纯文本、确定性（可被 raw 钉死回放验证）。
_APPEARANCE_HEAD_RE = re.compile(r"^\s*([^\s：:【]+)\s*[：:]")
_APPEARANCE_LABEL_RE = re.compile(r"【([^】]{1,8})】")
_APPEARANCE_CLAUSE_RE = re.compile(r"[；;。，,]")
_APPEARANCE_CLAUSE_NORM_RE = re.compile(r"[\s，,、。；;：:·「」『』“”\"'（）()\[\]—\-]+")
_APPEARANCE_TRIM = "；;，,。 \n\t"
# 表格里的**空值占位**（真机数据实测：陆莹纱的「穿着打扮」列写的字面就是「未提及」）。
# 这不是「禁词表」而是**空值忽略表**：占位符不代表事实，并入会把「未提及」当外貌写进提示词
# ⇒ 按「表格无该值」处理（与「完全无描述 ⇒ 只说一个男人/女人」同一口径：宁缺勿编）。
_APPEARANCE_PLACEHOLDER_RE = re.compile(
    r"^(?:未提及|未提到|未描写|未设定|未给出|未知|不详|暂无|待补|待定|无|没有|不适用|"
    r"n/?a|none|null|unset|unknown)$",
    re.IGNORECASE,
)


def _is_placeholder_value(text: str) -> bool:
    """表格单元格是不是**空值占位**（`未提及` / `—` / `-` / `无` …）⇒ 不当事实用。"""
    key = _appearance_clause_key(text)
    if not key:
        return True
    return bool(_APPEARANCE_PLACEHOLDER_RE.match(key))


# **单槽位属性**：同一时刻只可能有一个取值 ⇒ 跨来源时必须先做「同物筛查」，
# 不同物（婚纱 vs 兔女郎装）**只能按优先级覆盖，绝不能并存**——
# 2026-09-21 用户当场点出：「剧情的兔女郎服饰和上个表格的婚纱两个 tag 可能同时存在，
# 即服装被你兼容了」。外貌／身材是**可叠加属性**（发色＋五官＋体型各自独立）⇒ 照旧融合。
_APPEARANCE_EXCLUSIVE_LABELS = frozenset({"穿着", "服装", "着装", "打扮", "衣着"})
_APPEARANCE_SHARED_MIN_CHARS = 3


def _appearance_shared_subject(left: str, right: str) -> bool:
    """两段文本是否在讲**同一件东西**（共享 ≥3 个连续字符）。

    只服务**单槽位属性**的互斥筛查：同物 ⇒「同一套服装的不同状态」（`宗主仙裙` vs
    `宗主仙裙半褪后重新拢好` 共享 4 字）⇒ 可融合；不同物 ⇒ 互斥 ⇒ 按优先级覆盖。
    ⚠ 这是**筛查不是语义理解**：同色不同款（`黑色长裙` vs `白色长裙`）共享不足 3 字 ⇒
    判互斥 ⇒ 覆盖（与用户口径「表格补充 > 角色条目」同向，且**宁可少写不并存**）。
    """
    left_key = _appearance_clause_key(left)
    right_key = _appearance_clause_key(right)
    size = _APPEARANCE_SHARED_MIN_CHARS
    if len(left_key) < size or len(right_key) < size:
        return False
    grams = {left_key[i:i + size] for i in range(len(left_key) - size + 1)}
    return any(
        right_key[i:i + size] in grams for i in range(len(right_key) - size + 1)
    )


def _appearance_clauses(text: str) -> list[str]:
    """按分句切（`；;。，,`）、去首尾空白、丢空串。只用于判重，不改写原文。"""
    return [
        part.strip() for part in _APPEARANCE_CLAUSE_RE.split(str(text or ""))
        if part.strip()
    ]


def _appearance_clause_key(text: str) -> str:
    """分句归一化键：去掉空白与中英标点 ⇒ 只比字面内容（判「完全重复」用）。"""
    return _APPEARANCE_CLAUSE_NORM_RE.sub("", str(text or "")).strip()


def _appearance_label_spans(text: str) -> list[tuple[str, int, int]]:
    """返回 `[(桶名, 内容起, 内容止)]`——桶名＝`【…】`里的标签（如 `外貌`／`身材`／`穿着`）。

    文本没有【】标记 ⇒ **空表**（该块当一个未标桶处理）。
    """
    hits = list(_APPEARANCE_LABEL_RE.finditer(str(text or "")))
    spans: list[tuple[str, int, int]] = []
    for index, hit in enumerate(hits):
        end = hits[index + 1].start() if index + 1 < len(hits) else len(text)
        spans.append((hit.group(1), hit.end(), end))
    return spans


def _table_appearance_facts(tables) -> dict[str, list[tuple[str, str]]]:
    """从通用表读「外貌特征」「穿着打扮」两列 ⇒ `{姓名: [(桶名, 值), ...]}`（零 LLM）。

    只这两列；**不读状态列**（在场状态等另有其路，见 `agent_retrieval._repo_table_absent_actors`）。
    同名列后出现的表覆盖前面的表（与旧实现一致）。
    """
    if isinstance(tables, dict):
        tables = tables.get("tables") or []
    rows_by_actor: dict[str, list[tuple[str, str]]] = {}
    for table in tables or []:
        if not isinstance(table, dict):
            continue
        cols = table.get("columns") or []
        if "姓名" not in cols:
            continue
        name_idx = cols.index("姓名")
        appearance_idx = cols.index("外貌特征") if "外貌特征" in cols else None
        wardrobe_idx = cols.index("穿着打扮") if "穿着打扮" in cols else None
        for row in (table.get("rows") or []):
            if not isinstance(row, list) or len(row) <= name_idx:
                continue
            name = str(row[name_idx]).strip()
            if not name:
                continue
            facts: list[tuple[str, str]] = []
            if appearance_idx is not None and len(row) > appearance_idx:
                value = str(row[appearance_idx]).strip()
                if value and not _is_placeholder_value(value):
                    facts.append(("外貌", value))
            if wardrobe_idx is not None and len(row) > wardrobe_idx:
                value = str(row[wardrobe_idx]).strip()
                if value and not _is_placeholder_value(value):
                    facts.append(("穿着", value))
            if facts:
                rows_by_actor[name] = facts
    return rows_by_actor


def _merge_table_into_block(block: str, facts: list[tuple[str, str]]) -> str:
    """把表格事实并入条目块：**条目在前、表格在后**；归一化后完全重复的分句不重写。

    `facts` ＝ `[(桶名, 分句串), ...]`。桶名已存在 ⇒ 追加进该桶（**最新状态落在最后**，
    与既有「剧情正文／wardrobe 的当前状态优先」同一读法）；桶名不存在 ⇒ 在块尾新起一个桶。
    表格式里带的分句在条目里已逐字（归一化后）出现 ⇒ **整条不写**（不重复、不改写条目原文）。
    ⚠ **单槽位属性（穿着）先做同物筛查**：两侧共享 ≥3 连续字 ⇒ 同一套服装的不同状态 ⇒ 融合；
    **不共享 ⇒ 互斥 ⇒ 表格整体覆盖**（绝不并存两套服装）。
    无任何新增 ⇒ 返回**原串对象**（`is` 相同，调用方据此保留逐字节原样）。
    """
    additions: list[tuple[str, str]] = []
    out = block
    for label, content in facts:
        fresh = [c for c in _appearance_clauses(content) if _appearance_clause_key(c)]
        if not fresh:
            continue
        span = next(
            (item for item in _appearance_label_spans(out) if item[0] == label), None,
        )
        if span is None:
            additions.append((label, "；".join(fresh)))
            continue
        existing_text = out[span[1]:span[2]]
        if label in _APPEARANCE_EXCLUSIVE_LABELS and not _appearance_shared_subject(
            existing_text, content,
        ):
            # ⚠ 单槽位属性（穿着）× 两侧**不同物** ⇒ 互斥：按优先级让表格**覆盖**，
            # 旧的整套丢掉（婚纱／兔女郎装这类不可能同时在身上）。
            # ⚠ 桶内尾随的**换行必须留下**——条目是整卡正文，`【穿着】…\n【绑定世界书】…`，
            # 吞掉换行会把下一个标签粘在穿着内容里（真机实测过）。
            kept_tail = existing_text[len(existing_text.rstrip(" \n\t\r")):]
            out = out[:span[1]] + "；".join(fresh) + kept_tail + out[span[2]:]
            continue
        existing = {
            _appearance_clause_key(clause)
            for clause in _appearance_clauses(existing_text)
        }
        new_clauses = [
            clause for clause in fresh if _appearance_clause_key(clause) not in existing
        ]
        if not new_clauses:
            continue
        body = out[span[1]:span[2]]
        kept = body.rstrip(_APPEARANCE_TRIM)
        trailing = body[len(kept):]
        joiner = "；" if kept and not kept.endswith(("；", "，", ";", ",")) else ""
        out = (
            out[:span[1]] + kept + joiner + "；".join(new_clauses)
            + trailing + out[span[2]:]
        )
    if additions:
        tail = "；".join(f"【{label}】{content}" for label, content in additions)
        base = out.rstrip()
        joiner = "；" if base and not base.endswith(("；", "，", ";", ",")) else ""
        out = (base + joiner + tail) if base else tail
    return out if out != block else block


def _rewrite_actor_block(text: str, name: str, facts: list[tuple[str, str]]) -> str:
    """把 `name` 的段落（行首 `名字：` 起，到下一个段落头为止）与表格事实融合。

    没有该角色的段落 ⇒ 原样返回（调用方再决定要不要新起一行）。
    """
    lines = text.split("\n")
    start = None
    for index, line in enumerate(lines):
        match = _APPEARANCE_HEAD_RE.match(line)
        if match and match.group(1).strip() == name:
            start = index
            break
    if start is None:
        return text
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _APPEARANCE_HEAD_RE.match(lines[index]):
            end = index
            break
    block = "\n".join(lines[start:end])
    merged = _merge_table_into_block(block, facts)
    if merged is block or merged == block:
        return text
    return "\n".join([*lines[:start], *merged.split("\n"), *lines[end:]])


def actor_appearance_sources(appearance: str, actors: list[str], tables) -> dict[str, str]:
    """逐角色报外观事实来源（零 LLM、确定性）：`entry` / `table` / `merged` / `none`。

    `merged` ＝ 条目段与表格行**都**为该角色贡献了事实（＝第 36 刀融合的生效面）。
    判据是**段落头**（行首 `名字：`）与表格「姓名」列，不看子串 ⇒ 与
    `_filter_illustration_appearance` 同口径。供 run_trace／探针做确定性诊断。
    """
    table_names = set(_table_appearance_facts(tables))
    sources: dict[str, str] = {}
    for actor in actors or []:
        name = str(actor).strip()
        if not name:
            continue
        in_entry = False
        for line in str(appearance or "").split("\n"):
            match = _APPEARANCE_HEAD_RE.match(line)
            if match and match.group(1).strip() == name:
                in_entry = True
                break
        in_table = name in table_names
        sources[name] = (
            "merged" if in_entry and in_table
            else "entry" if in_entry
            else "table" if in_table
            else "none"
        )
    return sources


def supplement_actor_table_appearance(
    appearance: str, actors: list[str], tables,
) -> str:
    """把通用表的「外貌特征／穿着打扮」与既有外观资料**融合**（第 36 刀）。

    用户 2026-09-21 定案：**剧情表现 > 表格补充 > 角色条目**。原先只在「条目没点名该角色」
    时补表格（实为「条目 > 表格」，与口径相反）；现在**两边都有也融合**：
    ① 条目有该角色段 ⇒ 表格分句**并入同一属性桶**（条目在前、表格在后；完全重复的不重写）；
    ② 条目没有、只有表格 ⇒ 仍按旧形态**新起一行** `名字：【外貌】…；【穿着】…`；
    ③ 没表 / 表里没这个角色 / 主体为空 ⇒ **原样返回**（零回归）。

    2026-08-31 实锤的另一半原因仍在：星织恋↔角色卡·绮柔 条目名失配 ⇒ 条目整段缺粮，
    靠表格兜底。只读该两列，不做任何 LLM 判定。
    """
    if not actors:
        return appearance or ""
    rows_by_actor = _table_appearance_facts(tables)
    if not rows_by_actor:
        return (appearance or "").strip()
    out = (appearance or "").strip()
    for actor in actors:
        name = str(actor).strip()
        if not name:
            continue
        facts = rows_by_actor.get(name)
        if not facts:
            continue
        merged = _rewrite_actor_block(out, name, facts)
        if merged != out:
            out = merged
            continue
        if name not in out:  # 条目没这个角色 ⇒ 新起一行（旧形态，逐字节不变）
            facts_text = "；".join(f"【{label}】{content}" for label, content in facts)
            out = (out + "\n" if out else "") + name + "：" + facts_text
    return out


# ── 主人公性别（零 LLM，2026-09-20）───────────────────────────────────────────
# 本模块只负责**两件不碰条目正文的事**：① 通用表「性别」列读取（`gender_from_tables`）；
# ② 把到手性别作为画面事实注入外观段（`supplement_protagonist_gender`）。
# ⚠ 条目正文的性别**文本解析器**（`gender_from_text`）属主是 `worldbook_store`：
# importlinter `roleplay-agency-stack` 里本模块与 `character_state` 同层，而链路
# `character_state → repo_meta → character_store → worldbook_store` 又必须经过
# worldbook_store —— 它一旦反向 import 本模块即成同层依赖违规（2026-09-20 lint-imports 实测
# BROKEN）。故解析器随条目正文属主下沉，本模块不再持有它。
GENDER_FEMALE = "女"
GENDER_MALE = "男"


def gender_from_tables(tables, name: str) -> str:
    """从通用表（主角信息表 / 重要角色表 / 角色状态表）的「性别」列取某人性别（零 LLM）。

    只在世界书条目解析不到时作兜底（调用方定序）。取不到（无表 / 无该列 / 无该行 / 空值）⇒ `""`。
    只做**规范化**（含「女」⇒女、含「男」⇒男），不改写原值语义。
    """
    target = str(name or "").strip()
    if not target:
        return ""
    if isinstance(tables, dict):
        tables = tables.get("tables") or []
    for table in tables or []:
        if not isinstance(table, dict):
            continue
        cols = table.get("columns") or []
        if "姓名" not in cols or "性别" not in cols:
            continue
        name_idx = cols.index("姓名")
        gender_idx = cols.index("性别")
        for row in (table.get("rows") or []):
            if not isinstance(row, list) or len(row) <= max(name_idx, gender_idx):
                continue
            if str(row[name_idx]).strip() != target:
                continue
            raw = str(row[gender_idx]).strip()
            if "女" in raw:
                return GENDER_FEMALE
            if "男" in raw:
                return GENDER_MALE
            if raw:
                return raw
    return ""


def supplement_protagonist_gender(
    appearance: str, actors: list[str], protagonist: str, gender: str,
    *, viewpoint: str = "",
) -> str:
    """把**主人公性别**作为画面事实补进外观段（零 LLM，纯函数）。

    用户 2026-09-20 定案：只对主人公生效，且只作事实注入（不机械拼硬标签）。
    四重短路保证「取不到 ⇒ 逐字节不动」：
    ① `gender` 为空 ⇒ 原样返回（宁缺勿猜）；
    ② `viewpoint == "first_person"` ⇒ 原样返回（**第 37 刀**：第一人称下主人公＝镜头本身、
       不占画面 ⇒ 把他的性别当画面事实注入，会让模型在 `2girls` 之外多写 `1boy`）；
    ③ 主人公不在本帧主体里（未入画）⇒ 原样返回；
    ④ 外观段已含 `【性别】X` ⇒ 原样返回（幂等）。
    主人公已在外观段里 ⇒ 在**它那一行**追加；否则另起一行 `名字：【性别】X`。
    ⚠ 缺省 `viewpoint=""` ⇒ 逐字节等于旧行为（老调用方零回归）。
    """
    name = str(protagonist or "").strip()
    value = str(gender or "").strip()
    out = (appearance or "").strip()
    if not name or not value:
        return appearance or ""
    if str(viewpoint or "").strip() == "first_person":
        return appearance or ""
    if name not in [str(actor).strip() for actor in (actors or [])]:
        return appearance or ""
    fact = f"【性别】{value}"
    if fact in out:
        return appearance or ""
    lines = out.splitlines()
    for index, line in enumerate(lines):
        if name in line:
            lines[index] = f"{line.rstrip()}；{fact}"
            return "\n".join(lines)
    return (out + "\n" if out else "") + f"{name}：{fact}"



Renderer = Callable[[SceneRequest], str]  # 吃 SceneRequest，返回图片地址（url 或 data URI）
_RENDERERS: dict[str, Renderer] = {}


def register_renderer(fmt: str, fn: Renderer) -> None:
    """注册一个渲染器。新增图像格式 = 在这里登记一项，不改触发/组装逻辑。"""
    _RENDERERS[fmt] = fn


def get_renderer(fmt: str) -> Renderer | None:
    """取渲染器；未注册返回 None（调用方决定降级或跳过出图）。"""
    return _RENDERERS.get(fmt)


def available_formats() -> list[str]:
    """已注册的渲染格式（供前端选择/前端能力探测）。"""
    return sorted(_RENDERERS)
