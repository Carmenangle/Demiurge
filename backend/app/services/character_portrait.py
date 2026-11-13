"""角色定妆（一键生成角色底图）：把「一个角色的稳定形象」编译成一张肖像提示词。

第 92 刀（2026-11-06）· 用户规格原话：「角色底图在没有的情况下，但是有绑定多元数据插入的
生图工作流模版时，可以利用该工作流一键生成，生成时会参考角色条目外貌，使用绑定的角色lora
和风格lora去生成」。底图（`characterLoras[name].baseImage`）缺失时用它补上。

三条边界：

· **只出提示词**。不提交 ComfyUI、不落盘、不碰 LoRA —— 提交／轮询／写回 `baseImage` 在前端
  （`lib/portraitBaseImage.ts`），与高潮点生图共用 `workflow_submission` 那条链；LoRA 栈
  （角色 LoRA ＋ 风格 LoRA）也在那一侧按 `loraMode` 组装。
· **事实只读既有真源，零 LLM、不猜不编**。外貌定序＝世界书条目／角色卡（显式源）→ 已确认
  外貌锚（只补显式源没点名的角色，红线 4）→ 通用表【外貌】/【穿着】融合（第 36 刀口径）；
  性别定序＝世界书条目正文 → 通用表「性别」列（第 46 刀口径）。全部取不到 ⇒
  `source="none"`，**照常出稿**——定妆是「补底图」的便利入口，不该因为资料缺失就把用户堵在门外。
· **恒第三人称、画面主体只有该角色、正面全身站姿**。预设里的 `viewpoint` 属「剧情出图」
  口径；带进定妆会让第一人称闸（`_first_person_narrator` → `_strip_narrator_from_model_view`／
  `_contract_subject_names`）把这个角色从主体名单与模型载荷里一起摘掉，出成「没有人的空镜」。
  取景按用户口径写死（2026-11-06 修订）：全身入镜＋正面朝向镜头、双眼看向镜头＋动作从
  `_PORTRAIT_POSES` 随机取一条（只为看清身体细节，不是剧情动作）。
  ⚠ 原「身体回转约 20°、隐约可见侧身与背身」已废止：它与动作表里的「回望」叠加后，编译器的
  【机位由内容反推】会选 `three-quarter back view`，真机成稿就是背身（2026-11-06 实锤）。
"""
from __future__ import annotations

import random
from typing import Any, Callable, Mapping

from app.services import (
    character_store,
    image_prompt_profiles,
    scene_illustration,
    table_store,
    worldbook_store,
)

DEFAULT_PROFILE = "anima_tags"
# 9:16（2026-11-08 用户定案）：2:3 的定妆成稿（真机 1056×1536）脚底被挤出画框，
# 底图要当后续所有出图的全身锚点 ⇒ 画幅固定 9:16（latent 576×1024 @1024，输出 864×1536）。
DEFAULT_ASPECT_RATIO = "9:16"
APPEARANCE_SOURCE_WORLDBOOK = "worldbook"
APPEARANCE_SOURCE_CHARACTER_CARD = "character_card"

# 定妆画面＝单人、全身、正面站姿、无剧情动作。写成确定性 prose（不给模型自由发挥剧情的空间），
# 与 `protected_narrative`/`wardrobe`/`locale` 一律留空同口径：底图要的是**跨楼层稳定**的形象。
# 用户口径（2026-11-06 修订）：「你不如就正常说看向镜头就行」——原「回转约 20°」会与动作表里
# 的背向姿势叠加，把机位推成 `three-quarter back view`（真机成稿实锤）。
_PORTRAIT_FRAMING = (
    "角色定妆立绘：{name} 一人出镜，正面朝向镜头、双眼看向镜头，神情是本人的常态。"
    "竖构图全身肖像：从头顶到脚底（含鞋子）完整入镜，头顶与脚下各留少量边距，"
    "脸、发型与全身比例都清晰稳定，可作后续出图的形象参考。"
    "站姿自然正面，重心落在一条腿上、双肩放松，面部与身体正面的细节都看得清。"
    "动作：{pose}。该动作只用于让身体线条与细节更好读，不是剧情动作。"
    "背景为简洁的纯色影棚布景，光线均匀柔和。"
    "没有剧情动作，没有其他人物，没有额外道具，不叙事、不推进情节。"
)

# 动作预设表：全部为「正面站姿＋手位变化」，不含道具、位移与转身（道具会污染底图、位移会带出剧情、
# 转身会与「看向镜头」的取景合同冲突）。随机取自本表 ⇒ 每次定妆换一张姿态，点第二次就是另一姿态。
_PORTRAIT_POSES = (
    "双手在身前轻轻交握、指尖自然相扣",
    "一手抬到肩侧轻拢头发、另一手垂在身侧",
    "一手叉腰、另一手垂在身侧",
    "一侧肩膀略沉、双臂自然垂在身侧",
    "双手轻捏衣摆两侧、略微向外提起",
    "两脚一前一后迈开半步、脚步定住",
)


def _read(source: Callable[[], object], default: str = "") -> str:
    """一路事实源读失败 ⇒ 静默降级到下一路（与 `_subject_gender_map` 同口径：缺事实不打断出图）。"""
    try:
        return str(source() or default).strip()
    except Exception:  # noqa: BLE001 [EXC-保留] 事实兜底缺失只降级来源，不得冒泡打断定妆
        return default


def _entry_appearance(base: str, repo_id: str, name: str, appearance_source: str) -> str:
    """显式源：世界书条目的视觉段（默认）或角色卡正文（`character_card` 档）。"""
    if appearance_source == APPEARANCE_SOURCE_CHARACTER_CARD:
        card_base = ""
        try:
            card_base = character_store.repo_card_base(base, repo_id, name) or ""
        except Exception:  # noqa: BLE001 [EXC-保留] 卡快照路径异常按「没有卡」处理
            card_base = ""
        card = None
        if card_base:
            try:
                card = character_store.read_card(card_base, name)
            except Exception:  # noqa: BLE001 [EXC-保留] 卡坏文件按「没有卡」处理
                card = None
        description = str((card or {}).get("description") or "").strip()
        return f"{name}：{description}" if description else ""
    return _read(lambda: worldbook_store.repo_visual_profiles(base, repo_id, name))


def _tables(base: str, repo_id: str) -> list:
    try:
        value = table_store.load(base, repo_id)
    except Exception:  # noqa: BLE001 [EXC-保留] 表格读失败按「没有表」处理
        return []
    return value if isinstance(value, list) else []


def _gender(base: str, repo_id: str, name: str, tables: list) -> str:
    """逐角色性别：世界书条目正文优先、通用表「性别」列兜底；都解析不出 ⇒ `""`（宁缺勿猜）。"""
    gender = _read(lambda: worldbook_store.repo_character_gender(base, repo_id, name))
    return gender or scene_illustration.gender_from_tables(tables, name)


def collect_appearance(
    base: str, repo_id: str, name: str, *, appearance_source: str = APPEARANCE_SOURCE_WORLDBOOK,
    include_table: bool = True,
) -> dict[str, Any]:
    """一个角色的定妆事实（零 LLM）：`{appearance, gender, source}`。

    `appearance` ＝ 条目／卡正文 → 表格融合后的**单角色**外貌段；
    `source` ∈ `entry`／`table`／`merged`／`none`（给面板如实报「外貌来自哪」）。
    2026-11-11 用户口径：正文外貌锚已移除——世界书/角色卡已有外貌时，锚只会与其冲突。
    `include_table=False`：只取条目/卡（聊天出图的固定外貌锚用；表格是当前状态，
    可能与固定发色/瞳色冲突，不能覆盖身份）。
    """
    target = str(name or "").strip()
    if not target:
        raise ValueError("角色名为空，无法定妆")
    entry = _entry_appearance(base, repo_id, target, appearance_source)
    tables = _tables(base, repo_id)
    if include_table:
        appearance = scene_illustration.supplement_actor_table_appearance(
            entry, [target], tables,
        )
        ledger_tables = tables
    elif entry:
        # 条目/卡有外貌就只用它；表格是当前状态，不能覆盖固定发色/瞳色。
        appearance = entry
        ledger_tables = []
    else:
        # 条目也没有外貌时才退回表格，宁缺勿猜。
        appearance = scene_illustration.supplement_actor_table_appearance(
            "", [target], tables,
        )
        ledger_tables = tables
    ledger = scene_illustration.actor_appearance_sources(entry, [target], ledger_tables)
    return {
        "appearance": appearance,
        "gender": _gender(base, repo_id, target, tables),
        "source": ledger.get(target, "none"),
    }


def _pick_pose(pose: str = "") -> str:
    """定妆动作：给了就用给的，没给⇒从预设表随机取一条（不开放旋钮，换动作＝再点一次一键定妆）。"""
    return str(pose or "").strip() or random.choice(_PORTRAIT_POSES)


def portrait_scene(
    name: str, appearance: str, gender: str = "",
    *, aspect_ratio: str = DEFAULT_ASPECT_RATIO, rating: str = "nsfw", pose: str = "",
) -> dict[str, Any]:
    """定妆画面合同（喂给同一条 Profile 编译器的 scene）。

    ⚠ 三件事写死，不给调用方留旋钮：`viewpoint="third_person"`、`narrator=""`、
      主体名单＝`actors`＝`frame_subjects`＝`[该角色]`。留旋钮就等于把「空镜」缺陷重新打开。
    ⚠ `gender` 为空 ⇒ **不写** `subject_genders`（缺键时编译器退回中性措辞，宁缺勿猜）。
    ⚠ 取景（全身＋正面看向镜头）也写死在 prose 里；只有**动作**留一个内部参数：
      空⇒随机，供 `generate_portrait` 先定条再回传给面板显示。
    """
    target = str(name or "").strip()
    if not target:
        raise ValueError("角色名为空，无法定妆")
    scene: dict[str, Any] = {
        "narrative": _PORTRAIT_FRAMING.format(name=target, pose=_pick_pose(pose)),
        "appearance": str(appearance or "").strip(),
        "wardrobe": "",
        "locale": "",
        "draft_prompt": "",
        "actors": [target],
        "frame_subjects": [target],
        "viewpoint": "third_person",
        "narrator": "",
        "rating": str(rating or "nsfw"),
        "aspect_ratio": str(aspect_ratio or DEFAULT_ASPECT_RATIO),
    }
    if gender:
        scene["subject_genders"] = {target: str(gender)}
    return scene


def generate_portrait(
    base: str,
    repo_id: str,
    name: str,
    *,
    profile: str = DEFAULT_PROFILE,
    appearance_source: str = APPEARANCE_SOURCE_WORLDBOOK,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    rating: str = "nsfw",
    pose: str = "",
    generate_text: Callable[[str, str], str],
    overrides: Mapping[str, Any] | None = None,
    preset_dir: str = "",
    preset_name: str = "",
    user_name: str = "",
) -> dict[str, Any]:
    """定妆成稿：取事实 → 组画面 → 交给 `image_prompt_profiles.generate_result`（不另立口径）。

    返回编译结果（`prompt`／`negative_prompt`／`strategy`／`validation_errors`／`field_ledger`）
    并附上 `name`／`appearance`／`gender`／`appearance_origin`／`pose`，供面板如实回显来源与动作。
    外貌为空也照常编译（词表兜底仍能出「单人肖像」）。

    `preset_dir`／`preset_name` ＝ 用户当前的偏置/防拦截预设，与 `/prompt/profile` 同口径拼进
    system；**两者任一为空 ⇒ `generate_text` 原样透传**（不包一层，零回归）。
    """
    facts = collect_appearance(base, repo_id, name, appearance_source=appearance_source)
    chosen_pose = _pick_pose(pose)
    scene = portrait_scene(
        name, facts["appearance"], facts["gender"],
        aspect_ratio=aspect_ratio, rating=rating, pose=chosen_pose,
    )
    text = generate_text
    if str(preset_dir or "").strip() and str(preset_name or "").strip():
        text = lambda system, user: generate_text(  # noqa: E731 — 只在这一档需要预设正文
            image_prompt_profiles.system_with_preset(
                system, scene,
                preset_dir=preset_dir, preset_name=preset_name, user_name=user_name,
            ),
            user,
        )
    result = dict(image_prompt_profiles.generate_result(
        str(profile or DEFAULT_PROFILE), scene, text, **(overrides or {}),
    ))
    return {
        **result,
        "name": str(name or "").strip(),
        "profile": str(profile or DEFAULT_PROFILE),
        "appearance": facts["appearance"],
        "gender": facts["gender"],
        "appearance_origin": facts["source"],
        "pose": chosen_pose,
    }
