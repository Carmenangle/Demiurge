"""逐镜图片提示词编译：把「角色外貌锚点 + 景别/机位 + 分镜拍点」补进原始 image_prompt。

背景（2026-11-12 用户实锤）：`shot_07` 出图成了臀腿特写、脸也歪了。取证发现
`review_still_*.json` 里明明有 `framing=中景/门口与床`、`camera_motion=固定`、
`video_beats` 4 拍、`character_refs[].base_image`，但**这些一个都没进 image_prompt** ——
`image_prompt` 只有 tag 行 + 一段自然语言，构图与身份全靠模型自己猜。

本模块只做「把已有事实编译成提示词」，不生成、不改写原稿内容：
- `appearance_anchor()`：用 WD14 对角色状态图反推外貌标签（发色/瞳色/体型/发型），
  作为**该角色这身打扮的真实外貌**，不再靠手写 `golden blonde long hair`；
- `framing_tags()`：`中景/门口与床` → `medium shot` + 多主体同框；
- `camera_tags()`：`固定` → `static camera`；
- `compose_shot_prompt()`：把上面三样接到原稿 tag 行末尾（tag 行权重最高），
  并追加一行 `[构图]` 英文短句。

⛔ 不改原稿的 tag 与自然语言正文，只做「追加」；调用方要保留原文以便审阅对比。
"""
from __future__ import annotations

from pathlib import Path

# 景别：只认「/」前那一段，避免把「门口与床」这类场景词当景别
_SHOT_SIZE = {
    "大特写": "extreme close-up",
    "特写": "close-up",
    "近景": "close shot",
    "中近景": "medium close-up",
    "中景": "medium shot",
    "中全景": "medium full shot",
    "全景": "full shot",
    "远景": "wide shot",
    "大全景": "extreme wide shot",
    "环境空镜": "wide establishing shot, no people",
}

# 机位运动：固定优先，其余按关键词兜底
_CAMERA = {
    "固定": "static camera",
    "轻微下摇": "slight tilt down",
    "下摇": "tilt down",
    "上摇": "tilt up",
    "缓慢横移": "slow lateral truck",
    "横移": "lateral truck",
    "推进": "dolly in",
    "拉远": "dolly out",
    "跟随": "tracking shot",
    "摇": "pan",
}

# WD14 里属于「身份」而非「内容」的标签前缀，只取这些做外貌锚点
_APPEARANCE_SUFFIXES = (
    "_hair", "hair", "_eyes", "eyes", "_ears", "ears",
    "breasts", "_hips", "hips", "_waist", "waist",
    "muscular", "slim", "curvy", "tall",
)
# 会被 we 主动排除的外观标签（属于服装/场景，不是身体）
_APPEARANCE_BLOCKLIST = {
    "hair_ornament", "hairband", "hair_bow", "hair_flower", "hair_ribbon",
    "hair_between_eyes", "hair_over_one_eye", "hair_up", "hair_pulled_back",
}


def appearance_anchor(image_path: Path | str | None, *, min_score: float = 0.6,
                      limit: int = 8) -> list[str]:
    """用 WD14 从角色图的**当前状态**反推外貌标签。

    返回空列表表示拿不到（模型未装/图不存在/没命中），调用方不应因此中断出图。
    """
    if not image_path:
        return []
    path = Path(image_path)
    if not path.is_file():
        return []
    try:
        from app.services import wd14_tagger

        if not wd14_tagger.model_available():
            return []
        tags = wd14_tagger.predict(path, include_rating=False)
    except Exception:  # noqa: BLE001 打标失败不阻断出图
        return []

    picked: list[str] = []
    for name, score in tags:
        if score < min_score:
            continue
        low = name.lower()
        if low in _APPEARANCE_BLOCKLIST:
            continue
        if any(low == s or low.endswith(s) for s in _APPEARANCE_SUFFIXES):
            picked.append(low.replace("_", " "))
    # `breasts` 会先于 `large breasts` 命中（分更高），但它被后者包含 ⇒
    # 去掉被更具体标签覆盖的短标签，避免 tag 行里两个都写。
    picked = [t for t in picked if not any(t != o and t in o for o in picked)]
    # 保持 WD14 的分序（picked 已是按分降序），截断上限
    return picked[:limit]


# 「我」是第一人称不入画；蛮啾是小动物，不算「人」。判「单个人类主体」时要排除。
_NON_HUMAN = {"我", "你", "蛮啾"}


def is_single_human(characters: list[str] | None) -> bool:
    """本镜是不是「只有一个人」——决定要不要加「画面里只出现一个人」的硬约束。

    真机实锤：`shot_02` 的 `characters=['腓特烈']`、tag 行也有 `solo`，
    仍画出两个一模一样的金发女人（复制）。`solo` 在 Krea2 上压不住，需显式否定重复。
    """
    humans = [str(name) for name in (characters or []) if str(name) and str(name) not in _NON_HUMAN]
    return len(humans) == 1


def framing_tags(framing: str) -> tuple[list[str], bool]:
    """返回 (景别/构图 tag, 是否多主体同框)。"""
    text = str(framing or "").strip()
    if not text:
        return [], False
    head, _, tail = text.partition("/")
    tags: list[str] = []
    size = _SHOT_SIZE.get(head.strip())
    if size:
        tags.append(size)
    # 没有「/」时整句就是场景描述（如「门口与床」），仍要判多主体。
    scope = tail if tail.strip() else head
    multi = any(sep in scope for sep in ("与", "和", "、"))
    if multi:
        tags.append("two subjects in one frame")
    return tags, multi


def camera_tags(camera_motion: str) -> list[str]:
    text = str(camera_motion or "").strip()
    if not text:
        return []
    if text in _CAMERA:
        return [_CAMERA[text]]
    for key, value in _CAMERA.items():
        if key in text:
            return [value]
    return []


def compose_shot_prompt(
    base_prompt: str,
    *,
    framing: str = "",
    camera_motion: str = "",
    appearance: list[str] | None = None,
    scene_hint: str = "",
    single_subject: bool = False,
) -> str:
    """把外貌/景别/机位接到原稿 tag 行末尾，并追加一行 `[构图]`。

    只追加、不改写：原稿 tag 行与自然语言正文逐字保留，方便审阅稿对照。
    """
    base = str(base_prompt or "").rstrip()
    if not base:
        return base

    added: list[str] = []
    for tag in (appearance or []):
        if tag and tag.lower() not in base.lower():
            added.append(tag)

    f_tags, multi = framing_tags(framing)
    for tag in f_tags:
        if tag.lower() not in base.lower():
            added.append(tag)
    for tag in camera_tags(camera_motion):
        if tag.lower() not in base.lower():
            added.append(tag)
    if single_subject:
        for tag in ("only one person in frame", "no duplicate characters"):
            if tag not in base.lower():
                added.append(tag)

    if not added:
        return base

    lines = base.split("\n", 1)
    head = lines[0].rstrip().rstrip(",")
    head = head + ", " + ", ".join(added)
    out = [head] + (lines[1:] if len(lines) > 1 else [])

    composition = ["Keep the framing unambiguous."]
    if f_tags:
        composition.append(f"Use a {f_tags[0]}.")
    if multi:
        composition.append(
            "Both subjects must be visible at once: foreground and background never cropped out."
        )
    if single_subject:
        composition.append(
            "Exactly one person appears in this frame; do not duplicate or mirror her."
        )
    if scene_hint:
        composition.append(f"The moment takes place at {scene_hint}.")
    if len(composition) > 1:
        out.append(" ".join(composition))
    return "\n".join(out)
