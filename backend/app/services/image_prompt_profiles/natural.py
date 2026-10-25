"""Natural（自然散文）域：确定性兜底。2026-10-24 审计中期项拆域；函数体逐字节未改。"""
from __future__ import annotations
from collections.abc import Mapping
from app.services.image_prompt_profiles._base import (
    _english_scene_details,
    _environment_phrase,
    _fallback_multi_actor_sentences,
    _multi_actor_count,
    _two_subject_phrase,
)


def _natural_scene_fallback(scene: Mapping[str, object]) -> str:
    multi_sentences = _fallback_multi_actor_sentences(scene)
    if multi_sentences:
        actor_count, count_word = _multi_actor_count(scene)
        environment = _environment_phrase(scene)
        if actor_count == 2:
            # 第 46 刀：旧实现把两人帧写死成 `two adult women`（一男一女帧同样如此）⇒
            # 改按画面主体的**实际性别组成**；性别拿不到时用中性 `two adult characters`。
            opening = (
                "A balanced two-character composition places "
                f"{_two_subject_phrase(scene)} in clearly separated positions against "
                f"{environment}. "
            )
        else:
            opening = (
                f"A balanced {count_word}-character composition places {count_word} adult characters in clearly "
                f"separated positions against {environment}. "
            )
        return (
            opening + " ".join(multi_sentences) + " Their visible relationship, "
            "spacing, gaze directions, and contact point remain the overall visual focus. Directional light, material "
            "response, cast shadows, controlled color hierarchy, and a clean high-fidelity finish preserve one coherent scene."
        )
    details = "; ".join(_english_scene_details(scene))
    facts = details or "the visible adult characters performing the supplied decisive action"
    return (
        f"The image preserves {facts}. The decisive story action is the single visual focus within an "
        "environmental narrative composition. The highest detail remains on the characters' faces, hands, "
        "clothing state, and visible contact point while secondary figures and the background recede in clarity. "
        "A coherent directional light source, consistent material shading, cast shadows, controlled color hierarchy, and "
        "clean high-fidelity finish reinforce the visible character relationship without changing any scene fact."
    )
