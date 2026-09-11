"""Provider-aware Prompt Compiler.

本模块只编译最终消息位置和交替 role；预设、世界书与表格仍由各自属主产生内容。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROVIDER_PROFILES = frozenset({"openai_compatible", "claude_compatible"})


@dataclass(frozen=True)
class CompiledPrompt:
    messages: list[dict[str, str]]
    manifest: list[dict[str, Any]]
    provider_profile: str


def normalize_provider_profile(value: str) -> str:
    normalized = (value or "").strip().lower()
    return normalized if normalized in PROVIDER_PROFILES else "openai_compatible"


def _clean(messages: list[dict]) -> list[dict[str, str]]:
    return [
        {"role": str(message.get("role") or "user"),
         "content": str(message.get("content") or "").strip()}
        for message in messages
        if isinstance(message, dict) and str(message.get("content") or "").strip()
    ]


def _manifest(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    seen_dialogue = False
    result: list[dict[str, Any]] = []
    for order, message in enumerate(messages):
        role = message["role"]
        if role != "system":
            seen_dialogue = True
        position = "history_after" if role == "system" and seen_dialogue else (
            "system_head" if role == "system" else "dialogue"
        )
        result.append({
            "source": f"wire:{order}", "role": role, "position": position,
            "order": order, "priority": 100 if role == "system" else 50,
            "chars": len(message["content"]),
            "token_estimate": max(1, len(message["content"]) // 4),
        })
    return result


def _merge_turns(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for message in messages:
        role = "assistant" if message["role"] in ("assistant", "ai") else "user"
        if result and result[-1]["role"] == role:
            result[-1]["content"] += "\n\n" + message["content"]
        else:
            result.append({"role": role, "content": message["content"]})
    return result


def compile_messages(messages: list[dict], *, provider_profile: str) -> CompiledPrompt:
    """编译已按预设顺序组装的消息。

    OpenAI 兼容档保留历史后 system 的原位；Claude 兼容档不把它无条件前移，
    而是收口为贴近最后 user 的「本轮执行合同」。
    """
    cleaned = _clean(messages)
    profile = normalize_provider_profile(provider_profile)
    manifest = _manifest(cleaned)
    if profile == "openai_compatible":
        return CompiledPrompt(cleaned, manifest, profile)

    head: list[str] = []
    dialogue: list[dict[str, str]] = []
    tail_contracts: list[str] = []
    seen_dialogue = False
    for message in cleaned:
        if message["role"] == "system":
            (tail_contracts if seen_dialogue else head).append(message["content"])
            continue
        seen_dialogue = True
        dialogue.append(message)
    turns = _merge_turns(dialogue)
    if tail_contracts:
        contract = "【本轮执行合同】\n" + "\n\n".join(tail_contracts)
        if turns and turns[-1]["role"] == "user":
            turns[-1]["content"] = contract + "\n\n【当前输入】\n" + turns[-1]["content"]
        else:
            turns.append({"role": "user", "content": contract})
    compiled: list[dict[str, str]] = []
    if head:
        compiled.append({"role": "system", "content": "\n\n".join(head)})
    compiled.extend(turns)
    return CompiledPrompt(compiled or [{"role": "user", "content": ""}], manifest, profile)
