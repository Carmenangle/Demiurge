"""把遗留 image_agent ReAct 流适配为多 Agent 专家节点结果。"""
from __future__ import annotations

from typing import Any

from app.services import image_agent


def run(ctx: Any, message: str, images: list[str], trace: list[str]) -> dict:
    result_text: list[str] = []
    image_recs: list[dict] = []
    inspiration_cards: list[dict] = []
    interrupted = False
    try:
        for event in image_agent.stream_agent(
            ctx["thread_id"], message, images or None,
            ctx["chat_base"], ctx["chat_key"], ctx["chat_model"],
            ctx["gen_base"], ctx["gen_key"], ctx["gen_model"],
            ctx.get("size", "1024x1024"), ctx["output_dir"], ctx["repo_id"],
            ctx["embed_base"], ctx["embed_key"], ctx["embed_model"],
            cancel_event=ctx.get("cancel_event"), proxy_url=ctx.get("proxy", ""),
            style_template=ctx.get("style_template", ""), agent_id=ctx.get("agent_id", ""),
            memory_mode="external_turn",
            image_quality=ctx.get("image_quality", "high"),
            chat_proxy_url=ctx.get("chat_proxy", ""),
            gen_proxy_url=ctx.get("gen_proxy", ""),
            embed_proxy_url=ctx.get("embed_proxy", ""),
            search_provider=ctx.get("search_provider", ""),
            persona_block=ctx.get("_chat_persona_block", ""),
            # 聊天/剧情主链已按 context_max_tokens 裁剪过 ctx.history；工具专家复用同一份，
            # 不再让 image_agent 去读 chat_memory 的全量历史（长聊会撑爆模型窗口）。
            history=ctx.get("history"),
        ):
            if event.get("interrupted"):
                interrupted = True
            if event.get("delta"):
                result_text.append(event["delta"])
            if event.get("image"):
                rec = {"id": event.get("image_id"), "url": event["image"]}
                if event.get("regeneration"):
                    rec["regeneration"] = event["regeneration"]
                image_recs.append(rec)
            if event.get("inspiration"):
                inspiration_cards.append(event["inspiration"])
            if event.get("error"):
                result_text.append(f"（工具专家出错：{event['error']}）")
    except Exception as exc:  # noqa: BLE001
        result_text.append(f"工具专家执行失败：{exc}")
    text = "".join(result_text).strip()
    if text and str(ctx.get("interaction_mode") or "story") != "story":
        # 聊天室人设块带防拦截预设：工具专家最终答复也要还原 @()@ 标记。
        from app.services import chat_followup, chat_room
        text = chat_room.restore_guard_markers(ctx, text)
        try:
            chat_followup.note_bot(
                str(ctx.get("thread_id") or ""), bubbles=1,
                max_followups=ctx.get("chat_followup_max"),
                max_reopens=ctx.get("chat_reopen_max"),
                cadence=ctx.get("chat_cadence"),
            )
        except Exception:  # noqa: BLE001 [EXC-保留] 节奏状态写失败不影响工具结果
            pass
    return {
        "result_text": text,
        "image_recs": image_recs,
        "insp_cards": inspiration_cards,
        "trace": trace,
        "_interrupted": interrupted,
    }
