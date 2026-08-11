"""提示词遵从度评估：只读最终 wire Trace，不重放模型也不执行副作用。"""
from __future__ import annotations

import re
from typing import Any

_REFUSAL = re.compile(
    r"^\s*(?:I'm\s+Claude\s+Code[.!,;:\s-]*)?"
    r"(?:I\s+(?:can't|cannot|can\s+not|won't|will\s+not)\s+"
    r"(?:help|assist|comply|generate|create|produce|write|describe|provide)|"
    r"(?:我)?(?:不能|无法)(?:描写|描述|生成|创作|协助|帮助|提供|处理)|"
    r"抱歉[^。！!?]{0,40}(?:不能|无法))",
    re.I,
)


def is_refusal(text: str) -> bool:
    """只识别回复起始的明确自述拒答，不把剧情中「拒绝」误判为模型拒答。"""
    return bool(_REFUSAL.search((text or "").strip()[:500]))


def _data(record: dict[str, Any] | None) -> dict[str, Any]:
    raw = record.get("data") if isinstance(record, dict) else None
    return raw if isinstance(raw, dict) else {}


def _last(records: list[dict[str, Any]], event: str, *, agent: str = "") -> dict[str, Any] | None:
    for record in reversed(records):
        if record.get("event") != event:
            continue
        data = _data(record)
        if not agent or data.get("agent") == agent:
            return record
    return None


def evaluate_turn(records: list[dict[str, Any]]) -> dict[str, Any]:
    """把一轮 Trace 归因到本地注入、模型遵从或上游拒答。"""
    context = _data(_last(records, "turn.context_ready"))
    request = _data(_last(records, "model.request", agent="roleplay"))
    response = _data(_last(records, "model.response", agent="roleplay"))
    profile = _data(_last(records, "illustration.profile"))
    worldbook = _data(_last(records, "worldbook.resolved"))
    rag = _data(_last(records, "rag.retrieve"))
    table_retrieval = _data(_last(records, "table.retrieve"))
    messages = request.get("messages") if isinstance(request.get("messages"), list) else []
    manifest = request.get("prompt_manifest") if isinstance(request.get("prompt_manifest"), list) else []
    wire_messages = [message for message in messages if isinstance(message, dict)]
    preset_name = str(context.get("preset_name") or "").strip()
    attached_name = str(request.get("preset") or "").strip()
    raw_response = str(response.get("content") or "")
    refusal = is_refusal(raw_response)
    request_recorded = bool(request)
    response_recorded = bool(response)
    preset_requested = bool(preset_name)
    preset_attached = not preset_requested or attached_name == preset_name
    content_contract = "<content>" in raw_response and "</content>" in raw_response
    if not request_recorded:
        outcome = "local_request_missing"
    elif not preset_attached:
        outcome = "local_injection_missing"
    elif refusal:
        outcome = "upstream_refusal"
    elif not response_recorded or not content_contract:
        outcome = "model_noncompliance"
    else:
        outcome = "ok"
    hits = rag.get("hits") if isinstance(rag.get("hits"), list) else []
    table_hits = int(table_retrieval.get("hit_count") or 0)
    return {
        "outcome": outcome,
        "request_recorded": request_recorded,
        "response_recorded": response_recorded,
        "preset_requested": preset_requested,
        "preset_attached": preset_attached,
        "wire_roles": [str(message.get("role") or "") for message in wire_messages],
        "wire_chars": sum(len(str(message.get("content") or "")) for message in wire_messages),
        "provider_profile": str(request.get("provider_profile") or ""),
        "prompt_segments": len(manifest),
        "history_after_segments": sum(
            1 for item in manifest
            if isinstance(item, dict) and item.get("position") == "history_after"
        ),
        "content_contract": content_contract,
        "upstream_refusal": refusal,
        "profile_strategy": str(profile.get("strategy") or ""),
        "worldbook_selected": len(worldbook.get("selected_indices") or []),
        "worldbook_keyword_selected": len(worldbook.get("keyword_indices") or []),
        "rag_hits": len(hits),
        "table_hits": table_hits,
    }
