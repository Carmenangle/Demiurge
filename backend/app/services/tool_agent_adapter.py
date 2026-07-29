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
    return {
        "result_text": "".join(result_text).strip(),
        "image_recs": image_recs,
        "insp_cards": inspiration_cards,
        "trace": trace,
        "_interrupted": interrupted,
    }
