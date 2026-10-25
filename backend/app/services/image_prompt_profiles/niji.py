"""Niji 域：分段拼装、确定性兜底、第一人称 guard。2026-10-24 审计中期项拆域；函数体逐字节未改。"""
from __future__ import annotations
from collections.abc import Mapping
from app.services.image_prompt_profiles._base import (
    _english_scene_details,
    _fallback_multi_actor_sentences,
    _first_person_head_guards,
)


def _niji_sections_join(sections: list[str]) -> str:
    """niji 四段拼回：**内容段末尾统一以 `", "` 收尾**（2026-09-21 第 33 刀用户口径）。

    用户原话（复核第 32 刀后真机跑批）：「niji_sections 合格，但是**段与段的末尾建议加上 `", "`**，
    保持良好的习惯」。理由＝段是逗号列表粒度，任何后续拼接／截取都不该缺分隔符。
    ⚠ **参数段（以 `--` 开头的那一段，如 `--ar 4:3 --niji 6`）不加** —— 它是 niji 参数串，
    不是关键词列表；加了会把参数语法写坏。空段保持空（`_errors` 会照旧判「四段」）。
    """
    out: list[str] = []
    for section in sections:
        if not section:
            out.append("")
            continue
        if section.lstrip().startswith("--"):
            out.append(section)
            continue
        out.append(section.rstrip().rstrip(",").rstrip() + ", ")
    return "\n".join(out)


def _first_person_niji_guards(sections: list[str], scene: Mapping[str, object]) -> list[str]:
    """niji 四段的第一人称收口（第 64 刀）：**只动 `subject` 段**，其余段逐字节不动。

    ⚠ `subject` 段本身以 `--` 开头时（畸形输入）同样不动 —— 防写坏参数语法。
    """
    if str(scene.get("viewpoint") or "").strip() != "first_person":
        return sections
    if not sections or sections[0].lstrip().startswith("--"):
        return sections
    return [_first_person_head_guards(sections[0], scene), *sections[1:]]


def _niji_scene_fallback(scene: Mapping[str, object]) -> str:
    multi_sentences = _fallback_multi_actor_sentences(scene)
    details = "; ".join(_english_scene_details(scene))
    subject = " ".join(multi_sentences) if multi_sentences else (
        details or "visible adult characters performing the supplied decisive story action"
    )
    ratio = str(scene.get("aspect_ratio") or "2:3")
    if ratio not in ("1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9"):
        ratio = "2:3"
    # 2026-09-21 第 33 刀（③ 同一口径 · 真机 i34 实测）：**词表兜底**也是 niji 四段之一——
    # 真机 `L3·单人锚点[379]` 掉兜底时，兜底三段末尾没有 `", "`（仅 LLM 路径走 `_niji_sections_join`）
    # ⇒ 同一档「合格稿有、兜底稿没有」自相矛盾。收尾习惯必须一致 ⇒ 兜底也走同一处 join；
    # `--ar` 参数段照旧不动（由 `_niji_sections_join` 内部按 `--` 前缀跳过）。
    return _niji_sections_join([
        subject,
        "refined two-dimensional narrative illustration, cohesive linework, controlled color and material motif",
        "environmental composition, directional light, highest detail on faces hands and contact point, layered soft background",
        f"--ar {ratio} --niji 6",
    ])
