"""Agent 上下文窗口：历史选取、token 预算与执行提示词整理。"""
from __future__ import annotations

import re
from typing import Any

from app.services import chat_memory, llm as _llm


_TOKEN_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")
_CONTEXT_DEPENDENT_EXEC_RE = re.compile(
    r"(?:按|沿用|保持|继续|接着|基于|根据|照).{0,12}"
    r"(?:刚才|之前|前面|上面|上述|原来|已有|这个|设定|方案|版本)|"
    r"(?:其他|其它|其余).{0,5}(?:不变|保持|沿用)|"
    r"^(?:就这样|就这个|按这个来|继续生成|继续出图)"
)


def is_context_dependent(text: str) -> bool:
    return _CONTEXT_DEPENDENT_EXEC_RE.search(text or "") is not None


def estimate_tokens(text: str) -> int:
    """跨模型近似：中日韩字符约 1 token，其它非空白字符约 4 字符/token。"""
    cjk = len(_TOKEN_CJK_RE.findall(text or ""))
    other = _TOKEN_CJK_RE.sub("", text or "")
    other_chars = len(re.sub(r"\s", "", other))
    return cjk + (other_chars + 3) // 4


def _clip_to_token_budget(text: str, budget: int) -> str:
    if estimate_tokens(text) <= budget:
        return text
    marker = "\n…（中间内容已按 token 预算截断）…\n"
    low, high = 0, len(text)
    best = marker
    while low <= high:
        keep = (low + high) // 2
        left = keep // 2
        right = keep - left
        candidate = text[:left] + marker + (text[-right:] if right else "")
        if estimate_tokens(candidate) <= budget:
            best = candidate
            low = keep + 1
        else:
            high = keep - 1
    return best


def recent_history(thread_id: str, max_tokens: int = 20_000,
                   per_role: int = 6) -> list[dict]:
    """分别取用户与 AI 最近六条消息，再在全局 token 上限内均衡裁剪。"""
    try:
        history = chat_memory.get_history(thread_id)
        selected: list[tuple[int, dict]] = []
        counts = {"user": 0, "assistant": 0}
        for index in range(len(history) - 1, -1, -1):
            item = history[index]
            role = item.get("role")
            if role not in counts or counts[role] >= per_role:
                continue
            content = (item.get("content") or "").strip()
            if not content:
                continue
            selected.append((index, {"role": role, "content": content}))
            counts[role] += 1
            if all(count >= per_role for count in counts.values()):
                break
        items = [item for _, item in sorted(selected, key=lambda pair: pair[0])]
        if not items:
            return []

        content_budget = max(1, max_tokens - len(items) * 4)
        full_costs = [estimate_tokens(item["content"]) for item in items]
        if sum(full_costs) <= content_budget:
            return items

        base = content_budget // len(items)
        allocations = [min(cost, base) for cost in full_costs]
        remaining = content_budget - sum(allocations)
        for index in range(len(items) - 1, -1, -1):
            extra = min(full_costs[index] - allocations[index], remaining)
            allocations[index] += extra
            remaining -= extra
            if remaining <= 0:
                break
        return [
            {**item, "content": _clip_to_token_budget(item["content"], allocations[index])}
            for index, item in enumerate(items)
        ]
    except Exception:  # noqa: BLE001
        return []


def history_text(ctx: Any) -> str:
    """把历史拼成供 Supervisor 和回答节点使用的单轮上下文。"""
    history = ctx.get("history") or []
    if not history:
        return ""
    lines = [("用户" if item["role"] == "user" else "助手") + "：" + item["content"]
             for item in history]
    return "【最近对话：用于衔接对象与约束，本轮最新要求优先】\n" + "\n".join(lines) + "\n\n"


def standalone_execution_prompt(ctx: Any, text: str) -> str:
    """仅在本轮依赖上文时，把最近约束整理为可独立执行的提示词。"""
    original = (text or "").strip()
    if not original or not (ctx.get("history") or []) or not is_context_dependent(original):
        return original
    chat_fn = ctx.get("chat_fn") or _llm.chat
    system = (
        "你是多轮请求整理器。根据最近对话，把本轮要求改写为一段可独立执行的完整提示词。"
        "必须保留已确认的角色、构图、服装、颜色、材质、画风和负面约束；本轮最新修改覆盖旧要求；"
        "已被用户否决的内容不得恢复；不要补充用户未要求的新设计。只输出完整提示词，不要解释。"
    )
    user = history_text(ctx) + "本轮执行要求：" + original
    try:
        resolved = chat_fn(
            ctx["chat_base"], ctx["chat_key"], ctx["chat_model"],
            system, user, temperature=0.2, proxy=ctx.get("proxy", ""),
        )
        return (resolved or "").strip() or original
    except Exception:  # noqa: BLE001
        return original
