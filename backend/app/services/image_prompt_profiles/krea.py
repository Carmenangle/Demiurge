"""Krea 域：两段式 tags+散文的解析与材质词清洗、合同错误、视觉事实补全、确定性兜底、
第一人称 guard。2026-10-24 审计中期项拆域；函数体逐字节未改。"""
from __future__ import annotations
import re
from collections.abc import Mapping
from app.services.image_prompt_profiles._base import (
    _CJK_RE,
    _REFUSAL_RE,
    _character_names,
    _concrete_scene_facts,
    _environment_phrase,
    _fallback_multi_actor_sentences,
    _first_person_head_guards,
    _missing_visual_facts,
    _multi_actor_contract_errors,
    _multi_actor_count,
    _scene_camera_tag,
    _strip_wrapping,
    _two_subject_phrase,
    _visual_fact_errors,
)


def _krea_prompt(raw: str, scene: Mapping[str, object] | None = None) -> str:
    _ = scene
    text = _strip_wrapping(raw).split("——自检——", 1)[0].strip()
    # 两段式（2026-08-30 用户定稿）：第一段内容/构图 tags、第二段内容文段。
    # 旧行为把所有行合并成单行——那正是格式被破坏的源头。
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]
    if len(lines) >= 2:
        return _normalize_krea_style(lines[0]) + "\n" + _normalize_krea_style(
            " ".join(lines[1:]))
    if lines:
        return _normalize_krea_style(lines[0])
    return ""


def _normalize_krea_style(prompt: str) -> str:
    """清除会锁死真人材质的皮肤微观词，不代替 LoRA 决定画风。"""
    text = prompt
    replacements: tuple[tuple[str, str], ...] = (
        (r"(?:整体)?(?:必须)?(?:采用|呈现|为)?(?:精致)?(?:超写实)?"
         r"(?:真人摄影|真实照片|写实摄影|二次元插画|动漫插画)(?:风格|美学)?[，,]?", ""),
        (r"皮肤呈(?:现)?半透明(?:的)?质感", "肌肤受光自然"),
        (r"隐约透出(?:底层)?微血管", "以细腻明暗表现暖色反光"),
        (r"(?:脸上|面部|皮肤)?毛孔(?:清晰)?(?:可见)?", "面部明暗过渡柔和"),
    )
    replacements += (
        (r"\b(?:photorealistic\s+live-action|live-action\s+photorealistic|"
             r"realistic\s+photography|detailed\s+anime\s+illustration|anime\s+illustration|"
             r"manga\s+illustration|3D\s+render(?:ing)?)\s*(?:imagery|style|aesthetic)?[.,;:]?\s*", ""),
        (r"\bphotorealistic\b[.,;:]?\s*", ""),
        (r"\b(?:live-action photography|anime(?: style)?|manga(?: style)?|3D render(?:ing)?)\b[.,;:]?\s*", ""),
        (r"\brealistic human skin\b", "natural skin tones"),
        (r"\btranslucent skin\b", "natural tonal transitions across the skin"),
        (r"\b(?:microscopic|visible)\s+(?:facial\s+)?(?:veins?|pores?)\b", "subtle tonal detail"),
        (r"\b(?:faint|visible)\s+veins?\s+visible\b", "subtle tonal detail"),
        (r"\bfaint\s+vein\b", "subtle shading"),
        (r"\bskin\s+texture\b", "skin shading"),
        (r"\brealistic\s+(?:softness|skin|detail|rendering)\b", "clean shading"),
        (r"\bhigh\s+image\s+fidelity\b", "clean presentation"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)
    # 两段式：逐行清洗但保留换行（旧行为合并成单行会破坏第一段 tags/第二段文段结构）
    return "\n".join(" ".join(line.split()) for line in text.splitlines())


def _krea_contract_errors(
    prompt: str, scene: Mapping[str, object], *, check_length: bool,
) -> list[str]:
    errors: list[str] = []
    if not prompt.strip():
        return ["提示词为空"]
    if _REFUSAL_RE.search(prompt):
        errors.append("模型返回拒答")
    # 两段式（2026-08-30 用户定稿）：第一段内容/构图 tags，第二段英文内容文段
    lines = [ln.strip() for ln in prompt.splitlines() if ln.strip()]
    if len(lines) != 2:
        return errors + ["Krea2必须输出两段：第一段为内容与构图tags，第二段为英文内容文段"]
    tags_line, prose = lines
    if _CJK_RE.search(tags_line):
        errors.append("第一段tags必须为纯英文")
    tags = [t.strip() for t in tags_line.split(",") if t.strip()]
    if len(tags) < 6:
        errors.append("第一段必须包含至少六个英文内容/构图tags")
    if _CJK_RE.search(prose):
        errors.append("第二段内容文段必须为纯英文，禁止夹杂中文")
    if re.search(r"\([^)]*:\s*\d+(?:\.\d+)?\)", prose):
        errors.append("第二段禁止权重语法（构图权重只能写在第一段tags）")
    if re.search(r"\b(?:masterpiece|best quality)\b", prose, re.I):
        errors.append("质量词只能由程序前置，不得写进文段")
    if check_length:
        words = re.findall(r"\b[A-Za-z][A-Za-z'-]*\b", prose)
        if not 80 <= len(words) <= 500:
            errors.append("英文单词数必须为80到500")
    errors.extend(_multi_actor_contract_errors("krea2", prompt, scene))
    errors.extend(_visual_fact_errors(prompt, scene))
    return errors


def _inline_krea_errors(prompt: str, scene: Mapping[str, object]) -> list[str]:
    return _krea_contract_errors(prompt, scene, check_length=False)


def _complete_inline_krea_facts(prompt: str, scene: Mapping[str, object]) -> str:
    """只补同轮成稿遗漏的可视事实，保留模型已经完成的高潮与艺术决策。"""
    errors = _inline_krea_errors(prompt, scene)
    fact_errors = _visual_fact_errors(prompt, scene)
    missing = _missing_visual_facts(prompt, scene)
    recoverable = bool(missing) and set(errors) == set(fact_errors) and all(
        error.startswith("角色条目中的可视事实未落实：") for error in fact_errors
    )
    if not recoverable:
        return ""
    subject = "The visible adult characters are specifically distinguished by "
    if len(_character_names(scene)) <= 1:
        subject = "The primary adult character is specifically defined by "
    addition = subject + ", ".join(missing) + "."
    first_sentence = re.search(r"[.!?](?:\s|$)", prompt)
    if first_sentence:
        end = first_sentence.end()
        repaired = prompt[:end].rstrip() + " " + addition + " " + prompt[end:].lstrip()
    else:
        repaired = prompt.rstrip(" .") + ". " + addition
    return "" if _inline_krea_errors(repaired, scene) else repaired


def _first_person_krea_guards(prompt: str, scene: Mapping[str, object]) -> str:
    """krea2 的两段式也走第一人称收口（第 64 刀）：**只动首行**，散文行逐字节不动。"""
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return prompt
    lines = prompt.splitlines()
    if not lines:
        return prompt
    return "\n".join([_first_person_head_guards(lines[0], scene), *lines[1:]])


def _krea_scene_fallback(scene: Mapping[str, object]) -> str:
    """连续硬失败时给出两段式纯英文剧情插画描述（第一段 tags、第二段内容文段）。"""
    multi_sentences = _fallback_multi_actor_sentences(scene)
    concrete_facts = _concrete_scene_facts(scene)
    fact_sentence = (
        " The visible subjects and scene are concretely defined by "
        + ", ".join(concrete_facts) + "."
        if concrete_facts and not multi_sentences else ""
    )
    camera = _scene_camera_tag(scene)
    # 第一段：内容/构图 tags（质量 tags 由程序前置）
    draft_tags = [
        tag.strip(" ,.;") for tag in str(scene.get("draft_prompt") or "").split(",")
        if tag.strip(" ,.;") and tag.isascii()  # 第一段 tags 必须纯英文
    ]
    tags_line = ", ".join(list(dict.fromkeys([
        *draft_tags[:10], camera, "dramatic composition", "clear focal hierarchy",
        "coherent perspective", "layered background depth"])))
    actor_count, count_word = _multi_actor_count(scene)
    environment = _environment_phrase(scene, default="a layered background")
    if not multi_sentences:
        subject_opening = (
            f"A {camera} depicts the primary adult woman in one continuous narrative moment, with her visible action "
            "as the decisive action and visual focus."
        )
    elif actor_count == 2:
        # 第 46 刀：两人帧旧实现写死 `two adult women`（一男一女帧同样如此）⇒ 改按画面主体
        # 的**实际性别组成**；性别拿不到 ⇒ 中性 `two adult characters`（与 anima/natural 同口径）。
        subject_opening = (
            "A balanced two-character environmental composition gives "
            f"{_two_subject_phrase(scene)} distinct silhouettes, "
            f"readable positions, and one shared visual focus against {environment}."
        )
    else:
        subject_opening = (
            f"A balanced {count_word}-character environmental composition gives {count_word} adult characters "
            f"distinct silhouettes, readable positions, and one shared visual focus against {environment}."
        )
    character_sentences = (" " + " ".join(multi_sentences)) if multi_sentences else ""
    relationship_sentence = (
        " Their visible actions, spacing, gaze directions, and contact relationship remain readable as one coherent scene."
        if multi_sentences else ""
    )
    prose = (
        subject_opening
        + character_sentences
        + relationship_sentence
        + fact_sentence
        + " The composition makes every stated body position, contact point, current garment, prop, and location "
          "simultaneously readable, using coherent perspective, directional light, consistent material shading, and controlled "
          "background depth. Maintain consistent anatomy, stable contours, clean edges, restrained detail, and even "
          "tonal transitions."
    )
    return tags_line + "\n" + prose
