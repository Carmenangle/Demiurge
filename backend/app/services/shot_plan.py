"""全局模式分镜计划（纯函数，2026-11-09）。

把判据选出的锚点数组转成**按正文序的镜头清单**：

- `cut`：本锚点单图动态延伸，下一锚点硬切进入；
- `move`：本锚点到下一锚点的双图运镜（下一锚点必须存在、原文相邻、同场景、
  `transition_desc` 非空）；
- `move_single`：本锚点单图运镜（`transition_desc` 必填），下一锚点硬切进入。

降级一律向 `cut` 收（硬切最多是少一次运镜；错误的 move 会把单图空间逼成乱晃）。
本模块不调用 LLM / 不读存储；提示词编译与事件组装由下游消费。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services import image_prompt_extract, scene_card, scene_illustration, video_prompt

# 与 `video_prompt._SCENE_SHIFT_CUES` 同源；分镜额外把显式分隔线也算场景/时间切换。
SCENE_SHIFT_CUES = (*video_prompt._SCENE_SHIFT_CUES, "———", "——", "***")

_MIN_SHOT_S = 3
_MAX_SHOT_S = 15
# 2026-11-09 离线模拟定案：不同镜头能承受的时长不同——
# cut＝单图固定机位动态延伸，过长必然让模型在单图空间里自造运动（用户 8s 样片即此症），
# 故上限收到 6s；move_single＝单图一条连续运镜，4–10s；move＝双图首尾帧，5–15s。
_SHOT_LIMITS: dict[str, tuple[int, int]] = {
    # 环境镜要沿主动线走完空间，太短会像「原地抖一下」⇒ 下限 5s。
    "establish": (5, 10),
    "cut": (3, 6),
    "move_single": (4, 10),
    "move": (5, 15),
}
_DEFAULT_TOTAL_MIN = 8
_CHARS_PER_ESTIMATE_SECOND = 80


def shot_limits(shot_type: str) -> tuple[int, int]:
    """该镜头类型的 (最短, 最长) 秒数；未知类型沿用全局兜底。"""
    return _SHOT_LIMITS.get(shot_type, (_MIN_SHOT_S, _MAX_SHOT_S))


def image_time_second(duration_s: int, timing: str) -> int:
    """图片相对本镜的**具体秒数**（用户 2026-11-10：必须交代图片在第几秒）。

    start ⇒ 0（首帧）；mid ⇒ 本镜时长中点；end ⇒ 本镜末秒。
    """
    duration = max(0, int(duration_s or 0))
    mode = str(timing or "start").strip().lower()
    if mode == "end":
        return duration
    if mode == "mid":
        return max(1, duration // 2)
    return 0


def _bigrams(text: str) -> set[str]:
    """中文字符二元组（去标点/空白）；用于锚点与 beats 的粗匹配，0 LLM。"""
    clean = re.sub(r"[\s，。！？!?；;：:、「」『』“”\"'（）()…—\-]", "", text or "")
    return {clean[index:index + 2] for index in range(len(clean) - 1)}


def _beat_score(anchor: str, beat: dict) -> tuple[int, int]:
    """锚点与单段 beats 的匹配分：**(尾句动作重叠, 全句重叠)**。

    动作通常在锚点最后一个分句；尾句优先，避免环境段靠氛围词抢走图片位置
    （镜0：beat0 日光树冠 vs beat1 人物入场，两者全句分相同，尾句分定胜负）。
    """
    text = " ".join(
        str(beat.get(key) or "") for key in ("description", "subject_motion", "framing")
    )
    target = _bigrams(text)
    full = len(_bigrams(anchor) & target)
    tail = re.split(r"[，,]", (anchor or "").strip("。！？!? "))[-1]
    return len(_bigrams(tail) & target), full


_PERSON_SIGNAL_RE = re.compile(
    r"她|他|你|我|姑娘|女人|男人|女子|男子|身影|人影|背影|孩童|孩子|蛮啾|人物",
)
_BODY_SIGNAL_RE = re.compile(r"手|脸|肩|眼|瞳|眸|唇|脚|腿|臂|腕|指|发|耳")


def _has_person_signal(text: str) -> bool:
    """粗判一段文字里有没有人物/身体局部；用来防止纯环境段抢走人物镜的图片位置。"""
    return bool(_PERSON_SIGNAL_RE.search(text or "") or _BODY_SIGNAL_RE.search(text or ""))


def _beat_text(beat: dict) -> str:
    return " ".join(
        str(beat.get(key) or "")
        for key in ("framing", "camera_motion", "subject_motion", "description")
    )


def _beat_seconds(beat: dict) -> int:
    try:
        return max(1, int(float(beat.get("duration_s") or 2)))
    except (TypeError, ValueError):
        return 2


def resolve_image_position(item: "ShotPlanItem") -> tuple[int, str]:
    """图片在本镜里的**实际秒数**与位置。

    判据只给 start/mid/end，但图片是按锚点句生成的：若锚点落在第 2 段，
    图片就不是首帧（用户 2026-11-10 实锤：镜0 的图是 3–6s 的人物入场，
    不能塞进视频的第 1 段）。这里用锚点与 beats 的二元组匹配选段：
    - end ⇒ 末段；mid ⇒ 中点；start ⇒ 与锚点最匹配的那一段（通常是第 2 段）。
    """
    # move 是双图首尾帧：两张图分别是起点/终点，不存在「图片落在中段」。
    if item.shot_type == "move":
        return 0, "start"
    beats = [beat for beat in (item.video_beats or []) if isinstance(beat, dict)]
    if not beats:
        return image_time_second(item.duration_s, item.image_timing), item.image_timing
    total = sum(_beat_seconds(beat) for beat in beats)
    timing = str(item.image_timing or "start").strip().lower()
    if timing == "end":
        return total, "end"
    if timing == "mid":
        return max(1, total // 2), "mid"
    best_index, best_score = 0, (-1, -1)
    for index, beat in enumerate(beats):
        score = _beat_score(item.start_anchor, beat)
        if score > best_score:
            best_index, best_score = index, score
    # 尾句没有动作命中、锚点里有人物 ⇒ 纯环境段不能占图片位（用户 2026-11-10 实锤：
    # 神权镜0/4/5 图片是人物，却被 0s 的空院/货车段抢走）。找第一段有人物信号的 beat。
    if (best_score[0] == 0 and _has_person_signal(item.start_anchor)
            and not _has_person_signal(_beat_text(beats[0]))):
        for index, beat in enumerate(beats):
            if _has_person_signal(_beat_text(beat)):
                best_index = index
                break
    if best_index <= 0:
        return 0, "start"
    second = sum(_beat_seconds(beat) for beat in beats[:best_index])
    return second, "at"


def image_position_for_anchor(anchor: dict) -> tuple[int, str]:
    """判据锚点（尚未建计划）→ (图片秒数, timing)；供生产先写「本帧落点」。"""
    item = ShotPlanItem(
        shot_index=0,
        shot_type=scene_illustration.normalize_shot_type(anchor.get("shot_type")),
        start_anchor=str(anchor.get("anchor") or ""),
        end_anchor="", transition_desc="", duration_s=0,
        video_mode="", image_key="", end_image_key="",
        image_timing=str(anchor.get("image_timing") or "start"),
        video_beats=[
            beat for beat in (anchor.get("video_beats") or []) if isinstance(beat, dict)
        ],
    )
    return resolve_image_position(item)


def bind_character_images(
    actors: list[str] | tuple[str, ...],
    base_images: dict[str, str] | None,
    *, start_index: int = 2, include_missing: bool = False,
) -> tuple[list[dict], list[str]]:
    """在场角色 → 角色底图编号（图片2 起步；图片1 留给本镜画面）。

    返回 `(bindings, missing)`：
    - bindings：`{"name","image_index","base_image","has_base_image"}`，按 actors 顺序；
      同一底图路径只占一个编号（后续角色复用该编号并标 `shared=True`）。
    - missing：没有底图的名字。`include_missing=False`（生产/审阅默认）时不占编号，
      避免后面角色的图片编号整体前移（缺底图的只报「未配置角色底图」）。
      `include_missing=True` 仅供需要预排槽位的调用方。
    """
    bindings: list[dict] = []
    missing: list[str] = []
    seen_names: set[str] = set()
    seen_sources: dict[str, int] = {}
    next_index = int(start_index)
    for raw in actors or []:
        name = str(raw).strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        source = str((base_images or {}).get(name) or "").strip()
        if not source:
            missing.append(name)
            if include_missing:
                bindings.append({
                    "name": name, "image_index": next_index,
                    "base_image": "", "has_base_image": False,
                })
                next_index += 1
            continue
        if source in seen_sources:
            bindings.append({
                "name": name, "image_index": seen_sources[source],
                "base_image": source, "has_base_image": True, "shared": True,
            })
            continue
        seen_sources[source] = next_index
        bindings.append({
            "name": name, "image_index": next_index,
            "base_image": source, "has_base_image": True,
        })
        next_index += 1
    return bindings, missing


def beat_cast_text(beats: list[dict] | tuple[dict, ...] | None) -> str:
    """本镜时间分镜的人名扫描文本：只取各段 `description` 与 `subject_motion`。

    不能拿整段 `narrative` 扫描：分镜窗口常包含相邻段落，下一镜才出场的角色会被
    提前算进本镜（堕落魔戒镜0 实锤：分镜只演蛮啾，`腓特烈` 却因同窗口正文被绑定成
    图片2）。出场名单只认**这一镜真要演的段**。
    """
    parts: list[str] = []
    for beat in beats or []:
        if not isinstance(beat, dict):
            continue
        for key in ("description", "subject_motion"):
            value = str(beat.get(key) or "").strip()
            if value:
                parts.append(value)
    return "\n".join(parts)


def shot_cast_names(
    actors: list[str] | tuple[str, ...],
    subjects: list[str] | tuple[str, ...],
    frame_subjects: list[str] | tuple[str, ...],
    known: list[str] | tuple[str, ...],
    *, anchor_text: str = "", beats: list[dict] | tuple[dict, ...] | None = None,
) -> list[str]:
    """视频出场名单（顺序＝角色底图绑定顺序）。

    并集口径：提示词 actors → 锚点 subjects → 帧主体 → 本镜锚点句/时间分镜点名的
    配置角色。只有**配置角色**参与点名扫描；群演/未配置角色保留在 actors 里供审计，
    但不会因正文提及而被拉进名单（用户 2026-11-10：出场角色必须与配置一致）。
    """
    known_names = [str(name).strip() for name in known if str(name).strip()]
    known_set = set(known_names)
    scan = "\n".join([str(anchor_text or ""), beat_cast_text(beats)])
    named = [name for name in known_names if name and name in scan]
    return list(dict.fromkeys([
        *[str(name).strip() for name in (actors or []) if str(name).strip()],
        *[str(name).strip() for name in (subjects or []) if str(name).strip() in known_set],
        *[
            str(name).strip() for name in (frame_subjects or [])
            if str(name).strip() in known_set
        ],
        *named,
    ]))


# ── 分镜动作归属守卫（2026-11-11）────────────────────────────────────────────
# 用户实锤：`手腕被拽` 既没说是谁的手腕，也没说是谁在拽；`手探出画面/手指在单上点划`
# 也没有归属。NSFW 里两个女角色在场时，`她` 还会指代不明。
_BODY_BINDING = (
    "手腕", "手指", "指尖", "指节", "手掌", "手臂", "胳膊", "手", "腿", "脚", "靴",
    "腰", "肩", "背", "胸", "乳", "臀", "下体", "腹股沟", "大腿", "小臂", "臂弯",
    "锁骨", "柱身", "肉棒", "阴茎", "性器",
)
_BODY_FOR_PRONOUN = set(_BODY_BINDING) - {"眼", "唇", "脸", "头"}
_ACTION_VERBS = (
    "探", "压", "按", "抓", "拉", "拽", "摸", "扣", "扶", "提", "掀", "伸", "停", "点",
    "划", "比", "拢", "悬", "转", "晃", "抬", "落", "穿", "解", "剥", "捏", "揉", "握",
    "抱", "推", "蹭", "拍", "打", "扯", "套", "戴", "摘", "捂", "捧", "托", "撩", "拨",
    "夹", "绞", "缠", "贴", "抵", "顶", "撞", "勾", "咬", "舔", "含", "插", "抽", "射",
    "流", "滴", "绷", "颤", "抖", "伏", "起伏",
)
_AGENT_WORDS = (
    "镜头角色", "叙事者", "我", "你", "他", "她", "风", "光", "日", "月", "海风",
    "夜风", "水", "雨", "手", "脚", "舌", "唇", "牙", "指", "掌",
    "木刺", "门框", "衣带", "石", "墙", "剑", "刀", "绳", "链", "布料", "衣料",
    "袖口", "床单", "婚纱", "裙", "帕", "被褥", "被子",
)
_BODY_START = re.compile("^(?:" + "|".join(_BODY_BINDING) + ")")
_HAS_ACTION = re.compile("|".join(_ACTION_VERBS))
_PERSON_NOUN = re.compile(r"者|人|男|女|兵|卫|角色")
# 「被」作名词（被子/被褥/掀被）不是被动句。
_PASSIVE = re.compile(r"(?<!掀)(?<!盖)(?<!裹)(?<!蒙)被(?!子|褥|单|面|角|沿|窝|下)")
# 只有真正涉及性器官/性状态的拍才强制点名；普通 NSFW 作品里的「她伸手接电文」
# 不强制重复名字（否则每个动作都要写全名，反而淹没动作）。
_NSFW_EXPLICIT = re.compile(
    r"肉棒|柱身|阴茎|性器|下体|腹股沟|内射|射精|白浊|精液|淫|穴|乳|臀"
)


def nsfw_requires_named(text: str, rating: str = "") -> bool:
    """NSFW 且这一拍确实写到性器官/性状态 ⇒ 肢体/器官必须点名。"""
    return str(rating or "").strip().lower() == "nsfw" and bool(
        _NSFW_EXPLICIT.search(text or "")
    )


def ownership_errors(
    text: str, *, cast: list[str] | tuple[str, ...] = (),
    require_named: bool = False,
) -> list[str]:
    """返回分镜文本里的「无主动作 / 无主身体部位 / 代词指代不明」错误。

    - `句首裸身体部位 + 动作`：`手探出画面`、`手指在单上点划`；
    - `被动句缺施动者`：`手腕被拽`、`腰侧被隔布蹭磨`；
    - `require_named=True`（NSFW/两个女角色在场）时，肢体/器官绑定用「她/他」
      且同一句没有点名角色 ⇒ 报错。
    """
    if not text:
        return []
    names = [str(name).strip() for name in cast if str(name).strip()]
    owners = (*_AGENT_WORDS, *names)
    errors: list[str] = []
    # 句子级：用于判断「同一完整句是否已经点名」
    for sentence in re.split(r"[。；;]", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        # 同一句内主语延续：`，` 之内前面出现过主语/名字，后面的裸身体部位就合法；
        # 只有跨 `；`/`。` 换句才要求重新点名（用户 2026-11-11 定案）。
        subject_seen = False
        named_seen = False
        for clause in re.split(r"[，,]", sentence):
            clause = clause.strip("。 ")
            if not clause:
                continue
            clause_has_owner = any(owner in clause for owner in owners)
            clause_has_name = any(name and name in clause for name in names)
            if clause_has_owner:
                subject_seen = True
            if clause_has_name:
                named_seen = True
            if not any(body in clause for body in _BODY_BINDING):
                continue
            if (
                _BODY_START.match(clause) and _HAS_ACTION.search(clause)
                and not subject_seen
            ):
                errors.append(f"无主身体部位：{clause}")
            if _PASSIVE.search(clause):
                after = _PASSIVE.split(clause, 1)[1].lstrip()
                agent_hit = (
                    any(word in after[:8] for word in owners)
                    or bool(_PERSON_NOUN.search(after[:6]))
                )
                if not agent_hit:
                    errors.append(f"被动句缺施动者：{clause}")
            if (
                require_named and not named_seen
                and any(part in clause for part in _BODY_FOR_PRONOUN)
                and re.search(r"[她他]", clause)
            ):
                errors.append(f"NSFW 肢体/器官未点名：{clause}")
    return list(dict.fromkeys(errors))


def with_frame_focus(narrative: str, anchor: str, second: int = 0) -> str:
    """段落摘录**保持全文**，只在末尾追加「本帧落点」一行。

    用户 2026-11-10 定案：图片输入与高潮点生图同口径（锚点所在段落附近 +
    条目/状态事实），不截成单句；同段多镜靠这一行锚点定位各自那一帧。
    ⚠ 图片是**定格**：必须写「动作结果/静止状态」，不能把锚点里的
    连续动作原样交给模型（否则会画出「走过去→坐下→摊开」的过程）。
    """
    text = (narrative or "").strip()
    needle = (anchor or "").strip()
    where = f"第 {int(second)} 秒" if int(second or 0) > 0 else "首帧"
    # 2026-11-10 复审（用户口径：图片是定格结果，不是过程）：只说「不写过程」小模型会
    # 把整段的后续动作一并画进去（镜 10 首帧写了掀被坐起）。这里显式限定「只取锚点这一
    # 瞬间」，段落其余文字只用于场景/服装/光线等静态事实；并给出英文静态句要求。
    focus = (
        f"【本帧落点】{where}的定格画面（只画这一瞬间）：本帧的人物姿态与动作结果只取锚点这一句，"
        "段落里锚点之前或之后的动作只用于确定场景、服装、光线与人物位置，不得画进本帧；"
        "只写这一秒已经呈现的静止状态与动作结果（站定/坐定/躺定/停住），"
        "不写过程、不写后续动作；英文用静态句（stands/sits/lies/rests），"
        "不用 walking/entering/reaching 等进行时"
    )
    if needle:
        focus = f"{focus}；锚点：{needle}"
    return f"{text}\n{focus}" if text else focus


@dataclass
class ShotPlanItem:
    """一个镜头 = 从某个锚点出发的一段视频。"""

    shot_index: int              # 起始锚点下标（正文序）
    shot_type: str               # establish / cut / move / move_single
    start_anchor: str
    end_anchor: str             # 仅 move：下一锚点；其余空串
    transition_desc: str         # move / move_single / 运镜型 establish
    duration_s: int
    video_mode: str              # shot_establish / shot_establish_move / shot_cut / ...
    image_key: str
    end_image_key: str           # 仅 move
    start_offset: int | None = None
    end_offset: int | None = None
    framing: str = ""            # 景别/角度（胸部俯拍、脚部特写…）
    camera_motion: str = ""      # 机位怎么动
    subject_motion: str = ""     # 主体怎么动（固定机位也必须有）
    sound_cue: str = ""          # 触发本镜的声音/事件
    image_timing: str = "start"  # 图片相对本镜的位置：start/mid/end/at（at=锚点落在第 N 段）
    image_time_s: int = 0        # 图片对应的**具体秒数**（首帧=0，at=该段起点，中段/尾帧按比例）
    locale: str = ""            # 该镜所在地点（判据给；空=回落全局 locale）
    paragraphs: list[int] = field(default_factory=list)  # 覆盖账本：本帧覆盖的自然段号
    video_beats: list[dict] = field(default_factory=list)  # 关键帧内部时间轴（段间硬切）
    scene_key: str = ""          # 同场景分组键（scene-0/scene-1…）
    scene_anchor: str = ""       # 同场景共用的空间/陈设锚点（自动派生，非手工）


@dataclass
class ShotPlan:
    sequence_id: str
    total_shots: int
    total_duration_s: int
    items: list[ShotPlanItem]
    warnings: list[str] = field(default_factory=list)
    # 角色名 → 当前服装锚点（从 `<status>` 的 `[在场]` 括号自动解析）。
    wardrobe_by_actor: dict[str, str] = field(default_factory=dict)
    # 场景键 → 场景卡（空间/建筑/陈设/光线/机位；空场景环境图的上游）。
    scene_cards: dict[str, dict] = field(default_factory=dict)


def _visible_text(text: str) -> str:
    try:
        return image_prompt_extract.visible_narrative_text(text or "")
    except Exception:  # noqa: BLE001 [EXC-保留] 纯兜底：可见正文解析失败不影响分镜计划
        return text or ""


def _locate(text: str, anchor: str) -> int | None:
    """锚点在正文里的**原始**句末偏移。

    分镜窗口/同场景判定要看两锚点之间的原文，**不能先吸附到段尾**（同段两个锚点
    会塌成同一偏移）；真正建槽时由下游再按段落吸附。
    """
    if not anchor:
        return None
    return scene_illustration.illustration_anchor_offset(text or "", anchor)


def same_scene(prev: dict, cur: dict, text: str) -> bool:
    """两个锚点之间是否没有显式场景切换。

    只在两处都能定位时才判定；定位不到 ⇒ False（move 降级 cut，宁硬切不乱晃）。
    """
    prev_off = prev.get("anchor_offset")
    cur_off = cur.get("anchor_offset")
    if prev_off is None or cur_off is None:
        return False
    if cur_off <= prev_off:
        return False
    between = (text or "")[prev_off:cur_off]
    return not any(cue in between for cue in SCENE_SHIFT_CUES)


# ── 连续锚点（2026-11-12）────────────────────────────────────────────────────
# 用户实锤：在验证脚本里逐镜写死 anchor 只能救一次，全自动生产必须从正文/状态块
# 自己派生。scene_anchor＝同场景各镜共用的空间/陈设事实；wardrobe_anchor＝同角色
# 当前服装事实。两者都只做「有据提取」，正文/状态没写就不编。
_SCENE_CUE_RE = re.compile(
    r"门|窗|墙|台|毯|廊|厅|房|仓|院|街|桥|梯|桌|床|地|顶|灯|幕|帷|柱|梁|阶|路|柜|镜|帘|纱|烛",
)
CLOTHING_CUES = (
    "婚纱", "礼服", "长裙", "短裙", "纱裙", "连衣裙", "旗袍", "裙装",
    "长袍", "战袍", "袍服", "袍子", "西装", "制服", "校服", "修女服",
    "外套", "风衣", "衬衫", "毛衣", "针织", "卫衣", "睡衣", "浴衣", "上衣",
    "白衣", "黑衣", "红衣", "铠甲", "甲胄", "披风", "斗篷",
    "头纱", "面纱", "项圈", "领带", "围裙", "围巾", "腰带", "束腰",
    "胸衣", "内衣", "泳装", "比基尼", "高跟鞋", "靴子", "丝袜", "手套", "帽子", "裸",
)
# 旧私有名保留：`parse_wardrobe_anchors` 与既有测试都按这个名引用；
# 2026-11-12 起角色状态卡（`character_state_card`）也从这里取词表，公开名作单一真源。
_CLOTHING_CUES = CLOTHING_CUES
# 场景地标只收「建筑/空间固定物」，不收花桶、酒杯这类可移动道具——后者出现在
# 建立镜不代表后续每镜都要有，硬塞会把特写拍成仓库全景。
_SPATIAL_LANDMARK_RE = re.compile(
    r"(?:木|侧|大|后|前|正|铁|旧|新|白|红|石|雕花|双开)?"
    r"(?:门框|侧门|木门|大门|红毯|地毯|木板台|台子|台前|走廊|长廊|仓库|礼堂|大厅|卧室|寝殿|房间|窗|墙)"
)
_GENERIC_ACTOR_NAMES = frozenset({"我", "你", "玩家", "{{user}}"})
# 分镜分组只认**显式转场**：人物入场/走近/时间词（走进、来到、片刻后、此时）都不换场；
# 「——」在正文里常是台词破折号，只有整行 `——`/`***` 才算分隔线。
_SCENE_GROUP_CUES = ("镜头一转", "转场", "另一边")
_SCENE_GROUP_RULE_RE = re.compile(r"(?m)^\s*(?:—{2,}|\*{3,})\s*$")


def _scene_shift_between(text: str, prev_off: int | None, cur_off: int | None) -> bool:
    if prev_off is None or cur_off is None or cur_off <= prev_off:
        return False
    between = (text or "")[prev_off:cur_off]
    if "镜头一转" in between or "转场" in between:
        return True
    # 「另一边的乳房」是身体部位，不是场景切换。
    if re.search(r"另一边(?!的)", between):
        return True
    return bool(_SCENE_GROUP_RULE_RE.search(between))


def _anchor_start(text: str, anchor: str, offset: int | None) -> int | None:
    """锚点句首偏移：优先按原文逐字回找；判据改写过的锚点退回所在句首。"""
    if offset is None:
        return None
    value = str(anchor or "")
    if value:
        found = (text or "").rfind(value, 0, offset)
        if found >= 0:
            return found
    cut = max(((text or "").rfind(mark, 0, offset) for mark in "。！？!?；;\n"), default=-1)
    return 0 if cut < 0 else cut + 1


def split_top_level(value: str) -> list[str]:
    """按 `；;、，,` 切分，但忽略括号内的分隔符。

    `[在场]` 的括号里常写「若干，端奶瓶」「白色婚纱，项圈藏在头纱下」——括号内的
    逗号不是角色分隔符，先切开就会把服装状态截断。
    """
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for char in value or "":
        if char in "（(":
            depth += 1
        elif char in "）)":
            depth = max(0, depth - 1)
        if char in "；;、，," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(char)
    parts.append("".join(buf))
    return [part.strip() for part in parts if part.strip()]


def _norm_actor_name(raw: str) -> str:
    name = re.split(r"[（(：:]", raw or "")[0].strip()
    return re.sub(r"(若干|们|等)$", "", name).strip()


def parse_wardrobe_anchors(present: str) -> dict[str, str]:
    """从 `[在场]` 行解析「角色（服装状态）」；动作/数量括号不算服装锚点。

    例：`腓特烈（白色婚纱，项圈藏在头纱下），蛮啾（若干，端奶瓶）`
    → `{"腓特烈": "白色婚纱，项圈藏在头纱下"}`。
    `[衣着]` 是全局服装真源，不在这里重复解析。
    """
    anchors: dict[str, str] = {}
    for clause in split_top_level(present):
        hit = re.match(r"^(.+?)[（(](.+)[）)]\s*$", clause)
        if hit:
            name, detail = _norm_actor_name(hit.group(1)), hit.group(2).strip()
        else:
            hit = re.match(r"^(.+?)[：:]\s*(.+)$", clause)
            if not hit:
                continue
            name, detail = _norm_actor_name(hit.group(1)), hit.group(2).strip()
        if not name or name in _GENERIC_ACTOR_NAMES or not detail:
            continue
        if not any(cue in detail for cue in _CLOTHING_CUES):
            continue
        anchors[name] = f"{anchors[name]}，{detail}" if name in anchors else detail
    return anchors


def _wardrobe_key(name: str, wardrobe_by_actor: dict[str, str]) -> str:
    if name in wardrobe_by_actor:
        return name
    # 判据/正文常用全名或简称（腓特烈大帝 ↔ 腓特烈），不做编辑距离，只认包含关系。
    for key in wardrobe_by_actor:
        if key and (key in name or name in key):
            return key
    return ""


def wardrobe_anchor_for(
    actors: list[str] | tuple[str, ...], wardrobe_by_actor: dict[str, str] | None,
) -> str:
    """按本镜出场角色取服装锚点；只写角色名与状态块已给的服装，不编造。"""
    mapping = wardrobe_by_actor or {}
    parts: list[str] = []
    for raw in actors or []:
        name = str(raw).strip()
        if not name:
            continue
        key = _wardrobe_key(name, mapping)
        if key:
            parts.append(f"{name}：{mapping[key]}")
    return "；".join(dict.fromkeys(parts))


def _locale_similar(prev: str, cur: str) -> bool:
    """两个 locale 是否指向同一场景；任一为空 ⇒ 不据此判切换（保守）。"""
    left, right = _bigrams(prev or ""), _bigrams(cur or "")
    if not left or not right:
        return True
    overlap = len(left & right) / max(1, min(len(left), len(right)))
    return overlap >= 0.34


def _scene_window(
    text: str, start: int | None, next_start: int | None, *, boundary: int | None = None,
) -> str:
    """取该镜所在自然段的**空间描述**；对白与「本帧落点」不算场景事实。

    `boundary`＝上一场景最后一镜的锚点偏移。场景切换句（「镜头一转」）通常写在
    新场景首镜**之前**，只从首镜锚点往后取会把「卧室里只有床头的灯亮着」漏掉。
    """
    if start is None:
        return ""
    paragraph_start = (text or "").rfind("\n\n", 0, start)
    paragraph_start = 0 if paragraph_start < 0 else paragraph_start + 2
    # 首镜从**段落开头**取：场景设定句常写在锚点动作句之前（「雨夜，面馆玻璃门…」）。
    begin = paragraph_start
    if boundary is not None and 0 <= boundary <= start:
        between = (text or "")[boundary:start]
        cue_pos = max((between.rfind(cue) for cue in _SCENE_GROUP_CUES), default=-1)
        if cue_pos >= 0:
            begin = boundary + cue_pos
        else:
            landmarks = list(_SPATIAL_LANDMARK_RE.finditer(between))
            if landmarks:
                # 新场景的地标常写在首镜动作之前（「卧室的门被推开…」）；从最后一条
                # 地标所在**整句**开始，把同句的床/窗帘/床头柜一起带进空间锚点。
                pos = landmarks[-1].start()
                cut = max((between.rfind(mark, 0, pos) for mark in "。！？!?；;\n"),
                          default=-1)
                begin = boundary + (cut + 1 if cut >= 0 else pos)
            else:
                begin = max(paragraph_start, boundary)
    paragraph_end = (text or "").find("\n\n", start)
    if paragraph_end < 0:
        paragraph_end = len(text or "")
    end = paragraph_end
    if next_start is not None and start < next_start < paragraph_end:
        # 同段多镜：不能把后面镜头的动作也并进场景锚点。
        end = next_start
    window = (text or "")[begin:end]
    window = re.sub(r"[「“][^」”\n]{0,200}[」”]", " ", window)
    window = window.replace("【本帧落点】", " ")
    window = re.sub(
        r"^(?:镜头一转|片刻后|此时|另一边|回到|转场)[，,：:\s]*", "", window.strip(),
    )
    window = re.sub(r"\s+", " ", window).strip(" ，。；;、")
    if len(window) > 200:
        cut = max(window.rfind(mark, 0, 200) for mark in "。！？!?；;")
        window = window[:cut + 1] if cut > 60 else window[:200]
    return window.strip(" ，。；;、")


def _scene_landmarks(items: list[dict], group: list[int], window: str) -> list[str]:
    """汇总本场景各镜提到的固定地标（侧门/门框/红毯…），补进共用空间锚点。

    建立镜可能只写「木门前」，而「侧门」只出现在入场镜的动作句里；不把这类
    地标汇总进来，后续特写就会丢掉空间参照（用户 2026-11-11 实锤）。
    """
    landmarks: list[str] = []
    for index in group:
        item = items[index]
        texts = [str(item.get("anchor") or "")]
        for beat in item.get("video_beats") or []:
            if isinstance(beat, dict):
                texts.append(str(beat.get("description") or ""))
        for text in texts:
            for match in _SPATIAL_LANDMARK_RE.finditer(text):
                landmark = match.group(0)
                if not landmark or landmark in window or landmark in landmarks:
                    continue
                landmarks.append(landmark)
                if len(landmarks) >= 6:
                    return landmarks
    return landmarks


def assign_scene_anchors(items: list[dict], text: str) -> list[dict]:
    """给每个锚点写 `scene_key` / `scene_anchor`（同场景共用一个空间锚点）。

    分组：① `same_scene` 判显式切换词；② 判据给了不同 `locale` 且不相似时也切开。
    锚点：优先 establish（环境空镜信息最全），否则取空间线索最多的一镜；窗口取
    该镜所在自然段，同段多镜只取到下一锚点为止。
    """
    if not items:
        return items
    offsets: list[int | None] = []
    starts: list[int | None] = []
    for item in items:
        offset = item.get("anchor_offset")
        if offset is None:
            offset = _locate(text, str(item.get("anchor") or ""))
        offsets.append(offset)
        starts.append(_anchor_start(text, str(item.get("anchor") or ""), offset))
    groups: list[list[int]] = []
    for index, item in enumerate(items):
        if not groups:
            groups.append([index])
            continue
        previous = items[index - 1]
        same = not _scene_shift_between(text, offsets[index - 1], offsets[index])
        if same and not _locale_similar(
            str(previous.get("locale") or ""), str(item.get("locale") or ""),
        ):
            same = False
        if same:
            groups[-1].append(index)
        else:
            groups.append([index])
    for scene_index, group in enumerate(groups):
        candidates = [
            index for index in group
            if scene_illustration.normalize_shot_type(items[index].get("shot_type")) == "establish"
        ] or list(group)
        chosen = max(
            candidates,
            key=lambda index: (
                scene_illustration.normalize_shot_type(items[index].get("shot_type")) == "establish",
                len(_SCENE_CUE_RE.findall(str(items[index].get("anchor") or ""))),
                -index,
            ),
        )
        next_start = starts[chosen + 1] if chosen + 1 < len(items) else None
        first = group[0]
        anchor_text = _scene_window(
            text, starts[first],
            starts[first + 1] if first + 1 < len(items) else None,
            boundary=offsets[first - 1] if first > 0 else None,
        )
        if not anchor_text:
            anchor_text = _scene_window(text, starts[chosen], next_start)
        if not anchor_text:
            anchor_text = str(items[chosen].get("anchor") or "").strip()
        landmarks = _scene_landmarks(items, group, anchor_text)
        if landmarks:
            anchor_text = f"{anchor_text}；同场景地标：{'、'.join(landmarks)}"
        for index in group:
            items[index]["scene_key"] = f"scene-{scene_index}"
            items[index]["scene_anchor"] = anchor_text[:240]
            # 场景卡用完整窗口（含同场景地标后缀）；scene_anchor 只保留前 240 字给提示词。
            items[index]["scene_window"] = anchor_text[:1200]
    return items


def _window_weight(item: ShotPlanItem, text: str, next_offset: int | None) -> int:
    """该镜头正文窗口的权重（字符数）；窗口取不到时按锚点文本长度兜底。"""
    if item.start_offset is None:
        return max(1, len(item.start_anchor) * 2 + 20)
    if item.shot_type == "move" and item.end_offset is not None:
        return max(1, item.end_offset - item.start_offset)
    end = next_offset if next_offset is not None else len(text or "")
    return max(1, end - item.start_offset)


def plan_shot_durations(
    items: list[ShotPlanItem], text: str, total_s: int = 0,
    *, caps_duration: int = 0,
) -> tuple[list[int], int, list[str]]:
    """按正文窗口占比把整楼总时长预算分给各镜。

    返回 `(每镜秒数, 实际总秒数, warnings)`。单镜时长按**镜头类型**夹值
    （cut 3–6s / move_single 4–10s / move 5–15s，见 `_SHOT_LIMITS`）；预算不足/超出时
    抬到下限或压到上限并显式告警，绝不静默改口径。
    """
    warnings: list[str] = []
    n = len(items)
    if n <= 0:
        return [], 0, warnings
    # 关键帧自带视频时间轴：段时长就是成片时长，直接采用（不按总预算压缩、不砍近景）。
    beat_totals = [
        sum(int(beat.get("duration_s") or 2) for beat in item.video_beats)
        if item.video_beats else 0
        for item in items
    ]
    if any(beat_totals):
        durations = [
            max(3, min(15, total)) if total else shot_limits(items[index].shot_type)[0]
            for index, total in enumerate(beat_totals)
        ]
        warnings.append(
            "关键帧自带 video_beats 时间轴：按段落合计出片，不按总时长预算压缩",
        )
        return durations, sum(durations), warnings
    # 模板固定时长：只有调用方明确给 caps_duration 且没有总预算时才整批用它。
    if total_s <= 0 and caps_duration > 0:
        warnings.append(
            f"视频模板固定时长 {caps_duration}s：每镜按该值出片，总时长＝{n * caps_duration}s",
        )
        return [int(caps_duration)] * n, int(caps_duration) * n, warnings
    if total_s <= 0:
        visible = _visible_text(text)
        # 覆盖式分镜不设 30s 上限：镜头多就按每镜约 4s 自然增长，不砍镜（用户定案「不要压缩」）。
        estimated = max(n * 4, len(visible) // _CHARS_PER_ESTIMATE_SECOND)
        total_s = max(_DEFAULT_TOTAL_MIN, estimated)
        warnings.append(f"未设视频时长，按正文长度估算整楼总时长 {total_s}s")
    limits = [shot_limits(item.shot_type) for item in items]
    min_total = sum(low for low, _high in limits)
    max_total = sum(high for _low, high in limits)
    if total_s < min_total:
        warnings.append(
            f"总时长 {total_s}s 不足以给 {n} 镜各自下限（合计 {min_total}s："
            f"cut≥3s / move_single≥4s / move≥5s），已抬到 {min_total}s",
        )
        total_s = min_total
    if total_s > max_total:
        warnings.append(
            f"总时长 {total_s}s 超过 {n} 镜各自上限之和 {max_total}s（"
            f"cut≤6s / move_single≤10s / move≤15s），已压到 {max_total}s",
        )
        total_s = max_total

    next_offsets: list[int | None] = [
        items[i + 1].start_offset if i + 1 < n else None for i in range(n)
    ]
    weights = [max(1, _window_weight(item, text, next_offsets[i])) for i, item in enumerate(items)]
    weight_sum = sum(weights) or n
    durations = [
        max(limits[i][0], min(limits[i][1], round(total_s * weight / weight_sum)))
        for i, weight in enumerate(weights)
    ]
    diff = int(total_s) - sum(durations)
    guard = 0
    while diff != 0 and guard < 1000:
        guard += 1
        if diff > 0:
            candidates = [i for i, d in enumerate(durations) if d < limits[i][1]]
            if not candidates:
                break
            # 余量优先补给窗口更长的镜头（权重降序，稳定）
            candidates.sort(key=lambda i: (-weights[i], i))
            for i in candidates[:diff]:
                durations[i] += 1
                diff -= 1
        else:
            candidates = [i for i, d in enumerate(durations) if d > limits[i][0]]
            if not candidates:
                break
            candidates.sort(key=lambda i: (weights[i], i))
            for i in candidates[:abs(diff)]:
                durations[i] -= 1
                diff += 1
    actual_total = sum(durations)
    if actual_total != int(total_s):
        warnings.append(
            f"分镜时长无法精确落在 {total_s}s（实际 {actual_total}s）："
            "按镜头类型夹值（cut 3–6s / move_single 4–10s / move 5–15s）导致",
        )
    return durations, actual_total, warnings


_ENV_COMPOSITIONS = frozenset({"远景", "全景", "环境空镜", "远景/环境空镜", "大远景"})
# 开场环境镜默认沿主动线推近：用户定案「从门前的红地毯运镜延伸到仓库中央的台子上」。
_DEFAULT_ESTABLISH_MOVE = "镜头从场景入口沿主要动线缓慢推近到场景中央，保持空间连续"
# 人物入场拍：判据常把它当「低分过渡」跳过（真机实锤：开场后直接切到婚纱细节），
# 这里做确定性补拍，保证「门一响 → 人走进来(全身)」这一拍永远存在。
_ENTRANCE_CUES = ("走进", "推门", "进门", "踏入", "迈进来", "从侧门", "跨进门", "走进来")
# 人物动作句的起首人称：判据常把这类句子误标成 establish（环境空镜）。
_PERSON_PRONOUNS = ("她", "他", "你", "我", "她们", "他们")
# 对白句与「说话动作」信号：对白不做音频也必须拍口型（用户 2026-11-09 定案）。
_DIALOGUE_RE = re.compile(r"[「“][^」”\n]{1,}[」”]")
_SPEECH_ACTION_CUES = ("开口", "嘴唇", "口型", "说话", "念", "答", "低声", "出声", "喉")
# 口型只在**脸部可见**时补：背对/背影构图里写「嘴唇开合」会毁构图（用户 2026-11-10）。
_BACK_FACING_CUES = (
    "背对", "背向", "背过身", "背影", "后脑", "后颈", "背脊", "背身", "背朝", "背面",
)
_FACE_VISIBLE_CUES = (
    "正脸", "正面", "面向", "看向", "望向", "抬头", "抬眼", "回头",
    "转过脸", "侧过脸", "侧脸", "露出脸", "脸上",
)
# 画面动作句的最低信号：用来把「纯对白锚点」换成前一句真正可拍的动作。
_VISUAL_CUES = (
    "走", "坐", "站", "拿", "放", "搁", "抽", "摊", "看", "转", "抬", "低",
    "推", "拉", "披", "褪", "收", "握", "按", "压", "迈", "进", "出", "停",
    "靠", "倚", "跪", "蹲", "躺", "抱", "提", "拾", "递", "接", "开", "关",
)
# 近景信号：这些关键帧不再单独出图，合并进上一条全身/主画面关键帧的 video_beats。
_CLOSEUP_CUES = (
    "特写", "近景", "上半身", "胸部", "乳房", "背部", "腰后", "腰线", "腰部",
    "脚部", "高跟", "手部", "手指", "头纱",
)


def _is_closeup_anchor(item: dict) -> bool:
    signal = f"{item.get('composition') or ''} {item.get('framing') or ''}"
    return any(cue in signal for cue in _CLOSEUP_CUES)


def _closeup_beat(item: dict) -> dict:
    return {
        "duration_s": 3,
        "cut": True,
        "framing": str(item.get("framing") or item.get("composition") or "").strip()[:60],
        "camera_position": str(item.get("camera_position") or "沿用主帧机位").strip()[:100],
        "camera_motion": str(item.get("camera_motion") or "固定").strip()[:80],
        "subject_motion": str(
            item.get("subject_motion") or "主体随呼吸与动作轻微起伏"
        ).strip()[:160],
        "description": str(item.get("anchor") or "").strip()[:400],
        "sound_cue": str(item.get("sound_cue") or "").strip()[:60],
        "viewpoint": str(item.get("viewpoint") or "").strip()[:20],
    }


def _can_merge_closeup(previous: dict, item: dict) -> bool:
    if not _is_closeup_anchor(item):
        return False
    if scene_illustration.normalize_shot_type(previous.get("shot_type")) in ("move", "establish"):
        return False
    return bool(
        not previous.get("subjects")
        or not item.get("subjects")
        or set(previous.get("subjects") or []) & set(item.get("subjects") or [])
    )


def _merge_closeups_into_keyframes(items: list[dict]) -> list[dict]:
    """相邻近景不单独出图：合并进上一条主画面关键帧的 video_beats（2026-11-09 用户定案）。

    - 只在上一帧不是 `move`（双图运镜）或 `establish`（环境）时合并；
    - 主帧若原本没有时间轴，先用它自己的画面补第一段（cut=false），再追加近景段（cut=true）；
    - 主帧若原本是 `move_single`，合并后改 `cut`（同一张图内部硬切，不再声明连续路径）。
    """
    if len(items) < 2:
        return items
    merged: list[dict] = []
    for item in items:
        if not merged:
            merged.append(item)
            continue
        previous = merged[-1]
        if not _can_merge_closeup(previous, item):
            merged.append(item)
            continue
        if not previous.get("video_beats"):
            previous["video_beats"] = [{
                "duration_s": 3,
                "cut": False,
                "framing": str(
                    previous.get("framing") or previous.get("composition") or "全身"
                ).strip()[:60],
                "camera_position": str(
                    previous.get("camera_position") or "沿用主帧机位"
                ).strip()[:100],
                "camera_motion": str(previous.get("camera_motion") or "固定").strip()[:80],
                "subject_motion": str(previous.get("subject_motion") or "").strip()[:160],
                "description": str(previous.get("anchor") or "").strip()[:400],
                "sound_cue": str(previous.get("sound_cue") or "").strip()[:60],
                "viewpoint": str(previous.get("viewpoint") or "").strip()[:20],
            }]
        previous["video_beats"] = [*previous["video_beats"], _closeup_beat(item)]
        # 被合并的近景帧也要把覆盖段号带进主帧，否则覆盖账本会凭空少段。
        scene_illustration.merge_paragraph_ledger(previous, item)
        if scene_illustration.normalize_shot_type(previous.get("shot_type")) == "move_single":
            previous["shot_type"] = "cut"
            previous["transition_desc"] = ""
    return merged


def _sentence_with_cue(segment: str, cues: tuple[str, ...]) -> str:
    for sentence in re.split(r"(?<=[。！？!?])", segment or ""):
        text = sentence.strip()
        if text and any(cue in text for cue in cues):
            return text
    return ""


def fit_video_beats(value: object, *, min_total: int = 3, max_total: int = 15) -> list[dict]:
    """关键帧内部时间轴：最多 4 段，单段 1–8s，总量夹到 3–15s。

    近景/硬切都在这里表达，**不再为每个近景单独生图**（2026-11-09 用户定案）。
    """
    if not isinstance(value, list):
        return []
    beats: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            duration = int(float(item.get("duration_s") or 2))
        except (TypeError, ValueError):
            duration = 2
        beats.append({
            "duration_s": max(1, min(8, duration)),
            "cut": bool(item.get("cut")),
            "framing": str(item.get("framing") or "").strip()[:60],
            "camera_position": str(item.get("camera_position") or "").strip()[:100],
            "camera_motion": str(item.get("camera_motion") or "").strip()[:80],
            "subject_motion": str(item.get("subject_motion") or "").strip()[:160],
            "description": str(item.get("description") or "").strip()[:400],
            "sound_cue": str(item.get("sound_cue") or "").strip()[:60],
            # 逐拍视角（pov/observer）：全链保留，不能在这里丢。
            "viewpoint": str(item.get("viewpoint") or "").strip()[:20],
        })
        if len(beats) >= 4:
            break
    if not beats:
        return []
    durations = [int(beat["duration_s"]) for beat in beats]
    total = sum(durations)
    if total > max_total:
        # 按比例压到上限（保序、每段至少 1s），再补/削到精确上限。
        durations = [max(1, round(d * max_total / total)) for d in durations]
        while sum(durations) > max_total:
            index = max(range(len(durations)), key=lambda i: durations[i])
            if durations[index] <= 1:
                break
            durations[index] -= 1
        while sum(durations) < max_total:
            index = min(range(len(durations)), key=lambda i: durations[i])
            if durations[index] >= 8:
                break
            durations[index] += 1
    elif total < min_total:
        durations[-1] += min_total - total
    for beat, duration in zip(beats, durations):
        beat["duration_s"] = int(duration)
    return beats


def _first_sentence(text: str, limit: int = 120) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    match = re.search(r"[^。！？!?]+[。！？!?]?", text)
    return (match.group(0) if match else text).strip()[:limit]


def _establish_anchor(sentence: str, original_index: float = -1) -> dict:
    return {
        "anchor": sentence,
        "composition": "环境空镜",
        "subjects": [],
        "reason": "补开场环境覆盖（判据未返回环境镜）",
        "score": 1,
        "shot_type": "establish",
        "transition_desc": "",
        "framing": "远景/环境空镜",
        "camera_motion": "固定",
        "subject_motion": "环境中的道具、群演、光线与尘埃持续轻微运动",
        "sound_cue": "",
        "original_index": original_index,
    }


def _ensure_entrance_shot(items: list[dict], text: str) -> list[dict]:
    """开场环境镜之后若没有「人物走进来」这一拍，从正文补一条全身硬切。"""
    if len(items) < 2:
        return items
    first = items[0]
    if scene_illustration.normalize_shot_type(first.get("shot_type")) != "establish":
        return items
    if any(
        any(cue in str(item.get("anchor") or "") for cue in _ENTRANCE_CUES)
        for item in items[1:]
    ):
        return items
    first_start = first.get("anchor_offset")
    if first_start is None:
        first_start = _locate(text, str(first.get("anchor") or ""))
    if first_start is None:
        return items
    boundary = _locate(text, str(items[1].get("anchor") or "")) or len(text or "")
    # `_locate` 返回的是锚点**句末**偏移（见 illustration_anchor_offset），不要再加 anchor 长度。
    segment = (text or "")[first_start:boundary]
    sentence = _sentence_with_cue(segment, _ENTRANCE_CUES)
    if not sentence:
        return items
    sound = "门轴声" if "门" in sentence else ("开门声" if "开" in sentence else "")
    first_original = first.get("original_index", 0)
    # 入场主体沿用**后面第一个有人物 subjects 的锚点**（刚走进来的角色），避免回落到全场群演。
    subject_source = next(
        (item for item in items[1:] if item.get("subjects")), items[1],
    )
    entrance_subjects = [
        str(name).strip() for name in (subject_source.get("subjects") or [])
        if str(name).strip()
    ]
    return [first, {
        "anchor": sentence,
        "composition": "全身",
        "subjects": entrance_subjects,
        "reason": "补人物入场拍（门响 → 走进来，全身）",
        "score": 1,
        "shot_type": "cut",
        "transition_desc": "",
        "framing": "全身",
        "camera_motion": "固定",
        "subject_motion": "她推门走进来，裙摆与头纱随步伐摆动",
        "sound_cue": sound,
        # 插在两锚点之间：用半整数保留判据原下标链（move 的「原文相邻」判定仍按 0,1,2…）。
        "original_index": float(first_original) + 0.5,
    }, *items[1:]]


def _is_dialogue_anchor(anchor: str, text: str) -> bool:
    """锚点是不是纯对白（引号内台词或以引号开头）——对白不能当画面起始锚点。"""
    value = (anchor or "").strip()
    if not value:
        return False
    if value[0] in "「“":
        return True
    return any(value in match.group(0) for match in _DIALOGUE_RE.finditer(text))


def _visual_fallback(text: str, pos: int) -> str:
    """从对白锚点往前找最近一句可拍的动作/环境句（不跨镜、不改正文）。"""
    prefix = (text or "")[max(0, pos - 400):pos]
    for sentence in reversed(re.split(r"(?<=[。！？!?])", prefix)):
        sentence = sentence.strip()
        if len(sentence) < 8 or _DIALOGUE_RE.search(sentence):
            continue
        if any(cue in sentence for cue in _VISUAL_CUES):
            return sentence
    return ""


def _replace_dialogue_anchors(items: list[dict], text: str) -> list[dict]:
    """把判据误选的「纯对白锚点」换成前一句画面动作句（用户 2026-11-10 实锤）。"""
    for item in items:
        anchor = str(item.get("anchor") or "").strip()
        if not _is_dialogue_anchor(anchor, text):
            continue
        pos = _locate(text, anchor)
        if pos is None:
            continue
        fallback = _visual_fallback(text, pos)
        if fallback:
            item["anchor"] = fallback
    return items


def _infer_anchor_paragraphs(text: str, item: dict) -> dict:
    """判据漏写 `paragraphs` 时，用 anchor 逐字落段兜底（只认逐字命中）。"""
    if item.get("paragraphs"):
        return item
    raw = str(item.get("anchor") or "").strip()
    if not raw:
        return item
    for index, paragraph in enumerate(re.split(r"\n\s*\n", text or "")):
        if paragraph.strip() and raw in paragraph:
            item["paragraphs"] = [index]
            break
    return item


def prepare_storyboard_anchors(
    anchors: list[dict], text: str, main_actors: list[str] | set[str] | None = None,
) -> list[dict]:
    """覆盖式分镜的第一道闸：保证开场环境镜 + 人物入场拍，首镜标为 establish。

    三种修法（都不额外调 LLM）：
    ① 判据首条本身就是环境（远景/全景且主体不含主要角色）⇒ 原地提升为 establish；
    ② 判据首条已在正文第一段之后 ⇒ 从开场正文补一条 establish（首句逐字）；
    ③ establish 之后没有「走进/推门/进门」拍 ⇒ 从两锚点之间的正文补一条全身硬切。
    """
    items = [
        dict(item) for item in (anchors or [])
        if isinstance(item, dict) and str(item.get("anchor") or "").strip()
    ]
    if not items:
        return []
    # 对白不能当画面起始锚点：先换成前一句动作句（用户 2026-11-10 实锤）。
    items = _replace_dialogue_anchors(items, text)
    first = items[0]
    main = {str(name).strip() for name in (main_actors or []) if str(name).strip()}
    subjects = {str(name).strip() for name in (first.get("subjects") or [])}
    composition = str(first.get("composition") or "").strip()
    main_in_first = bool(subjects & main)
    # 「人物出场句」才配叫伪 establish：**首分句**里出现主要角色名，或以人称代词开头
    #（「角色甲的手停在半空」「她没有立刻接话」）。
    # ⛔ 名字出现在后面的分句不算（「日头爬到檐角…她便从前殿回来了」仍是环境开场）；
    # ⛔ 也不能只看 subjects ∩ main——判据可能给环境镜 subjects=[]，蛮啾/群演也常在 [在场] 里。
    anchor_head = str(first.get("anchor") or "").lstrip("*＊ \t")
    head_clause = re.split(r"[，,。；;！？!?]", anchor_head, maxsplit=1)[0]
    starts_with_main = any(name and name in head_clause for name in main)
    starts_with_person = anchor_head.startswith(_PERSON_PRONOUNS)
    first_type = scene_illustration.normalize_shot_type(first.get("shot_type"))
    demoted = False
    if first_type == "establish" and (starts_with_main or starts_with_person):
        # 判据把「人物动作」误标成 establish：降回 cut。否则下游按环境空镜清空人物
        # （真机实锤 2026-11-09：开场人物镜被清成空景，图片与锚点对不上）。
        first["shot_type"] = "cut"
        first_type = "cut"
        demoted = True
    if first_type != "establish":
        promoted = False
        # 已判为伪 establish 的不再按「构图是环境」反推回 establish——
        # 否则 subjects 为空时会被重新提升（2026-11-09 单测实锤）。
        if not demoted and composition in _ENV_COMPOSITIONS and not main_in_first:
            first["shot_type"] = "establish"
            if not str(first.get("framing") or "").strip():
                first["framing"] = composition
            promoted = True
        if not promoted:
            first_offset = first.get("anchor_offset")
            if first_offset is None:
                first_offset = _locate(text, str(first.get("anchor") or ""))
            # 首锚点就在**第一段**里 ⇒ 开场已被覆盖（短文本不因比例阈值被误补镜）。
            first_paragraph_end = (text or "").find("\n\n")
            if first_paragraph_end < 0:
                first_paragraph_end = len(text or "")
            if first_offset is not None and first_offset > first_paragraph_end:
                opening = (text or "")[:first_offset].strip()
                paragraph = next(
                    (part.strip() for part in re.split(r"\n\s*\n", opening) if part.strip()), "",
                )
                sentence = _first_sentence(paragraph)
                # 开场句里已出现主要角色 ⇒ 不是环境镜，不能拿来做 establish
                # （真机实锤 2026-11-09：开场「角色甲的手停在半空」被当空镜清空人物）。
                mentions_main = any(name and name in sentence for name in main)
                if sentence and sentence in (text or "") and not mentions_main:
                    first_original = first.get("original_index", 0)
                    items = [_establish_anchor(sentence, first_original - 1), *items]
    items = _ensure_entrance_shot(items, text)
    # 覆盖账本：判据漏写段号时按锚点逐字落段；近景合并/去重前先补齐，
    # 被合并的段号才能随 `merge_paragraph_ledger` 带进主帧。
    items = [_infer_anchor_paragraphs(text, item) for item in items]
    # 相邻近景合并进主画面关键帧：不再为胸/背/腰/脚单独出图（用户定案）。
    items = _merge_closeups_into_keyframes(items)
    # 全自动连续锚点：同场景各镜共用空间锚点，生产/离线模拟都从这里取，不再手工写死。
    items = assign_scene_anchors(items, text)
    return items


def _beats_mention_speech(beats: list[dict]) -> bool:
    """beats 里是否写到了可拍的说话动作（不看 sound_cue——声音不等于口型）。"""
    text = " ".join(
        str(beat.get(key) or "")
        for beat in beats
        for key in ("framing", "camera_motion", "subject_motion", "description")
    )
    return any(cue in text for cue in _SPEECH_ACTION_CUES)


def _dialogue_face_visible(beats: list[dict], raw: dict, anchor: str) -> bool:
    """说话角色在**末段**是否脸部可见（正脸/侧脸/回头）；纯背影 ⇒ 不补口型。

    只在「背对类」出现且没有「回头/侧脸/正脸」类时判不可见；没有朝向线索时
    按可见处理，避免把普通正面对白误杀。
    """
    last = beats[-1] if beats else {}
    focus = " ".join([
        str(anchor or ""),
        str(raw.get("framing") or ""),
        *[str(last.get(key) or "") for key in (
            "framing", "camera_motion", "subject_motion", "description",
        )],
    ])
    if any(cue in focus for cue in _BACK_FACING_CUES):
        return any(cue in focus for cue in _FACE_VISIBLE_CUES)
    return True


def build_shot_plan(
    anchors: list[dict], text: str, *, sequence_id: str = "",
    total_s: int = 0, caps_duration: int = 0,
    main_actors: list[str] | set[str] | None = None,
    present: str = "", wardrobe_by_actor: dict[str, str] | None = None,
) -> ShotPlan:
    """锚点数组 → 镜头清单（正文序、每锚点一支镜头、move 消耗下一锚点当尾帧）。

    `present`＝状态块 `[在场]` 原文：同场景/同角色锚点都从这里自动派生，调用方
    不需要（也不应该）逐镜手写 anchor。
    """
    anchors = prepare_storyboard_anchors(anchors, text, main_actors)
    seq = sequence_id or "shots"
    warnings: list[str] = []
    items: list[ShotPlanItem] = []
    scene_windows: dict[str, str] = {}
    scene_locales: dict[str, str] = {}
    scene_framings: dict[str, str] = {}
    count = len(anchors)
    for index, raw in enumerate(anchors):
        anchor = str(raw.get("anchor") or "").strip()
        shot_type = scene_illustration.normalize_shot_type(raw.get("shot_type"))
        transition_desc = str(raw.get("transition_desc") or "").strip()[:200]
        camera_motion = str(raw.get("camera_motion") or "").strip()[:60]
        start_offset = raw.get("anchor_offset")
        if start_offset is None:
            start_offset = _locate(text, anchor)
        nxt = anchors[index + 1] if index + 1 < count else None
        next_original = None
        if nxt is not None:
            next_original = nxt.get("original_index", index + 1)
        current_original = raw.get("original_index", index)
        end_offset = None

        if shot_type == "move":
            next_offset = None
            if nxt is not None:
                next_offset = nxt.get("anchor_offset")
                if next_offset is None:
                    next_offset = _locate(text, str(nxt.get("anchor") or ""))
            reason = ""
            if nxt is None:
                reason = "最后一锚点没有下一帧"
            elif not transition_desc:
                reason = "缺少 transition_desc"
            elif next_original != current_original + 1:
                reason = "下一锚点在批量选择中被裁掉，不再相邻"
            elif not same_scene(
                {"anchor_offset": start_offset}, {"anchor_offset": next_offset}, text,
            ):
                reason = "两锚点之间有显式场景切换"
            if reason:
                warnings.append(f"第 {index} 锚点 move 降级 cut：{reason}")
                shot_type = "cut"
                transition_desc = ""
        elif shot_type == "move_single":
            if not transition_desc:
                warnings.append(f"第 {index} 锚点 move_single 降级 cut：缺少 transition_desc")
                shot_type = "cut"
        if shot_type == "move":
            end_anchor = str(nxt.get("anchor") or "").strip() if nxt else ""
            end_offset = nxt.get("anchor_offset") if nxt else None
            if end_offset is None:
                end_offset = _locate(text, end_anchor)
            video_mode = "shot_move"
            end_image_key = f"{seq}:{index + 1}"
        elif shot_type == "establish":
            end_anchor = ""
            end_image_key = ""
            # 首镜环境空镜默认沿主动线推近（判据写「固定」也按用户口径改成运镜）；
            # 后续环境镜尊重判据的机位运动。
            if index == 0 and not transition_desc and camera_motion in ("", "固定"):
                transition_desc = _DEFAULT_ESTABLISH_MOVE
                camera_motion = "缓慢推近"
            video_mode = "shot_establish_move" if transition_desc else "shot_establish"
        else:
            end_anchor = ""
            end_image_key = ""
            video_mode = "shot_cut" if shot_type == "cut" else "shot_move_single"

        raw_beats = raw.get("video_beats")
        beats = fit_video_beats(raw_beats)
        if isinstance(raw_beats, list) and len(raw_beats) > 4:
            warnings.append(
                f"第 {index} 关键帧 video_beats {len(raw_beats)} 段超过上限 4，已截断",
            )
        # 对白必须有说话动作（用户 2026-11-09 定案）：不做音频也要拍到口型/张嘴。
        # 只告警不改稿——判据漏写时让人在离线模拟/真机审阅时看得见。
        if beats:
            window_end = end_offset if shot_type == "move" else None
            if window_end is None and nxt is not None:
                window_end = nxt.get("anchor_offset")
                if window_end is None:
                    window_end = _locate(text, str(nxt.get("anchor") or ""))
            if window_end is None:
                window_end = len(text)
            window = (
                text[start_offset:window_end]
                if start_offset is not None and window_end is not None else ""
            )
            # 对白只在**本镜第一段**内补口型：跨段对白属于别的时刻，
            # 塞进本镜会变成无关台词（用户 2026-11-10 实锤：石案镜头被塞前殿点卯台词）。
            paragraph_end = window.find("\n\n")
            if paragraph_end < 0:
                paragraph_end = len(window)
            local_dialogue = _DIALOGUE_RE.search(window[:paragraph_end])
            any_dialogue = _DIALOGUE_RE.search(window)
            if local_dialogue is not None and not _beats_mention_speech(beats):
                if not _dialogue_face_visible(beats, raw, anchor):
                    # 背对/后脑构图里没有嘴：补口型会强迫模型把脸转过来，破坏构图。
                    warnings.append(
                        f"第 {index} 关键帧含对白但说话角色背对镜头/脸部不可见，未补口型",
                    )
                else:
                    # 判据漏写时**确定性补**到末段：对白不做音频，但口型必须有。
                    # 只补末段（对白通常在本镜后段），不改段数/时长；台词截短只作口型提示。
                    line = local_dialogue.group(0)
                    short_line = line if len(line) <= 24 else line[:24] + "…"
                    last = beats[-1]
                    description = str(last.get("description") or "").strip()
                    last["description"] = (
                        f"{description}；嘴唇开合（无声音）；对白：{short_line}"
                        if description else
                        f"嘴唇开合（无声音）；对白：{short_line}"
                    )
                    subject_motion = str(last.get("subject_motion") or "").strip()
                    last["subject_motion"] = (
                        f"{subject_motion}；嘴唇开合（无声音）"
                        if subject_motion else "嘴唇开合（无声音）"
                    )
                    warnings.append(
                        f"第 {index} 关键帧含对白但 video_beats 未写说话动作，已补到末段",
                    )
            elif any_dialogue is not None:
                warnings.append(
                    f"第 {index} 关键帧窗口含对白但不在本镜第一段，未补口型",
                )
        scene_key = str(raw.get("scene_key") or "").strip()[:32]
        if scene_key and scene_key not in scene_windows:
            scene_windows[scene_key] = str(
                raw.get("scene_window") or raw.get("scene_anchor") or anchor
            ).strip()[:1200]
            scene_locales[scene_key] = str(raw.get("locale") or "").strip()[:80]
            scene_framings[scene_key] = str(raw.get("framing") or "").strip()[:40]
        items.append(ShotPlanItem(
            shot_index=index, shot_type=shot_type,
            start_anchor=anchor, end_anchor=end_anchor,
            transition_desc=transition_desc,
            duration_s=0, video_mode=video_mode,
            image_key=f"{seq}:{index}", end_image_key=end_image_key,
            start_offset=start_offset, end_offset=end_offset,
            framing=str(raw.get("framing") or "").strip()[:40],
            camera_motion=camera_motion,
            subject_motion=str(raw.get("subject_motion") or "").strip()[:80],
            sound_cue=str(raw.get("sound_cue") or "").strip()[:40],
            image_timing=str(raw.get("image_timing") or "start").strip()[:8] or "start",
            locale=str(raw.get("locale") or "").strip()[:80],
            paragraphs=list(raw.get("paragraphs") or []),
            video_beats=beats,
            scene_key=scene_key,
            scene_anchor=str(raw.get("scene_anchor") or "").strip()[:240],
        ))
    durations, actual_total, duration_warnings = plan_shot_durations(
        items, text, total_s, caps_duration=caps_duration,
    )
    warnings.extend(duration_warnings)
    for item, duration in zip(items, durations):
        item.duration_s = int(duration)
        # 图片位置换算成具体秒数：判据只给 start/mid/end，这里按锚点落在哪一段
        # 反推真实秒数（锚点在第 2 段 ⇒ 图片不是首帧，是 3–6s 的画面）。
        item.image_time_s, item.image_timing = resolve_image_position(item)
    # 同场景多镜却没有空间锚点＝正文没有可用陈设描述，显式告警，不假装连续。
    scene_sizes: dict[str, int] = {}
    for item in items:
        if item.scene_key:
            scene_sizes[item.scene_key] = scene_sizes.get(item.scene_key, 0) + 1
    warned_scenes: set[str] = set()
    for item in items:
        if (item.scene_key and item.scene_key not in warned_scenes
                and scene_sizes.get(item.scene_key, 0) > 1 and not item.scene_anchor):
            warned_scenes.add(item.scene_key)
            warnings.append(
                f"{item.scene_key} 有 {scene_sizes[item.scene_key]} 镜，但正文未提取到空间锚点"
                "（scene_anchor 为空）",
            )
    wardrobe_map = dict(wardrobe_by_actor or parse_wardrobe_anchors(present))
    scene_cards = {
        key: scene_card.extract_scene_card(
            window,
            scene_key=key,
            locale=scene_locales.get(key, ""),
            framing=scene_framings.get(key, ""),
        )
        for key, window in scene_windows.items()
    }
    return ShotPlan(
        sequence_id=seq, total_shots=len(items),
        total_duration_s=actual_total, items=items, warnings=warnings,
        wardrobe_by_actor=wardrobe_map, scene_cards=scene_cards,
    )
