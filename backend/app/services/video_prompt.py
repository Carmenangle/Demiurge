"""视频提示词编译 + 上交给视频模型的参数组装（V1.5/V1.6）。

参照 MiniMax H3 提示词模板规律（docs 外链 `H3-提示词模版规律.md`）：
H3 高服从字面执行，本质是「把成片逐秒、逐镜头、逐像素规定死」。
两套模板语义截然不同，分开建模，不共用：

- climax（高潮点·动作代入）：完整五段式（第 81 刀 ⑥ 退役、第 87 刀摘旋钮；
  七段式草稿见用户定稿 2026-08-28）。参考绑定声明
  「图片1中心的角色为 X」；动作段用 [时间分镜] 逐拍切段（0–Xs / X–Ys / 逐拍递增）。
- firstlast（首尾帧·剧情影片）：完整五段式。首帧→演变→尾帧覆盖整个桥段
  起承转合，含时间分镜；首帧带「上楼层尾帧」衔接上下文（转场判断融入生成）。

本模块是**纯函数**（不调 LLM / 不调网络 / 不提交视频），用于：
1. 单测覆盖两套模板的区块完整性；
2. dry-run 把「最终要交给视频模型的参数」完整组装出来供人核对。

审计修正（docs/PLAN-VIDEO-FIRSTLAST.md）：
- R2 缺图守卫：firstlast 缺图时参考绑定/时间分镜诚实标注「无参考图，以文字为准」，
  不产出「引用不存在的图」的提示词。
- R3 元信息三件套：模型名（透传，不硬编码）+ 时长 + 画幅（size 派生）。
- R6 音频：无逐字对白时只写纯音乐结构，不写「台词=逐字」诱导幻觉。
- R7 负面约束（第 81 刀退役 ⑥、第 87 刀摘除旋钮）：**不再进视频提示词，也不再是可配置
  字段**——这条链的节点只有 `positive` 一路输入，负面清单会以正面文本进模型；
  身份锁改由 [参考绑定] 承担，全片一次。
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.services import prompt_clean

# 元信息：默认时长（秒）。preset videoDurationHint 可覆盖。
_DEFAULT_DURATION = 15

# 高潮视频时长（2026-10-31 用户定案）：由剧情动作量判定，提取失败兜底 5 秒。
# 上限 2026-11-04 由 8 放到 15（用户定案「上限放到 15 秒，仍按动作量判定」）：判定权与
# 下限不变，只是重动作楼层可以一路演到 15 秒。实测约 1.3 秒/帧 ⇒ 15 秒 ≈ 362 帧 ≈ 7.8 分钟/条，
# 且长镜头后半段动作容易漂——这两条代价是这次放宽明确接受的，不是漏掉的。
CLIMAX_DURATION_MIN = 4
CLIMAX_DURATION_MAX = 15
CLIMAX_DURATION_FALLBACK = 5


def clamp_climax_duration(value: object) -> int | None:
    """把「剧情判定的秒数」夹到 4–15；非数字/非正数返回 None（调用方走兜底）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return max(CLIMAX_DURATION_MIN, min(CLIMAX_DURATION_MAX, int(round(seconds))))

# ⑤[音频] 口径（2026-09-20 用户定案）：**视频生成的音频只有效果音**。
# 理由：台词/旁白由「多元数据插入」里的**独立音频生成功能**负责，视频提示词再写一遍
# 会让两套音频链互相打架。用户原话：「视频生成的音频生成不包括台词，只有效果音」。
# ⚠ 两项数据仍完整留在 `spec["audio_design"]` 里（不删数据，只不渲染），
#    开关集中在这两个常量，一行即可改回。
_VIDEO_AUDIO_EMIT_MUSIC = False
_VIDEO_AUDIO_EMIT_LINES = False

# 首尾帧时间分镜的最小三段式（起-承-合），节奏名可被调用方覆盖。
_FIRSTLAST_BEATS = ("开场定格", "主体演变", "收尾定格")

# 镜头语言词汇表（单一事实来源：docs 外链《镜头运动》《镜头角度》）。
# 句式与文档「提示词：」一致，可直接交给 MiniMax H3；按 motion 强度分档，
# 开场定场、中段循环换镜、收尾收束，避免「每拍同一个运镜」的退化。
_CAMERA_ESTABLISH = {
    0: "固定镜头，相机完全静止，零运动，只记录画面内的动作",
    2: "摄像机缓缓向主体的面部移动，画面逐渐收窄",
    3: "低角度仰拍，摄像机以快速弧线围绕主体运动",
}
_CAMERA_MIDDLE = {
    0: (
        "摄像机镜头向左移动，缓慢摇动让更多元素入画",
        "镜头焦点从前景主体切换到背景，引导注意力转移",
        "摄像机缓缓向主体的面部移动，画面逐渐收窄",
    ),
    2: (
        "镜头绕主体旋转90度",
        "镜头横向移动，跟随主体动作",
        "摄像机缓缓向主体的面部移动，画面逐渐收窄",
    ),
    3: (
        "手持摄像机镜头，剧烈抖动，运动模糊",
        "镜头快速推近主体后骤停",
        "摄像机以快速弧线围绕主体运动",
    ),
}
_CAMERA_SOLO = {
    0: "摄像机缓缓向主体的面部移动，画面逐渐收窄",
    2: "镜头绕主体旋转90度",
    3: "低角度仰拍，摄像机以快速弧线围绕主体运动",
}
_CAMERA_CLOSE = {
    0: "镜头慢慢拉远",
    2: "镜头慢慢拉远",
    3: "镜头快速推近主体后骤停",
}
# 有底图（I2V）时首拍不得重新定场：换机位等于让模型另起一格构图，首帧就「看不出底图」。
_CAMERA_FRAME_HOLD = "固定镜头，机位与构图逐字保持图片1，不做任何推拉摇移"
# 元信息的首帧声明：光说「作为起始帧」不够，要写清首帧逐字复现哪些维度。
_FRAME_FIDELITY_NOTE = (
    "；使用输入图片作为准确起始帧："
    "首帧必须逐字复现图片1的机位、构图、人物姿态与服装，再从该定格继续演变"
)
# 剧情句含场景切换提示词时改用转场型运镜（衔接/转场的导演思维）。
# ⚠ 只在 firstlast 生效：climax 走下方「机位契约」，逐拍换镜（含这里的甩镜头/遮挡揭示
# 转场句）是高潮视频「像有转场」的直接来源（2026-11-04 用户实锤）。
_SCENE_SHIFT_CUES = ("另一边", "来到", "走进", "走出", "片刻后", "此时", "镜头一转", "回到", "转场")
_CAMERA_SHIFT_SLOW = "遮挡揭示转场：镜头被前景遮挡后移开，揭示新场景"
_CAMERA_SHIFT_FAST = "甩镜头转场：镜头快速切换，伴有强烈的运动模糊效果"
# 身份锁：⑥ 退役（见 `SECTION_NEGATIVE_RETIRED` 处注释）后唯一落点是 [参考绑定]，全片只写一次。
_IDENTITY_LOCK = "人物身份和五官不能发生变化"
# ③ 主体/场景拼接里的占位值（`(待定)` / `暂无` / `-` 这类）：它们不携带任何信息，
# 却会在拼接里挂成孤零零的分句（实测稿子「…递给她。，(待定)，IB031港区…」）。
_PLACEHOLDER_WORDS = frozenset({
    "待定", "暂定", "暂无", "未定", "未知", "待补", "待补充", "无", "略",
    "-", "—", "–", "tbd", "n/a", "na", "none",
})

# ── 高潮「机位契约」（2026-11-04 用户定案）────────────────────────────────────
# 高潮点延伸的镜头动不动，取决于这一帧的高潮是**主体在动**还是**世界在展开**：
# - `action_hold`（人物动作为高潮）：全片一条固定机位，动作在这**一格**里继续呈现。
#   逐拍换镜（含档位词表与模型自报运镜句）一律不吃——每拍重新瞄准会让首帧定格的机位/
#   景别在第 2 拍就丢掉，且多条互斥机位挤在同一段几秒窗口里就是用户报的「镜头到处转」。
# - `scene_reveal`（进入新场景的大场面）：全片只做**一条连续运动**（横移/上摇择一），
#   允许机位动，但不逐拍换镜；方向由画面线索判，判不出用水平横移（最不打扰构图的一条）。
#   这一档画面里的**世界照常运动**（人流走动、车辆、蒸汽、水面），人物动作也不冻结。
# 默认档＝`action_hold`：误判成「不动」只是少一次运镜，误判成「动」就是原缺陷，代价不对称。
#
# 🔴 措辞纪律（2026-11-04 第 79 刀）：**两档文案一律不得出现切镜/转场类触发词**
# （切镜、镜头切换、转场、跳切、多机位）与运镜动作名（推拉摇移）。原因不是"模型看不懂否定式"，
# 而是 `MiniMaxH3ImageToVideo` 的入参只有 `clip/vae/first_frame/last_frame/prompt/width/height/length`、
# 输出只有 `positive`——**这条链没有 negative 通道**，段落全文（含已退役的旧 ⑥）是以正面文本进同一条流的；
# 而 H3 官方写法手册把 `the shot cuts to / transitions to / switches to` 逐字列为**切镜触发词表**。
# 机位怎么不动要用官方运动类型名 `Static Shot` 的**肯定式**说，而不是用「无切镜」把触发词念一遍。
# 幅度**不是**边界（用户口径：Live2D 一样可以有很明显的动作）；边界是「只由这一格自身的形变
# 与物理跟随产生，不引入画面之外的元素、部位或道具」。
CLIMAX_INTENT_HOLD = "action_hold"
CLIMAX_INTENT_REVEAL = "scene_reveal"
# 宽画幅阈值：16:9≈1.78、4:3≈1.33 算宽；1:1、2:3、9:16 不算（用户口径「大场面一般是 16:9」）。
_WIDE_RATIO_MIN = 1.25
# 逐拍机位句只回答「这一拍的机位叫什么」；「全程一致 / 沿用图片1 / 背景光影」这些
# 全片规则由 ① 的 `_HOLD_META` + `_FRAME_FIDELITY_NOTE` 说**一次**（第 81 刀实测：审计稿
# 5 拍各复述一遍这条 48 字长句＝240 字，占 ④ 段 535 字的四成五，而 ① 早已说过——
# 重复不增加约束强度，只把动作事实挤出预算）。
# 文生视频（缺图守卫 R2）不得引用不存在的图 ⇒ 无首帧时不写「图片1」。
_HOLD_CAMERA_FRAME = "固定镜头（Static Shot），画框沿用图片1"
_HOLD_CAMERA_NOFRAME = "固定镜头（Static Shot）"
_HOLD_MOTION = ("主体的动作幅度按剧情需要，可以明显；但每一帧都由起始那一格画面自身的形变与"
                "物理跟随产生——重心与躯干的往复、肢体的幅度、发丝衣料与液体的甩动，"
                "不引入画面之外的元素、部位或道具")
_HOLD_RHYTHM_LOOP = "按剧情节奏推进，末态回到起始那一格的同一姿势，可无缝循环"
# 「无缝循环」不是随时成立的断言：只有末态确实落回起始构型才谈得上循环。真机实测
# （2026-11-04 第 82 刀）首拍＝俯身撑边、末拍＝抬腿踩凳＋背弓起，① 却无条件断言
# 「末态回到起始那一格的同一姿势」——同一条提示词里两句互斥，模型只能违反一条：
# 把末拍拽回起始姿势（剧情动作被吃），或忽略循环句（那句白写）。
_HOLD_RHYTHM_ADVANCE = "按剧情节奏推进，末态停在最后一拍给定的那一格"
# 回位线索：动作序列自己声明回到起始构型时才保留循环断言（英文一律小写比对）。
_RETURN_CUES = (
    "回到", "恢复", "重回", "退回", "坐回", "收回", "原姿势", "同一姿势", "原样",
    "back to", "returns to", "returning to", "same position", "same pose",
    "same stance", "as before",
)
_HOLD_META = "全片一条固定镜头（Static Shot），机位、画框、景别与背景光影全程保持一致"
_REVEAL_CAMERA_PAN = "镜头沿水平方向缓慢连续横移，依次揭示画面两侧的环境"
_REVEAL_CAMERA_TILT = "镜头缓慢连续向上摇摄，从近处一路扫向高处"
_REVEAL_META = "全片一条连续镜头运动揭示环境，机位沿这条路径持续向前，中途不间断"
_REVEAL_WORLD = ("画面中的世界照常运动：行人持续走动、车辆与环境元素按剧情推进，"
                 "在场人物的动作照常展开、幅度不受限制")
# 环境展开线索（②判据）：新场景进入词（复用 _SCENE_SHIFT_CUES）＋大场面名词。
_REVEAL_CUES = (
    "全景", "远景", "俯瞰", "鸟瞰", "辽阔", "广袤", "绵延", "天际线", "地平线",
    "群山", "山脉", "海面", "海浪", "舰队", "港口", "港区", "城市", "都市",
    "高楼", "大厦", "塔楼", "耸立", "广场", "大厅", "宫殿", "城堡", "废墟",
    "荒岛", "星空", "云海", "草原", "峡谷", "海岸", "码头", "船坞", "厂房",
)
# 上摇线索：竖直方向主体（判不出退化成横移）。
_TILT_UP_CUES = ("高楼", "大厦", "塔楼", "耸立", "天际线", "尖顶", "烟囱", "崖", "向上")

# ── 第 84 刀（2026-11-05）：第一人称镜头声明 ────────────────────────────────────
# 用户口径（原话）：「图片提示词既然是第一人称，那视频提示词也应该以第一人称展开，
#   描述也是像图片提示词一样，视频提示词也是从镜头伸出扮演角色的肢体才对」。
# 真机实锤（同一轮试跑入参）：这条链**从不读 `viewpoint`**（改前 `video_prompt.py` 里
#   `viewpoint`／`narrator` 出现 0 次），于是把叙事者写成**画面里一个完整的第三人**——
#   ③ `a younger man behind her`、④ `The man thrusts forward`／`The man，承接图片1…`，
#   而成稿图片1 里他只有从画框下缘进入的肢体。首帧锁要求「逐字复现图片1」，
#   这两句直接把它拽成第二个人物 ⇒ 模型要么画出一个不存在的全身，要么忽略首帧。
# ⚠ 措辞纪律（沿用图片侧第 68 刀定案）：**不写解释句**——「镜头即叙述者的双眼」
#   「叙述者不出现在画面里」模型读不懂（它不知道谁是叙事者），视角用**肯定式**
#   `first person view` 声明，叙事者的在场只写成**具体部位在画面里的位置**
#   （`entering from the lower frame edge`，与图片侧第 65 刀同一措辞）。
_POV_VIEW_ANCHORED = "第一人称视角（POV，first person view），镜头与图片1共用同一视点位置"
# 运镜档（`scene_reveal`）不绑图片1：机位本来就在动，绑了就是与「沿这条路径持续向前」互斥。
_POV_VIEW_PLAIN = "第一人称视角（POV，first person view）"
# 肢体事实的呈现限定：只有**没带**画框边缘字样的事实才补这一句（成稿 tag 自己已经带，
# 复述一遍就是白占 ① 的字数预算——与第 81 刀「重复不增加约束强度」同口径）。
_POV_LIMB_TAIL = "，entering from the lower frame edge"
# 第 84 刀：叙事者肢体事实的**成稿真源**——本帧出图提示词里带「画框边缘」限定的那些 tag。
_FRAME_EDGE_MARK = "frame edge"
# ═══ 第 90 刀（2026-11-05）：裸短语要补**归属解释**（用户判据原话）═══════════════════
# 用户原话：「肯定是要解释信息归属于哪个对象的，短句是不够用的」。
# 第 84 刀把图片侧第 68 刀的「不写解释句」延伸过来，结果 ① 只剩一串裸肢体 tag
# （`a pair of hands entering from the lower frame edge gripping her waist`）——**没有任何一处
# 说明那双手是谁的**。模型完全可以把它挂回画面里那位角色自己身上（她自己抓住自己的腰），
# 于是第 84 刀想防的「叙事者在画面里消失」换个形式又发生了。
# ⚠ 但**位置与形态**照图片侧的单一真源，不是把解释撒进每条短语：
#   图片侧的归属由**中文条款**承担（`image_prompt_profiles/_base.py`【视角·第一人称·
#   叙事者从画面下缘进入】＝「{who}的身体局部从画面下缘进入」＋【谁是{who}】），
#   而 tag 本体按第 69 刀「只写手与腿，**不写这只手的主人**」保持裸短语；
#   ⇒ 视频侧同构：①（全片规则位）**解释一次**，④ 每拍用一个**短回指**
#   （`_POV_BEAT_ACTOR`）指回那句解释。逐拍重复解释会让 ④ 膨胀（模版机位句已占其三成字数），
#   与第 81 刀「重复不增加约束强度」同口径。
# ⚠ 措辞仍避开英文元语言 `narrator`／`camera`／`point of view`：模型不知道那是谁，
#   且 ① 里「一条固定镜头」已经把 camera 这个词占给机位。归属要用**画面里看得见的名词**说。
_POV_LIMB_OWNER_CLAIM = (
    "以下从画框边缘进入的肢体属于镜头后面那一位本人"
    "（他在这一格里只有这些局部，没有躯干与面孔），"
    "被这些肢体接触到的部位则属于画面中的角色"
)


def frame_limb_facts(image_prompt: str) -> list[str]:
    """从**已成稿的出图提示词**里读出「叙事者局部肢体入画」的 tag（第 84 刀）。

    为什么读成稿而不是在视频侧重推一遍：这条链的契约是「首帧逐字复现图片1」，那么
    「图片1 里到底进来了哪些肢体」的唯一真源就是图链自己编译出的那几条 tag
    （`lower frame edge`＝图片侧第 65 刀 `_FP_LIMB_ENTRY_TAG` 追加的限定，逐字取用、
    不新造措辞）。真机实锤：同一帧 `_narrator_limb_facts` 从 narrative 抽不出事实
    （那 335 字正文全是台词＋心理，没有肢体句），而成稿 tag 里明写着
    `a pair of hands entering from the lower frame edge gripping her waist`。
    ⚠ 只读首行（anima／tags 档的 tag 行）；散文档取不到 ⇒ 返回空表 ⇒ 调用方另有一路兜底。
    """
    if not image_prompt:
        return []
    out: list[str] = []
    for tag in image_prompt.splitlines()[0].split(","):
        text = tag.strip()
        if text and _FRAME_EDGE_MARK in text.lower() and text not in out:
            out.append(text)
    return out

# ── 五段式区块键（2026-10-25 提示词模版骨架；顺序/标题可被模版覆盖）────────────
SECTION_META = "meta"
SECTION_BINDING = "binding"
SECTION_SUBJECT = "subject"
SECTION_SEGMENTS = "segments"
SECTION_AUDIO = "audio"
# ⑥ 负面约束**已退役**（第 81 刀 2026-11-04）：这条链只有 `positive` 一路输入，
# 「worst quality / bad anatomy / extra fingers」这类词以正面文本进模型＝叫它去看缺陷。
# 第 87 刀把旋钮整个摘掉：键表降到五键、`VideoSkeleton` 不再有 `negative_extra` 字段、
# store/路由/前端三个数据面同步删字段——留着一条写不进成稿的输入框就是谎报能力。
# 已存模版的**遗留形状**（六键 `section_order`、`section_titles.negative`、
# `negative_extra`）在载入时归一，不判非法：旧数据不该让整条模版消失。
SECTION_NEGATIVE_RETIRED = "negative"
_RETIRED_SECTION_KEYS: frozenset[str] = frozenset({SECTION_NEGATIVE_RETIRED})
SECTION_KEYS: tuple[str, ...] = (
    SECTION_META, SECTION_BINDING, SECTION_SUBJECT,
    SECTION_SEGMENTS, SECTION_AUDIO,
)
# 内置默认标题（climax/firstlast 的 ④ 段叫「时间分镜」，transition 叫「转场分镜」）
_DEFAULT_SECTION_TITLES: dict[str, str] = {
    SECTION_META: "元信息",
    SECTION_BINDING: "参考绑定",
    SECTION_SUBJECT: "主体/场景",
    SECTION_SEGMENTS: "时间分镜",
    SECTION_AUDIO: "音频",
}


def prune_retired_section_keys(keys: Iterable[str]) -> list[str]:
    """剔除已退役区块键（⑥），其余保持相对顺序。

    唯一负责「旧数据 ⇒ 新键表」的接缝：store 校验、`from_data` 解析、路由入参都走它，
    免得三处各写一遍过滤式后漏一处（漏的那处会让整条模版被判非法而消失）。
    """
    return [str(key or "").strip() for key in keys
            if str(key or "").strip() not in _RETIRED_SECTION_KEYS]
# firstlast 三段式节奏名的内置默认（与 _time_segments 内联标签一致）
_DEFAULT_BEAT_LABELS: tuple[str, str, str] = ("开场定格", "剧情演变", "收尾定格")


@dataclass(frozen=True)
class VideoSkeleton:
    """视频提示词骨架覆盖（提示词模版数据层；缺省 None = 逐字现行为）。

    只覆盖**文案与默认值**，不改分镜/参考绑定/缺图守卫/画幅派生等确定性逻辑：
    - section_titles：区块键 → 标题（不含 []）；缺键回落内置默认；
    - section_order：区块键的排列（必须是 SECTION_KEYS 的五键排列，防乱序破坏五段语义；
      遗留的六键排列在 `from_data` 里剔除 ⑥ 后按相对顺序保留，不判非法）；
    - camera_band_overrides：motion 档位(0/1/2/3) → 单句运镜文案（≤120 字）；
    - beat_labels：firstlast 的开场/中段/收尾节奏名；
    - default_duration：duration_hint 为空时的兜底秒数（转场形态同样只在显式提供时写）；
    - meta_note：元信息追加文案。

    ⛔ 没有「负面追加」字段：第 81 刀退役 ⑥、第 87 刀摘除旋钮——这条链只有 `positive`
    一路输入，任何负面串进提示词都成了「叫模型去看缺陷」的正面文本。
    """

    section_titles: dict[str, str] = field(default_factory=dict)
    section_order: tuple[str, ...] | None = None
    camera_band_overrides: dict[int, str] = field(default_factory=dict)
    beat_labels: tuple[str, ...] | None = None
    default_duration: int | None = None
    meta_note: str = ""

    @classmethod
    def from_data(cls, data: object) -> "VideoSkeleton | None":
        """防御性解析（store 保存/加载时已校验；此处再兜底：坏形状一律忽略该字段）。

        data 为空/非映射 ⇒ None（调用方走「无骨架」现行为路径）。
        """
        if not isinstance(data, dict):
            return None
        titles: dict[str, str] = {}
        raw_titles = data.get("section_titles")
        if isinstance(raw_titles, dict):
            for key, value in raw_titles.items():
                k = str(key or "").strip()
                v = str(value or "").strip().rstrip("：:")
                if k in SECTION_KEYS and v:
                    titles[k] = v[:200]
        order: tuple[str, ...] | None = None
        raw_order = data.get("section_order")
        if isinstance(raw_order, list):
            keys = tuple(prune_retired_section_keys(raw_order))
            if keys and sorted(keys) == sorted(SECTION_KEYS) and len(set(keys)) == len(SECTION_KEYS):
                order = keys
        bands: dict[int, str] = {}
        raw_bands = data.get("camera_band_overrides")
        if isinstance(raw_bands, dict):
            for key, value in raw_bands.items():
                try:
                    band = int(str(key).strip())
                except ValueError:
                    continue
                v = str(value or "").strip()
                if band in (0, 1, 2, 3) and v:
                    bands[band] = v[:120]
        beats: tuple[str, ...] | None = None
        raw_beats = data.get("beat_labels")
        if isinstance(raw_beats, list):
            labels = tuple(str(item or "").strip()[:50] for item in raw_beats if str(item or "").strip())
            if labels:
                beats = labels[:6]
        duration: int | None = None
        raw_duration = data.get("default_duration")
        if isinstance(raw_duration, int) and not isinstance(raw_duration, bool) and 2 <= raw_duration <= 20:
            duration = raw_duration
        meta_note = str(data.get("meta_note") or "").strip()[:500]
        return cls(
            section_titles=titles, section_order=order, camera_band_overrides=bands,
            beat_labels=beats, default_duration=duration, meta_note=meta_note,
        )

    def title(self, key: str, default: str) -> str:
        return self.section_titles.get(key) or default

    def order(self) -> tuple[str, ...]:
        return self.section_order or SECTION_KEYS

    def beat_label(self, idx: int, default: str) -> str:
        if self.beat_labels and idx < len(self.beat_labels):
            return self.beat_labels[idx]
        return default


def default_skeleton_data(mode: str) -> dict[str, Any]:
    """内置骨架数据投影（供提示词模版工作台展示/克隆；纯数据，不参与编译）。"""
    segments_title = "转场分镜" if mode == "transition" else "时间分镜"
    titles = dict(_DEFAULT_SECTION_TITLES)
    titles[SECTION_SEGMENTS] = segments_title
    return {
        "section_titles": titles,
        "section_order": list(SECTION_KEYS),
        # per-band 代表句（_CAMERA_SOLO）：单拍形态的档位文案，供 UI 预览默认值
        "camera_band_overrides": {str(band): _CAMERA_SOLO[band] for band in (0, 2, 3)},
        "beat_labels": list(_DEFAULT_BEAT_LABELS),
        "default_duration": _DEFAULT_DURATION,
        "meta_note": "",
    }


def _camera_band(motion: int) -> int:
    """motion 强度 → 词汇表档位（0 慢 / 2 中 / 3 快）。"""
    return 3 if motion >= 3 else (2 if motion == 2 else 0)


def _beat_camera(motion: int, idx: int, total: int, desc: str = "",
                 band_overrides: dict[int, str] | None = None,
                 has_frame: bool = False) -> str:
    """第 idx/total 拍的运镜：开场定场、收尾收束、中段按档位循环换镜；
    单拍用档位代表句；句子带场景切换提示时改用转场型运镜。

    band_overrides（提示词模版骨架）：该档有覆盖文案时，全档位统一用它
    （用户显式选择「这一档就用这句」）；缺键回落内置词汇表。
    has_frame（I2V 有底图）：首拍改成「逐字保持图片1机位」，中段每 2 拍才换一次镜
    ——每拍都换镜会让模型逐拍重新构图，首帧定格的机位/景别在第二拍就丢了。
    """
    band = _camera_band(motion)
    if band_overrides and band in band_overrides:
        return band_overrides[band]
    if total <= 1:
        return _CAMERA_FRAME_HOLD if has_frame else _CAMERA_SOLO[band]
    if idx == 0:
        return _CAMERA_FRAME_HOLD if has_frame else _CAMERA_ESTABLISH[band]
    if idx == total - 1:
        return _CAMERA_CLOSE[band]
    if any(cue in desc for cue in _SCENE_SHIFT_CUES):
        return _CAMERA_SHIFT_FAST if band >= 2 else _CAMERA_SHIFT_SLOW
    vocab = _CAMERA_MIDDLE[band]
    if has_frame:
        return vocab[((idx - 1) // 2) % len(vocab)]
    return vocab[(idx - 1) % len(vocab)]


def _ratio_from_size(size: str) -> float:
    """size → 宽高比数值（`1280x720` / `16:9` → 1.778）。解析不出返回 0.0。"""
    s = (size or "").strip().lower()
    s = s.replace("*", "x").replace("×", "x").replace("：", ":")
    m = re.match(r"^(\d+)\s*[x:]\s*(\d+)$", s)
    if not m:
        return 0.0
    w, h = int(m.group(1)), int(m.group(2))
    return w / h if h > 0 else 0.0


def _climax_scan_text(spec: dict[str, Any]) -> str:
    """高潮帧的判定文本：剧情＋场景＋画面级要素＋动作链＋（模型自报）运镜句。

    只拼文本、不改写任何字段；缺项跳过（不硬猜）。
    """
    chunks: list[str] = [
        str(spec.get("narrative") or ""),
        str(spec.get("locale") or ""),
        str(spec.get("video_subject_scene") or ""),
        str(spec.get("composition") or ""),
        str(spec.get("camera") or ""),
    ]
    for item in spec.get("action_sequence") or []:
        if isinstance(item, dict):
            chunks.append(str(item.get("desc") or ""))
    for fact in spec.get("visual_facts") or []:
        if isinstance(fact, dict):
            chunks.append(str(fact.get("value") or fact.get("evidence") or ""))
    return "　".join(c for c in chunks if c)


@dataclass(frozen=True)
class ClimaxCameraContract:
    """高潮一支的机位契约（判定结果 + 它带来的文案）。

    契约只覆盖「机位 / 逐拍条款 / ①全片声明」三个面；分镜切拍、缺图守卫、
    画幅派生一概不动（对齐红线：模版与契约都不改输出结构）。
    `notes` 只写一次（① 是「全片规则」的位置，逐拍重复会让 ④ 膨胀成噪声）；
    `beat_fx` / `beat_sync` 是逐拍条款（空串 = 这拍不写）。
    契约**不产出负面文本**（原 ⑥ 区块已于第 87 刀摘除）——H3 节点没有 negative 通道，
    写「无切镜」等于把切镜触发词念进正面文本流（见上方措辞纪律），所以这里只出正面文案。
    """

    intent: str
    reason: str
    camera: str
    notes: tuple[str, ...]
    beat_fx: str
    beat_sync: str

    @property
    def holds_camera(self) -> bool:
        return self.intent == CLIMAX_INTENT_HOLD


def _hold_rhythm(spec: dict[str, Any]) -> str:
    """hold 档的节奏句：循环断言只跟**实际分镜**走（第 82 刀）。

    - 单拍（画面级要素兜底/无动作序列）⇒ 整条时间轴就是首帧那一格自身的往复，循环成立；
    - 多拍 ⇒ 只在末拍自己声明回位时保留「可无缝循环」，否则改说末态停在哪儿。
      末拍来自 action_sequence **或 narrative 分句**，两者都算——真机实测末拍是
      「抬腿踩凳＋背弓起」，与「回到起始姿势」直接互斥。
    """
    beats = _climax_beat_plan(spec)
    if len(beats) < 2:
        return _HOLD_RHYTHM_LOOP
    low = beats[-1][1].lower()
    return _HOLD_RHYTHM_LOOP if any(cue in low for cue in _RETURN_CUES) else _HOLD_RHYTHM_ADVANCE


def _first_person_view(spec: dict[str, Any]) -> bool:
    """本帧是否第一人称（第 84 刀）。

    ⚠ 判据**只读** `spec["viewpoint"]`、不重算——人称真源是出图侧第 51 刀的
      `scene_illustration.viewpoint_scope`，在这里再判一次就是「同一条口径两个属主」。
    ⚠ 缺失／非 `first_person` ⇒ `False` ⇒ 第三人称与旧稿件逐字节不变（零回归锁）。
    """
    return str(spec.get("viewpoint") or "").strip() == "first_person"


def _pov_limb_phrase(spec: dict[str, Any]) -> str:
    """叙事者**局部肢体入画**的肯定式短语（第 84 刀）；无事实 ⇒ 空串。

    事实源＝`spec["narrator_limbs"]`，由出图侧接缝（`agency_scene_comfy`）算好下发，
    两路按序去重：① 本帧成稿出图提示词里的 `lower frame edge` tag（`frame_limb_facts`，
    真源）；② 图片侧 `_narrator_limb_facts`（正文肢体句，兜底）。本模块**不重算人称、
    不重算部位归属**——判据属主是第 51 刀的 `viewpoint_scope` 与图片侧 43/47/62/65 刀，
    在这里再写一份就是「同一条口径两个属主」（§1.0 元教训）。
    """
    raw = spec.get("narrator_limbs")
    if not isinstance(raw, (list, tuple)):
        return ""
    limbs = [str(item).strip() for item in raw if str(item).strip()]
    if not limbs:
        return ""
    body = "、".join(dict.fromkeys(limbs))
    if all(_FRAME_EDGE_MARK in item.lower() for item in limbs):
        return body
    return body + _POV_LIMB_TAIL


def _pov_shot_notes(spec: dict[str, Any], *, anchored: bool) -> tuple[str, ...]:
    """第一人称镜头声明（进 ① 的 notes 尾部）。

    ⚠ 零回归锁：`viewpoint` 缺失／非 `first_person` ⇒ 空元组 ⇒ ① 逐字节等于旧行为
      （第三人称与「用户没开第一人称」的既有稿件不受任何影响）。
    ⚠ `anchored` 只在 hold 档＋有首帧时为真：无首帧不得引用不存在的图（缺图守卫 R2），
      reveal 档机位本来在动，不能再说「与图片1同一视点」。
    第 90 刀：**有肢体事实才补归属主句**，且它排在事实列表之前——裸 tag 只说「有一双手从
      画框下缘进来抓住她的腰」，不说那双手是谁的；归属在 ① 解释一次，④ 用 `_POV_BEAT_ACTOR`
      回指它（图片侧同构：条款承担归属、tag 本体保持裸短语）。
      无肢体事实 ⇒ 主句不出现（没有指代对象，写了只是白占字数）。
    """
    if not _first_person_view(spec):
        return ()
    notes = [_POV_VIEW_ANCHORED if anchored else _POV_VIEW_PLAIN]
    limbs = _pov_limb_phrase(spec)
    if limbs:
        notes.append(_POV_LIMB_OWNER_CLAIM)
        notes.append(limbs)
    return tuple(notes)


def resolve_climax_shot_intent(spec: dict[str, Any], size: str = "",
                               has_frame: bool = False) -> ClimaxCameraContract:
    """高潮机位契约判定（纯机械、零 LLM、确定性）。

    升格 `scene_reveal` 需**两条正向证据同时成立**：
    ① 宽画幅（ratio ≥ `_WIDE_RATIO_MIN`）；② 环境展开线索命中（新场景进入词／大场面名词）。
    只满足一条留在 `action_hold`（判错代价不对称，见上方常量注释）。

    画幅真源优先级：调用方 `size`（video_config 派生）> `spec["aspect_ratio"]`（出图侧
    判定值，可能是 keyword_infer 猜的）——后者只当兜底，`reason` 里标明用的是哪一路，
    免得拿猜值当实证。
    """
    motion = int(spec.get("motion") or 0)
    ratio_src = str(size or "")
    ratio = _ratio_from_size(ratio_src)
    src = "size"
    if ratio <= 0:
        ratio_src = str(spec.get("aspect_ratio") or "")
        ratio = _ratio_from_size(ratio_src)
        src = "spec.aspect_ratio"
    wide = ratio >= _WIDE_RATIO_MIN
    text = _climax_scan_text(spec)
    cues = [c for c in (*_REVEAL_CUES, *_SCENE_SHIFT_CUES) if c in text]
    if wide and cues:
        moves = [c for c in _TILT_UP_CUES if c in text]
        reason = (
            f"wide={ratio_src}({ratio:.2f},{src});"
            f"cues={','.join(cues[:4])};move={'tilt_up' if moves else 'pan'}"
        )
        return ClimaxCameraContract(
            intent=CLIMAX_INTENT_REVEAL, reason=reason,
            camera=_REVEAL_CAMERA_TILT if moves else _REVEAL_CAMERA_PAN,
            notes=(_REVEAL_META, *_pov_shot_notes(spec, anchored=False), _REVEAL_WORLD),
            beat_fx=_climax_fx(motion),
            beat_sync=_climax_beat_sync(motion),
        )
    return ClimaxCameraContract(
        intent=CLIMAX_INTENT_HOLD,
        reason=f"wide={ratio_src or '未知'}({ratio:.2f},{src});cues={','.join(cues[:4]) or '无'}",
        camera=_HOLD_CAMERA_FRAME if has_frame else _HOLD_CAMERA_NOFRAME,
        notes=(_HOLD_META, *_pov_shot_notes(spec, anchored=has_frame),
               _HOLD_MOTION, _hold_rhythm(spec)),
        beat_fx=_hold_beat_fx(motion), beat_sync="",
    )


def _climax_beat_camera(contract: ClimaxCameraContract, explicit: str) -> str:
    """逐拍共用的机位句。

    - `action_hold`：只用契约的固定句——预设/模型运镜句在此档**一律不吃**（写了会和
      「机位静止」自相矛盾，而那正是缺陷本体）；
    - `scene_reveal`：模型/预设给了具体运动就用它（比词表更贴这一帧），否则用契约句。
    """
    if contract.holds_camera:
        return contract.camera
    return (explicit or "").strip() or contract.camera


def _aspect_from_size(size: str) -> str:
    """从 size 派生画幅（1280x720 → 16:9；已含 ':' 则原样）。R3 补 H3 元信息画幅。"""
    s = (size or "").strip().lower().replace("*", "x").replace("×", "x").replace("：", ":")
    if not s:
        return ""
    if "x" not in s and ":" in s:
        return s  # 已是 16:9 这类比例
    m = re.match(r"^(\d+)\s*x\s*(\d+)$", s)
    if not m:
        return ""
    w, h = int(m.group(1)), int(m.group(2))
    if w <= 0 or h <= 0:
        return ""
    g = math.gcd(w, h)
    return f"{w // g}:{h // g}"


def _subject_scene(spec: dict[str, Any]) -> str:
    """③ 主体/场景：只写「这一格里看得见」的可还原视觉，禁止整卡堆料。

    三级来源（第 81 刀收窄）：
    1. agent 提取的简化视觉描述（video_subject_scene，已去堆砌 + 专名视觉展开）；
    2. 画面主体（`subjects`＝出图侧的画面主体名单）的英文视觉描述；
    3. 中文 appearance **按在场角色名逐条取**（`_appearance_by_actor` 拆得出人名才写）。
    第 3 级拆不出人名就**不写**：整串 appearance 往往是世界书段落 + 全部角色外貌，
    实测一条 3166 字的稿子里 ③ 占 61 %，而首帧已由 ① 逐字声明「复现图片1」——
    堆料不补信息，只把画面主体淹没。衣着/场景两个独立维度照常拼接，占位值除外。
    """
    video_scene = str(spec.get("video_subject_scene") or "").strip()
    if video_scene:
        return video_scene
    parts: list[str] = []
    for subject in spec.get("subjects") or []:
        if isinstance(subject, dict):
            desc = str(subject.get("description") or "").strip()
            if desc and desc not in parts:
                parts.append(desc)
    if not parts:
        names = _on_frame_names(spec)
        gloss = _appearance_by_actor(str(spec.get("appearance") or ""), names)
        parts = [f"{name}（{gloss[name]}）" for name in names if gloss.get(name)]
    # 衣着 + 场景（独立维度，始终拼接）
    for key in ("wardrobe", "locale"):
        val = str(spec.get(key) or "").strip()
        if val and val not in parts and not _is_placeholder(val):
            parts.append(val)
    return "，".join(parts)


def _on_frame_names(spec: dict[str, Any]) -> list[str]:
    """画面在场角色名：`actors` 优先，空则退判据的 `frame_subjects`（画面主体名单）。

    两个都空 ⇒ 空表（不硬猜主体，调用方据此放弃需要名字才能归属的写法）。
    """
    for key in ("actors", "frame_subjects"):
        names = [str(item).strip() for item in (spec.get(key) or []) if str(item).strip()]
        if names:
            return list(dict.fromkeys(names))
    return []


def _is_placeholder(value: str) -> bool:
    """判断一个字段值是占位文字还是真内容（`(待定)`/`暂无`/`-` ⇒ 不是内容）。"""
    core = re.sub(r"^\s*[\(（]?|[\)）]?\s*$", "", value).strip().lower()
    return core in _PLACEHOLDER_WORDS


def _appearance_by_actor(appearance: str, actors: list[str]) -> dict[str, str]:
    """从中文外貌文本拆「角色→外貌」映射。

    支持三种格式：`名字(外貌)` 顿号/分号/逗号分隔、`名字：外貌` 多行段落头、
    单角色纯文本（无分隔且只有一个在场角色）。拆不出返回空 dict（不硬猜）。
    """
    src = (appearance or "").strip()
    if not src:
        return {}
    paren = re.findall(r"([^\s、;；，,]+?)\s*[\(（]([^\)）]+)[\)）]", src)
    if paren:
        out: dict[str, str] = {}
        for name, desc in paren:
            name, desc = name.strip(), desc.strip()
            if name and desc:
                out[name] = desc
        return out
    lines = [ln for ln in src.splitlines() if ln.strip()]
    header = re.compile(r"^\s*([^\s：:]+)\s*[：:]\s*(.+)$")
    if len(lines) >= 2 and all(header.match(ln) for ln in lines):
        mapped: dict[str, str] = {}
        for ln in lines:
            m = header.match(ln)
            if m:
                mapped[m.group(1).strip()] = m.group(2).strip()
        return mapped
    # 纯文本（无括号/冒号/换行分隔）：用 actors 名字做前缀匹配，拆出「名字 + 外貌」。
    for actor in sorted(actors, key=len, reverse=True):
        if src.startswith(actor) and len(src) > len(actor):
            rest = src[len(actor):].strip("，,、；;：: ")
            if rest:
                return {actor: rest}
    if len(actors) == 1 and not re.search(r"[：:\(（]", src):
        return {actors[0]: src}
    return {}


def _identity_gloss(spec: dict[str, Any], actors: list[str]) -> str:
    """角色身份样貌绑定串：让视频模型把「角色名」对应到具体长相。

    样貌来源：subjects(name→description 英文视觉) 优先，缺失回退 appearance
    （中文纯外貌，见 _appearance_by_actor）。都缺的角色只写名字（诚实，不硬凑）。
    当 agent 已产出简化外貌/场景（video_subject_scene）时，不再用原始中文
    appearance 兜底——否则堆砌词（丰腴肥熟/酥雌醇媚）会经参考绑定回流，违背 P4。"""
    gloss: dict[str, str] = {}
    for subject in spec.get("subjects") or []:
        if isinstance(subject, dict):
            name = str(subject.get("name") or "").strip()
            subj_desc = str(subject.get("description") or "").strip()
            if name and subj_desc:
                gloss[name] = subj_desc
    app_gloss: dict[str, str] = {}
    if not str(spec.get("video_subject_scene") or "").strip():
        app_gloss = _appearance_by_actor(str(spec.get("appearance") or ""), actors)
    parts: list[str] = []
    for actor in actors:
        desc: str | None = gloss.get(actor) or app_gloss.get(actor)
        parts.append(f"{actor}（{desc}）" if desc else actor)
    return "、".join(parts)


def _climax_frame_role(spec: dict[str, Any], first_frame_desc: str = "") -> str:
    """图片职责描述（图片↔角色绑定）：外部 first_frame_desc 优先；否则用
    「{画面角色}的高潮动作画面」，让视频模型知道这张高潮图是谁的高潮图——
    单角色、多角色都绑定，不只单角色一种情况。无在场角色才退「高潮动作画面」占位。

    视频模型拿到参考图时只看得见图，不知道图里的人对应剧情里的哪个名字；
    把角色名写进图片职责描述，等于声明「这张图就是这些角色的画面」，名字才落得实。"""
    desc = (first_frame_desc or "").strip()
    if desc and desc != "高潮动作画面":
        return desc
    actors = [str(a).strip() for a in (spec.get("actors") or []) if str(a).strip()]
    if actors:
        return f"{'、'.join(actors)}的高潮动作画面"
    return "高潮动作画面"


def _reference_binding_climax(first_frame_desc: str, spec: dict[str, Any],
                              has_frame: bool = False) -> str:
    """② 参考绑定（climax 单图）：图片1中心的角色为 X，锁身份 + 角色样貌。

    不再写「画面另含未绑定角色…」提示（用户拍板 2026-08-28）：只需声明
    「图片1中心的角色为 X」即可，无名配角由视频模型按画面自行区分。
    画面级动作细节由 [时间分镜] 承载。

    has_frame（I2V 有底图）且没有在场角色名时：身份锁改用「图片1中人物」这种
    不需要名字的写法——缺名的自动帧若只声明职责，模型就没有任何一条约束要求
    沿用图里那张脸，首帧容易换人。

    第 81 刀：[负面约束] 退役后，「人物身份和五官不能发生变化」这条全片锁落在
    这里（② 是身份绑定的位置），且整条提示词只出现一次。
    """
    actors = [str(a).strip() for a in (spec.get("actors") or []) if str(a).strip()]
    if actors:
        lines = [f"图片1中心的角色为{'、'.join(actors)}"]
        lines.append(f"保持 {_identity_gloss(spec, actors)} 的身份、脸部、服装、发型、造型完全一致")
    else:
        desc = _climax_frame_role(spec, first_frame_desc)
        lines = [f"图片1={desc}（唯一参考画面，作为起始帧）"]
        if has_frame:
            lines.append("保持图片1中人物的身份、脸部、服装、发型、造型完全一致")
    lines.append(_IDENTITY_LOCK)
    return "；".join(lines)


def _reference_binding_firstlast(first_frame_desc: str, last_frame_desc: str,
                                 spec: dict[str, Any],
                                 has_first: bool = True, has_last: bool = True) -> str:
    """② 参考绑定（firstlast 双图）：图片1=首帧、图片2=尾帧，职责钉死。

    缺图时诚实标注「无参考图，以文字为准」，不假装有图（R2）。
    身份锁（⑥ 退役后）在这里只写一次。
    """
    f = (first_frame_desc or "").strip() or "楼层开头画面"
    l = (last_frame_desc or "").strip() or "楼层结尾画面"
    if has_first:
        lines = [f"图片1={f}（首帧/起始画面）"]
    else:
        lines = [f"首帧（无参考图，以文字为准）：{f}"]
    if has_last:
        lines.append(f"图片2={l}（尾帧/目标画面）")
    else:
        lines.append(f"尾帧（无参考图，以文字为准）：{l}")
    actors = [str(a).strip() for a in (spec.get("actors") or []) if str(a).strip()]
    if actors:
        lines.append(f"画面角色：{'、'.join(actors)}")
        lines.append(f"保持 {_identity_gloss(spec, actors)} 的身份、脸部、服装、发型、造型完全一致")
    lines.append(_IDENTITY_LOCK)
    return "；".join(lines)


def _reference_binding_transition(prev_tail_desc: str, first_frame_desc: str,
                                  spec: dict[str, Any],
                                  has_prev_tail: bool = True, has_first: bool = True) -> str:
    """② 参考绑定（转场双图）：图片1=上一楼层尾帧（转场起点）、图片2=当前楼层首帧（转场终点）。

    缺图时诚实标注「无参考图，以文字为准」（R2 同套路），不假装有图。
    身份锁（⑥ 退役后）在这里只写一次。
    """
    pt = (prev_tail_desc or "").strip() or "上一楼层尾帧画面"
    ff = (first_frame_desc or "").strip() or "当前楼层首帧画面"
    if has_prev_tail:
        lines = [f"图片1={pt}（上一楼层尾帧/转场起点）"]
    else:
        lines = [f"上一楼层尾帧（无参考图，以文字为准）：{pt}"]
    if has_first:
        lines.append(f"图片2={ff}（当前楼层首帧/转场终点）")
    else:
        lines.append(f"当前楼层首帧（无参考图，以文字为准）：{ff}")
    actors = [str(a).strip() for a in (spec.get("actors") or []) if str(a).strip()]
    if actors:
        lines.append(f"画面角色：{'、'.join(actors)}")
        lines.append(f"保持 {_identity_gloss(spec, actors)} 的身份、脸部、服装、发型、造型完全一致")
    lines.append(_IDENTITY_LOCK)
    return "；".join(lines)


def _meta(duration_hint: int, camera: str, model_name: str = "", size: str = "",
          default_duration: int | None = None) -> str:
    """① 元信息：模型名（透传不硬编码）+ 时长 + 画幅 + 运镜（R3 补三件套）。

    default_duration（提示词模版骨架）：duration_hint 为空时的兜底秒数；
    None/0 ⇒ 仍走内置 _DEFAULT_DURATION（逐字现行为）。
    """
    fallback = default_duration if default_duration else _DEFAULT_DURATION
    dur = int(duration_hint) if duration_hint else fallback
    parts: list[str] = []
    if model_name and str(model_name).strip():
        parts.append(str(model_name).strip())
    parts.append(f"{dur} seconds")
    aspect = _aspect_from_size(size)
    if aspect:
        parts.append(aspect)
    if camera and str(camera).strip():
        parts.append(f"镜头运动={str(camera).strip()}")
    return "，".join(parts)


def _meta_transition(duration_hint: int, camera: str,
                     model_name: str = "", size: str = "",
                     default_duration: int | None = None) -> str:
    """① 元信息（转场形态，坑G）：不硬控时长。

    转场时长不预设死值（用户定调）：duration_hint=0 → 不写秒数（交视频模型/模板
    默认），绝不兑底 _DEFAULT_DURATION；前端提交侧有转场时长（transitionDurationHint）
    才写具体秒数。正片（climax/firstlast）仍走 _meta 的 videoDurationHint。
    default_duration（提示词模版骨架）：仅当模版显式提供时才写（用户选择即为意图）。
    """
    parts: list[str] = []
    if model_name and str(model_name).strip():
        parts.append(str(model_name).strip())
    dur = int(duration_hint or 0)
    if dur <= 0 and default_duration:
        dur = int(default_duration)
    if dur > 0:
        parts.append(f"{dur} seconds")
    else:
        parts.append("时长=视频模型默认（短桥段）")
    aspect = _aspect_from_size(size)
    if aspect:
        parts.append(aspect)
    if camera and str(camera).strip():
        parts.append(f"镜头运动={str(camera).strip()}")
    return "，".join(parts)


def _split_narrative_beats(narrative: str) -> list[str]:
    """剧情逐句切拍：。！？；、换行切句 + 引号句合并归属。

    - 引号内的分隔符不切拍（「开饭了，都过来！」是一个完整对白拍）；
    - 闭引号后紧跟正文且无句点时同样断拍（对白归属对白拍，不把闭引号
      粘到下一事件拍的开头）；
    - 去掉引号包裹片段后仍命中拒答句式的句子整句剔除——主模型拒答句
      （「我不能协助这项请求。」一类）不得流进 [时间分镜]；引号内的
      对白原样保留（「不能满足你」是正常台词，不过滤）。
    """
    text = str(narrative or "")
    punct = "。！？；" + chr(10)
    open_q = "「“『‘"
    close_q = "」”』’"
    sentences: list[str] = []
    start = 0
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in open_q:
            depth += 1
        elif ch in close_q:
            depth = max(0, depth - 1)
            nxt = text[i + 1] if i + 1 < len(text) else ""
            # 仅当闭引号后紧跟正文（字母/汉字，非逗号等停顿标点）才断拍
            if depth == 0 and nxt and re.match(r"[\w]", nxt):
                s = text[start:i + 1].strip()
                if s:
                    sentences.append(s)
                start = i + 1
        elif ch in punct and depth == 0:
            s = text[start:i + 1].strip()
            if s:
                sentences.append(s)
            start = i + 1
        i += 1
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    kept: list[str] = []
    for s in sentences:
        bare = re.sub(r"「[^」]*」|“[^”]*”|『[^』]*』|‘[^’]*’", "", s)
        if bare and prompt_clean.REFUSAL_RE.search(prompt_clean.restore_jailbreak(bare)):
            continue
        kept.append(s)
    return kept


# 对白归属短句（闭引号后紧跟「老周问。」一类）：必须与台词同拍，否则口型同步那一拍
# 与说话人那一拍分家。⚠ 只服务 firstlast 中段，不参与 climax 的任何判据。
_SPEAKER_ATTRIBUTION_VERBS = ("问", "说", "道", "答", "应", "喊")
_ATTRIBUTION_END_PUNCT = "。，：；"
_ATTRIBUTION_MAX_LEN = 14
_QUOTE_CHARS = "「」“”『』‘’"


def _ends_with_speech_verb(text: str) -> bool:
    """剥掉收尾标点后落在说话动词上（`老周问。`／`老周说道：` 同判）。"""
    stem = text.rstrip(_ATTRIBUTION_END_PUNCT)
    return bool(stem) and any(stem.endswith(verb) for verb in _SPEAKER_ATTRIBUTION_VERBS)


def _is_speaker_attribution(sentence: str) -> bool:
    """短叙述句是否为「谁说的」而非一个独立事件拍。"""
    text = sentence.strip()
    return (
        len(text) <= _ATTRIBUTION_MAX_LEN
        and not any(quote in text for quote in _QUOTE_CHARS)
        and _ends_with_speech_verb(text)
    )


def _is_attribution_lead(sentence: str) -> bool:
    """引号前那段（`老周问了一句，「第二句台词。」`）是否为归属而非事件。

    动词不要求收尾——真机的复合对白句里动词后面还跟着「了一句」这类补语。
    判据仍只有「短 + 以标点收尾 + 段里有说话动词」三条，动词之外的实词一概不看，
    所以 `她站起身，把伞挂到门后，「面还得等会儿。」` 这种事件前缀不会被并进上一拍。
    """
    text = sentence.strip()
    cut = min((pos for pos in (text.find(quote) for quote in _QUOTE_CHARS) if pos >= 0), default=-1)
    if cut <= 0:
        return False
    lead = text[:cut].strip()
    return (
        len(lead) <= _ATTRIBUTION_MAX_LEN
        and lead[-1] in _ATTRIBUTION_END_PUNCT
        and any(verb in lead for verb in _SPEAKER_ATTRIBUTION_VERBS)
    )


def _fold_firstlast_mid(
    beats: list[tuple[str, str]], quota: int,
) -> list[tuple[str, str]]:
    """把中段压进 `quota` 拍：与 `_bound_beats` 同口径的均分合并（首尾各占一拍）。

    不能直接复用二元版 `_bound_beats`：那里 tuple 是 (label, desc)、合并的是 desc，
    而这里 (content, extra) 要合并的是 content。分组公式 `total*idx//quota` 与
    `_bound_beats` 逐字一致 ⇒ 拍数 ≤ 秒数时整除边界严格递增，不产生 0s–0s 空窗口；
    组内文本保序去重合并，剧情事实一条不丢。
    """
    if quota <= 0 or len(beats) <= quota:
        return beats
    total = len(beats)
    out: list[tuple[str, str]] = []
    for idx in range(quota):
        lo = total * idx // quota
        hi = total * (idx + 1) // quota
        group = beats[lo:hi]
        if not group:
            continue
        content = "；".join(dict.fromkeys(part for part, _ in group if part))
        extra = "；".join(dict.fromkeys(part for _, part in group if part))
        out.append((content or "主体动作按剧情自然演变", extra))
    return out


def _time_segments(spec: dict[str, Any], duration_hint: int,
                   first_frame_desc: str, last_frame_desc: str,
                   prev_tail_desc: str,
                   has_first: bool = True, has_last: bool = True,
                   skeleton: "VideoSkeleton | None" = None) -> str:
    """④ 时间分镜（firstlast 核心）：首帧→剧情逐句成拍→尾帧，覆盖整段桥段。

    - 首帧段带「上楼层尾帧」衔接上下文（转场判断融入生成，不单独判定）；
    - 中段把 narrative 按句子切拍：每个事件/每句对白各占一拍（引语拍标台词同步）；
      跳过已被 opening 覆盖的开头句、对白归属与台词同拍、超量均分合并不丢剧情
      （第 80 刀，见 `_fold_firstlast_mid`）；
    - 尾帧段收尾定格 + 拉镜头收束；缺图时诚实标注（R2）。
    - 运镜取自镜头语言词汇表，按拍位分配（定场→换镜→收束），不再全程同一「极缓推进」。
    - skeleton（提示词模版骨架）：beat_labels 覆盖三段节奏名、camera_band_overrides
      覆盖运镜档位文案、default_duration 覆盖时长兜底；None = 逐字现行为。
    """
    sk = skeleton or VideoSkeleton()
    fallback_dur = sk.default_duration if sk.default_duration else _DEFAULT_DURATION
    dur = int(duration_hint) if duration_hint else fallback_dur
    narrative = str(spec.get("narrative") or "").strip()
    motion_raw = spec.get("motion")
    motion = int(motion_raw) if isinstance(motion_raw, (int, float)) else 0
    model_cam = str(spec.get("camera") or "").strip()

    f_open = (first_frame_desc or "楼层开头画面").strip()
    l_close = (last_frame_desc or "楼层结尾画面").strip()

    # 首帧段：有图定格首帧 / 无图文字开场；带衔接上下文。
    open_content = f"定格于首帧画面：{f_open}" if has_first else f"以文字首帧开场：{f_open}"
    if prev_tail_desc:
        open_extra = f"承接上一镜头尾帧（{prev_tail_desc}）自然延续，机位/光影/人物状态连贯，无突兀跳切"
    else:
        open_extra = "作为本段独立开场镜头"

    # 中段：剧情逐句成拍（每事件/每对白一镜），引语拍标注台词同步。
    # _split_narrative_beats 负责引号句合并归属 + 拒答句剔除，不把拒答正文编进分镜。
    # 第 80 刀三处修正（只作用 firstlast；climax 走 _climax_time_segments）：
    # ① 开场定格已逐字带上 first_frame_desc（楼层开头部分），中段若从第一句起会把
    #    同一段画面再演一遍——离线真链路模拟里 12s 的前 4 秒都在重复「推门收伞」。
    #    只跳过**开头连续**被 opening 覆盖的那几句，后文有意重复的句子不动。
    # ② 闭引号后紧跟的「老周问。」是对白归属，与台词同拍，否则口型同步与说话人分家；
    #    第 86 刀补另一半：真机更常见的是「归属＋第二句台词」同句，只认纯短句会把归属
    #    整条留给第二句，第一句台词因此没人说过（`_is_attribution_lead`）。
    # ③ 取消 [:8] 硬截断：narrative 全段都得有交代，超量按时长均分合并而非丢弃。
    sentences = _split_narrative_beats(narrative)
    opening = (first_frame_desc or "").strip()
    covered = 0
    while covered < len(sentences) and opening and sentences[covered].strip() in opening:
        covered += 1
    sentences = sentences[covered:]
    merged_sentences: list[str] = []
    for sent in sentences:
        previous = merged_sentences[-1] if merged_sentences else ""
        if previous and ("「" in previous or "“" in previous) and (
                _is_speaker_attribution(sent) or _is_attribution_lead(sent)):
            merged_sentences[-1] = f"{previous}{sent}"
        else:
            merged_sentences.append(sent)
    mid_beats: list[tuple[str, str]] = []
    for sent in merged_sentences:
        content = sent
        if "「" in sent or "“" in sent:
            content += "；台词随口型同步"
        mid_beats.append((content, "与节拍/重音同步" if int(motion) >= 2 else ""))
    if not mid_beats:
        mid_beats = [("主体动作按剧情自然演变", "")]
    # 首尾各占一拍 ⇒ 中段最多 dur-2 拍，总拍数 ≤ dur：`dur*idx//n` 不再整除出空窗口。
    quota = max(0, dur - 2)
    if quota == 0:
        # 时长 <3s（现实下界 4，仅防御）：整段剧情并进开场拍，只留首尾两拍。
        joined = "；".join(dict.fromkeys(content for content, _ in mid_beats if content))
        if joined:
            open_content = f"{open_content}；{joined}"
        mid_beats = []
    else:
        mid_beats = _fold_firstlast_mid(mid_beats, quota)

    # 尾帧段：有图定格尾帧 / 无图文字收尾；收束为下一镜头留过渡点。
    close_content = f"收尾定格于尾帧画面：{l_close}" if has_last else f"以文字尾帧收尾：{l_close}"
    close_extra = "动作自然收束、神态定格，为下一镜头衔接留出自然过渡点"

    beats: list[tuple[str, str, str]] = [(open_content, open_extra, sk.beat_label(0, "开场定格"))]
    for content, extra in mid_beats:
        beats.append((content, extra, sk.beat_label(1, "剧情演变")))
    beats.append((close_content, close_extra, sk.beat_label(2, "收尾定格")))

    lines: list[str] = []
    n = len(beats)
    for idx, (content, extra, label) in enumerate(beats):
        start = dur * idx // n
        end = dur * (idx + 1) // n
        cam = model_cam or _beat_camera(
            int(motion), idx, n, content, sk.camera_band_overrides or None)
        tail = f"（{extra}）" if extra else ""
        lines.append(f"[{start}s–{end}s｜{label}]：{cam}；{content}{tail}")
    return "\n".join(lines)


def _transition_segments(spec: dict[str, Any],
                         prev_tail_desc: str, first_frame_desc: str,
                         has_prev_tail: bool = True, has_first: bool = True,
                         skeleton: "VideoSkeleton | None" = None) -> str:
    """④ 转场分镜（短桥段）：上一楼层尾帧定格 → 自然过渡 → 当前楼层首帧定格。

    短桥段不套正片时长分段（9.5 时长分档）：无时间轴，交模型按短桥段默认节奏。
    缺图时诚实标注「以文字为准」（R2 同套路），不假装有图。
    skeleton：camera_band_overrides 的 0 档覆盖 [过渡] 拍的运镜文案。
    """
    pt = (prev_tail_desc or "").strip() or "上一楼层尾帧画面"
    ff = (first_frame_desc or "").strip() or "当前楼层首帧画面"
    sk = skeleton or VideoSkeleton()
    band0 = sk.camera_band_overrides.get(0) if sk.camera_band_overrides else None
    cam_hint = str(spec.get("camera") or "").strip() or band0 or _CAMERA_SOLO[0]
    lines = [
        f"[转场起点]：{('定格于上一楼层尾帧画面：' + pt) if has_prev_tail else ('以文字重现上一楼层尾帧：' + pt + '（无参考图，以文字为准）')}",
        f"[过渡]：{cam_hint}自然过渡到当前楼层首帧，机位运动连贯、光影衔接、人物状态延续，无突兀跳切",
        f"[转场终点]：{('收束定格于当前楼层首帧画面：' + ff) if has_first else ('以文字收束到当前楼层首帧：' + ff + '（无参考图，以文字为准）')}",
    ]
    return "\n".join(lines)


# ── ⑤ 的卡拍时刻与 ④ 的实际拍界对账（2026-11-05 第 91 刀）───────────────────────
# 病灶（真机 21:27 那一轮 prompt_only 成稿，零云端）：④ 是 `0–3s / 3–6s / 6–10s`，
# ⑤ 却写 `同步=action beats land at 0s, 5s, 9s; nothing else until end`——时刻是提取 LLM
# 自己估的（它看不见 `_climax_time_segments` 里 `dur*idx//n` 的整除结果），编译侧从没核对，
# 于是「卡拍时刻」落在 ④ 第 2、3 拍的**中间**，下游模型只能把音效对齐到不存在的节点上。
# ⚠ 本刀**只改落地，不改判据**：② 提取合同里那句 sync 规则一字不动（它不该知道拍界怎么算），
#   拍界的真源＝④ **已经写出来的那几行**（不是另算一遍，否则两条公式可以各自漂移）。
# 失败面必须窄 ⇒ 只在「整串里恰好一处、由顿号/逗号/斜杠连接的 ≥2 个时刻，且串里没有游离时刻」
# 时对账；无时刻、单时刻（`每 2 秒卡拍`）、两处列表、时刻被 `and` 分成两段 ⇒ 逐字节返回原串。
# 只换数字与紧邻单位，句子其余部分（措辞/标点/句尾）一律不动 ⇒ 幂等。
_BEAT_LINE_RE = re.compile(r"^(\d+)–\d+s")
_BEAT_TIME_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:s\b|秒)")
_BEAT_TIME_RUN_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:s\b|秒)(?:\s*[、,，/]\s*\d+(?:\.\d+)?\s*(?:s\b|秒))+"
)


def beat_starts_from_segments(segments: str) -> list[int]:
    """从 ④ 的成品文本里读出每拍的起点秒数（`0–3s｜…` ⇒ 0）。解析不出 ⇒ 空表。"""
    out: list[int] = []
    for line in (segments or "").splitlines():
        m = _BEAT_LINE_RE.match(line.strip())
        if m:
            out.append(int(m.group(1)))
    return out


def _beat_time_unit(token: str) -> str:
    return re.sub(r"^\d+(?:\.\d+)?", "", token).strip() or "s"


def reconcile_sync_with_beats(sync: str, beat_starts: list[int]) -> str:
    """把 sync 里那一串卡拍时刻逐位换成 ④ 的真实拍点；任何不确定 ⇒ 原样返回。

    时刻数≠拍数时整串换成真实拍点（拍点是唯一真源，多出来的时刻本来就无处可落）。
    """
    text = sync or ""
    starts = [int(v) for v in (beat_starts or [])]
    if not starts:
        return text
    total = _BEAT_TIME_RE.findall(text)
    if len(total) < 2:
        return text
    runs = list(_BEAT_TIME_RUN_RE.finditer(text))
    if len(runs) != 1:
        return text
    run = runs[0]
    tokens = _BEAT_TIME_RE.findall(run.group(0))
    if len(tokens) != len(total):  # 游离时刻（`0s、5s and 9s`）⇒ 逐位换会留下错位的一格
        return text
    spans = [m.span() for m in _BEAT_TIME_RE.finditer(run.group(0))]
    sep = run.group(0)[spans[0][1]:spans[1][0]] or "、"
    units = [_beat_time_unit(t) for t in tokens]
    rebuilt = sep.join(f"{v}{units[min(i, len(units) - 1)]}" for i, v in enumerate(starts))
    return text[:run.start()] + rebuilt + text[run.end():]


def _audio_hint(
    spec: dict[str, Any],
    audio_lines: list[Any] | None = None,
    *,
    include_lines: bool | None = None,
    beat_starts: list[int] | None = None,
) -> str:
    """⑤ 音频：**只有效果音**（2026-09-20 用户定案）。

    台词/旁白归「多元数据插入」里的**独立音频生成功能**，视频提示词一律不写；
    音乐结构同样不写（用户口径「只有效果音」）。两项数据仍留在 `spec["audio_design"]`
    里，只由 `_VIDEO_AUDIO_EMIT_MUSIC` / `_VIDEO_AUDIO_EMIT_LINES` 控制是否渲染。
    `audio_lines`（comfy_audio 对白）参数**保留仅为兼容**，不再进提示词。
    `beat_starts`（2026-11-05 第 91 刀）：④ 已经落地的那几拍的真实起点；给了就把
    `sync` 里的卡拍时刻与之对账（见 `reconcile_sync_with_beats`）。None／空 ⇒ 不碰。
    """
    audio_raw = spec.get("audio_design")
    design: dict[str, Any] = dict(audio_raw) if isinstance(audio_raw, dict) else {}
    music = str(design.get("music") or "").strip()
    sfx: list[str] = []
    for item in design.get("sfx") or []:
        s = str(item).strip()
        if s and s not in sfx:
            sfx.append(s)
    lines: list[str] = []
    for ln in design.get("lines") or []:
        if isinstance(ln, dict):
            text = str(ln.get("text") or "").strip()
            speaker = str(ln.get("speaker") or "").strip()
        else:
            text = str(ln).strip()
            speaker = ""
        if text:
            # 台词时点：at_s 是提取 LLM 按剧情位置推算的『什么时候说』（秒）——
            # 有就标进去，视频模型才知道这句落在哪个情节位置；缺失诚实省略。
            at = ln.get("at_s") if isinstance(ln, dict) else None
            at_txt = ""
            if isinstance(at, (int, float)) and not isinstance(at, bool):
                at_txt = f"{at:g}s｜"
            lines.append(f"{at_txt}{speaker}：{text}" if speaker else f"{at_txt}{text}")
    sync = str(design.get("sync") or "").strip()
    if beat_starts:
        sync = reconcile_sync_with_beats(sync, beat_starts)

    # 无 audio_design 时：台词从 audio_lines 参数兜底
    if not design and audio_lines:
        for ln in audio_lines:
            if isinstance(ln, dict):
                text = str(ln.get("text") or "").strip()
                speaker = str(ln.get("speaker") or "").strip()
            else:
                text = str(ln).strip()
                speaker = ""
            if text:
                lines.append(f"{speaker}：{text}" if speaker else text)

    del audio_lines, include_lines  # 兼容参数，按 2026-09-20 口径不再进提示词
    parts: list[str] = []
    if _VIDEO_AUDIO_EMIT_MUSIC:
        parts.append(f"音乐={music or '按本集风格铺底'}")
    if sfx:
        parts.append(f"音效={'、'.join(sfx)}")
    else:
        parts.append("音效=环境声")
    if lines and _VIDEO_AUDIO_EMIT_LINES:
        parts.append(f"台词={'；'.join(lines)}")
    parts.append(f"同步={sync or '视觉事件卡拍'}")
    return "；".join(parts)


def _climax_action_beat(spec: dict[str, Any]) -> str:
    """高潮动作延伸：优先用 action_sequence（主模型同轮输出的「定格动作 → 剧情描述的
    完整动作」时序，与图片提示词同源），缺失回退画面级要素
    （subjects/visual_facts/composition），再回退中文 narrative 原文。"""
    seq = spec.get("action_sequence")
    if isinstance(seq, list) and seq:
        beats: list[str] = []
        for item in seq:
            if not isinstance(item, dict):
                continue
            beat = str(item.get("beat") or "").strip()
            desc = str(item.get("desc") or "").strip()
            if not desc:
                continue
            beats.append(f"{beat}: {desc}" if beat else desc)
        if beats:
            return "；".join(beats)
    # 动作段只承载「动作/姿态/接触关系」，不得用 subjects.description（外貌）
    # 兜底——否则动作段会退化成整段外貌描述（P1/P5 缺陷）。外貌属于 [主体/场景]。
    parts: list[str] = []
    for fact in spec.get("visual_facts") or []:
        if isinstance(fact, dict):
            f = str(fact.get("fact") or "").strip()
            if f and f not in parts:
                parts.append(f)
    composition = str(spec.get("composition") or "").strip()
    if composition and composition not in parts:
        parts.append(composition)
    if parts:
        return ", ".join(parts)
    return str(spec.get("narrative") or "").strip() or "动作瞬间"


def _climax_fallback_beats(spec: dict[str, Any]) -> list[tuple[str, str]]:
    """无 action_sequence 时的动作段兜底：画面级要素优先（单拍，旧行为），
    否则按句子切分 narrative 成多拍，避免「动作断链」退化成整段单拍。"""
    single = _climax_action_beat(spec)
    narrative = str(spec.get("narrative") or "").strip()
    # 命中画面级要素（visual_facts/composition）或 narrative 为空时保持单拍，不臆造分镜
    if single != narrative or not narrative:
        return [("主体动作", single)]
    sentences = _split_narrative_beats(narrative)
    if not sentences:
        # narrative 整段为拒答/空壳：回退诚实占位，不把拒答句编进 [时间分镜]
        return [("主体动作", "主体动作按剧情自然演变")]
    if len(sentences) == 1:
        return [("主体动作", sentences[0])]
    labels = ("定格起点", "主体动作", "延伸", "收尾")
    out: list[tuple[str, str]] = []
    for idx, sent in enumerate(sentences[:8]):
        label = labels[idx] if idx < len(labels) else "延伸"
        out.append((label, sent))
    return out


def _climax_beat_plan(spec: dict[str, Any]) -> list[tuple[str, str]]:
    """④ 的分镜计划（裁剪/折叠**之前**）：优先 action_sequence，缺失回退画面级要素或
    narrative 分句。抽出来是因为 ① 的循环节拍也要看「末拍到底停在哪儿」——那不能只认
    action_sequence，narrative 兜底同样会切出多拍（第 82 刀）。"""
    beats: list[tuple[str, str]] = []
    seq = spec.get("action_sequence")
    if isinstance(seq, list) and seq:
        for item in seq:
            if not isinstance(item, dict):
                continue
            beat = str(item.get("beat") or "").strip()
            desc = str(item.get("desc") or "").strip()
            if desc:
                beats.append((beat or "节拍", desc))
    return beats or _climax_fallback_beats(spec)


def _climax_camera(spec: dict[str, Any], motion: int) -> str:
    """运镜：主模型 camera 优先（直接采用，不叠词汇表）；否则用档位代表句。

    `_climax_time_segments` 会在此基础上按拍位替换为词汇表句（定场/换镜/收束），
    此处只返回单拍代表句供兼容旧调用。
    """
    camera = str(spec.get("camera") or "").strip()
    if camera:
        return camera
    return _CAMERA_SOLO[_camera_band(motion)]


def _climax_fx(motion: int) -> str:
    """纯特效：按 motion 强度给动态手法（节拍同步由 _climax_beat_sync 单独承担）。"""
    if motion >= 3:
        return "能量爆发式特效，画面错位"
    if motion >= 2:
        return "强化动作张力"
    return ""


def _hold_beat_fx(motion: int) -> str:
    """静机位档的逐拍动态条款：默认不写（微动属全片规则，已在 ① 声明一次）。

    只保留高运动量（motion≥3）的能量特效，并把旧文案里的「画面错位」换成「画面保持稳定」——
    错位＝叫模型抖动整格画面，与固定机位冲突；特效留在主体身上。
    """
    return "能量爆发式特效，画面保持稳定" if motion >= 3 else ""


def _climax_beat_sync(motion: int) -> str:
    """节拍同步：按 motion 强度给节奏手法。"""
    if motion >= 2:
        return "与节拍/重音同步"
    return "按自然节奏推进"


# ── hold 档「构型迁移短语」裁剪（2026-11-04 第 79 刀 1）────────────────────────
# 首帧＝高潮图那一格，它的构型是**前提**而不是**剧情**：④ 里「把某条腿抬起来／踩上矮凳／
# 弓起身／衣料滑出」这类**迁移过程**是在要求模型于首帧之后的整段时间里重新构图——首帧锁就是这么失效的，
# 也是「视频像有转场」的直接来源。往复与物理跟随（thrust/sway/spray/drip）不改构型，留在时间轴。
# 只按从句剔短语，不整句重写：全被剔空的拍退出 ④，一整段都空则回落原句（宁可留迁移，
# 也不让 ④ 凭空少一整拍）。代价有界——④ 少一个短语，兜住那个构型的是首帧本身＋ ① 的
# 「首帧必须逐字复现图片1的机位、构图、人物姿态与服装」；🔴 不是 ③：③ 取外貌＋场景，
# `profile_prompt` 在本模块一次都没被读（2026-11-04 第 82 刀更正旧注释的误述）。
# 主语随主句一起被剔的那种拍由 `_prune_beat_action` 补回指首帧。
# ⚠ 词表纪律：英文必须**带方向小品词**才算迁移。`standing`／`gripping` 单独出现是既成状态，
#   图里就有，剔掉等于删画面事实；`stand up`／`pull off` 才是迁移。中文按词本身判（抬起／踩上／
#   弓起），不接 `\b`——CJK 全是 word char，边界会失效。
_POSE_SHIFT_EN: tuple[str, ...] = (
    r"\b(?:lift|lifts|lifting|raise|raises|raising)\b[^,.;]{0,40}\b(?:leg|legs|thigh|thighs|foot|feet|arm|arms|hand|hands|hip|hips|knee|knees)\b",
    r"\b(?:leg|legs|thigh|thighs|foot|feet|knee|knees)\b[^,.;]{0,30}\b(?:lift|rais|plant|rest|tuck|hook|pin)",
    r"\bplant(?:s|ed|ing)?\b[^,.;]{0,30}\b(?:foot|feet|leg|legs|on)\b",
    r"\b(?:bend|bends|bent|arch|arches|arched|lean|leans|leaning|twist|twists|twisted)\b[^,.;]{0,30}\b(?:forward|backward|back|over|down|up|body|waist|sharply|aside)\b",
    r"\b(?:slip|slips|slipped|slid|slide|slides|sliding|pull|pulls|pulled|push|pushes|pushed|take|takes)\b[^,.;]{0,30}\b(?:out|off|down|up|away|aside|open)\b",
    r"\b(?:stand|stands|sit|sits|kneel|kneels|lie|lies|lay)\b[^,.;]{0,12}\b(?:up|down|back)\b",
    r"\b(?:step|steps|stepped|walk|walks|walked|run|runs|rush|rushes|march(?:es|ed)?)\b[^,.;]{0,15}\b(?:in|into|out|away|to|toward|onto|off|up|down|back)\b",
    r"\b(?:turn|turns|turned|roll|rolls|rolled|spin|spins)\b[^,.;]{0,15}\b(?:around|over|to|back|away)\b",
    r"\b(?:open|opens|opening|close|closes|closing)\b[^,.;]{0,25}\b(?:door|doors|window|windows|eyes|mouth|legs|curtain|curtains|zip)\b",
    r"\b(?:put|puts|placing)\b[^,.;]{0,25}\b(?:on|onto|over|against)\b",
)
_POSE_SHIFT_ZH: tuple[str, ...] = (
    "抬起", "提起", "踩上", "踏上", "跨上", "坐上", "站起", "起身", "躺下", "趴下",
    "跪下", "弯下", "俯下", "弓起", "转过身", "走到", "走向", "滑出", "滑下", "脱下",
    "褪下", "拉开", "推开", "掀开", "撑起", "放到", "压在", "抵在",
)
_POSE_SHIFT = re.compile("|".join((*_POSE_SHIFT_EN, *_POSE_SHIFT_ZH)), re.IGNORECASE)
# 从句切分：捕获分隔符，只取偶数位（奇数位是分隔符本身）。
_CLAUSE_SEP = re.compile(r"([,;，；、。])")
# 剩下从句用什么连接：中文逗号 vs 英文逗号（按源文本判，不混排）。
_CJK = re.compile(r"[一-鿿]")


def _drop_pose_shift_clauses(desc: str) -> str:
    """剔掉一句动作文本里的「构型迁移」从句，返回剩下的往复/状态从句。

    返回空串 = 这一句从头到尾只在讲迁移（这拍该退出 ④）。
    """
    text = (desc or "").strip()
    if not text:
        return ""
    kept = [part.strip() for idx, part in enumerate(_CLAUSE_SEP.split(text))
            if idx % 2 == 0 and part.strip() and not _POSE_SHIFT.search(part)]
    if not kept:
        return ""
    joiner = "，" if _CJK.search(text) else ", "
    return joiner.join(kept)


# 主句被剔时补回的主语落点：不重述迁移过程，只把「谁」指回首帧那一格。
_FRAME_ANAPHORA = "承接图片1的同一构型"
# 主语短语的长度上界：超过它说明切点左侧不只是一个主语，交回原句更诚实。
_SUBJECT_HEAD_MAX = 60
# 结尾停在介词／连词上＝切到了短语中部，左侧不是完整主语。
_SUBJECT_TAIL_BAD = re.compile(
    r"(?:\b(?:and|or|but|with|of|to|in|on|at|by|for|as|that|which|while|then)\b"
    r"|和|与|而|然后|把|被|正|就|又|再)$",
    re.IGNORECASE,
)


def _leading_subject(clause: str) -> str:
    """迁移动词短语**之前**那段＝这句的主语；抽不出返回空串。

    `_POSE_SHIFT_EN` 每条都是「动词＋方向小品词＋身体部位」的整短语，命中点即谓语开头，
    所以切点左侧天然就是主语（`A tall woman | bends forward over...`）。
    """
    match = _POSE_SHIFT.search(clause)
    if not match:
        return ""
    head = clause[:match.start()].strip(" \t,;，；、。:")
    if not head or len(head) > _SUBJECT_HEAD_MAX or _SUBJECT_TAIL_BAD.search(head):
        return ""
    return head


# 第 84 刀（2026-11-05）：第一人称下**不能当画面施动者**的「整人指称」。
# 真机实锤（同一轮试跑成稿 ④ 第 4 拍）：`The man，承接图片1的同一构型，driving deeper…`
# ——第 83 刀补的主语把**叙事者钉成了画面里的第二个完整人物**，而首帧图片1 里他只有从画框
# 下缘进入的肢体；① 又要「逐字复现图片1」⇒ 模型只能凭空补一个全身，或忽略首帧（转场感）。
# ⚠ 只认「**整人**指称」：代词 `he/him/himself`／`他`，以及**定指＋人物名词**（`the man`、
#   `那名男子`）。这类短语没有任何可辨识外貌，在第一人称帧里不可能另有其人——本链的在场
#   角色按图片侧「多人分述」条款必须写成「可辨识身份＋衣着」（真稿即 `A tall woman with
#   long dark-blonde hair`），所以**带描述的不定指不动**（`a man behind her` 交给 ② 提取合同）。
# ⚠ **肢体所有格不动**（`his hands`／`her knees`）：第一人称下「他的手」正是**该入画**的形态
#   （图片侧第 43/47/65 刀同源），剔掉它会把可见肢体也抹了。
# ⚠ 零回归锁：`first_person=False`（第三人称，或调用方没给人称）⇒ 本闸**完全不参与**，
#   逐字节等于第 83 刀行为。
_POV_WHOLE_PERSON_RE = re.compile(
    r"^\s*(?:"
    r"(?:he|him|himself)\b"
    r"|(?:the|that|this)\s+(?:man|men|guy|guys|boy|boys|fellow|fellows|youth|figure|person)\b"
    r"|(?:他(?![的]))"
    r"|(?:那|这)(?:名|个)?(?:男子|男人|青年|少年)"
    r")",
    re.IGNORECASE,
)
# 第 90 刀：整人指称被剔之后，这一拍的**归属主语**（不再是空着）。
# 第 84 刀那一支写成 `f"{_FRAME_ANAPHORA}，{kept}"`＝把「谁在做」整条抹掉，只留回指。
# 当时的取舍记在 docstring 里：「代价是这一拍的动作没有明示施动者」。用户 2026-11-05 明确
# 不接受这个代价：「肯定是要解释信息归属于哪个对象的，短句是不够用的」——没有施动者的那一拍
# 模型只能把动作挂到画面里已有的人物身上（于是她的膝盖／她的乳房去「driving deeper」了）。
# ⚠ 主语的名词中心是**肢体**、不是人：`镜头后面那一位伸进画面的肢体`——「镜头后面那一位」
#   作定语说明归属，「伸进画面的肢体」作中心语保证它仍只以局部在场（与 ① 的
#   `_POV_LIMB_OWNER_CLAIM` 同一口径，两句互为解释，不新造第三种说法）。
# ⚠ 本短语**自带归属**，不依赖 ① 是否列了肢体事实：本帧没有肢体证据时 ① 不写主句，
#   若这里再用一个需要 ① 定义的短回指就成了悬空指代。
_POV_BEAT_ACTOR = "镜头后面那一位伸进画面的肢体"


def _prune_beat_action(desc: str, *, first_person: bool = False) -> str:
    """逐拍裁剪＋施动者护栏（第 82/83/84/90 刀）。

    `_drop_pose_shift_clauses` 只保证「不把迁移写回时间轴」，不保证「剩下的句子还有主语」：
    最常见的形态是**带主语的主句本身就是迁移**，剔完只剩分词／介词引导的从句——离线重放
    真稿入参时 4 拍里 3 拍如此，模型收到的是没有主语的动作，只能自己猜由谁在做。
    第 83 刀把回指写成「谁 + 承接图片1的同一构型 + 剩下从句」，构图连续性与施动者同时补回。
    抽不出主语时**保留原句**（不回落＝交出一句无主语动作，模型凭空补人的代价比留一个迁移
    短语更大——首帧已由 ① 逐字声明复现，迁移短语至多让动作早一拍发生）。
    整句皆迁移仍返回空串＝这一拍退出 ④。

    第 84 刀：`first_person` 为真且主语是「整人指称」⇒ 不能把 `The man` 这样的主语写回去
    （那会把叙事者钉成画面里的第二个全身，与 ①「逐字复现图片1」直接冲突）。
    第 84 刀当时的做法是**只补回指、不补主语**；第 90 刀按用户判据改为**补归属主语**
    `_POV_BEAT_ACTOR`——施动者说清楚（属于镜头后面那一位），形态仍只是伸进画面的肢体，
    两个目标不再二选一。
    """
    text = (desc or "").strip()
    kept = _drop_pose_shift_clauses(text)
    if not kept:
        return ""
    clauses = [p.strip() for idx, p in enumerate(_CLAUSE_SEP.split(text))
               if idx % 2 == 0 and p.strip()]
    if not clauses or not _POSE_SHIFT.search(clauses[0]):
        return kept  # 主句不是迁移 ⇒ 主语本来就在剩下的从句里，不动
    subject = _leading_subject(clauses[0])
    if not subject:
        return text
    if first_person and _POV_WHOLE_PERSON_RE.search(subject):
        return f"{_POV_BEAT_ACTOR}，{_FRAME_ANAPHORA}，{kept}"
    return f"{subject}，{_FRAME_ANAPHORA}，{kept}"


def _bound_beats(beats: list[tuple[str, str]], dur: int) -> list[tuple[str, str]]:
    """把拍数压进时长内：每拍至少 1 秒（2026-11-04 修「零时长死拍」）。

    旧切法 `start = dur*idx//n` 在 `n > dur` 时相邻边界整除到同一个整数，产出
    `0–0s / 1–1s / 3–3s` 这类**空窗口**——模型对 0 秒的拍只能忽略或乱并，
    8 拍挤 5 秒还各带一句互斥机位，正是「镜头到处转」的第二个来源。
    超量时按顺序**均分成 dur 组**（`n ≤ len` ⇒ 每组 ≥1 拍，且整除边界严格递增），
    组内动作文本按保序去重合并，不丢动作事实。
    """
    if dur <= 0 or len(beats) <= dur:
        return beats
    total = len(beats)
    out: list[tuple[str, str]] = []
    for idx in range(dur):
        lo = total * idx // dur
        hi = total * (idx + 1) // dur
        group = beats[lo:hi]
        if not group:
            continue
        label = next((beat for beat, _ in group if beat), "节拍")
        text = "；".join(dict.fromkeys(desc for _, desc in group if desc))
        out.append((label, text or "主体动作按剧情自然演变"))
    return out


def _climax_time_segments(spec: dict[str, Any], duration_hint: int,
                          skeleton: "VideoSkeleton | None" = None,
                          has_frame: bool = False,
                          contract: ClimaxCameraContract | None = None) -> str:
    """④ 时间分镜（climax）：把高潮动作切成 0–Xs / X–Ys / … 逐拍段，
    每段 = 运镜 + 主体动作 + 特效 + 节拍同步。

    不写拍名（2026-11-05 第 83 刀）：`定格起点／延伸／收尾` 是提取层的**内部流程词**，
    视频模型读不出含义，只会占掉每拍的字数预算；逐拍那句机位文案才是有效指令。
    `action_sequence[].beat` 仍留在提取合同里（排序与 `_bound_beats` 并组要用），只是不进提示词。

    运镜取自「机位契约」（2026-11-04）：全片**一句**机位逐拍复用——`action_hold` 固定
    机位（不吃档位词表/模型运镜句），`scene_reveal` 一条连续运动。自定义模版显式锁了
    当前档（`camera_band_overrides`）时用户意图优先，仍走锁定的那句。
    身份/五官一致性不再逐拍重申，统一收进 ② [参考绑定]（第 81 刀起，全片只出现一次）。

    拍数受时长上界约束（见 `_bound_beats`）：`has_frame`（I2V）时首拍不重新定场。

    优先 action_sequence（定格起点/延伸/收尾的完整动作，与图片提示词同源）；
    缺失回退单一动作瞬间（画面级要素或 narrative）——取拍逻辑见 `_climax_beat_plan`，
    ① 的循环节拍与 ④ 用的是同一份计划。每段按总时长均分。
    skeleton：default_duration 覆盖时长兜底、camera_band_overrides 覆盖运镜档位。
    hold 档 + 有首帧时逐拍先过 `_prune_beat_action`（构型迁移短语退出时间轴，
    主语被主句一起带走的那一拍补一句回指图片1，④ 不留无主语从句）。
    """
    sk = skeleton or VideoSkeleton()
    fallback_dur = sk.default_duration if sk.default_duration else _DEFAULT_DURATION
    dur = int(duration_hint) if duration_hint else fallback_dur
    motion = int(spec.get("motion") or 0)
    model_cam = str(spec.get("camera") or "").strip()
    ctr = contract or resolve_climax_shot_intent(spec, "", has_frame)
    # 模版显式锁档 > 契约（契约自己已决定吃不吃模型运镜句）> 逐拍换镜已彻底退出 climax
    locked_band = (sk.camera_band_overrides or {}).get(_camera_band(motion))
    cam = locked_band or _climax_beat_camera(ctr, model_cam)
    fx, sync = ctr.beat_fx, ctr.beat_sync

    beats = _climax_beat_plan(spec)
    if ctr.holds_camera and has_frame:
        pov = _first_person_view(spec)
        pruned = [(beat, _prune_beat_action(desc, first_person=pov)) for beat, desc in beats]
        beats = [pair for pair in pruned if pair[1]] or beats
    beats = _bound_beats(beats, dur)

    lines: list[str] = []
    n = len(beats)
    for idx, (_, action) in enumerate(beats):
        start = dur * idx // n
        end = dur * (idx + 1) // n
        parts = [p for p in (cam, action, fx, sync) if p]
        lines.append(f"{start}–{end}s｜{'；'.join(parts)}")
    return "\n".join(lines)


def _assemble_blocks(blocks: dict[str, str], skeleton: "VideoSkeleton | None") -> str:
    """按骨架顺序拼接区块；无骨架 = 既有固定顺序（逐字现行为）。"""
    order = (skeleton or VideoSkeleton()).order()
    return "\n\n".join(blocks[key] for key in order if blocks.get(key))


def compile_climax_video_prompt(
    spec: dict[str, Any],
    *,
    style_prefix: str = "",
    duration_hint: int = 0,
    camera: str = "",
    model_name: str = "",
    size: str = "",
    first_frame_desc: str = "",
    audio_lines: list | None = None,
    has_frame: bool = False,
    pending_frame: bool = False,
    skeleton: "VideoSkeleton | None" = None,
    contract: ClimaxCameraContract | None = None,
) -> str:
    """高潮点·五段式（2026-11-04 第 81 刀：⑥ 负面约束退役）。
    ① 元信息 ② 参考绑定 ③ 主体/场景 ④ 时间分镜 ⑤ 音频。
    风格声明已停用（有参考图定调，style_prefix 仅兼容）。

    动作段用 [时间分镜] 逐拍切段（0–Xs / X–Ys / …），不再用「定格起点/延伸/收尾」
    长句。has_frame=True 时在元信息标注「使用输入图片作为准确起始帧」（I2V）。
    pending_frame：提示词在成图之前编译、由下游链把这一张图挂成首帧（自动插画链的形状）
    ——此时同样按 I2V 写首帧声明，否则提示词会把自己当文生视频，首帧自由构图。
    skeleton（提示词模版骨架）：区块标题/顺序、meta_note、
    default_duration；None = 逐字现行为。
    contract（机位契约）：None ⇒ 就地按 `size` + spec 判定（见 resolve_climax_shot_intent）；
    ① 的契约声明与逐拍机位、动态条款由它决定，④ 的构型迁移短语在 hold 档由它裁剪。
    `action_hold` 档下预设的「镜头运动」不再写进 ①（写了就与「机位静止」自相矛盾）。
    """
    sk = skeleton or VideoSkeleton()
    will_have_frame = bool(has_frame or pending_frame)
    ctr = contract or resolve_climax_shot_intent(spec, size, will_have_frame)
    blocks: dict[str, str] = {}
    meta = _meta(duration_hint, "" if ctr.holds_camera else camera,
                 model_name, size, sk.default_duration)
    frame_note = _FRAME_FIDELITY_NOTE if will_have_frame else ""
    note = "；".join(p for p in (*ctr.notes, sk.meta_note) if p)
    note = f"；{note}" if note else ""
    blocks[SECTION_META] = f"[{sk.title(SECTION_META, '元信息')}]：{meta}{frame_note}{note}"
    blocks[SECTION_BINDING] = (
        f"[{sk.title(SECTION_BINDING, '参考绑定')}]："
        f"{_reference_binding_climax(first_frame_desc, spec, will_have_frame)}"
    )
    subject = _subject_scene(spec)
    if subject:
        blocks[SECTION_SUBJECT] = f"[{sk.title(SECTION_SUBJECT, '主体/场景')}]：{subject}"
    segments = _climax_time_segments(spec, duration_hint, sk, will_have_frame, ctr)
    blocks[SECTION_SEGMENTS] = (
        f"[{sk.title(SECTION_SEGMENTS, '时间分镜')}]：\n"
        + segments
    )
    # climax：高潮定格时刻对白通常已说完（用户定稿 2026-08-28）——动作窗口不带台词，
    # 无论来源（audio_design.lines / comfy_audio 兜底）一律不列。
    # 第 91 刀：⑤ 的卡拍时刻按**上面这段 ④** 的真实拍点对账（拍界只有一份真源＝已落地的行）。
    audio = _audio_hint(spec, audio_lines, include_lines=False,
                        beat_starts=beat_starts_from_segments(segments))
    blocks[SECTION_AUDIO] = f"[{sk.title(SECTION_AUDIO, '音频')}]：{audio}"
    # ⑥ 负面约束**不产出**（第 81 刀）：这条链的节点只有 `positive` 一路输入，
    # 「worst quality / extra fingers」这类词进模型就是正面内容；身份锁已折进 [参考绑定]。
    return _assemble_blocks(blocks, sk)


def compile_firstlast_video_prompt(
    spec: dict[str, Any],
    *,
    style_prefix: str = "",
    duration_hint: int = 0,
    camera: str = "",
    model_name: str = "",
    size: str = "",
    first_frame_desc: str = "",
    last_frame_desc: str = "",
    prev_tail_desc: str = "",
    has_first: bool = True,
    has_last: bool = True,
    audio_lines: list | None = None,
    skeleton: "VideoSkeleton | None" = None,
) -> str:
    """首尾帧·剧情影片：五段式（H3 骨架；⑥ 负面约束已退役，见 climax 同处注释）。

    区块：① 元信息 ② 双图绑定 ③ 主体/场景 ④ 时间分镜 ⑤ 音频。
    风格声明已停用（有参考图定调）。has_first/has_last 标识是否真的提供了首/尾帧图（R2 缺图守卫）。
    skeleton（提示词模版骨架）：区块标题/顺序、beat_labels、camera_band_overrides、
    default_duration、meta_note；None = 逐字现行为。
    """
    sk = skeleton or VideoSkeleton()
    blocks: dict[str, str] = {}
    note = f"；{sk.meta_note}" if sk.meta_note else ""
    blocks[SECTION_META] = (
        f"[{sk.title(SECTION_META, '元信息')}]："
        f"{_meta(duration_hint, camera, model_name, size, sk.default_duration)}{note}"
    )
    blocks[SECTION_BINDING] = (
        f"[{sk.title(SECTION_BINDING, '参考绑定')}]："
        f"{_reference_binding_firstlast(first_frame_desc, last_frame_desc, spec, has_first, has_last)}"
    )
    subject = _subject_scene(spec)
    if subject:
        blocks[SECTION_SUBJECT] = f"[{sk.title(SECTION_SUBJECT, '主体/场景')}]：{subject}"
    blocks[SECTION_SEGMENTS] = (
        f"[{sk.title(SECTION_SEGMENTS, '时间分镜')}]：\n"
        + _time_segments(spec, duration_hint, first_frame_desc, last_frame_desc,
                         prev_tail_desc, has_first, has_last, sk)
    )
    blocks[SECTION_AUDIO] = f"[{sk.title(SECTION_AUDIO, '音频')}]：{_audio_hint(spec, audio_lines)}"
    return _assemble_blocks(blocks, sk)


def compile_transition_video_prompt(
    spec: dict[str, Any],
    *,
    style_prefix: str = "",
    duration_hint: int = 0,
    camera: str = "",
    model_name: str = "",
    size: str = "",
    prev_tail_desc: str = "",
    first_frame_desc: str = "",
    has_prev_tail: bool = True,
    has_first: bool = True,
    skeleton: "VideoSkeleton | None" = None,
) -> str:
    """转场视频·短桥段：上一楼层尾帧 → 当前楼层首帧 的自然过渡。

    五段式骨架（对齐 firstlast；⑥ 负面约束已退役，见 climax 同处注释）：
    ① 元信息 ② 参考绑定（图片1=上尾帧、图片2=当前首帧）③ 主体/场景
    ④ 转场分镜 ⑤ 音频。风格声明已停用（有参考图定调）。

    坑G：时长不预设死值——duration_hint=0 时不写秒数（_meta_transition 交视频模型
    默认），不兑底 _DEFAULT_DURATION；前端提交侧有转场时长才写。
    skeleton（提示词模版骨架）：default_duration 仅显式提供时才写（用户选择即为意图）。

    has_prev_tail/has_first 标识是否真的提供了上尾帧图/当前首帧图（R2 缺图守卫）。
    """
    sk = skeleton or VideoSkeleton()
    blocks: dict[str, str] = {}
    note = f"；{sk.meta_note}" if sk.meta_note else ""
    blocks[SECTION_META] = (
        f"[{sk.title(SECTION_META, '元信息')}]："
        f"{_meta_transition(duration_hint, camera, model_name, size, sk.default_duration)}{note}"
    )
    blocks[SECTION_BINDING] = (
        f"[{sk.title(SECTION_BINDING, '参考绑定')}]："
        f"{_reference_binding_transition(prev_tail_desc, first_frame_desc, spec, has_prev_tail, has_first)}"
    )
    subject = _subject_scene(spec)
    if subject:
        blocks[SECTION_SUBJECT] = f"[{sk.title(SECTION_SUBJECT, '主体/场景')}]：{subject}"
    blocks[SECTION_SEGMENTS] = (
        f"[{sk.title(SECTION_SEGMENTS, '转场分镜')}]：\n"
        + _transition_segments(spec, prev_tail_desc, first_frame_desc,
                               has_prev_tail, has_first, sk)
    )
    blocks[SECTION_AUDIO] = f"[{sk.title(SECTION_AUDIO, '音频')}]：{_audio_hint(spec, None)}"
    return _assemble_blocks(blocks, sk)


def _clean_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """对 scene_spec 文本字段做破甲标记还原（复用共享清洗规则 prompt_clean）。

    图像生成的防拦截是两层：① restore_jailbreak 还原 @()@ 破甲标记（纯函数，本层）；
    ② _apply_regex(IMAGE_PROMPT) 用户正则清洗（接线层，需 ctx，不在纯函数边界内）。

    本模块只做第①层兜底，统一走 prompt_clean.clean_spec_text_fields，
    避免 @(x)@ 残留进视频提示词。
    """
    return prompt_clean.clean_spec_text_fields(spec)


def build_video_request(
    *,
    mode: str,
    spec: dict[str, Any],
    video_config: dict[str, Any],
    preset: dict[str, Any] | None = None,
    first_frame: str = "",
    last_frame: str = "",
    first_frame_desc: str = "",
    last_frame_desc: str = "",
    prev_tail_desc: str = "",
    audio_lines: list | None = None,
    pending_frame: bool = False,
    skeleton: "VideoSkeleton | None" = None,
) -> dict[str, Any]:
    """组装「上交给视频模型的参数」（dry-run，不提交）。

    对齐 video_gen 的 generate / generate_with_images 签名：
    - 文生：JSON {model, prompt, size}
    - 图生：multipart image[] + {model, prompt, size}

    关键区分（两层，必须一致）：
    - 图地址（first_frame / last_frame）→ 进 images[] 数组，按顺序对应「图片1/图片2」。
    - 图职责描述（first_frame_desc / last_frame_desc）→ 进 prompt 的参考绑定文字
      （H3 要求写「图片1=首帧」这类职责，不是地址）。
    - 缺图时产出 warnings + 诚实降级措辞（R2）。

    mode="transition"（W3 转场视频，短桥段）参数映射：
    - first_frame = 上一楼层尾帧图地址（图片1，转场起点），
      last_frame = 当前楼层首帧图地址（图片2，转场终点）；
    - first_frame_desc = 当前首帧描述（终点），prev_tail_desc = 上尾帧描述（起点）；
      last_frame_desc 在 transition 分支不使用；图地址 first_frame/last_frame 分别映射
      「图片1=上尾帧、图片2=当前首帧」；
    - 转场时长走 preset.transitionDurationHint（坑G：不预设死值，缺省交模型默认），
      正片（climax/firstlast）仍走 videoDurationHint。
    - skeleton（提示词模版骨架）：自定义视频模版的区块标题/顺序/per-band 运镜/
      节奏名/默认时长；None = 逐字现行为（既有调用方零改动）。
    - pending_frame（climax 自动链）：提示词在成图**之前**编译、首帧由下游链补挂 ⇒
      仍按 I2V 写首帧声明与机位保持，否则这段提示词自认文生视频，首帧自己重新构图。
    """
    preset = preset or {}
    spec = _clean_spec(spec or {})
    style_prefix = str(preset.get("stylePrefix") or preset.get("style_template") or "")
    duration_hint = int(preset.get("videoDurationHint") or 0)
    transition_duration_hint = int(preset.get("transitionDurationHint") or 0)
    # 高潮时长（2026-10-31）：剧情动作量判定值（spec.video_duration_s）优先于模板默认；
    # 用户显式 videoDurationHint 仍是最高优先。首尾帧/转场不走这条（各自覆盖整段/短桥段）。
    if mode == "climax" and duration_hint <= 0:
        duration_hint = clamp_climax_duration(spec.get("video_duration_s")) or 0
    camera = str(preset.get("videoCamera") or "")

    base_url = str(video_config.get("base_url") or "").strip().rstrip("/")
    model = str(video_config.get("model") or video_config.get("modelName") or "")
    proxy = str(video_config.get("proxy") or "")
    # 视频默认 16:9（R9：不再沿用图片的 1024x1024）
    # ⚠ `size_cfg` 是**未兜底**的原值：机位契约的「宽画幅」证据只认它，不认这条 1280x720 默认——
    # 否则「没填画幅」会被读成「横屏 ⇒ 宽画幅成立」，正好把默认偏向升格，与判错代价不对称相反。
    size_cfg = str(video_config.get("size") or "").strip()
    size = size_cfg or "1280x720"
    # 高潮机位契约判定（仅 climax；其余模式为 None——首尾帧/转场的镜头语义本就允许运动）
    shot_intent: dict[str, str] | None = None

    if mode == "firstlast":
        has_first = bool(first_frame)
        has_last = bool(last_frame)
        prompt = compile_firstlast_video_prompt(
            spec, style_prefix=style_prefix,
            duration_hint=duration_hint, camera=camera, model_name=model, size=size,
            first_frame_desc=first_frame_desc or "首帧/楼层开头画面",
            last_frame_desc=last_frame_desc or "尾帧/楼层结尾画面",
            prev_tail_desc=prev_tail_desc,
            has_first=has_first, has_last=has_last, audio_lines=audio_lines,
            skeleton=skeleton,
        )
        images = [img for img in (first_frame, last_frame) if img]
        binding = {
            "图片1": _binding_entry(first_frame_desc or "首帧/楼层开头画面", first_frame),
            "图片2": _binding_entry(last_frame_desc or "尾帧/楼层结尾画面", last_frame),
        }
    elif mode == "transition":
        # W3 转场视频：图片1=上尾帧（起点），图片2=当前首帧（终点），短桥段
        has_prev_tail = bool(first_frame)
        has_first = bool(last_frame)
        prompt = compile_transition_video_prompt(
            spec, style_prefix=style_prefix,
            duration_hint=transition_duration_hint, camera=camera,
            model_name=model, size=size,
            prev_tail_desc=prev_tail_desc or "上一楼层尾帧画面",
            first_frame_desc=first_frame_desc or "当前楼层首帧画面",
            has_prev_tail=has_prev_tail, has_first=has_first,
            skeleton=skeleton,
        )
        images = [img for img in (first_frame, last_frame) if img]
        binding = {
            "图片1": _binding_entry(prev_tail_desc or "上一楼层尾帧画面", first_frame),
            "图片2": _binding_entry(first_frame_desc or "当前楼层首帧画面", last_frame),
        }
    else:
        # 参考绑定点：图片1只声明「图片1中心的角色为 X」（有在场角色时）；无在场角色
        # 才退回到 _climax_frame_role 的职责占位描述。画面级动作细节由 [时间分镜] 承载。
        desc = _climax_frame_role(spec, first_frame_desc)
        # 机位契约（2026-11-04）：判定口径与 compile_climax_video_prompt 内部一致
        # （size + 是否会有首帧），这里先算一次，既传进编译也带进 trace。
        ctr = resolve_climax_shot_intent(
            spec, size_cfg, bool(first_frame) or pending_frame)
        shot_intent = {"intent": ctr.intent, "reason": ctr.reason}
        prompt = compile_climax_video_prompt(
            spec, style_prefix=style_prefix,
            duration_hint=duration_hint, camera=camera, model_name=model, size=size,
            first_frame_desc=desc, audio_lines=audio_lines, has_frame=bool(first_frame),
            pending_frame=pending_frame,
            skeleton=skeleton, contract=ctr,
        )
        images = [first_frame] if first_frame else []
        actors = [str(a).strip() for a in (spec.get("actors") or []) if str(a).strip()]
        role = f"图片1中心的角色为{'、'.join(actors)}" if actors else desc
        binding = {
            "图片1": _binding_entry(role, first_frame),
        }

    warnings = _missing_frame_warnings(mode, first_frame, last_frame, pending_frame)
    seconds = transition_duration_hint if mode == "transition" else duration_hint

    return {
        "mode": mode,
        "submit": {
            "endpoint": base_url,               # 用户填的原样（代码不猜版本/单复数）
            "model": model,
            "prompt": prompt,                    # 编译出的视频提示词（含参考绑定职责文字）
            "images": images,                    # 参考图地址（climax 1 张 / firstlast 2 张）
            "size": size,
            "duration": int(seconds or 0),       # 显式判定秒数（0=未判定，前端不注入，保留模板默认）
            "proxy": proxy or None,
            "content_type": "multipart/form-data" if images else "application/json",
        },
        "reference_binding": binding,
        "prompt_sections": _section_names(mode, skeleton),
        "shot_intent": shot_intent,
        "warnings": warnings,
    }


def _missing_frame_warnings(mode: str, first_frame: str, last_frame: str,
                            pending_frame: bool = False) -> list[str]:
    """缺图守卫（R2）：把缺失帧的后果显式列给调用方/人核对。"""
    warnings: list[str] = []
    if mode == "firstlast":
        if not first_frame:
            warnings.append("缺首帧图：首帧将以文字描述开场（退化为文生/待上游补图）")
        if not last_frame:
            warnings.append("缺尾帧图：尾帧将以文字描述收尾")
        if not first_frame and not last_frame:
            warnings.append("首尾帧图均缺：将退化为纯文生视频（无参考图）")
    elif mode == "transition":
        # 坑F：转场起点（上尾帧图）缺省 → 降级文字转场；终点（当前首帧图）缺省 → 文字收束
        if not first_frame:
            warnings.append("缺上尾帧图：转场降级为文字转场（以文字重现上一楼层尾帧）")
        if not last_frame:
            warnings.append("缺当前首帧图：转场终点以文字描述")
        if not first_frame and not last_frame:
            warnings.append("转场双图均缺：将退化为纯文生短桥段（无参考图）")
    else:
        if not first_frame:
            # pending_frame（自动链）：图在提示词编译之后才产出，由下游链补挂成首帧，
            # 不是「将以文字生成动作画面」——按实际后果写，免得 trace 指错方向。
            if pending_frame:
                warnings.append("高潮参考图编译时未产出：首帧由下游链补挂本张成图")
            else:
                warnings.append("缺高潮参考图：将以文字描述生成动作画面")
    return warnings


def _binding_entry(role: str, source: str) -> str:
    """参考绑定条目：职责 → 图地址（供人核对两层是否对应）。"""
    if source:
        return f"{role} → {source}"
    return f"{role} → （未提供图地址，将退化为文生或由上游补图）"


def _section_names(mode: str, skeleton: "VideoSkeleton | None" = None) -> list[str]:
    """提示词**实际含**哪些区块（供人核对完整性）。风格声明已停用（有参考图定调）。

    ⑥ 负面约束退役 ⇒ 键表里已没有它（第 87 刀摘旋钮）：`prompt_sections` 是给人在
    dry-run 里核对的清单，列一个编译器不产出的区块等于谎报。
    skeleton（提示词模版骨架）：按自定义标题/顺序重排编号；None = 内置固定名。
    """
    if skeleton is not None and (skeleton.section_titles or skeleton.section_order):
        default_segments = "转场分镜" if mode == "transition" else "时间分镜"
        out: list[str] = []
        for key in skeleton.order():
            fallback = default_segments if key == SECTION_SEGMENTS else _DEFAULT_SECTION_TITLES[key]
            out.append(f"{'①②③④⑤'[len(out)]}{skeleton.title(key, fallback)}")
        return out
    if mode == "transition":
        return ["①元信息", "②参考绑定", "③主体/场景", "④转场分镜", "⑤音频"]
    return ["①元信息", "②参考绑定", "③主体/场景", "④时间分镜", "⑤音频"]


def parse_video_plan(reply: str) -> dict[str, Any]:
    """解析视频提示词专用提取（动作延伸 + 简化外貌/场景）的 JSON 回复。

    容忍 markdown 代码块包裹与破甲残留；字段不合法时只丢弃对应键，不整体失败。
    产出 {action_sequence: [{beat, desc}], subject_scene: str, duration_s: int}；
    duration_s 是剧情判定的高潮时长，已夹到 4–15（缺省时调用方兜底 5）。

    拒答防御（对齐生图链）：正文性字段（desc/subject_scene/music/sfx/sync）命中
    拒答句式即丢弃该条——模型把「我不能协助这项请求」写进 JSON 时，不得让它
    流进 [时间分镜]/[音频] 段；台词原文不过滤（防拦截正文里「我不能满足你」这类
    正常对白必须原样保留）。整体无效（拒答/无 JSON）时返回 {}，
    调用方据此判定重试或回退纯函数兜底。
    """
    raw = (reply or "").strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    seq = data.get("action_sequence")
    if isinstance(seq, list):
        beats: list[dict[str, str]] = []
        for item in seq[:8]:
            if not isinstance(item, dict):
                continue
            beat = str(item.get("beat") or "").strip()
            desc = prompt_clean.restore_jailbreak(str(item.get("desc") or "")).strip()
            if not desc or prompt_clean.REFUSAL_RE.search(desc):
                continue
            beats.append({"beat": beat or "延伸", "desc": desc})
        if beats:
            out["action_sequence"] = beats
    subject_scene = data.get("subject_scene")
    if isinstance(subject_scene, str) and subject_scene.strip():
        scene_text = prompt_clean.restore_jailbreak(subject_scene).strip()
        if scene_text and not prompt_clean.REFUSAL_RE.search(scene_text):
            out["subject_scene"] = scene_text
    audio_design = data.get("audio_design")
    if isinstance(audio_design, dict):
        design: dict[str, Any] = {}
        music = prompt_clean.restore_jailbreak(str(audio_design.get("music") or "")).strip()
        if music and not prompt_clean.REFUSAL_RE.search(music):
            design["music"] = music
        sfx: list[str] = []
        for item in audio_design.get("sfx") or []:
            s = prompt_clean.restore_jailbreak(str(item)).strip()
            if not s or prompt_clean.REFUSAL_RE.search(s) or s in sfx:
                continue
            sfx.append(s)
        if sfx:
            design["sfx"] = sfx
        lines: list[dict[str, Any]] = []
        for ln in audio_design.get("lines") or []:
            if not isinstance(ln, dict):
                continue
            speaker = prompt_clean.restore_jailbreak(str(ln.get("speaker") or "")).strip()
            text = prompt_clean.restore_jailbreak(str(ln.get("text") or "")).strip()
            if text:
                entry: dict[str, Any] = {"speaker": speaker, "text": text}
                # at_s：提取 LLM 按剧情位置推算的台词时点（秒）——数字/数字串才透传
                at_s = ln.get("at_s")
                if isinstance(at_s, (int, float)) and not isinstance(at_s, bool):
                    entry["at_s"] = max(0.0, float(at_s))
                elif isinstance(at_s, str):
                    try:
                        entry["at_s"] = max(0.0, float(at_s.strip()))
                    except ValueError:
                        pass
                lines.append(entry)
        if lines:
            design["lines"] = lines
        sync = prompt_clean.restore_jailbreak(str(audio_design.get("sync") or "")).strip()
        if sync and not prompt_clean.REFUSAL_RE.search(sync):
            design["sync"] = sync
        if design:
            out["audio_design"] = design
    duration_s = clamp_climax_duration(data.get("duration_s"))
    if duration_s is not None:
        out["duration_s"] = duration_s
    return out
