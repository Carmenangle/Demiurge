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
# ⚠ 2026-11-09：firstlast 已停用本词汇表（单图无空间信息，逐拍换镜必晃）；
# 保留给后续多镜头/场景板管线，当前只有 `_beat_camera` 的保留调用面。
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
# 环境空镜的首帧声明：没有人物，锁的是空间/道具/光线（不能写「人物姿态与服装」）。
_ESTABLISH_FRAME_NOTE = (
    "；使用输入图片作为准确起始帧："
    "首帧必须逐字复现图片1的机位、构图、空间关系、道具与光线，再从该定格继续演变"
)
# 剧情句含场景切换提示词时改用转场型运镜（衔接/转场的导演思维）。
# ⚠ 曾只在 firstlast 生效；2026-11-09 firstlast 固定机位后不再触发。climax 走下方
# 「机位契约」，逐拍换镜（含这里的甩镜头/遮挡揭示转场句）是高潮视频「像有转场」的
# 直接来源（2026-11-04 用户实锤）。本句保留给后续多镜头/场景板管线。
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
# 2026-11-09 分镜序列：运镜镜头全片只走一条连续路径，不逐拍换镜。
_SHOT_MOVE_CAMERA = "镜头沿本片声明的路径连续移动，中途不换镜、不重新构图"
# ① 只写一次完整路径；「中途不换镜」由 ④ 每拍的 `_SHOT_MOVE_CAMERA` 承担，
# 避免元信息与每拍契约把同一句重复两遍（2026-11-09 离线模拟实锤）。
_SHOT_MOVE_META = "全片一条连续运镜：{path}"
# 环境空镜（establish）：只交代空间/道具/群演，不引入主要人物。
_ESTABLISH_HOLD_META = (
    "全片一条固定镜头（Static Shot），机位、画框与空间关系不变；"
    "画面保持环境级运动（道具、群演、光线与尘埃继续动），不要引入主要人物"
)
_ESTABLISH_MOVE_META = (
    "全片一条连续运镜：{path}；镜头沿这条路径持续移动，"
    "画面只交代空间与环境（道具、群演、光线继续动），不要引入主要人物"
)
# 图片落在第 N 秒的环境开场：前段只演环境，图片所在秒起人物按分镜入画。
_ESTABLISH_MOVE_LATE_META = (
    "全片一条连续运镜：{path}；镜头沿这条路径持续移动，"
    "前段只交代空间与环境（道具、群演、光线继续动），"
    "到图片1所在秒数对齐构图后，人物按[时间分镜]继续"
)
_ESTABLISH_HOLD_LATE_META = (
    "全片一条固定镜头（Static Shot），机位、画框与空间关系不变；"
    "前段只演环境级运动（道具、群演、光线与尘埃继续动），"
    "到图片1所在秒数对齐构图后，人物按[时间分镜]继续"
)
_ESTABLISH_WORLD = (
    "环境中的世界照常运动：道具、白纱/窗帘、光线与尘埃持续变化，"
    "群演或宠物按各自动作继续，不要凭空加入主要人物"
)
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
# 多拍/多机位分镜：段间本来就要硬切换机位，不能再锁「与图片1共用同一视点位置」。
# 保留 POV 的实质约束（每段都是叙事者本人的视线），只放开机位位置与视线方向。
_POV_VIEW_MULTI = (
    "第一人称视角（POV，first person view），每一段都是镜头后面那一位本人的视线；"
    "按[时间分镜]改变景别与视线方向，不得切到第三人称机位，不得出现镜头角色的全身"
)
# 同一支视频里既有 POV 又有第三人称旁观：逐段标注视角，不再全局锁一种。
_POV_VIEW_MIXED = (
    "混合视角（POV，first person view）：标『第一人称』的段是镜头后面那一位本人的视线，"
    "不得出现他的全身；标『第三人称』的段是旁观机位，叙事者不在画面里；"
    "段间硬切按段切换视角，不得把两种视角混在同一段"
)
# 全片旁观：只写肯定式旁观取景，避免出现「第一人称」触发词。
_OBSERVER_VIEW_NOTE = "第三人称旁观机位：按[时间分镜]执行各段机位，画面按旁观视角取景"
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

    ⚠ 2026-11-09：firstlast 已改为全片固定机位，不再调用本函数（单图无空间
    信息，逐拍换镜会把首帧定格的机位/景别在第 2 拍就丢掉）。保留给后续多镜头/
    场景板管线；当前唯一调用面是保留测试与未来的镜头序列生成。

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


def _pov_limb_notes(spec: dict[str, Any]) -> tuple[str, ...]:
    """叙事者肢体事实的归属说明；无事实 ⇒ 空元组。"""
    limbs = _pov_limb_phrase(spec)
    if not limbs:
        return ()
    return (_POV_LIMB_OWNER_CLAIM, limbs)


def _pov_shot_notes(spec: dict[str, Any], *, anchored: bool,
                    cuts: bool = False, force: bool = False) -> tuple[str, ...]:
    """第一人称镜头声明（进 ① 的 notes 尾部）。

    ⚠ 零回归锁：`viewpoint` 缺失／非 `first_person` ⇒ 空元组 ⇒ ① 逐字节等于旧行为
      （第三人称与「用户没开第一人称」的既有稿件不受任何影响）。
    ⚠ `force=True`：分镜逐拍显式标了 pov（作品级未必是第一人称）时，
      绕过作品级 `viewpoint` 闸门，按这一支视频的逐拍视角发 POV 声明。
    ⚠ `anchored` 只在 hold 档＋有首帧时为真：无首帧不得引用不存在的图（缺图守卫 R2），
      reveal 档机位本来在动，不能再说「与图片1同一视点」。
    ⚠ `cuts=True`（shot_video_beats 有多拍/硬切）⇒ 用多拍声明：段间本来就要换机位，
      再写「与图片1共用同一视点位置」就是同一条提示词里的硬互斥。
    第 90 刀：**有肢体事实才补归属主句**，且它排在事实列表之前——裸 tag 只说「有一双手从
      画框下缘进来抓住她的腰」，不说那双手是谁的；归属在 ① 解释一次，④ 用 `_POV_BEAT_ACTOR`
      回指它（图片侧同构：条款承担归属、tag 本体保持裸短语）。
      无肢体事实 ⇒ 主句不出现（没有指代对象，写了只是白占字数）。
    """
    if not force and not _first_person_view(spec):
        return ()
    if cuts:
        notes = [_POV_VIEW_MULTI]
    else:
        notes = [_POV_VIEW_ANCHORED if anchored else _POV_VIEW_PLAIN]
    notes.extend(_pov_limb_notes(spec))
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
    """③ 主体/场景：只写「这一格里看得见」的角色名、当前衣着与场景。

    ⛔ 不再复述角色外貌：图片1 在生成时已经接收了角色卡/条目的外貌，视频只需要
    知道「谁、当前什么状态、在哪」。旧实现从 `subjects`/`appearance` 复制整段卡面，
    导致 ③ 被 300–400 字的外貌淹没（2026-11-11 用户实锤）。
    1. `video_subject_scene`（已含「角色名（当前状态）+ 场景」）优先；
    2. 否则只写画面角色名；
    3. 再拼当前衣着（wardrobe）与地点（locale），占位值除外。
    """
    video_scene = str(spec.get("video_subject_scene") or "").strip()
    if video_scene:
        return video_scene
    parts: list[str] = []
    # `subjects` 是**本帧画面事实**（动作/姿态/光线），不是角色卡；可以保留。
    # ⛔ 只删 `appearance` 整卡兜底：外貌已由图片1承载。
    for subject in spec.get("subjects") or []:
        if isinstance(subject, dict):
            desc = str(subject.get("description") or "").strip()
            if desc and desc not in parts:
                parts.append(desc)
    if not parts:
        parts = list(_on_frame_names(spec))
    # 衣着 + 场景（独立维度，始终拼接）
    for key in ("wardrobe", "locale"):
        val = str(spec.get(key) or "").strip()
        if val and val not in parts and not _is_placeholder(val):
            parts.append(val)
    return "，".join(parts)


def _transition_subject_scene(spec: dict[str, Any]) -> str:
    """③ 主体/场景（转场专用）：只约束过渡本身，不复述整楼层事实。

    实稿发现 4：转场编译沿用整楼层 spec ⇒ ③ 写进全部角色外貌 + 「走廊+卧室+楼下侧室门」
    三个地点（`video_subject_scene` 是整段楼层的简化视觉）。转场只有几秒、两端已经由
    图片1/图片2 定死，整楼层清单只会让模型在过渡里凭空换场。⇒ 只留「以两张图为准」
    的边界句；在场名单由 ② 的「画面角色」承载，这里不重复。
    """
    return ("主体外观与所在场景以图片1、图片2为准；"
            "转场途中不新增人物、不换地点、不引入两张图里没有的物件或装置")


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
    # 先判「名字：外貌」段落头，再判 `名字(外貌)`：长角色卡的外貌正文里常带括号
    # （`乳房（尺寸110+）`），旧顺序会把这些括号前的短语当角色名，返回一整张垃圾映射
    # ⇒ 视频身份锁拿不到真外貌（2026-11-11 示例作品甲实锤）。
    lines = [ln for ln in src.splitlines() if ln.strip()]
    header = re.compile(r"^\s*([^\s：:]+)\s*[：:]\s*(.+)$")
    if lines and all(header.match(ln) for ln in lines):
        mapped: dict[str, str] = {}
        for ln in lines:
            m = header.match(ln)
            if m:
                mapped[m.group(1).strip()] = m.group(2).strip()
        if len(lines) == 1:
            # 单条也可能是「腓特烈大帝：…」：角色配置名常是条目名的一部分
            # （腓特烈 / 腓特烈大帝）。精确优先，其次取唯一以 actor 开头的条目键；
            # ⛔ 不能走下面的前缀兜底，否则会把「大帝：…」整段当成外貌（2026-11-11 实锤）。
            for actor in actors:
                if actor in mapped:
                    return {actor: mapped[actor]}
                candidates = [key for key in mapped if key.startswith(actor)]
                if len(candidates) == 1:
                    return {actor: mapped[candidates[0]]}
            # 没有 actor 命中 ⇒ 可能是整卡 dump（`世界观：…；纪含章(…)`）：
            # 继续走括号分支，不能把整条 dump 当成某个角色的外貌。
        else:
            resolved: dict[str, str] = {}
            for actor in actors:
                if actor in mapped:
                    resolved[actor] = mapped[actor]
                    continue
                candidates = [key for key in mapped if key.startswith(actor)]
                if len(candidates) == 1:
                    resolved[actor] = mapped[candidates[0]]
            return resolved or mapped
    paren = re.findall(r"([^\s、;；，,]+?)\s*[\(（]([^\)）]+)[\)）]", src)
    if paren:
        out: dict[str, str] = {}
        for name, desc in paren:
            name, desc = name.strip(), desc.strip()
            if name and desc:
                out[name] = desc
        return out
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
    """视频身份锁只写角色名，不复述角色卡外貌。

    外貌已由图片1（生成时接收角色卡/条目）与图片2+角色底图承载；视频文本再复述
    300–400 字卡面只会淹没动作与机位（2026-11-11 用户口径：光图片就涵盖外貌）。
    """
    return "、".join(actor for actor in actors if actor)


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
    timing = str(spec.get("shot_image_timing") or "start").strip().lower()
    second = int(spec.get("image_time_s") or 0)
    late_frame = timing in ("mid", "end", "at")
    if spec.get("establish"):
        # 环境空镜：不锁人物身份，只锁空间/道具/光线（否则模型会把开场画成人物特写）。
        if late_frame:
            return (
                f"图片1=本镜第 {second} 秒的画面"
                "（空间、道具、光线与人物均按图片1；不要额外添加图片里没有的主要人物）"
            )
        return "图片1=本镜环境空镜的起始画面（只锁空间、道具与光线；不要凭空添加主要人物）"
    if actors:
        lines = [f"图片1中心的角色为{'、'.join(actors)}"]
        if spec.get("role_refs"):
            # 底图可能不是当前造型（腓特烈婚纱模式）：角色参考只锁外貌，
            # 服装/姿势/场景一律以本镜生成的图片1为准（用户 2026-11-11 定案）。
            lines.append(
                "服装、姿势与场景以图片1为准；角色外貌只按角色参考图锁定"
                "（不参考底图的服装与姿势）"
            )
        else:
            lines.append(f"保持 {_identity_gloss(spec, actors)} 的身份、脸部、服装、发型、造型完全一致")
    else:
        desc = _climax_frame_role(spec, first_frame_desc)
        anchor = f"本镜第 {second} 秒的画面" if late_frame else "起始帧"
        lines = [f"图片1={desc}（唯一参考画面，作为{anchor}）"]
        if has_frame:
            lines.append("保持图片1中人物的身份、脸部、服装、发型、造型完全一致")
    lines.append(_IDENTITY_LOCK)
    return "；".join(lines)


def firstlast_role_refs(actors: list[str] | tuple[str, ...],
                        base_images: dict[str, str] | None) -> list[str]:
    """首尾帧「参考生成」的角色参考图名单（图片3 起的编号顺序）。

    与前端 `planFirstlastReferenceImages` **同口径**：按在场角色顺序、只给有角色底图的
    才占一个槽、同一张底图只占一次（两个角色共用一张底图时后者不占位）。
    后端只决定「第几张图是谁」；真正上传由前端按视频模板的参考图槽完成。
    没底图的角色不占位 ⇒ 不多报一个编号（R2：不假装有图）。
    """
    out: list[str] = []
    seen_names: set[str] = set()
    seen_sources: set[str] = set()
    for raw in actors or ():
        name = str(raw or "").strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        source = str((base_images or {}).get(name) or "").strip()
        if not source or source in seen_sources:
            continue
        seen_sources.add(source)
        out.append(name)
    return out


def _reference_binding_firstlast(first_frame_desc: str, last_frame_desc: str,
                                 spec: dict[str, Any],
                                 has_first: bool = True, has_last: bool = True) -> str:
    """② 参考绑定（firstlast 双图）：图片1=首帧、图片2=尾帧，职责钉死。

    缺图时诚实标注「无参考图，以文字为准」，不假装有图（R2）。
    身份锁（⑥ 退役后）在这里只写一次。
    批 3：`spec["role_refs"]`（在场且有角色底图的名单）按序补「图片3+＝谁」——
    首尾帧画面是所在场次的真源，角色参考图只锁「这个人长什么样」，不锁姿势与衣着。
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
    for idx, raw in enumerate(spec.get("role_refs") or [], start=3):
        name = str(raw or "").strip()
        if name:
            lines.append(
                f"图片{idx}={name}（角色参考图：只锁身份、五官、发型与体型；"
                "当下衣着、姿势与所在场景以首尾帧画面为准）"
            )
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


def _scene_key(text: str) -> str:
    """场景比对键：大小写/空白差异不算换场景（提取模型常把同一地点换个大小写）。"""
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _firstlast_action_beats(spec: dict[str, Any]) -> list[tuple[str, str]]:
    """firstlast ④ 的动作流：`action_sequence` → [(desc, scene)]（空 desc 丢弃）。

    ⚠ 只服务 firstlast。climax 的 `_climax_beat_plan` 有自己的裁剪/折叠链（首帧构型
    迁移短语要剔、动作序列要并拍），两套口径不能互相借用。
    """
    seq = spec.get("action_sequence")
    if not isinstance(seq, list):
        return []
    out: list[tuple[str, str]] = []
    for item in seq:
        if not isinstance(item, dict):
            continue
        desc = str(item.get("desc") or "").strip()
        if desc:
            out.append((desc, str(item.get("scene") or "").strip()))
    return out


def _firstlast_narrative_sentences(
    spec: dict[str, Any], first_frame_desc: str,
) -> list[str]:
    """整段楼层正文逐句切拍（跳过被首帧覆盖的开头句、对白归属并入台词拍）。

    第 80 刀三处修正（只作用 firstlast；climax 走 `_climax_time_segments`）：
    ① 开场定格已逐字带上 first_frame_desc（楼层开头部分），中段若从第一句起会把
       同一段画面再演一遍——离线真链路模拟里 12s 的前 4 秒都在重复「推门收伞」。
       只跳过**开头连续**被 opening 覆盖的那几句，后文有意重复的句子不动。
    ② 闭引号后紧跟的「老周问。」是对白归属，与台词同拍，否则口型同步与说话人分家；
       第 86 刀补另一半：真机更常见的是「归属＋第二句台词」同句，只认纯短句会把归属
       整条留给第二句，第一句台词因此没人说过（`_is_attribution_lead`）。
    ③ 取消 [:8] 硬截断：narrative 全段都得有交代，超量按时长均分合并而非丢弃。
    """
    narrative = str(spec.get("floor_narrative") or spec.get("narrative") or "").strip()
    sentences = _split_narrative_beats(narrative)
    opening = (first_frame_desc or "").strip()
    covered = 0
    while covered < len(sentences) and opening and sentences[covered].strip() in opening:
        covered += 1
    sentences = sentences[covered:]
    merged: list[str] = []
    for sent in sentences:
        previous = merged[-1] if merged else ""
        if previous and ("「" in previous or "“" in previous) and (
                _is_speaker_attribution(sent) or _is_attribution_lead(sent)):
            merged[-1] = f"{previous}{sent}"
        else:
            merged.append(sent)
    return merged


def _group_firstlast_quotes(sentences: list[str], groups: int) -> list[list[str]]:
    """把正文引语按**句子顺序**分到 `groups` 个动作拍。

    动作流是英文视觉描述，本身不含正文引语；对白时间轴（`match_dialogue_to_beats`）
    靠拍里的「」逐字命中，所以换用动作拍时必须把引语按剧情位置挂回对应拍，
    否则所有台词都会退化成「未逐字命中」的兜底排布。

    归属按**相对位置**取最近的一拍（`idx*groups//total`）：第 i 句发生在全段
    第 i/total 处，就落进覆盖该时刻的那一拍。⚠ 不能复用 `_fold_firstlast_mid` 的
    `total*g//groups` 折叠边界——那是「把 N 拍并进 G 槽」，拿它反查句子会让引语
    整体后移一拍（6 句 / 4 拍时「开饭了」被挂到动作已经走开的下一拍）。
    """
    if groups <= 0 or not sentences:
        return [[] for _ in range(max(0, groups))]
    total = len(sentences)
    buckets: list[list[str]] = [[] for _ in range(groups)]
    for idx, sent in enumerate(sentences):
        slot = min(groups - 1, idx * groups // total)
        for quote in _beat_quotes(sent):
            if quote not in buckets[slot]:
                buckets[slot].append(quote)
    return buckets


def _fold_firstlast_mid(
    beats: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]], quota: int,
) -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    """把中段压进 `quota` 拍：与 `_bound_beats` 同口径的均分合并（首尾各占一拍）。

    不能直接复用二元版 `_bound_beats`：那里 tuple 是 (label, desc)、合并的是 desc，
    而这里 (content, extra, scenes, quotes) 要合并的是 content。分组公式 `total*idx//quota`
    与 `_bound_beats` 逐字一致 ⇒ 拍数 ≤ 秒数时整除边界严格递增，不产生 0s–0s 空窗口；
    组内文本保序去重合并，剧情事实一条不丢；场景按出现顺序去重保留（合并窗口里
    如果跨了地点，转场句照旧要出现在这一拍的文本里）。引语单独带着走——第 105 刀起
    由 `_attach_beat_quotes` 按**最终拍窗口**给预算，不能在合并前就拼进 content。
    """
    if quota <= 0 or len(beats) <= quota:
        return beats
    total = len(beats)
    out: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = []
    for idx in range(quota):
        lo = total * idx // quota
        hi = total * (idx + 1) // quota
        group = beats[lo:hi]
        if not group:
            continue
        content = "；".join(dict.fromkeys(part for part, _extra, _sc, _q in group if part))
        extra = "；".join(dict.fromkeys(part for _body, part, _sc, _q in group if part))
        scenes = tuple(dict.fromkeys(
            scene for _body, _extra, slots, _q in group for scene in slots if scene
        ))
        quotes = tuple(dict.fromkeys(
            quote for _body, _extra, _sc, qs in group for quote in qs
        ))
        out.append((content or "主体动作按剧情自然演变", extra, scenes, quotes))
    return out


def _fit_beat_quotes(quotes: tuple[str, ...], seconds: int) -> tuple[list[str], int]:
    """按本拍时长容量取能说完的**整句**台词，返回（保留的引语，被裁掉的句数）。

    实稿：15s 片子 7 拍中段、53 句台词 ⇒ 每 2 秒拍塞 8–10 句（108–230 字），
    物理上说不完，模型只能把整段挤进两秒或直接忽略。按 `_DIALOGUE_CHARS_PER_SEC`
    估容量：
    - 整句装不下就停（不跳句取后文，避免同拍内台词时序错位）；
    - 一句都装不下 ⇒ 本拍只演画面动作、不写对白（宁缺不错）。
    被裁掉的台词数由 `build_video_request` 落进 warnings（供人核对，不静默）。
    """
    budget = max(0, int(seconds * _DIALOGUE_CHARS_PER_SEC))
    kept: list[str] = []
    used = 0
    for quote in quotes:
        if used + len(quote) > budget:
            break
        kept.append(quote)
        used += len(quote)
    return kept, len(quotes) - len(kept)


def _render_beat_quotes(content: str, kept: list[str]) -> str:
    if not kept:
        return content
    return content + "；本拍对白：" + "".join(f"「{q}」" for q in kept) + "；台词随口型同步"


def _attach_beat_quotes(content: str, quotes: tuple[str, ...], seconds: int) -> str:
    """把引语按本拍时长预算挂进 content（`_fit_beat_quotes` + 渲染的薄包装）。"""
    if not quotes:
        return content
    kept, _dropped = _fit_beat_quotes(quotes, seconds)
    return _render_beat_quotes(content, kept)


# 拍窗口最短 2s（第 107 刀）：开场/收尾是定格拍，中段也要落得住动作。
_DIALOGUE_BEAT_FLOOR_SECONDS = 2


def _firstlast_beat_windows(
    beats: list[tuple[str, str, str, tuple[str, ...]]], dur: int,
) -> tuple[list[tuple[int, int]], int, int]:
    """按台词量分配拍窗口（第 107 刀），返回（窗口, 纯台词秒数, 各拍最少需要秒数）。

    旧实现 `dur*idx//n` 均分：15s / 5 拍每拍 3s，一句 28 字（≈6.3s）的台词物理上
    说不完，`_fit_beat_quotes` 只能整句裁掉（实稿 844 字 3 段 15s 只剩 6/9）。
    台词拍按 `_DIALOGUE_CHARS_PER_SEC` 配够说话时间、其余拍保 2s 定格/动作下限，
    余量从前向后轮流补 1s（开场先得，有利于切入）。

    - 所有拍需求相同（无台词 / 台词量恰好均匀）⇒ 逐字沿用旧均分，这类提示词零变化；
    - 需求总和超过时长 ⇒ 同样退回均分——不削别拍补台词拍，只由调用方报「至少需要
      约 Xs」，保证不出现 0s 拍，也不静默改变画面节奏。
    """
    n = len(beats)
    uniform = [(dur * idx // n, dur * (idx + 1) // n) for idx in range(n)]
    speech = [
        math.ceil(sum(len(quote) for quote in quotes) / _DIALOGUE_CHARS_PER_SEC)
        if quotes else 0
        for _content, _extra, _label, quotes in beats
    ]
    needs = [max(_DIALOGUE_BEAT_FLOOR_SECONDS, sec) for sec in speech]
    if len(set(needs)) == 1 or sum(needs) > dur:
        return uniform, sum(speech), sum(needs)
    add = [0] * n
    for i in range(dur - sum(needs)):
        add[i % n] += 1
    edges = [0]
    for need, bonus in zip(needs, add):
        edges.append(edges[-1] + need + bonus)
    return list(zip(edges, edges[1:])), sum(speech), sum(needs)


def _firstlast_beat_plan(
    spec: dict[str, Any], duration_hint: int,
    first_frame_desc: str, last_frame_desc: str,
    prev_tail_desc: str,
    has_first: bool = True, has_last: bool = True,
    skeleton: "VideoSkeleton | None" = None,
) -> tuple[list[tuple[int, int, str, str, str]], int, dict[str, int]]:
    """firstlast 分镜拍计划：[(start, end, label, content, extra)] + 实际时长（秒）
    + 台词预算统计 `{quotes_total, quotes_dropped, dialogue_seconds,
    beat_needs_seconds}`（第 105 刀：超出拍窗口容量的引语被裁掉，调用方据此落
    warnings，不静默；第 107 刀：拍窗口按台词需求加权，`beat_needs_seconds`
    是「台词拍按 4.5 字/秒 + 每拍 2s 下限」的总和，放不下时调用方报所需时长）。

    ⚠ **单一真源**：④ 的渲染与「对白时间轴」共用这一份拍界。两边各算一遍
    `dur*idx//n` 迟早会漂移，模型收到的「第几秒说哪句」就会对不上画面。

    中段取材＝提取层 `action_sequence` 去掉 desc[0]（合同要求它**复现**首帧，纯复述）
    或正文逐句；末拍是「**落在**尾帧」的动作收束（见第 102 刀），仍进中段。
    每拍 scene 只在变化处渲染成转场句（见下方渲染段）。

    拍数 ≤ 秒数（首尾各占一拍、中段最多 dur-2 拍）⇒ 不会出现 0s–0s 空窗口。
    """
    sk = skeleton or VideoSkeleton()
    fallback_dur = sk.default_duration if sk.default_duration else _DEFAULT_DURATION
    dur = int(duration_hint) if duration_hint else fallback_dur
    motion_raw = spec.get("motion")
    motion = int(motion_raw) if isinstance(motion_raw, (int, float)) else 0

    f_open = (first_frame_desc or "楼层开头画面").strip()
    l_close = (last_frame_desc or "楼层结尾画面").strip()

    # 首帧段：有图定格首帧 / 无图文字开场；带衔接上下文。
    open_content = f"定格于首帧画面：{f_open}" if has_first else f"以文字首帧开场：{f_open}"
    if prev_tail_desc:
        open_extra = f"承接上一镜头尾帧（{prev_tail_desc}）自然延续，机位/光影/人物状态连贯，无突兀跳切"
    else:
        open_extra = "作为本段独立开场镜头"

    # 中段两条取材路（批 2）：优先提取层的动作流（英文视觉描述、动作/分镜更细），
    # 不足 3 拍（只有开场＋收尾）或没有时退回批 1 的正文逐句成拍。
    # ⚠ 只丢 desc[0]：合同要求它**复现**楼层开头画面（纯复述，与开场定格拍逐字重复）；
    #   desc[-1] 的合同是「**落在**楼层结尾画面」，不是「只复述尾帧」——第 102 刀实稿：
    #   真实末拍＝「把银戒慢慢戴上→靠墙→楼下闷响」，旧实现 `action[1:-1]` 整拍丢掉，
    #   ④ 里只剩台词「这戒指，我戴上了」而没有戴戒动作。⇒ 末拍作为动作收束进中段，
    #   尾帧由收尾拍另行定格（帧锚点仍只在收尾拍出现一次）。
    sentences = _firstlast_narrative_sentences(spec, first_frame_desc)
    action = _firstlast_action_beats(spec)
    use_action = len(action) >= 3
    mid_beats: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = []
    if use_action:
        mid_action = action[1:]
        for (desc, scene), group_quotes in zip(
                mid_action, _group_firstlast_quotes(sentences, len(mid_action))):
            # 引语是「对白时间轴」的唯一锚点：换成英文动作拍后必须按剧情位置挂回。
            # 第 105 刀：先只带着走，等最终拍窗口算出来再按秒数预算挂进 content——
            # 折叠合并会改变每拍的时长，提前拼进去就无从裁量（实稿 53 句塞 15s）。
            # 变量名不用裸 `quotes`：mypy 在函数级复用品名，list[str] 推断会污染后面
            # 所有 tuple[str, ...] 解构，产生六处连锁类型错误。
            mid_beats.append((
                desc, "与节拍/重音同步" if motion >= 2 else "",
                (scene,) if scene else (), tuple(group_quotes),
            ))
    else:
        for sent in sentences:
            content = sent
            if "「" in sent or "“" in sent:
                content += "；台词随口型同步"
            mid_beats.append((content, "与节拍/重音同步" if motion >= 2 else "", (), ()))
        if not mid_beats:
            mid_beats = [("主体动作按剧情自然演变", "", (), ())]
    # 首尾各占一拍 ⇒ 中段最多 dur-2 拍，总拍数 ≤ dur：`dur*idx//n` 不再整除出空窗口。
    quota = max(0, dur - 2)
    if quota == 0:
        # 时长 <3s（现实下界 4，仅防御）：整段剧情并进开场拍，只留首尾两拍。
        joined = "；".join(dict.fromkeys(
            content for content, _extra, _sc, _q in mid_beats if content))
        if joined:
            open_content = f"{open_content}；{joined}"
        mid_beats = []
    else:
        mid_beats = _fold_firstlast_mid(mid_beats, quota)

    # 尾帧段：有图定格尾帧 / 无图文字收尾；收束为下一镜头留过渡点。
    close_content = f"收尾定格于尾帧画面：{l_close}" if has_last else f"以文字尾帧收尾：{l_close}"
    close_extra = "动作自然收束、神态定格，为下一镜头衔接留出自然过渡点"

    open_scene = action[0][1] if use_action else ""
    close_scene = action[-1][1] if use_action else ""
    beats: list[tuple[str, str, str, tuple[str, ...], tuple[str, ...]]] = [(
        open_content, open_extra, sk.beat_label(0, "开场定格"),
        (open_scene,) if open_scene else (), (),
    )]
    for content, extra, scenes, quotes in mid_beats:
        beats.append((content, extra, sk.beat_label(1, "剧情演变"), scenes, quotes))
    beats.append((
        close_content, close_extra, sk.beat_label(2, "收尾定格"),
        (close_scene,) if close_scene else (), (),
    ))

    # 换场景＝一次转场拍：只在**变化**处写「场景切换：…」，同一地点（含大小写/空白差异）
    # 不重复写。开场场景是起点、不渲染成切换；开场没给场景时中段第一个场景只「认下来」，
    # 否则会凭空多报一次切换。
    prev_scene = ""
    rendered: list[tuple[str, str, str, tuple[str, ...]]] = []
    for content, extra, label, scenes, quotes in beats:
        prefix = ""
        for scene in scenes:
            key = _scene_key(scene)
            if not key or key == _scene_key(prev_scene):
                continue
            if not prev_scene:
                prev_scene = scene
                continue
            prefix += f"场景切换：{scene}；"
            prev_scene = scene
        rendered.append((prefix + content, extra, label, quotes))

    windows, dialogue_seconds, beat_needs_seconds = _firstlast_beat_windows(rendered, dur)
    plan: list[tuple[int, int, str, str, str]] = []
    quotes_total = 0
    quotes_dropped = 0
    for idx, (content, extra, label, quotes) in enumerate(rendered):
        start, end = windows[idx]
        kept, dropped = _fit_beat_quotes(quotes, end - start)
        quotes_total += len(quotes)
        quotes_dropped += dropped
        plan.append((
            start, end, label,
            _render_beat_quotes(content, kept), extra,
        ))
    return plan, dur, {
        "quotes_total": quotes_total,
        "quotes_dropped": quotes_dropped,
        "dialogue_seconds": dialogue_seconds,
        "beat_needs_seconds": beat_needs_seconds,
    }


def _firstlast_camera_lock(has_first: bool) -> tuple[str, str]:
    """firstlast 全片机位止血（2026-11-09 用户定案）。

    首尾帧只给模型两张静图：图片1之外没有场景几何、视差和画外信息。逐拍换镜
    （环绕/手持/快速推近）会逼模型在单图空间里自己发明机位，真机成片表现为持续
    漂移/乱晃（用户提供的 8s 样片：scene 检测无硬切，沙发/灯/边桌等背景特征在缓慢
    位移，人物构图同时被推近）。故 firstlast 默认全片一条固定镜头，只让主体在格内
    动作；等场景板/previz 参考链落地、图片1之外真的有空间依据后，才允许恢复一条
    连续运镜。
    """
    return (_HOLD_CAMERA_FRAME if has_first else _HOLD_CAMERA_NOFRAME, _HOLD_META)


def _time_segments(spec: dict[str, Any], duration_hint: int,
                   first_frame_desc: str, last_frame_desc: str,
                   prev_tail_desc: str,
                   has_first: bool = True, has_last: bool = True,
                   skeleton: "VideoSkeleton | None" = None,
                   camera_override: str = "") -> str:
    """④ 时间分镜（firstlast 核心）：首帧→动作流/逐句剧情→尾帧，覆盖整段桥段。

    - 首帧段带「上楼层尾帧」衔接上下文（转场判断融入生成，不单独判定）；
    - 中段两条取材路（批 2）：提取层 `action_sequence` ≥3 拍时用它撑分镜（英文视觉描述、
      动作更细；只丢 desc[0]——它按合同只复述首帧，末拍是「落在尾帧」的动作收束仍进中段，
      第 102 刀），正文引语按句子的相对位置挂回最近一拍；
      不足 3 拍或没有时退回批 1 的正文逐句成拍（第 80 刀：跳过被 opening 覆盖的开头句、
      对白归属与台词同拍、超量均分合并不丢剧情，见 `_fold_firstlast_mid`）；
    - 每拍可带 scene：只在**变化**处渲染「场景切换：…」，换场景因此成为一次转场拍；
    - 尾帧段收尾定格；默认全片固定机位（2026-11-09 止血），缺图时诚实标注（R2）。
    - `camera_override` 非空 ⇒ 分镜序列的运镜镜头：全片复用同一条连续路径，不逐拍换镜。
    - 机位：默认全片一条固定镜头（Static Shot）；`camera_band_overrides` 不参与。
    - skeleton（提示词模版骨架）：beat_labels 覆盖三段节奏名、default_duration 覆盖
      时长兜底；None = 逐字现行为。
    - 拍界由 `_firstlast_beat_plan` 统一产出（与对白时间轴同一份）。
    """
    sk = skeleton or VideoSkeleton()
    beats, _, _quote_stats = _firstlast_beat_plan(
        spec, duration_hint, first_frame_desc, last_frame_desc, prev_tail_desc,
        has_first, has_last, sk,
    )
    cam = camera_override.strip() or _firstlast_camera_lock(has_first)[0]
    lines: list[str] = []
    for start, end, label, content, extra in beats:
        tail = f"（{extra}）" if extra else ""
        lines.append(f"[{start}s–{end}s｜{label}]：{cam}；{content}{tail}")
    return "\n".join(lines)


# ── 对白时间轴（第 99 刀，2026-11-08 用户定案）─────────────────────────────────
# 用户口径：对话生成的配音是一句一句拼起来的**紧凑**录音，直接整条塞给视频模型会把
# 「一句接一句」的节奏带进画面。所以拼接音频只当**音色/语气参考**（H3 `<Audio 1>` 的
# reference 关系），真正「哪一句在哪个场景/动作/镜头说」由 ④ 的拍窗口决定。
# 拍界真源 = `_firstlast_beat_plan`（不另算一遍）；台词与分镜的关联靠**引语文本**。
_QUOTE_PAIR_RE = re.compile(r"[「“『‘]([^」”』’]*)[」”』’]")
_DIALOGUE_UNNAMED = "旁白"
# 中文语速估算（用于「台词比视频还长」的诚实告警）：4.5 字/秒是字幕常态值。
_DIALOGUE_CHARS_PER_SEC = 4.5


def _dialogue_norm(text: str) -> str:
    """归一化台词文本：只留字母/数字/汉字，去掉标点与空白（比对用，不用于渲染）。"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text or "").lower()


def _beat_quotes(text: str) -> list[str]:
    """取一拍里的全部引语片段（「」/“”/『』/‘’）。"""
    return [m.group(1).strip() for m in _QUOTE_PAIR_RE.finditer(text or "") if m.group(1).strip()]


def match_dialogue_to_beats(
    audio_lines: list[Any] | None,
    beats: list[tuple[int, int, str, str, str]],
) -> list[tuple[int, tuple[int, int, str, str, str], dict[str, Any], bool]]:
    """把本轮 TTS 台词按**顺序**落到 firstlast 的拍窗口。

    返回 `[(beat_index, beat, line, exact)]`，按拍序（= 时间序）排列。

    命中规则（宁可不错位，也不静默丢句）：
    1. 文本命中：归一化后「台词 ⊂ 该拍引语」或「引语 ⊂ 台词」即算同一句；
       只在 `cursor` 及其之后的引语拍里找，保证多句台词的先后不被打乱。
    2. 未命中：按顺序铺到还空着的引语拍（一段引语里两句台词是常态）。
    3. 一拍引语都没有（正文全是动作描写）：按中段拍均分。
    """
    entries = [
        ln for ln in (audio_lines or [])
        if isinstance(ln, dict) and str(ln.get("text") or "").strip()
    ]
    if not entries or not beats:
        return []
    quote_beats = [
        (idx, _dialogue_norm("".join(_beat_quotes(beat[3]))))
        for idx, beat in enumerate(beats)
    ]
    quote_beats = [(idx, quote) for idx, quote in quote_beats if quote]

    out: list[tuple[int, tuple[int, int, str, str, str], dict[str, Any], bool]] = []
    used: dict[int, int] = {}
    pending: list[dict[str, Any]] = []
    cursor = 0
    for ln in entries:
        needle = _dialogue_norm(str(ln.get("text") or ""))
        hit = None
        for pos in range(cursor, len(quote_beats)):
            quote = quote_beats[pos][1]
            if needle and (needle in quote or (len(quote) >= 2 and quote in needle)):
                hit = pos
                break
        if hit is None:
            pending.append(ln)
            continue
        idx = quote_beats[hit][0]
        cursor = hit
        used[idx] = used.get(idx, 0) + 1
        out.append((idx, beats[idx], ln, True))

    if pending:
        free = [pos for pos, (idx, _) in enumerate(quote_beats) if idx not in used]
        if free:
            for i, ln in enumerate(pending):
                idx = quote_beats[free[min(i, len(free) - 1)]][0]
                used[idx] = used.get(idx, 0) + 1
                out.append((idx, beats[idx], ln, False))
        elif quote_beats:
            # 全部引语拍都被占：追加到最后一句台词所在拍（同窗口内自然连说）。
            target = quote_beats[-1][0]
            for ln in pending:
                out.append((target, beats[target], ln, False))
        else:
            # 一拍引语都没有（正文全是动作描写）：台词按顺序铺到中段拍，均分。
            mids = [idx for idx in range(1, len(beats) - 1)] or [len(beats) // 2]
            for i, ln in enumerate(pending):
                idx = mids[i * len(mids) // len(pending)]
                out.append((idx, beats[idx], ln, False))

    out.sort(key=lambda item: item[0])
    return out


def build_dialogue_reference(
    audio_lines: list[Any] | None,
    beats: list[tuple[int, int, str, str, str]],
    *,
    ref_label: str = "<Audio 1>",
) -> dict[str, Any] | None:
    """把本轮 TTS 台词编成「对白时间轴」参考块（firstlast 专用，第 99 刀）。

    用户口径（2026-11-08）：拼接音频是**紧凑**的逐句录音，整条塞给视频模型会把
    「一句接一句」的节奏带进画面。所以它只作 `<Audio 1>` 的 reference（音色/语气），
    「哪句在哪个镜头说」由 ④ 的拍窗口承载（拍界真源 `_firstlast_beat_plan`）。

    第 105 刀（实稿发现 5）：每个拍窗口按 `_DIALOGUE_CHARS_PER_SEC` 限速，装不下的
    整句**剔除**（与 ④ 的 `_fit_beat_quotes` 同口径）。15s 塞 53 句时 ⑤ 若照列，
    与 ④ 自相矛盾，模型只能把整段挤进两秒。剔除句数进 warnings（不静默）。

    返回结构化 dict（`text` 是可直接追加进 video_prompt 的渲染块）；无台词返回 None。
    台词**归属**（speaker）沿用 TTS 行；正文一句引语都没有时按中段拍均分并告警。
    """
    matched = match_dialogue_to_beats(audio_lines, beats)
    if not matched:
        return None
    speaker_ids: dict[str, str] = {}
    timeline: list[dict[str, Any]] = []
    dropped = 0
    inexact = 0
    used_chars: dict[int, int] = {}
    for idx, beat, line, exact in matched:
        start, end, label = int(beat[0]), int(beat[1]), str(beat[2])
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        capacity = max(0, int((end - start) * _DIALOGUE_CHARS_PER_SEC))
        used = used_chars.get(idx, 0)
        if used + len(text) > capacity:
            dropped += 1
            continue
        used_chars[idx] = used + len(text)
        name = str(line.get("speaker") or "").strip() or _DIALOGUE_UNNAMED
        sid = speaker_ids.setdefault(name, f"S{len(speaker_ids) + 1}")
        if not exact:
            inexact += 1
        timeline.append({
            "start": start, "end": end, "label": label,
            "speaker": name, "speaker_id": sid, "text": text, "exact": exact,
        })

    speakers = "；".join(f"({sid})={name}" for name, sid in speaker_ids.items())
    lines = [
        f"[对白参考]：{ref_label} 是本轮角色对白的连续参考录音（同轮 TTS 顺序拼接），"
        "只锁定音色、语气与情绪；不要复制它的音轨，也不要沿用「一句紧接一句」的紧凑节奏"
        "——成片人声在各拍窗口内重新生成。",
        f"[对白说话人]：{speakers}",
    ]
    if timeline:
        lines.append("[对白时间轴]：")
        for item in timeline:
            lines.append(
                f"[{item['start']}s–{item['end']}s｜{item['label']}] "
                f"({item['speaker_id']}) {item['speaker']}：<d>[Chinese] {item['text']}</d>"
            )
        lines.append(
            "[对白执行]：每句台词只在所属时间窗内说完，句间留自然停顿与环境声，"
            "口型与台词逐字对齐；参考音频的紧凑拼接不得带进画面节奏，台词逐字照读、不改写不增删。"
        )
    else:
        lines.append("[对白时间轴]：（无：本轮台词均超出所在拍可说完的容量，未安排进镜头）")

    warnings: list[str] = []
    if dropped:
        warnings.append(
            f"{dropped} 句台词超出所在拍可说完的容量（约 {_DIALOGUE_CHARS_PER_SEC} 字/秒），"
            "已从对白时间轴剔除：建议加长视频或精简台词"
        )
    if inexact:
        warnings.append(
            f"{inexact} 句台词未在正文引语中逐字命中，按顺序铺到空余镜头窗口（供人工核对）"
        )
    return {
        "ref_label": ref_label,
        "speakers": {sid: name for name, sid in speaker_ids.items()},
        "timeline": timeline,
        "text": "\n".join(lines),
        "warnings": warnings,
        "line_count": len(timeline),
    }



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
# 第一人称下「身后那人 / 身后的人」就是叙事者＝镜头，不能写成第三人称旁观。
# 2026-11-09 用户复审实锤：「等身后那人看清」在第一人称分镜里是错的。
# 只替换名词短语、保留前面的动词（「等…看清」→「等镜头看清」）。
_POV_BEHIND_PERSON_RE = re.compile(r"身后那?人")


def _pov_rewrite_beat_text(text: str) -> str:
    """第一人称 beats 改写：把「身后那人」指代换成「镜头」。"""
    return _POV_BEHIND_PERSON_RE.sub("镜头", text)


# 第一人称叙事者的男性部位必须带归属（用户 2026-11-10：不能只写「柱身」）。
_POV_NARRATOR_PART_RE = re.compile(
    r"(?<!镜头角色的)(?:他的)?(柱身|阴茎|肉棒|阳具|龟头|囊袋|前列腺)"
)


def _pov_bind_narrator_parts(text: str) -> str:
    return _POV_NARRATOR_PART_RE.sub(r"镜头角色的\1", text)


def _pov_replace_narrator(text: str, narrator: str) -> str:
    """第一人称段不得把叙事者写成第三人本名；统一换成「镜头角色」。"""
    name = str(narrator or "").strip()
    if not name:
        return text
    return text.replace(name, "镜头角色")


def _silent_speech_text(text: str) -> str:
    """对白只拍口型、不生成音频：统一写成「嘴唇开合（无声音）」。"""
    # 「准备开口说话」先收成「嘴唇微张」，避免下面的全局替换拼出
    # 「准备嘴唇开合（无声音）」这种别扭句。
    value = text.replace("准备开口说话", "嘴唇微张").replace("准备开口", "嘴唇微张")
    value = value.replace("开口说话", "嘴唇开合（无声音）")
    return re.sub(r"嘴唇开合(?!（无声音）)", "嘴唇开合（无声音）", value)


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
    timing = str(spec.get("shot_image_timing") or "start").strip().lower()
    if will_have_frame and timing in ("mid", "end", "at"):
        # 图片不是首帧：不能再写「作为准确起始帧/首帧必须复现图片1」，否则
        # 视频模型会把中段构图塞进 0s（用户 2026-11-10 实锤：镜0 图片是 3–6s）。
        second = int(spec.get("image_time_s") or 0)
        frame_note = (
            f"；输入图片不是首帧：图片1只是本镜第 {second} 秒的构图锚点，"
            f"不得当作视频第 0 秒；先按[时间分镜]演到第 {second} 秒，再对齐图片1的构图继续"
        )
    else:
        frame_note = (
            _ESTABLISH_FRAME_NOTE if spec.get("establish")
            else (_FRAME_FIDELITY_NOTE if will_have_frame else "")
        ) if will_have_frame else ""
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
    segments = (
        _shot_beats_segments(spec) if spec.get("shot_video_beats")
        else _climax_time_segments(spec, duration_hint, sk, will_have_frame, ctr)
    )
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
    prompt = _assemble_blocks(blocks, sk)
    # 对白口型措辞全局归一（含 move/hold 路径）：不得残留「开口说话」。
    prompt = _silent_speech_text(prompt)
    if _first_person_view(spec):
        # 第一人称：叙事者＝镜头，他的部位必须带「镜头角色的」归属，不能裸写部位名。
        prompt = _pov_bind_narrator_parts(prompt)
    return prompt


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
    camera_override: str = "",
) -> str:
    """首尾帧·剧情影片：五段式（H3 骨架；⑥ 负面约束已退役，见 climax 同处注释）。

    区块：① 元信息 ② 双图绑定 ③ 主体/场景 ④ 时间分镜 ⑤ 音频。
    风格声明已停用（有参考图定调）。has_first/has_last 标识是否真的提供了首/尾帧图（R2 缺图守卫）。
    `camera_override` 非空 ⇒ 分镜序列的 move 镜头：① 写一次完整路径，④ 每拍用短句
    复用同一条路径，不逐拍换镜；空 ⇒ 旧整楼 firstlast 固定机位（零回归）。
    """
    sk = skeleton or VideoSkeleton()
    blocks: dict[str, str] = {}
    path = camera_override.strip()
    if path:
        camera_note = _SHOT_MOVE_META.format(path=path)
        beat_camera = _SHOT_MOVE_CAMERA
    else:
        beat_camera, camera_note = _firstlast_camera_lock(has_first)
    # 覆盖式分镜：景别/主体运动/触发声同样进 ①（固定机位也必须有主体运动）。
    motion_note = "；".join(_shot_motion_notes(spec))
    if motion_note:
        camera_note = f"{camera_note}；{motion_note}" if camera_note else motion_note
    meta_notes = "；".join(p for p in (camera_note, sk.meta_note) if p)
    note = f"；{meta_notes}" if meta_notes else ""
    blocks[SECTION_META] = (
        f"[{sk.title(SECTION_META, '元信息')}]："
        f"{_meta(duration_hint, '', model_name, size, sk.default_duration)}{note}"
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
                         prev_tail_desc, has_first, has_last, sk,
                         camera_override=beat_camera)
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

    ⚠ 第 104 刀：`spec` 只用来取在场角色/机位（②⑤），**不再消费整楼层的
    `video_subject_scene` / `audio_design`**——短桥段复述整楼层事实会把别处的场景与
    事件音效塞进过渡（③ 用 `_transition_subject_scene`、⑤ 用中性环境声）。

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
    subject = _transition_subject_scene(spec)
    if subject:
        blocks[SECTION_SUBJECT] = f"[{sk.title(SECTION_SUBJECT, '主体/场景')}]：{subject}"
    blocks[SECTION_SEGMENTS] = (
        f"[{sk.title(SECTION_SEGMENTS, '转场分镜')}]：\n"
        + _transition_segments(spec, prev_tail_desc, first_frame_desc,
                               has_prev_tail, has_first, sk)
    )
    # 第 104 刀（实稿发现 4）：转场没有独立的音频提取，整楼层 `audio_design` 里的事件音效
    # （实稿「楼下闷响」发生在楼层结尾）搬进几秒的桥段会让过渡凭空塞事件 ⇒ 退回中性环境声，
    # 与「无 audio_design」时的口径逐字一致。
    blocks[SECTION_AUDIO] = f"[{sk.title(SECTION_AUDIO, '音频')}]：{_audio_hint({})}"
    return _assemble_blocks(blocks, sk)


def _clean_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """对 scene_spec 文本字段做破甲标记还原（复用共享清洗规则 prompt_clean）。

    图像生成的防拦截是两层：① restore_jailbreak 还原 @()@ 破甲标记（纯函数，本层）；
    ② _apply_regex(IMAGE_PROMPT) 用户正则清洗（接线层，需 ctx，不在纯函数边界内）。

    本模块只做第①层兜底，统一走 prompt_clean.clean_spec_text_fields，
    避免 @(x)@ 残留进视频提示词。
    """
    return prompt_clean.clean_spec_text_fields(spec)


def _caps_allows_last_frame(caps: dict[str, Any] | None) -> bool:
    """模板是否收第二张图（尾帧 / 转场终点）。caps=None（模板未知）⇒ 保持现行为 True。"""
    return True if not caps else bool(caps.get("supports_last_frame"))


def _caps_reference_slots(caps: dict[str, Any] | None) -> int:
    """模板能收的角色参考图槽数。caps=None（模板未知）⇒ 不设上限。"""
    if not caps:
        return 99
    try:
        return max(0, int(caps.get("reference_slots") or 0))
    except (TypeError, ValueError):
        return 0


def _shot_seconds(spec: dict[str, Any], duration_hint: int,
                  caps: dict[str, Any] | None) -> int:
    """单镜时长：分镜计划 > 调用方预算 > 模板默认 > 内置兜底。"""
    value = int(spec.get("shot_duration_s") or 0)
    if value > 0:
        return value
    if duration_hint > 0:
        return int(duration_hint)
    if caps:
        value = int(caps.get("duration") or 0)
        if value > 0:
            return value
    return _DEFAULT_DURATION


# 第一人称下「机位在她右后方 / 床尾 / 贴地」这类第三人称位置会与
# 「镜头＝叙事者眼睛」互斥：只归一这些明确站到角色外部的机位，
# 叙事者站得住的机位（院墙外/窄道入口/院门内侧/门内三步/案侧/床沿上方）原样保留。
# 原始 beat 仍留在审阅稿的「关键帧内部时间轴」里，供定位判据问题。
_POV_GAZE_RE = re.compile(r"(略俯视|俯视|仰视|平视|侧视|回望|回看)")
_POV_CAMERA_KEEP_MARKS = ("叙事者", "镜头角色", "第一人称", "POV", "视点")
# 第一人称下「侧后方/背后/背面」是第三人称机位角度；保留景别，去掉这个角度。
_POV_BACK_ANGLE_RE = re.compile(r"[，,/]?\s*(?:侧后方|正后方|背后|背面)\s*")
# 只有这些「明确站到角色外部」的机位才需要归一；叙事者站得住的机位
# （院墙外/窄道入口/院门内侧/门内三步/案侧/床沿上方）必须原样保留。
_POV_THIRD_PERSON_CAMERA_RE = re.compile(
    r"机位在(?:她|他)(?:的)?(?:右|左)(?:后|前)方"
    r"|(?:她|他)(?:的)?(?:右|左)(?:后|前)方"
    r"|肩后|床尾|红毯(?:左侧|右侧|一侧|侧前方|前端)|厂房上方|柱脚前|贴地"
)
# 判据没显式给 viewpoint 时的保守反推：
#   POV＝机位句里出现叙事者/第一人称/视点/画框边缘；
#   旁观＝明确在角色右后/左前/肩后、床尾、红毯一侧、贴地等叙事者不在的位置。
#   ⛔ 「机位在她背后/身后/侧后方」不列入旁观：叙事者本人可能就站在那里。
# ⛔ 不能用裸「镜头角色」当 POV 证据：第三人称作品也用它指画面里的主人公
#   （「女管事目光在镜头角色身上停了一瞬」「镜头角色仰倒在铁管旁」），会把旁观拍误判成 POV。
_BEAT_POV_MARKERS = (
    "叙事者", "第一人称", "POV", "视点", "镜头后", "画面下缘", "画框边缘",
)
_BEAT_OBSERVER_RE = re.compile(
    r"机位在(?:她|他)(?:的)?(?:右|左)(?:后|前)方"
    r"|(?:她|他)(?:的)?(?:右|左)(?:后|前)方"
    r"|肩后|床尾|红毯(?:左侧|右侧|一侧|侧前方|前端)|厂房上方|柱脚前|贴地|旁观|第三人称"
)


def _explicit_beat_viewpoint(beat: dict[str, Any]) -> str:
    """判据逐拍显式标的视角（优先于反推）。"""
    raw = str(beat.get("viewpoint") or "").strip().lower()
    if raw in ("pov", "first_person", "first-person", "first person", "第一人称", "叙事者", "主观"):
        return "pov"
    if raw in ("observer", "third_person", "third-person", "third person", "第三人称", "旁观", "客观"):
        return "observer"
    return ""


def _infer_beat_viewpoint(beat: dict[str, Any]) -> str:
    """从机位句反推单拍视角；两类线索同时出现或都没有 ⇒ 交给图片视角兜底。"""
    text = " ".join(
        str(beat.get(key) or "")
        for key in ("camera_position", "framing", "description", "subject_motion")
    )
    pov = any(mark in text for mark in _BEAT_POV_MARKERS)
    observer = bool(_BEAT_OBSERVER_RE.search(text))
    if pov and not observer:
        return "pov"
    if observer and not pov:
        return "observer"
    return ""


def _beat_viewpoint(beat: dict[str, Any]) -> str:
    return _explicit_beat_viewpoint(beat) or _infer_beat_viewpoint(beat)


def _beat_seconds(beat: dict[str, Any]) -> int:
    try:
        return max(1, int(float(beat.get("duration_s") or 2)))
    except (TypeError, ValueError):
        return 2


def _image_beat_index(spec: dict[str, Any], beats: list[dict[str, Any]]) -> int:
    """图片落在第几拍：图片已锁在视频第 N 秒，那一拍的视角必须跟图片一致。"""
    if not beats:
        return 0
    timing = str(spec.get("shot_image_timing") or "start").strip().lower()
    if timing == "end":
        return len(beats) - 1
    if timing == "start":
        return 0
    try:
        target = int(spec.get("image_time_s") or 0)
    except (TypeError, ValueError):
        target = 0
    cursor = 0
    for index, beat in enumerate(beats):
        duration = _beat_seconds(beat)
        if cursor <= target < cursor + duration:
            return index
        cursor += duration
    return len(beats) - 1


def _shot_viewpoint_plan(spec: dict[str, Any]) -> tuple[str, list[str]]:
    """本支视频的视角计划：返回 (pov|observer|mixed, 每拍有效视角)。

    图片所在拍以作品/图片视角为准（图片已经锁在视频第 N 秒），
    其余拍优先吃判据的 `viewpoint`，没标再从机位句反推，最后落回图片视角。
    """
    beats = [beat for beat in (spec.get("shot_video_beats") or [])
             if isinstance(beat, dict)]
    image_view = "pov" if _first_person_view(spec) else "observer"
    if not beats:
        return image_view, []
    image_index = _image_beat_index(spec, beats)
    effective: list[str] = []
    for index, beat in enumerate(beats):
        viewpoint = _beat_viewpoint(beat) or image_view
        if index == image_index:
            viewpoint = image_view
        effective.append(viewpoint)
    if all(viewpoint == "pov" for viewpoint in effective):
        return "pov", effective
    if all(viewpoint == "observer" for viewpoint in effective):
        return "observer", effective
    return "mixed", effective


def _pov_safe_camera_position(text: str) -> str:
    """POV 拍只归一明确站到角色外部的机位，其余原样保留。

    ⛔ 「她右后方」「肩后」「床尾」「贴地」不能原样送进 POV 段：模型会照它切到
      叙事者不可能站的位置，POV 与机位描述二选一。
    ✅ 「院墙外·仰视树冠（机位在窄道入口，镜头朝檐角）」「机位在门内三步，正对她」
      「机位在案侧，随她落座」是叙事者站得住的位置，必须保留，不能削成一句「叙事者视线」。
    ⚠ 这是编译守卫，不修改判据原始 beat；第三人称旁观拍原样保留。
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    if any(mark in raw for mark in _POV_CAMERA_KEEP_MARKS):
        return raw
    if _POV_THIRD_PERSON_CAMERA_RE.search(raw):
        gaze = _POV_GAZE_RE.search(raw)
        return f"叙事者视线·{gaze.group(1)}" if gaze else ""
    return raw


def _pov_safe_framing(text: str) -> str:
    """POV 拍删掉景别里隐含的第三人称背面角度，保留景别本身。"""
    raw = str(text or "").strip()
    if not raw:
        return ""
    cleaned = _POV_BACK_ANGLE_RE.sub("", raw).strip(" ，,/")
    return cleaned or raw


_IMAGE_ALIGN_PREFIX = "起帧对齐图片1"
_IMAGE_ALIGN_RE = re.compile(r"^\s*起帧对齐图片1[：:，,；;]?\s*")


def _shot_beats_segments(spec: dict[str, Any]) -> str:
    """关键帧内部时间轴：段间硬切、段内按该段机位；近景不再单独出图。"""
    beats = spec.get("shot_video_beats") or []
    mode, viewpoints = _shot_viewpoint_plan(spec)
    narrator = str(spec.get("narrator") or "").strip()
    image_pose = str(spec.get("image_pose") or "").strip()
    image_index = _image_beat_index(
        spec, [beat for beat in beats if isinstance(beat, dict)],
    )
    lines: list[str] = []
    cursor = 0
    for index, beat in enumerate(beats):
        if not isinstance(beat, dict):
            continue
        duration = _beat_seconds(beat)
        start, end = cursor, cursor + duration
        cursor = end
        fallback_viewpoint = "pov" if _first_person_view(spec) else "observer"
        viewpoint = viewpoints[index] if index < len(viewpoints) else fallback_viewpoint
        parts: list[str] = []
        if index > 0 and beat.get("cut"):
            parts.append("硬切")
        if mode == "mixed":
            parts.append("第一人称" if viewpoint == "pov" else "第三人称")
        # 图片锁在第 N 秒：只有图片所在拍写「起帧对齐图片1」；
        # 判据/本地模型写在别的拍上的同句一律剥掉，避免整支护词每拍都重对齐。
        if index == image_index:
            parts.append(_IMAGE_ALIGN_PREFIX)
            if image_pose:
                parts.append(f"起帧：{image_pose}姿")
        for key in ("framing", "camera_position", "camera_motion", "subject_motion", "description"):
            value = str(beat.get(key) or "").strip()
            if key == "description":
                value = _IMAGE_ALIGN_RE.sub("", value)
            if viewpoint == "pov":
                value = _pov_replace_narrator(value, narrator)
                if key == "framing":
                    value = _pov_safe_framing(value)
                if key == "camera_position":
                    value = _pov_safe_camera_position(value)
                if key in ("subject_motion", "description"):
                    value = _pov_rewrite_beat_text(value)
            if value and key in ("subject_motion", "description"):
                value = _silent_speech_text(value)
            if value:
                parts.append(value)
        sound = str(beat.get("sound_cue") or "").strip()
        if sound:
            parts.append(f"声音：{sound}")
        if parts:
            lines.append(f"{start}–{end}s｜" + "；".join(parts))
    return "\n".join(lines)


def _image_timing_notes(spec: dict[str, Any]) -> tuple[str, ...]:
    """图片相对本镜的位置（start/mid/end）——中段/尾帧必须明说，否则模型会把图当首帧。

    用户 2026-11-10 实锤：只说「中段/尾帧」不够，图片提示词必须能对上本镜**第几秒**，
    否则首帧图与视频开头对不上。秒数优先取 `image_time_s`，缺省按本镜时长推算。
    """
    timing = str(spec.get("shot_image_timing") or "").strip().lower()
    if timing not in ("mid", "end", "at"):
        return ()
    try:
        duration = int(spec.get("shot_duration_s") or 0)
    except (TypeError, ValueError):
        duration = 0
    try:
        second = int(spec.get("image_time_s") or 0)
    except (TypeError, ValueError):
        second = 0
    if second <= 0:
        if timing == "end":
            second = max(1, duration)
        else:
            second = max(1, (duration or 2) // 2)
    span = f"，共 {duration} 秒" if duration > 0 else ""
    if timing == "at":
        return (
            f"图片1=本镜第 {second} 秒的画面（不是首帧{span}）："
            f"视频从本镜第 1 段的起点开始演，先演完第 {second} 秒之前的段，"
            "到图片1的构图时对齐，再继续后面的段",
        )
    if timing == "mid":
        return (
            f"图片1=本镜中段画面（本镜第 {second} 秒{span}）："
            "视频从本镜第 1 段的起点开始演，到图片1的构图时对齐，再继续",
        )
    return (
        f"图片1=本镜尾帧画面（本镜第 {second} 秒{span}）："
        "视频从本镜第 1 段的起点开始演，最后落到图片1的构图",
    )


def _shot_role_ref_notes(spec: dict[str, Any], start_index: int = 2) -> tuple[str, ...]:
    """分镜视频的角色参考图绑定：图片1=本镜画面，图片2 起=角色底图。

    用户 2026-11-10 口径：视频靠角色底图锁外貌，与图片生成模式（文生图/图生图）无关。
    `share_last_frame` 型（move）由 firstlast 分支从图片3 起绑定，不走本函数。
    """
    names = [str(name).strip() for name in (spec.get("role_refs") or []) if str(name).strip()]
    if not names:
        return ()
    parts = [
        f"图片{start_index + index}={name} 的角色参考图"
        "（只锁身份、五官、发型与体型；当下衣着、姿势与所在场景以图片1为准）"
        for index, name in enumerate(names)
    ]
    return ("视频角色参考：" + "；".join(parts),)


def _beats_have_cuts(spec: dict[str, Any]) -> bool:
    """本镜的时间分镜是否包含多拍/硬切（决定 POV 声明能否继续锁图片1视点）。"""
    beats = [beat for beat in (spec.get("shot_video_beats") or [])
             if isinstance(beat, dict)]
    return len(beats) > 1 or any(bool(beat.get("cut")) for beat in beats)


def _shot_beats_contract(spec: dict[str, Any]) -> ClimaxCameraContract:
    """关键帧多镜头：图片1 锁人物身份/造型，场景与机位按[时间分镜]逐段执行。

    ⛔ 不再写「所有段共用图片1的场景」：多场景镜（侧门→走廊→卧室、榻边→水盆→门边）
      与这条硬约束互斥，模型只能二选一（丢场景或丢参考图）。场景切换以 ④ 明写的段为准。
    """
    if spec.get("establish"):
        first_note = (
            "本片以图片1的空间、道具与光线为准；按[时间分镜]逐段执行，"
            "段间硬切只换机位与取景，不新增主要人物"
        )
    elif spec.get("role_refs"):
        first_note = (
            "图片1=本镜人物、服装、姿势与场景参考；角色外貌只按角色参考图锁定；"
            "按[时间分镜]逐段执行，段间硬切到新机位，段内按该段机位运动；"
            "所有段以图片1的人物身份与造型为准，不重新设定人物；"
            "场景与空间按[时间分镜]逐段写明的为准，段间切景只换机位与取景，不新增人物"
        )
    else:
        first_note = (
            "本片以图片1为唯一人物、服装与场景参考；按[时间分镜]逐段执行，"
            "段间硬切到新机位，段内按该段机位运动；"
            "所有段以图片1的人物身份与造型为准，不重新设定人物；"
            "场景与空间按[时间分镜]逐段写明的为准，段间切景只换机位与取景，不新增人物"
        )
    notes: list[str] = [first_note]
    # 视角按这一支视频的逐拍计划走：图片所在拍的视角与图片一致；
    # 其余拍吃判据的 viewpoint，没标再从机位句反推。
    # - pov：全局 POV 声明（多拍/硬切不再锁图片1视点）；
    # - observer：不注入 POV，明确叙事者不在画面里；
    # - mixed：全局混合声明 + ④ 每段标「第一人称/第三人称」。
    viewpoint_mode, _viewpoints = _shot_viewpoint_plan(spec)
    if viewpoint_mode == "pov":
        notes.extend(_pov_shot_notes(
            spec, anchored=True, cuts=_beats_have_cuts(spec), force=True,
        ))
    elif viewpoint_mode == "mixed":
        notes.append(_POV_VIEW_MIXED)
        notes.extend(_pov_limb_notes(spec))
    else:
        notes.append(_OBSERVER_VIEW_NOTE)
    notes.extend(_image_timing_notes(spec))
    notes.extend(_shot_role_ref_notes(spec, 2))
    sound = str(spec.get("shot_sound_cue") or "").strip()
    if sound:
        notes.append(f"本帧触发声/事件：{sound}")
    return ClimaxCameraContract(
        intent=CLIMAX_INTENT_REVEAL, reason="shot_beats",
        camera="", notes=tuple(notes), beat_fx="", beat_sync="",
    )


def _shot_motion_notes(spec: dict[str, Any]) -> tuple[str, ...]:
    """覆盖式分镜的景别/主体运动/触发声：固定机位也必须让身体在动。"""
    notes: list[str] = []
    framing = str(spec.get("shot_framing") or "").strip()
    if framing:
        notes.append(f"景别/角度：{framing}")
    motion = str(spec.get("shot_subject_motion") or "").strip()
    if motion:
        notes.append(f"主体运动（固定机位也必须有）：{motion}")
    cue = str(spec.get("shot_sound_cue") or "").strip()
    if cue:
        notes.append(f"本镜触发声/事件：{cue}")
    return tuple(notes)


def _shot_hold_contract(spec: dict[str, Any], has_frame: bool) -> ClimaxCameraContract:
    """cut 镜头：全片固定机位，只让主体在格内做动态延伸。"""
    return ClimaxCameraContract(
        intent=CLIMAX_INTENT_HOLD, reason="shot_cut",
        camera=_HOLD_CAMERA_FRAME if has_frame else _HOLD_CAMERA_NOFRAME,
        notes=(_HOLD_META, *_pov_shot_notes(spec, anchored=has_frame),
               *_image_timing_notes(spec), *_shot_role_ref_notes(spec, 2),
               _HOLD_MOTION, _hold_rhythm(spec), *_shot_motion_notes(spec)),
        # 2026-11-09 用户定案：「能量爆发式特效」对分镜没有意义，cut 不写特效句。
        beat_fx="", beat_sync="",
    )


def _shot_establish_contract(spec: dict[str, Any]) -> ClimaxCameraContract:
    """环境空镜：固定机位或一条连续路径，只演环境级运动。"""
    path = str(spec.get("shot_camera") or "").strip()
    late = str(spec.get("shot_image_timing") or "").strip().lower() in ("mid", "end", "at")
    if path:
        meta = _ESTABLISH_MOVE_LATE_META if late else _ESTABLISH_MOVE_META
        return ClimaxCameraContract(
            intent=CLIMAX_INTENT_REVEAL, reason="shot_establish_move",
            camera=_SHOT_MOVE_CAMERA,
            notes=(meta.format(path=path), *_image_timing_notes(spec),
                   *_shot_role_ref_notes(spec, 2),
                   *_shot_motion_notes(spec), _ESTABLISH_WORLD),
            beat_fx="", beat_sync="",
        )
    meta = _ESTABLISH_HOLD_LATE_META if late else _ESTABLISH_HOLD_META
    return ClimaxCameraContract(
        intent=CLIMAX_INTENT_HOLD, reason="shot_establish",
        camera=_HOLD_CAMERA_FRAME,
        notes=(meta, *_image_timing_notes(spec),
               *_shot_role_ref_notes(spec, 2),
               *_shot_motion_notes(spec), _HOLD_MOTION, _hold_rhythm(spec)),
        beat_fx="", beat_sync="",
    )


def _shot_move_single_contract(spec: dict[str, Any]) -> ClimaxCameraContract:
    """move_single：单图 + ①声明的同一条连续机位路径，不逐拍换镜。"""
    path = str(spec.get("shot_camera") or "").strip() or _REVEAL_CAMERA_PAN
    motion = int(spec.get("motion") or 0)
    return ClimaxCameraContract(
        intent=CLIMAX_INTENT_REVEAL, reason="shot_move_single",
        camera=_SHOT_MOVE_CAMERA,
        notes=(_SHOT_MOVE_META.format(path=path), *_pov_shot_notes(spec, anchored=False),
               *_image_timing_notes(spec), *_shot_role_ref_notes(spec, 2),
               _REVEAL_WORLD, *_shot_motion_notes(spec)),
        beat_fx="", beat_sync=_climax_beat_sync(motion),
    )


# 分镜稀疏守卫：只查「这一段能不能拍」，不评价文学质量。
_SPARSE_ENV_CUES = (
    "光", "影", "阳", "灯", "窗", "门", "墙", "桌", "案", "地", "天", "夜", "晨", "暮",
    "雨", "风", "尘", "烟", "云", "树", "花", "草", "石", "木", "铁", "水", "火",
    "雪", "月", "衣", "裙", "发", "耳",
)
_SPARSE_ACTION_CUES = (
    "走", "站", "坐", "躺", "转", "抬", "伸", "握", "放", "推", "拉", "按", "跪", "趴",
    "抱", "靠", "退", "进", "停", "看", "望", "笑", "说", "开", "合", "落", "起",
    "晃", "抖", "颤", "蹭", "贴", "压", "抽", "掀", "盖", "穿", "脱", "解", "扣",
    "迈", "跑", "踢", "抓", "扶", "撑", "俯", "仰", "倾", "摆", "卷",
    "挂", "鼓", "飘", "吹", "垂", "扬", "拍", "揉", "摸", "碰",
)
# 特写/近景/局部本来就不需要交代环境与光线；不能拿环境词表误判为稀疏。
_SPARSE_CLOSEUP_CUES = (
    "特写", "近景", "中近景", "手部", "面部", "眼部", "嘴角", "局部", "微距",
)


def _sparse_beat_warnings(spec: dict[str, Any]) -> list[str]:
    """④ 每段是否写成了可拍画面；稀疏拍不静默通过，汇总成一条 warning。"""
    beats = [beat for beat in (spec.get("shot_video_beats") or [])
             if isinstance(beat, dict)]
    if not beats:
        return []
    issues: list[str] = []
    for index, beat in enumerate(beats, start=1):
        description = str(beat.get("description") or "").strip()
        subject = str(beat.get("subject_motion") or "").strip()
        camera = str(beat.get("camera_position") or "").strip()
        framing = str(beat.get("framing") or "").strip()
        if not description and not subject:
            issues.append(f"第{index}段无画面/动作")
            continue
        missing: list[str] = []
        if len(description) < 40:
            missing.append(f"description仅{len(description)}字")
        combined = f"{framing}{camera}{subject}{description}"
        closeup = any(cue in framing for cue in _SPARSE_CLOSEUP_CUES)
        if not closeup and not any(cue in combined for cue in _SPARSE_ENV_CUES):
            missing.append("缺环境/道具/光线")
        if not any(cue in combined for cue in _SPARSE_ACTION_CUES):
            missing.append("缺主体动作")
        if not camera:
            missing.append("缺机位")
        if missing:
            issues.append(f"第{index}段({'、'.join(missing)})")
    if not issues:
        return []
    return [
        "④ 分镜信息过稀：" + "；".join(issues)
        + "；视频模型只能自行补全（按合同写足 60–120 字可见画面）"
    ]


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
    caps: dict[str, Any] | None = None,
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
    - pending_frame（climax 自动链 / firstlast 首尾帧链 / transition 转场链）：提示词在成图
      **之前**编译、帧由下游链补挂 ⇒ 仍按 I2V/首尾帧声明，否则这段提示词自认文生视频、
      首帧自己重新构图。firstlast 的两张图总是成对产出（复用或重生）、转场的两张图也总是
      在提交前挂上（上尾帧来自历史、当前首帧由本链生成）⇒ 一个标记同时声明两张图。
    - caps（视频**工作流**模板能力，见 `template_store.video_template_caps`）：None＝模板未知
      ⇒ 逐字现行为（声明全部图片位）；给定 ⇒ 只声明模板真能消费的图片位（尾帧槽 / 参考图槽），
      且 firstlast 未显式设时长时取模板 `video_duration` 默认值——那是成片的物理长度，
      ① 的秒数、④ 的拍界与 `submit.duration` 必须同源（第 106 刀 · 实稿发现 1/2）。
    """
    preset = preset or {}
    spec = _clean_spec(spec or {})
    # 分镜角色参考图：图片1=本镜画面，图片2 起=角色底图（move 由 firstlast 分支从图片3 起）。
    # 槽数以模板能力为天花板：多声明一张前端不会上传的图，比少一张更坏（第 106 刀同源）。
    if mode in ("shot_cut", "shot_move_single", "shot_establish", "shot_establish_move"):
        ref_limit = _caps_reference_slots(caps)
        role_refs = [
            str(name).strip() for name in (spec.get("role_refs") or []) if str(name).strip()
        ][:ref_limit]
        spec = {**spec, "role_refs": role_refs}
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
    # 高潮机位契约判定（仅 climax；其余模式为 None——firstlast 自 2026-11-09 起
    # 固定机位，转场短桥段仍允许运动）
    shot_intent: dict[str, str] | None = None
    # 对白时间轴（第 99 刀）：只有 firstlast 正片产出；**不拼进 video_prompt**——
    # 模板没暴露参考音频槽时前端不追加，保持 2026-09-20「视频提示词只有效果音」口径。
    dialogue: dict[str, Any] | None = None
    # ④ 台词预算统计（第 105 刀）：仅 firstlast 的拍计划产出。
    quote_stats: dict[str, int] = {}

    if mode == "firstlast":
        # 批 1：生产链在成帧**之前**编译（图地址此时必为空），但首尾帧随后由前端链
        # 成对挂上 ⇒ pending_frame 视为「图会到」，② 不再写「无参考图」。
        # 第 106 刀（实稿发现 1/2）：模板能力是天花板——模板没有尾帧槽就没有「图片2」，
        # 没有参考图槽就不声明「图片3+」；未设时长时按模板 `video_duration` 默认值排拍界，
        # ① 的秒数与 ④ 的拍界、提交时长三处同源（实稿 15s 提示词 vs 10.125s 成片）。
        allows_last = _caps_allows_last_frame(caps)
        ref_limit = _caps_reference_slots(caps)
        has_first = bool(first_frame) or pending_frame
        has_last = (bool(last_frame) or pending_frame) and allows_last
        # 图片3+ 角色参考图同样以模板槽数为天花板：② 的职责文字由 `role_refs` 生成，
        # 超出槽数的只声明不剪 ⇒ 模型照样被指挥去看一张前端不会上传的图（发现 1 的同源缺口）。
        role_refs = [str(raw).strip() for raw in (spec.get("role_refs") or []) if str(raw).strip()]
        if len(role_refs) > ref_limit:
            spec = {**spec, "role_refs": role_refs[:ref_limit]}
        if duration_hint <= 0 and caps:
            duration_hint = int(caps.get("duration") or 0)
        ff_desc = first_frame_desc or "首帧/楼层开头画面"
        lf_desc = last_frame_desc or "尾帧/楼层结尾画面"
        # 拍界与 ④ 用同一份真源（`compile_firstlast_video_prompt` → `_time_segments`
        # 内部同样调 `_firstlast_beat_plan`，入参逐字对齐才不会两边漂移）。
        beats, beat_dur, quote_stats = _firstlast_beat_plan(
            spec, duration_hint, ff_desc, lf_desc, prev_tail_desc,
            has_first, has_last, skeleton,
        )
        dialogue = build_dialogue_reference(audio_lines, beats)
        prompt = compile_firstlast_video_prompt(
            spec, style_prefix=style_prefix,
            duration_hint=duration_hint, camera=camera, model_name=model, size=size,
            first_frame_desc=ff_desc, last_frame_desc=lf_desc,
            prev_tail_desc=prev_tail_desc,
            has_first=has_first, has_last=has_last, audio_lines=audio_lines,
            skeleton=skeleton,
        )
        images = [img for img in (first_frame, last_frame if allows_last else "") if img]
        binding = {
            "图片1": _binding_entry(ff_desc, first_frame, pending_frame),
        }
        if allows_last:
            binding["图片2"] = _binding_entry(lf_desc, last_frame, pending_frame)
        # 批 3 参考生成：图片3 起是角色参考图，编号与 ② 同源（`role_refs`，已按模板槽数裁剪），
        # 实际文件由前端按视频模板的参考图槽上传。
        for idx, name in enumerate(spec.get("role_refs") or [], start=3):
            if name:
                binding[f"图片{idx}"] = (
                    f"{name} 的角色参考图（只锁身份、五官、发型与体型；"
                    "当下衣着、姿势与所在场景以首尾帧画面为准）"
                )
    elif mode in ("shot_cut", "shot_move_single"):
        # 2026-11-09 分镜序列：cut＝单图固定机位动态延伸；move_single＝单图一条连续运镜。
        shot_seconds = _shot_seconds(spec, duration_hint, caps)
        has_frame = bool(first_frame) or pending_frame
        desc = _climax_frame_role(spec, first_frame_desc)
        ctr = (
            _shot_beats_contract(spec) if spec.get("shot_video_beats")
            else (_shot_hold_contract(spec, has_frame) if mode == "shot_cut"
                  else _shot_move_single_contract(spec))
        )
        shot_intent = {"intent": ctr.intent, "reason": ctr.reason}
        prompt = compile_climax_video_prompt(
            spec, style_prefix=style_prefix,
            duration_hint=shot_seconds, camera="", model_name=model, size=size,
            first_frame_desc=desc, audio_lines=[], has_frame=bool(first_frame),
            pending_frame=pending_frame, skeleton=skeleton, contract=ctr,
        )
        images = [first_frame] if first_frame else []
        actors = [str(a).strip() for a in (spec.get("actors") or []) if str(a).strip()]
        role = f"图片1中心的角色为{'、'.join(actors)}" if actors else desc
        binding = {"图片1": _binding_entry(role, first_frame)}
        # 图片2+：角色参考底图（只锁外貌；不参与本镜构图/动作）。
        for idx, name in enumerate(
            [str(n).strip() for n in (spec.get("role_refs") or []) if str(n).strip()], start=2,
        ):
            binding[f"图片{idx}"] = (
                f"{name} 的角色参考图（只锁身份、五官、发型与体型；"
                "当下衣着、姿势与所在场景以图片1为准）"
            )
    elif mode in ("shot_establish", "shot_establish_move"):
        # 2026-11-09 覆盖式分镜：环境空镜——只演空间/道具/群演，不锁人物身份。
        shot_seconds = _shot_seconds(spec, duration_hint, caps)
        has_frame = bool(first_frame) or pending_frame
        ctr = (
            _shot_beats_contract(spec) if spec.get("shot_video_beats")
            else _shot_establish_contract(spec)
        )
        shot_intent = {"intent": ctr.intent, "reason": ctr.reason}
        desc = "本镜环境空镜的起始画面"
        # 运镜型环境镜：逐拍机位必须用 ① 声明的路径，不能被 spec.camera（如「摇臂俯拍」）
        # 抢走——两者会互相矛盾（离线模拟 2026-11-09 实锤）。
        establish_spec = (
            {k: v for k, v in spec.items() if k != "camera"}
            if mode == "shot_establish_move" else spec
        )
        prompt = compile_climax_video_prompt(
            establish_spec, style_prefix=style_prefix,
            duration_hint=shot_seconds, camera="", model_name=model, size=size,
            first_frame_desc=desc, audio_lines=[], has_frame=bool(first_frame),
            pending_frame=pending_frame, skeleton=skeleton, contract=ctr,
        )
        images = [first_frame] if first_frame else []
        binding = {"图片1": _binding_entry(desc, first_frame, pending_frame)}
        # 环境镜也允许角色参考：锚点/主体点名了角色（如示例角色甲入场）且配了底图 ⇒ 图片2+。
        for idx, name in enumerate(
            [str(n).strip() for n in (spec.get("role_refs") or []) if str(n).strip()], start=2,
        ):
            binding[f"图片{idx}"] = (
                f"{name} 的角色参考图（只锁身份、五官、发型与体型；"
                "当下衣着、姿势与所在场景以图片1为准）"
            )
    elif mode == "shot_move":
        # 2026-11-09 分镜序列：两张锚点图 + ①一条完整运镜路径 + ④同一条路径逐拍复用。
        allows_last = _caps_allows_last_frame(caps)
        has_first = bool(first_frame) or pending_frame
        has_last = (bool(last_frame) or pending_frame) and allows_last
        shot_seconds = _shot_seconds(spec, duration_hint, caps)
        path = str(spec.get("shot_camera") or "").strip() or _REVEAL_CAMERA_PAN
        ff_desc = first_frame_desc or "本镜起始画面"
        lf_desc = last_frame_desc or "本镜结束画面"
        # ⚠ 分镜的叙事窗口是 spec.narrative（本镜两锚点之间）；绝不带整楼 floor_narrative。
        move_spec = {k: v for k, v in spec.items() if k != "floor_narrative"}
        if not str(move_spec.get("narrative") or "").strip():
            move_spec = {**move_spec, "narrative": str(move_spec.get("shot_narrative") or "")}
        prompt = compile_firstlast_video_prompt(
            move_spec, style_prefix=style_prefix,
            duration_hint=shot_seconds, camera="", model_name=model, size=size,
            first_frame_desc=ff_desc, last_frame_desc=lf_desc, prev_tail_desc="",
            has_first=has_first, has_last=has_last, audio_lines=None,
            skeleton=skeleton, camera_override=path,
        )
        images = [img for img in (first_frame, last_frame if allows_last else "") if img]
        binding = {"图片1": _binding_entry(ff_desc, first_frame, pending_frame)}
        if allows_last:
            binding["图片2"] = _binding_entry(lf_desc, last_frame, pending_frame)
        shot_intent = {"intent": "shot_move", "reason": path[:80]}
    elif mode == "transition":
        # W3 转场视频：图片1=上尾帧（起点），图片2=当前首帧（终点），短桥段。
        # 第 103 刀：转场同样在成图**之前**编译（上尾帧来自历史、当前首帧由本链刚生成），
        # 两张图随后由前端 W3 转场块上传挂入 ⇒ pending_frame 视为「图会到」，
        # 否则 ②/④ 恒写「无参考图，以文字为准」，与真实提交相反。
        # 第 106 刀（实稿发现 1）：转场的「图片2=当前首帧」同样受模板能力约束——
        # 模板没有第二图槽时，④ 按文字收束而不是宣称一张不会上传的图。
        allows_last = _caps_allows_last_frame(caps)
        has_prev_tail = bool(first_frame) or pending_frame
        has_first = (bool(last_frame) or pending_frame) and allows_last
        prompt = compile_transition_video_prompt(
            spec, style_prefix=style_prefix,
            duration_hint=transition_duration_hint, camera=camera,
            model_name=model, size=size,
            prev_tail_desc=prev_tail_desc or "上一楼层尾帧画面",
            first_frame_desc=first_frame_desc or "当前楼层首帧画面",
            has_prev_tail=has_prev_tail, has_first=has_first,
            skeleton=skeleton,
        )
        images = [img for img in (first_frame, last_frame if allows_last else "") if img]
        binding = {
            "图片1": _binding_entry(
                prev_tail_desc or "上一楼层尾帧画面", first_frame, pending_frame),
        }
        if allows_last:
            binding["图片2"] = _binding_entry(
                first_frame_desc or "当前楼层首帧画面", last_frame, pending_frame)
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

    warning_mode = (
        "firstlast" if mode == "shot_move"
        else ("establish" if mode in ("shot_establish", "shot_establish_move") else mode)
    )
    warnings = _missing_frame_warnings(
        warning_mode, first_frame, last_frame, pending_frame,
        allows_last=_caps_allows_last_frame(caps),
    )
    warnings.extend(_sparse_beat_warnings(spec))
    # 第 105 刀（实稿发现 5）：15s 塞 53 句 ⇒ ④ 每拍按 4.5 字/秒限速，装不下的引语
    # 不是丢掉不提，这里如实报「裁了多少句」，让人决定是加长视频还是精简台词。
    # 第 107 刀：拍窗口已按台词量加权，仍被裁 ⇒ 同时报「排进各拍至少需要多少秒」，
    # 否则人只知道「裁了」，不知道要加到多长才够。
    if quote_stats.get("quotes_dropped"):
        need = quote_stats.get("beat_needs_seconds") or 0
        hint = f"；本段台词排进各拍至少需要约 {need}s（当前 {beat_dur}s）" if need else ""
        warnings.append(
            f"④ 每拍对白按 {_DIALOGUE_CHARS_PER_SEC} 字/秒限速："
            f"{quote_stats['quotes_dropped']}/{quote_stats['quotes_total']} 句引语"
            f"超出所在拍可说完的容量，未写入分镜{hint}（建议加长视频或精简台词）"
        )
    if mode in ("shot_cut", "shot_move", "shot_move_single"):
        seconds = _shot_seconds(spec, duration_hint, caps)
    elif mode in ("shot_establish", "shot_establish_move"):
        seconds = _shot_seconds(spec, duration_hint, caps)
    elif mode == "transition":
        seconds = transition_duration_hint
    else:
        seconds = duration_hint

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
        # 对白时间轴（firstlast 且本轮有 TTS 台词才有；climax/transition 恒 None）
        "dialogue": dialogue,
        "warnings": warnings,
    }


def _missing_frame_warnings(mode: str, first_frame: str, last_frame: str,
                            pending_frame: bool = False,
                            *, allows_last: bool = True) -> list[str]:
    """缺图守卫（R2）：把缺失帧的后果显式列给调用方/人核对。

    `allows_last=False`（第 106 刀：模板没有第二图槽）⇒ 第二张图不会上传，
    按「模板装不下」写实际后果，不再谎报「尾帧由下游链补挂」。
    """
    warnings: list[str] = []
    if mode == "firstlast":
        if pending_frame:
            # 图在提示词编译之后才产出（前端首尾帧链）⇒ 不是「缺图」，按实际后果写。
            if allows_last:
                warnings.append("首尾帧图编译时未产出：首帧/尾帧由下游链补挂本张成帧")
            else:
                warnings.append(
                    "首帧图编译时未产出：由下游链补挂本张成帧"
                    "（视频模板无尾帧槽：尾帧图不会上传，收尾按文字描述）"
                )
        else:
            if not first_frame:
                warnings.append("缺首帧图：首帧将以文字描述开场（退化为文生/待上游补图）")
            if allows_last:
                if not last_frame:
                    warnings.append("缺尾帧图：尾帧将以文字描述收尾")
                if not first_frame and not last_frame:
                    warnings.append("首尾帧图均缺：将退化为纯文生视频（无参考图）")
            else:
                warnings.append(
                    "视频模板无尾帧槽：尾帧图不会上传，④ 收尾按文字描述"
                )
    elif mode == "establish":
        # 环境空镜：图在编译之后才产出（pending_frame）⇒ 按「下游补挂」写，不说成缺图。
        if pending_frame:
            warnings.append("环境空镜底图编译时未产出：首帧由下游链补挂本张成图")
        elif not first_frame:
            warnings.append("缺环境空镜底图：将以文字描述生成场景")
    elif mode == "transition":
        # 坑F：转场起点（上尾帧图）缺省 → 降级文字转场；终点（当前首帧图）缺省 → 文字收束。
        # 第 103 刀：编译发生在成图之前（pending_frame）⇒ 两张图随后由 W3 转场块补挂，
        # 不是「缺图」，按实际后果写，免得 trace 指错方向。
        if pending_frame:
            if allows_last:
                warnings.append("转场双图编译时未产出：上尾帧/当前首帧由下游链补挂成帧")
            else:
                warnings.append(
                    "转场仅一张图可挂（视频模板无第二图槽）：上尾帧由下游链补挂，"
                    "当前首帧按文字收束"
                )
            return warnings
        if not first_frame:
            warnings.append("缺上尾帧图：转场降级为文字转场（以文字重现上一楼层尾帧）")
        if allows_last:
            if not last_frame:
                warnings.append("缺当前首帧图：转场终点以文字描述")
            if not first_frame and not last_frame:
                warnings.append("转场双图均缺：将退化为纯文生短桥段（无参考图）")
        else:
            warnings.append("视频模板无第二图槽：当前首帧图不会上传，转场终点以文字描述")
    else:
        if not first_frame:
            # pending_frame（自动链）：图在提示词编译之后才产出，由下游链补挂成首帧，
            # 不是「将以文字生成动作画面」——按实际后果写，免得 trace 指错方向。
            if pending_frame:
                warnings.append("高潮参考图编译时未产出：首帧由下游链补挂本张成图")
            else:
                warnings.append("缺高潮参考图：将以文字描述生成动作画面")
    return warnings


def _binding_entry(role: str, source: str, pending: bool = False) -> str:
    """参考绑定条目：职责 → 图地址（供人核对两层是否对应）。"""
    if source:
        return f"{role} → {source}"
    if pending:
        return f"{role} → （编译时未产出，由下游链补挂成帧）"
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
            entry: dict[str, str] = {"beat": beat or "延伸", "desc": desc}
            # scene（批 2，firstlast 每拍场景）：只有非空且非拒答才收；缺省不写该键
            # （climax 的 action_sequence 没有这个字段，逐字维持旧结构）。
            raw_scene = item.get("scene")
            if isinstance(raw_scene, str):
                scene = prompt_clean.restore_jailbreak(raw_scene).strip()
                if scene and not prompt_clean.REFUSAL_RE.search(scene):
                    entry["scene"] = scene
            beats.append(entry)
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
                line_entry: dict[str, Any] = {"speaker": speaker, "text": text}
                # at_s：提取 LLM 按剧情位置推算的台词时点（秒）——数字/数字串才透传
                at_s = ln.get("at_s")
                if isinstance(at_s, (int, float)) and not isinstance(at_s, bool):
                    line_entry["at_s"] = max(0.0, float(at_s))
                elif isinstance(at_s, str):
                    try:
                        line_entry["at_s"] = max(0.0, float(at_s.strip()))
                    except ValueError:
                        pass
                lines.append(line_entry)
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
