"""Anima 域：prose 归一/杂 tag 清洗/质量词、schema 字段散文、合同与场景事实错误、
内容描述、确定性兜底、保内容与两段拯救。2026-10-24 审计中期项拆域；函数体逐字节未改
（三个反向依赖调度器的函数带函数级延迟导入 `_render`，防顶层环）。"""
from __future__ import annotations
import json
import re
from collections.abc import Mapping
from app.services import image_prompt_extract
from app.services.image_prompt_profile_templates import (
    ANIMA_QUALITY_TAGS,
)
from app.services.image_prompt_profiles._base import (
    _ANIMA_ACTION_RE,
    _REFUSAL_RE,
    _concrete_scene_facts,
    _contract_subject_genders,
    _fallback_multi_actor_sentences,
    _is_action_fact,
    _json_object,
    _multi_actor_count,
    _normalize_aspect_ratio,
    _scene_action_tags,
    _scene_camera_tag,
    _split_length_warnings,
    _strip_field_quotes,
    _strip_wrapping,
    _subject_count_words,
    _tag_already_actions_part,
)


_ANIMA_PROSE_LABELS: tuple[tuple[str, str], ...] = (
    (r"her body|body", "Her body is shown with "),
    (r"bound|restraints?", "She is bound with "),
    (r"position|pose", "She is positioned "),
    (r"appearance", "She appears with "),
    (r"clothing|wardrobe|costume", "She wears "),
    (r"action", "She is shown "),
    (r"expression", "Her expression shows "),
    (r"setting|location|background", "The scene takes place in "),
    (r"camera|composition", "The composition uses "),
    (r"lighting", "The lighting uses "),
)


# 只服务视觉命题的通用艺术词：是**独立 tag** 时才剔除（见 `_drop_generic_anima_tags`）。
_ANIMA_GENERIC_ART_TAGS = frozenset({
    "dramatic scene", "climactic moment", "dynamic composition", "action pose",
    "cinematic lighting",
})


def _anima_naturalize_prose(value: str) -> str:
    """把模型偶发的 `Label: fragment` 改成连续英文画面句。"""
    text = " ".join((value or "").splitlines()).strip()
    for label, replacement in _ANIMA_PROSE_LABELS:
        text = re.sub(
            rf"(^|[.!?]\s+)(?:{label})\s*:\s*",
            rf"\1{replacement}",
            text,
            flags=re.I,
        )
    text = _normalize_aspect_ratio(text)
    text = re.sub(r"\s*:\s*", ", ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _anima_clean_tag_head(value: str) -> str:
    """Anima 首行只能是逗号序列，不能携带标题式冒号。"""
    text = re.sub(r"\s*;\s*", ", ", value or "")
    labels = "|".join(label for label, _replacement in _ANIMA_PROSE_LABELS)
    text = re.sub(rf"\b(?:{labels})\s*:\s*", "", text, flags=re.I)
    text = _normalize_aspect_ratio(text)
    return re.sub(r"\s*:\s*", ", ", text)


def _drop_generic_anima_tags(tag_head: str) -> str:
    """按 **tag 粒度**剔除通用艺术词，保留原有逗号分隔。

    2026-09-20 真机实锤（L2/L3·anima 成稿）：原实现用一条正则
    `(?:^|,\\s*)(...)(?=,|\\.|$)\\s*,?\\s*` 就地删除，**前后两个分隔符一起被吃掉** ⇒
    `volumetric steam, cinematic lighting, depth of field` 粘成 `volumetric steamdepth of field`。
    改成先 split 再 join，分隔符由 join 重建，结构上不可能粘连；判定与旧正则等价
    （整 tag 命中才删，`dynamic action pose` 这类只含子串的 tag 不动）。
    """
    kept = [
        tag.strip()
        for tag in (tag_head or "").split(",")
        if tag.strip() and not re.fullmatch(r"[*_#`~\s]+", tag.strip())
        and tag.strip().rstrip(".").strip().lower() not in _ANIMA_GENERIC_ART_TAGS
    ]
    return ", ".join(kept)


def anima_quality_tags(rating: str = "sfw") -> str:
    if rating == "nsfw":
        return ANIMA_QUALITY_TAGS.replace(
            "score_9,", "score_9, sensitive, explicit,", 1,
        )
    return ANIMA_QUALITY_TAGS


_ANIMA_SCHEMA_FIELDS = ("visual_hook", "primary_focus", "supporting_elements", "content")


def _extra_field_prose(value: Mapping[str, object]) -> str:
    """`content` 缺画面句时，从对象里的**非规格**字符串字段取回（唯一调用点：`_normalize` JSON 分支）。

    真机 2026-09-19 实锤（`probe_prompt_delivery.py`）：模型把 tags 写进规格字段 `content`，
    却把画面句写进**非规格字段** `content_note`（`content` 里既无换行也无句点）⇒
    `partition(".")` 的 `prose` 为空 ⇒ 输出退化 1 行 ⇒ 三连判死（白烧一次 repair 调用）。
    口径：**只看非规格字段**；候选必须同时满足「含逗号」+「≥100 字符」+「含完整英文句」
    （与 `_errors` 的 `^[A-Za-z]…[.?!]` 同源）——真实画面句远超该阈值，而
    「Here is the JSON you requested.」这类附带说明既无逗号也过短 ⇒ 不入选，避免把说明当画面句；
    多个候选取最长。拒答文本即使被选入，也会被 `_errors` 的 `_REFUSAL_RE` 兜住。
    ⚠ 只在 `prose` 本已为空时被调用 —— 那个状态**必然**产出死稿，故单向放宽、零回归。
    """
    candidates: list[str] = []
    for key, raw_value in value.items():
        if key in _ANIMA_SCHEMA_FIELDS or not isinstance(raw_value, str):
            continue
        text = " ".join(raw_value.split()).strip()
        if len(text) < 100 or "," not in text:
            continue
        if re.search(r"^[A-Za-z][^.?!]{24,}[.?!](?:\s|$)", text):
            candidates.append(text)
    return max(candidates, key=len) if candidates else ""


_ANIMA_FIELD_LABEL_RE = re.compile(
    r"^\s*(visual_hook|primary_focus|supporting_elements|content)\s*[:：]\s*(.*)$",
    re.I,
)


def _labelled_fields_object(raw: str) -> dict[str, object]:
    """**字段式回复** → 等价对象（模型用 `字段: 值` 写全四个规格字段，只是没加花括号）。

    真机实锤（2026-09-21 第 33 刀 · `D:\\pytest-tmp\\i33\\tp` 的 `L3·NSFW多人` repair 轮）：
    模型写的是
        visual_hook: …\\n\\nprimary_focus: …\\n\\nsupporting_elements: [...]\\n\\ncontent: "…"
    ——四个字段**全在、值全非空**，只是外层没有 `{}`。这种文本 `_json_object` 必然返回 `{}`
    ⇒ 掉进「非 JSON」分支 ⇒ 那一支把 `", ".join((lines[0], body))` 当 tags ⇒ **整段原文进首行**
    （该格首行 2702 字），而结构判据只看「两行 + 首行 ≥6 个 tag」⇒ **一条格式红都不报** ⇒
    `_patch_failed_prompt` 照旧放行 ⇒ **交付 4833 字的半 JSON 残留**。

    ⚠ **准入刻意很窄**（不是 `_json_object` 的放宽容错，而是另一种**合法序列化形态**的识别）：
    ① 必须命中全部四个规格字段；② 每个值必须非空；③ 只认行首的 `字段:` 标签（续行并入前一字段）。
    任一条不满足 ⇒ 返回 `{}` ⇒ 与旧行为**逐字节相同**（fail-closed 不变）。
    """
    fields: dict[str, str] = {}
    current = ""
    for line in _strip_wrapping(raw).splitlines():
        match = _ANIMA_FIELD_LABEL_RE.match(line)
        if match:
            current = match.group(1).lower()
            fields[current] = match.group(2).strip()
            continue
        if current and line.strip():
            fields[current] = f"{fields[current]} {line.strip()}".strip()
    if not all(name in fields and fields[name] for name in _ANIMA_SCHEMA_FIELDS):
        return {}
    cleaned: dict[str, object] = {
        name: _strip_field_quotes(value) for name, value in fields.items()
    }
    # `supporting_elements` 在 `{}` 形态里是 **JSON 数组**，字段式回复只是把同一段文本照抄过来
    # （带外层中括号）⇒ 这里解回 list，输出才能与 `_json_object` **结构等价**：
    # `_anima_contract_errors` 要用 `isinstance(supporting, list)` 判它（否则白白多出一条契约红）。
    supporting = str(cleaned.get("supporting_elements") or "")
    if supporting.startswith("["):
        try:
            parsed = json.loads(supporting)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        if not isinstance(parsed, list):
            return {}
        cleaned["supporting_elements"] = parsed
    return cleaned


# anima `visual_hook` 的「必须命中」清单（第 22 刀，2026-09-20 生产链实锤 ★1）。
#
# ⚠ 这张表**决定模型的动机构成**：凡在这张表里出现的词，模型为了过校验就会去写它。
# 原表把两类词混列——**处理方式词**（构图/光学处理，只用已有元素即可写）
# 与**需要新反光面/材质的词**（`mirror` / `reflection` / `refraction` / `translucent`）。
# 后者的「合规解」在窗口里没有镜子时**只能是编一面镜子**：L1 窗口 44 字、原文没有任何
# 镜面，成稿仍写出 tag `wall mirror reflection` ＋ 文段 `a wall mirror catches a blurred
# reflection of her face`——模型不是「坏了」，是在**按必须命中的清单交作业**。
# ⇒ 本表只保留「不引入新物件即可满足」的构图与处理方式词；具体道具/材质词一律移出。
# 与修法 ②（`_unsourced_visual_device_errors` 出处闸）配套：一个消除动机，一个机械兜底。
#
# ⚠ 这一列只判「hook 有没有写到**怎么成像**」，不当词表考试（2026-09-20 真机实测两处误报）：
#   ① L3·anima 首轮写 `mirroring each other's posture … in a symmetrical V-shaped display`
#      ——语义完全合格，却因表里只有 `symmetry`（无 `symmetrical`）被判「没写装置」；
#   ② 收窄成「构图名词小表」后，L1/L2·anima 两格仍各白跑一次 repair（hook 措辞没落进小表）。
#   ⇒ 判据改成**词干 + `\w*` + 宽面**（光/镜头/构图/焦点/角度/色调任意一个即算），
#      只保留下限：必须谈「怎么拍」，不能只罗列「画面里有什么」（那由 `_ANIMA_VAGUE_HOOKS` 挡）。
#   道具词不在此列——编装置由出处闸（`_unsourced_visual_device_errors`）管。
_ANIMA_VISUAL_DEVICE_RE = re.compile(
    r"\b(?:fram\w*|occlu\w*|silhouette\w*|negative space|space|foreground\w*|background\w*|"
    r"depth|focus\w*|repet\w*|rhym\w*|contrast\w*|scale|proportion\w*|perspectiv\w*|"
    r"diagonal\w*|symmetr\w*|asymmetr\w*|shadow\w*|highlight\w*|light\w*|backlit|backlight\w*|"
    r"layer\w*|line of sight|leading line|gaze line|contact point|motion trail|"
    r"angle\w*|shot|composition\w*|palette\w*|tone\w*)\b",
    re.I,
)


_ANIMA_VAGUE_HOOKS = {
    "dramatic scene", "dramatic departure scene", "beautiful scene", "cinematic scene",
    "emotional scene", "climax scene", "character portrait", "group scene",
}


def _anima_contract_errors(raw: str) -> list[str]:
    # 第 33 刀（2026-09-21）：与 `_normalize` 同口径——`{}` 之外再认**字段式**（`字段: 值`）这一种
    # 合法序列化变体，否则 repair 轮明明按规格写全四字段、只是没加花括号，仍被判「必须输出包含艺术
    # 决策字段的JSON对象」而在结构层面白红（真机 `L3·NSFW多人` repair 轮实锤）。
    value = _json_object(raw) or _labelled_fields_object(raw)
    if not value:
        return ["必须输出包含艺术决策字段的JSON对象"]
    errors: list[str] = []
    hook = str(value.get("visual_hook") or "").strip()
    focus = str(value.get("primary_focus") or "").strip()
    supporting = value.get("supporting_elements")
    content = str(value.get("content") or "").strip()
    if (len(hook) < 18 or hook.lower() in _ANIMA_VAGUE_HOOKS
            or not _ANIMA_VISUAL_DEVICE_RE.search(hook)):
        errors.append("visual_hook必须写出具体可见的光学或构图装置")
    if (not focus or len(focus) > 300
            or re.search(r"[.;]\s*[A-Z]", focus)):
        # A2（2026-09-04 用户定案）：NSFW 高潮场景复合焦点是常态，不再按 160 字
        # 一刀切判死。primary_focus 只是模型中间思考字段，不直接进最终两行提示词；
        # content 合格时 focus 措辞过宽由 generate 分级保留（kept_content），仅对
        # 空 / 失控超长 / 分号句点罗列多个中心判错引导重写。
        errors.append("primary_focus必须且只能描述一个第一视觉中心")
    if not isinstance(supporting, list) or len(supporting) > 2:
        errors.append("supporting_elements辅助元素最多两个")
    elif any(not isinstance(item, str) or not item.strip() for item in supporting):
        errors.append("supporting_elements必须是非空英文字符串")
    if not content:
        errors.append("content不能为空")
    return errors


def _anima_scene_fact_errors(prompt: str, scene: Mapping[str, object]) -> list[str]:
    """只校验剧情硬事实，不约束镜头、光影、色材或视觉装置。"""
    encounter = scene.get("encounter")
    encounter_values = encounter.values() if isinstance(encounter, Mapping) else ()
    source = "\n".join((
        str(scene.get("narrative") or ""),
        str(scene.get("appearance") or ""),
        str(scene.get("wardrobe") or ""),
        str(scene.get("locale") or ""),
        " ".join(str(value) for value in encounter_values),
    ))
    content = prompt.lower()
    requirements = (
        (
            r"(?:搬|卸).{0,16}(?:木箱|药箱)|(?:木箱|药箱).{0,16}(?:搬|卸)",
            r"(?:unload|carry|lift|move)\w*.{0,80}(?:crate|box)|"
            r"(?:crate|box).{0,80}(?:unload|carry|lift|move)\w*",
            "正文明确搬运药材木箱，content必须保留搬箱动作",
        ),
        (r"药材|黄芪|艾草|干姜", r"medicinal herbs?|herbal medicine|astragalus|mugwort|ginger",
         "正文明确出现药材，content必须保留药材"),
        (r"宽肩", r"broad shoulders?", "人物宽肩特征不得丢失"),
        (r"方脸", r"square face", "人物方脸特征不得丢失"),
        (r"浓眉", r"thick eyebrows?", "人物浓眉特征不得丢失"),
        (r"虎牙", r"(?:prominent|visible|small) canine tooth|fang", "人物虎牙特征不得丢失"),
        (r"厚茧|老茧", r"calloused hands?", "人物掌心厚茧特征不得丢失"),
        (r"褐色短褂", r"brown .{0,20}(?:jacket|workwear|shirt)", "人物褐色短褂不得丢失"),
        (r"袖口挽|挽起袖|挽到肘", r"rolled(?:-up)? sleeves?", "人物挽袖状态不得丢失"),
        (r"孤儿院.{0,8}门|门.{0,8}孤儿院", r"orphanage (?:entrance|gate|doorway)",
         "孤儿院门口地点不得丢失"),
        (r"骡子.{0,12}(?:车|平板车)|骡车", r"mule(?:-drawn)? cart|mule cart",
         "骡车环境事实不得丢失"),
    )
    errors = [message for source_pattern, prompt_pattern, message in requirements
              if re.search(source_pattern, source) and not re.search(prompt_pattern, content)]
    has_matron_interaction = bool(
        re.search(r"院长", source) and re.search(r"招呼|查看|接受|迟疑|不安|惶然|伸手", source)
    )
    if has_matron_interaction:
        if not re.search(r"\b(?:matron|orphanage director|caretaker|headmistress)\b", content):
            errors.append("正文存在院长互动，content必须保留院长作为次级人物")
        if re.search(r"\b(?:solo|1girl)\b", content):
            errors.append("正文存在院长互动，禁止使用solo或1girl把关系场景退化为单人肖像")
    adult_occupation = bool(re.search(r"药材商贩|药材贩子|女商贩|女商人", source))
    if adult_occupation and not re.search(r"\b(?:adult woman|woman|2women)\b", content):
        errors.append("成年商贩身份不得退化为未标明成年身份的少女")
    unsupported_mule_action = re.search(
        r"\b(?:slap|slapping|pat|patting|hit|hitting|strike|striking)\b.{0,40}\b(?:mule|horse)\b",
        content,
    )
    source_mule_action = re.search(r"(?:拍|打|抚摸|抽|击).{0,12}(?:骡|马)", source)
    if unsupported_mule_action and not source_mule_action:
        errors.append("正文没有拍打骡子的动作，禁止凭空添加该动作")
    return errors


def _anima_content_description(
    scene: Mapping[str, object], facts: list[str], *, include_actions: bool = True,
) -> str:
    """把同一批具体 tags 复述为画面文段，不输出给下游的元指令。"""
    concrete = list(dict.fromkeys(fact.strip(" ,.;") for fact in facts if fact.strip(" ,.;")))
    actions = _scene_action_tags(scene) if include_actions else []
    # 主语同样按画面主体数选：`_character_names` 会把在场角色全算进来，
    # 单人定格帧会被写成 `The two adult characters is …`（2026-09-19 实锤）。
    # ⚠ 复数主语配 `is` 是语法破损（2026-09-20 真机 L1/L3 成稿实锤）⇒ 动词跟着数走。
    # 2026-09-20 第 24 刀：三人以上不得再写 `The two adult characters`（0 人措辞也按同一函数）。
    actor_count, count_word = _multi_actor_count(scene)
    plural = actor_count >= 2
    subject = "The primary adult woman" if not plural else (
        "The two adult characters" if actor_count == 2 else f"The {count_word} adult characters"
    )
    sentences: list[str] = []
    # 2026-09-20 第 27 刀（用户实锤病句）：`depicted with` 后面只能接**名词短语 / 独立主格**
    # （`both palms pressed against her own buttocks` 是独立主格，合法），接动名词会产出
    # `The three adult characters are depicted with withdrawing the fingers.` ⇒ 动名词事实改走
    # **动作槽**（与动作表 tag 合并去重），其余留在 `depicted with` 槽。
    states = [fact for fact in concrete if not _is_action_fact(fact)]
    # ⚠ 2026-09-20 第 28 刀（探针实锤）：动作事实也必须受 `include_actions` 管——旧实现只清空了
    # `_scene_action_tags(scene)`，从 `facts` 参数传进来的**动作事实会绕过开关** ⇒
    # `_anima_content_description(s, ['withdrawing the fingers'], include_actions=False)`
    # 仍输出 `In this instant, the concrete action is withdrawing the fingers.`。
    # 第 31 刀：判据由 `_GERUND_FACT_RE` 换成 `_is_action_fact`（并入「施动部位先行」的形状，
    # 使 `both hands spreading her buttocks apart` 仍归动作槽、不进 `depicted with` 名词槽）。
    if include_actions:
        action_facts = [fact for fact in concrete if _is_action_fact(fact)]
        actions = list(dict.fromkeys([*actions, *action_facts]))
    if states:
        sentences.append(
            f"{subject} {'are' if plural else 'is'} depicted with {', '.join(states)}."
        )
    if actions:
        sentences.append(f"In this instant, the concrete action is {', '.join(actions)}.")
    return " ".join(sentences) or f"{subject} occupies the depicted scene."


def _safe_anima_draft_tags(value: object) -> list[str]:
    """只接纳短英文内容草稿；拒绝艺术分析、权重主体和完整规划串。"""
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    candidates = [
        line for line in lines
        if not re.search(r"\b(?:masterpiece|best quality|score_\d+)\b", line, re.I)
    ]
    content = candidates[-1] if candidates else ""
    if (
        not content or not content.isascii() or _REFUSAL_RE.search(content)
        or ";" in content
        or re.search(r"\b(?:visual thesis|primary|secondary|tertiary|hierarchy)\s*:", content, re.I)
    ):
        return []
    content = re.sub(r"\([^()]*:\s*\d+(?:\.\d+)?\)", "", content)
    tags = [tag.strip(" ,.;") for tag in content.split(",") if tag.strip(" ,.;")]
    return list(dict.fromkeys(
        tag for tag in tags
        if len(tag) <= 120 and not re.search(r"\b(?:masterpiece|best quality|score_\d+)\b", tag, re.I)
    ))


def _anima_scene_fallback(scene: Mapping[str, object]) -> str:
    """独立 Profile 拒答时复用主剧情链已生成的英文高潮内容。"""
    from app.services.image_prompt_profiles._render import _normalize  # 延迟导入：_render 顶层 import 本模块，函数内取用防环
    encounter = scene.get("encounter")
    encounter_values = encounter.values() if isinstance(encounter, Mapping) else ()
    source = "\n".join((
        str(scene.get("narrative") or ""),
        str(scene.get("appearance") or ""),
        str(scene.get("wardrobe") or ""),
        str(scene.get("locale") or ""),
        " ".join(str(value) for value in encounter_values),
    ))
    fact_rules = (
        (r"女人|女性|妇人|女子|她|三十出头", "adult woman"),
        (r"三十出头", "early thirties"),
        (r"药材商贩|药材贩子|药商", "herb merchant"),
        (r"宽肩", "broad shoulders"),
        (r"厚背|结实|壮实", "sturdy build"),
        (r"方脸", "square face"),
        (r"浓眉", "thick eyebrows"),
        (r"虎牙", "prominent canine tooth"),
        (r"厚茧|老茧", "calloused hands"),
        (r"结实.{0,4}小臂", "strong forearms"),
        (r"褐色短褂", "brown work jacket"),
        (r"袖口挽|挽到肘", "rolled sleeves"),
        (r"靴底.{0,8}泥|泥.{0,8}靴", "muddy boots"),
        (r"风尘仆仆", "travel-worn"),
        (r"爽朗|热络", "open cheerful expression"),
        (r"骡子.{0,8}平板车|骡车", "mule-drawn cart"),
        (r"药材|黄芪|艾草|干姜", "bundled medicinal herbs"),
        (r"孤儿院.{0,6}门|门.{0,6}孤儿院", "orphanage entrance"),
        (r"院长.{0,12}(?:不安|迟疑|惶然|恐惧)|(?:不安|迟疑|惶然).{0,12}院长",
         "anxious matron"),
        (r"(?:搬|卸).{0,12}木箱|木箱.{0,12}(?:搬|卸)|搬下|搬到|卸下|卸货",
         "unloading a wooden medicine crate"),
        (r"跳下", "jumping down from the cart"),
        (r"尘土|扬尘", "dust in the air"),
    )
    facts = list(dict.fromkeys([
        *(tag for pattern, tag in fact_rules if re.search(pattern, source)),
        *_safe_anima_draft_tags(scene.get("draft_prompt")),
        *_concrete_scene_facts(scene, include_draft_prompt=False),
    ]))
    # 具体角色/动作/场景 tags 必须先于抽象镜头词；有具体外貌时去掉无信息量的 adult woman。
    direct_appearance = " ".join(str(scene.get("appearance") or "").split()).strip(" ,.;")
    has_specific_english_appearance = bool(
        direct_appearance and direct_appearance.isascii()
        and re.search(r"[A-Za-z]{3}", direct_appearance)
    )
    concrete_role_facts = [
        fact for fact in facts
        if (not has_specific_english_appearance or fact.lower() != "adult woman")
        and fact.lower() not in {"dramatic scene", "dramatic composition", "climactic moment",
                                "dynamic composition", "action pose", "cinematic lighting"}
    ]
    if concrete_role_facts:
        facts = concrete_role_facts
    if has_specific_english_appearance:
        facts = [direct_appearance, *[fact for fact in facts if fact != direct_appearance]]
    base = facts or ["visible decisive action", "environmental narrative composition"]
    content_tags = list(dict.fromkeys([
        *base, _scene_camera_tag(scene), "three-quarter view", "environmental composition",
        "directional light", "layered background", "detailed anime illustration",
    ]))
    content = ", ".join(content_tags)
    # 第 25 刀（发现即修）：追加动作 tag 前必须**判重**。第 24 刀新增的体位词（spreading / swaying /
    # both palms… / exposing…）不在 `_ANIMA_ACTION_RE` 里，于是 `content` 明明已含这些 tag
    # （由 `_concrete_scene_facts` 带入），却仍被追加一次 ⇒ 首行尾部出现重复串（真机 demo 实锤）。
    # 第 31 刀：**同部位闸接入兜底路径** —— `draft_prompt` 已写出的同部位动作
    # （如 `hands gripping and pulling apart their own buttocks`）不得再被规则表推出的
    # 第二条同义串（`both hands spreading her buttocks apart`）叠成两条。判据是**逐 tag
    # 内**「部位＋动词」共现（`_tag_already_actions_part`），不是整串共现，避免误吞体位词。
    action_tags = [
        tag for tag in _scene_action_tags(scene)
        if tag and tag not in content and not _tag_already_actions_part(content, tag)
    ]
    if action_tags and not _ANIMA_ACTION_RE.search(content):
        head, dot, tail = content.partition(".")
        content = f"{head.rstrip(',. ')}, {', '.join(action_tags)}{dot}{tail}"
    multi_sentences = _fallback_multi_actor_sentences(scene)
    if multi_sentences:
        # 2026-09-01 用户实锤：多角色分支把上面已组装的具体动作/事实 tags 整段
        # 覆盖成 2girls 通用模板，高潮动作全丢。改为：首行保留具体 tags（含动作），
        # 多角色分工句只作为正文补充。
        # 2026-09-20 第 24 刀：人数措辞改按**画面主体数**（三主体帧仍写 `2girls, two-shot` /
        # `The two adult characters' …` 是双口径残留）。2 人帧逐字节不变。
        # 第 46 刀（2026-09-22 用户定案）：人数词**不再硬写 `2girls`**，改按
        # `scene["subject_genders"]` 里**画面主体的实际性别组成**写（`2girls` / `2girls, 1boy` /
        # `1girl, 2boys`）。用户原话：「第三人称时按剧情的画面人物判断，男主主人公＋2 女主角
        # 就是 `1boy, 2girls`……**根据剧情判断在场的内容，不是锁死女角色**」
        # ＋「男主＋1 女兜底写 `2girls` 很明显有问题，正常应该是 `1boy, 1girl`」。
        # ⚠ 任一体性别解析不出 ⇒ 只留中性数词（`two-shot`），**不写具体性别族**（用户定案「不写」）。
        # ⚠ 全女二人帧仍得到 `2girls, two-shot, …` ⇒ 与旧措辞逐字节一致（零回归）。
        _actor_count, count_word = _multi_actor_count(scene)
        count_words = _subject_count_words(_contract_subject_genders(scene))
        gender_prefix = f"{count_words}, " if count_words else ""
        count_tags = (
            f"{gender_prefix}{count_word}-shot, balanced composition, clear spatial separation, "
        )
        count_tail = (
            f" The {count_word} adult characters' distinct appearances and actions form one readable relationship, "
        )
        content = (
            count_tags
            + content
            + ". "
            + " ".join(multi_sentences)
            + count_tail
            + "while the background, light direction, consistent material shading, and depth remain coherent."
        )
    if not re.search(r"(?:^|\.\s+)[A-Z][^.?!]{24,}[.?!](?:\s|$)", content):
        if "herb merchant" in facts and "unloading a wooden medicine crate" in facts:
            description = (
                "A sturdy adult herb merchant unloads a wooden medicine crate at the orphanage "
                "entrance, with her calloused hands, square face, and prominent canine tooth receiving "
                "the sharpest detail. Late-afternoon light catches road dust, rough wood, and bundled "
                "herbs while the anxious matron and mule cart recede into softer layers."
            )
        else:
            description = _anima_content_description(scene, content_tags)
        content = f"{content.rstrip(',. ')}. {description}"
    return _normalize("anima_tags", content, scene)


def _anima_keep_content(raw: str, fact_scene: Mapping[str, object]) -> str:
    """A1（2026-09-04 用户定案）：JSON 中间契约字段（visual_hook/primary_focus/
    supporting_elements）措辞违例，只说明模型没按中间协议组织艺术决策，不代表
    content（最终两行提示词的真源）不合格。NSFW 高潮场景复合焦点是常态，此前
    repair 后仍违例会把整稿作废 → deterministic 词表通用动作兜底：两次画像 LLM
    调用白跑、高潮动作失真。⚠ 2026-09-19 归因更正：事故串 `removing the visible object` 的**来源**
    是旧粗规则 `取出|拔出|抽出|移除 → removing the visible object`（第 7 项已按词义拆开），
    **不是** A1 造成的——A1 本身正是当初为「不再退词表」而立的；沿用旧串字样只为对上既有文档口径。
    这里剥离中间字段，仅用 content 重建两行；重建结果通过正文/事实校验才返回，
    否则空串由调用方继续词表兜底。"""
    from app.services.image_prompt_profiles._render import _errors, _normalize  # 延迟导入：_render 顶层 import 本模块，函数内取用防环
    if not raw or not str(raw).strip():
        return ""
    content_prompt = str(_normalize("anima_tags", raw, fact_scene)).strip()
    if not content_prompt:
        return ""
    errors = [
        *_errors("anima_tags", content_prompt, fact_scene),
        *_anima_scene_fact_errors(content_prompt, fact_scene),
    ]
    if errors:
        return ""
    return content_prompt


def _anima_two_line_rescue(raw: str, fact_scene: Mapping[str, object]) -> str:
    """第 58 刀（2026-09-24 用户口径）：两行格式合同判死时的**本地归一**。

    真机实锤：25 次编译里 3 次死于「第二行必须包含英文自然语言画面描述」——模型稿
    1400–1900 字内容合格（非空/非拒答/无 CJK）、只是没排成「第一行 tags ＋ 第二行
    英文画面描述」的两行结构；repair 重写仍不合格；而确定性兜底只有 662–794 字
    （`adult woman` 开头、**无角色身份**）。
    ⇒ 内容/英文/分级/禁词全过、只剩**格式**时，本地重排成两行，不整稿作废。
    组装不出来（tags < 6 或没有合格英文句）⇒ 返回 ""（调用方照旧走词表兜底，不劣化）。
    """
    from app.services.image_prompt_profiles._render import _errors  # 延迟导入：_render 顶层 import 本模块，函数内取用防环
    text = (raw or "").strip()
    if not text:
        return ""
    text = re.sub(r"```[a-zA-Z]*", "", text)
    restored = image_prompt_extract.restore_jailbreak(text)
    # ① 英文画面描述行：取最长的一条「字母开头、≥25 字符、句读收尾」的句子
    #    （正则与合同同款 ⇒ 提出来的句子必然过「第二行」那关；⚠ 必须排除换行，
    #    否则 tags 行（无句读）会被连同下一句一起吞成一个「跨行 prose」）
    prose = ""
    for match in re.finditer(r"[A-Za-z][^.?!\n]{24,}[.?!]", restored):
        cand = match.group(0).strip()
        if len(cand) > len(prose):
            prose = cand
    if not prose:
        return ""
    # ② tags 行：prose 之外的剩余文本里的 ASCII 逗号段（丢中英文混杂段/代码围栏）。
    #    ⚠ prose 是从 `restored`（restore_jailbreak 还原后）提取的，residue 也必须用
    #    `restored` 去 replace，否则含破甲标记时匹配不到 ⇒ prose 残留进 tags（2026-09-24 审计）。
    residue = restored.replace(prose, " ")
    tags: list[str] = []
    for seg in re.split(r"[\n;。！？]", residue):
        seg = seg.strip()
        if not seg or not seg.isascii():
            continue
        for tag in seg.split(","):
            tag = tag.strip().strip("-. ")
            if tag and tag.isascii() and len(tag) <= 40:
                tags.append(tag)
    tags = list(dict.fromkeys(tags))[:40]
    if len(tags) < 6:
        return ""
    rebuilt = f"{', '.join(tags)}\n{prose}"
    errors, _warnings = _split_length_warnings(_errors("anima_tags", rebuilt, fact_scene))
    if errors:
        return ""
    return rebuilt
