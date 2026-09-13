from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.agent_contracts import ModelConfig, RunContext


def from_payload(payload: Mapping[str, Any]) -> RunContext:
    """Build the canonical runtime context for both live and queued turns."""
    thread_id = str(payload.get("thread_id") or "home")
    card_name = str(payload.get("card_name") or "").strip()
    card_names = [
        str(name).strip()
        for name in (payload.get("card_names") or [])
        if str(name).strip()
    ]
    bound_cards = list(dict.fromkeys(card_names + ([card_name] if card_name else [])))
    opening_card = str(payload.get("opening_card_name") or "").strip()
    opening_card = opening_card or card_name or (bound_cards[0] if bound_cards else "")
    mask = payload.get("image_mask")

    return RunContext(
        thread_id=thread_id,
        message=str(payload.get("message") or ""),
        images=list(payload.get("images") or []),
        image_mask=dict(mask) if isinstance(mask, Mapping) else None,
        chat=ModelConfig(
            str(payload.get("base_url") or ""),
            str(payload.get("api_key") or ""),
            str(payload.get("model") or ""),
        ),
        generation=ModelConfig(
            str(payload.get("gen_base_url") or ""),
            str(payload.get("gen_api_key") or ""),
            str(payload.get("gen_model") or ""),
        ),
        video=ModelConfig(
            str(payload.get("video_base_url") or ""),
            str(payload.get("video_api_key") or ""),
            str(payload.get("video_model") or ""),
        ),
        embedding=ModelConfig(
            str(payload.get("embed_base_url") or ""),
            str(payload.get("embed_api_key") or ""),
            str(payload.get("embed_model") or "embedding-3"),
        ),
        size=str(payload.get("size") or "1024x1024"),
        image_quality=str(payload.get("image_quality") or "high"),
        output_dir=str(payload.get("output_dir") or ""),
        repo_id=str(payload.get("repo_id") or thread_id),
        message_id=str(payload.get("message_id") or ""),
        proxy_url=str(payload.get("proxy_url") or ""),
        chat_proxy_url=str(payload.get("chat_proxy_url") or ""),
        gen_proxy_url=str(payload.get("gen_proxy_url") or ""),
        video_proxy_url=str(payload.get("video_proxy_url") or ""),
        embed_proxy_url=str(payload.get("embed_proxy_url") or ""),
        search_provider=str(payload.get("search_provider") or ""),
        route_model=str(payload.get("route_model") or ""),
        provider_profile=str(payload.get("provider_profile") or "openai_compatible"),
        style_template=str(payload.get("style_template") or ""),
        agent_id=str(payload.get("agent_id") or ""),
        stream_output=bool(payload.get("stream_output", False)),
        approval_id=str(payload.get("approval_id") or ""),
        approval_action=str(payload.get("approval_action") or ""),
        edited_prompt=str(payload.get("edited_prompt") or ""),
        forced_route=str(payload.get("forced_route") or ""),
        user_message_id=str(payload.get("user_message_id") or ""),
        workspace_mode=str(payload.get("workspace_mode") or "story"),
        context_max_tokens=int(payload.get("context_max_tokens", 20_000)),
        context_count=int(payload.get("context_count", 6)),
        compact_trigger_pct=max(0, min(100, int(payload.get("compact_trigger_pct", 80)))),
        selfheal_attempts=max(0, min(5, int(payload.get("selfheal_attempts", 3)))),
        # 2026-09-04 设置整合：智能体参数预设（采样 + 输入/输出上限）从 settings 注入，
        # 后端不再从 preset 顶层取这些字段（preset_store.sampling_params 已废弃）。
        # 字段缺省由 `_roleplay_sampling` / chat 透传层用 builtin default 兜底。
        preset_sampling=payload.get("preset_sampling") or {},
        history_override=payload.get("history") if "history" in payload else None,
        story_turn=max(0, int(payload.get("story_turn") or 0)),
        character_dir=str(payload.get("character_dir") or ""),
        card_name=opening_card,
        card_names=bound_cards,
        opening_card_name=opening_card,
        preset_dir=str(payload.get("preset_dir") or ""),
        preset_name=str(payload.get("preset_name") or ""),
        user_name=str(payload.get("user_name") or ""),
        user_persona=str(payload.get("user_persona") or ""),
        persona_bound=bool(payload.get("persona_bound", False)),
        worldbook_dir=str(payload.get("worldbook_dir") or ""),
        worldbook_name=str(payload.get("worldbook_name") or ""),
        illustrate=bool(payload.get("illustrate", False)),
        comfy_illustrate=bool(payload.get("comfy_illustrate", False)),
        comfy_audio=bool(payload.get("comfy_audio", False)),
        comfy_video=bool(payload.get("comfy_video", False)),
        video_mode=str(payload.get("video_mode") or ""),
        prompt_profile=str(payload.get("prompt_profile") or "anima_tags"),
        appearance_source=str(payload.get("appearance_source") or "worldbook"),
        character_base_images=dict(payload.get("character_base_images") or {}),
        illustration_actor_names=list(payload.get("illustration_actor_names") or []),
        style_base_image=str(payload.get("style_base_image") or ""),
        attachments=list(payload.get("attachments") or []),
    )
