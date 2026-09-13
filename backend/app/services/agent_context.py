"""Agent 上下文窗口：历史选取、token 预算与执行提示词整理。"""
from __future__ import annotations

import re
from typing import Any, Callable

from app.services import chat_memory, chat_snapshot, llm as _llm


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


def resolve_history_budget(context_max_tokens: int, preset_sampling: Any) -> int:
    """有效历史预算 = min(用户设置「历史上下文上限」, 模型输入窗口 − 输出预留)。

    2026-09-11 上下文 token 化（对齐市面 harness）：上下文长度只由 token 决定，
    条数废弃。设置 <=0（无限）→ 只受模型窗口约束（模型未配 → 0=不裁剪，
    保持「无限」语义）；模型窗口未配 → 只用设置值。
    模型窗口取 preset_sampling.openai_max_context，输出预留取 openai_max_tokens
    （都是 settings 全局权威字段，前端 presetSamplingToWire 直传 snake_case）。
    """
    setting = context_max_tokens if isinstance(context_max_tokens, int) else 0
    sampling = preset_sampling if isinstance(preset_sampling, dict) else {}

    def _int_field(name: str) -> int:
        v = sampling.get(name)
        return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0

    model_in = _int_field("openai_max_context")
    model_out = _int_field("openai_max_tokens")
    model_budget = (model_in - model_out) if (model_in and model_out) else model_in
    if model_budget <= 0:
        model_budget = 0
    if setting <= 0:
        return model_budget
    if model_budget <= 0:
        return setting
    return min(setting, model_budget)


def _compact_history(items: list[dict], costs: list[int], budget: int,
                     summarize_fn: Callable[[list[dict]], str | None]) -> list[dict] | None:
    """自动压缩（对齐 /compact）：旧段交给 LLM 摘要，近期一半预算内原样保留。

    摘要挂在 assistant 角色消息上（AI 记忆语义）；LLM 失败/空摘要返回 None，
    调用方 fail-open 退回机械保头保尾裁剪。
    """
    if summarize_fn is None or len(items) < 3:
        return None
    keep_tail_budget = max(1, budget // 2)
    tail: list[dict] = []
    tail_cost = 0
    split = len(items)
    for index in range(len(items) - 1, 0, -1):
        if tail_cost + costs[index] > keep_tail_budget:
            split = index + 1
            break
        tail.append(items[index])
        tail_cost += costs[index]
        split = index
    else:
        split = 1
    old = items[:split]
    if not old or not tail:
        return None
    summary = summarize_fn(old)
    if not summary:
        return None
    head_msg = {"role": "assistant",
                "content": ("【历史压缩摘要（以上是更早对话的自动摘要：任务目标与演变、"
                            "已确认的决定与设定、用户否决项、待办均在此）】\n" + summary)}
    return [head_msg] + list(reversed(tail))


def recent_history(thread_id: str, max_tokens: int = 20_000,
                   per_role: int = 0,
                   history_override: list[dict] | None = None,
                   compact_trigger_pct: int = 0,
                   summarize_fn: Callable[[list[dict]], str | None] | None = None,
                   ) -> list[dict]:
    """按 token 预算取历史；per_role<=0 不限条数（2026-09-11 条数废弃，token 唯一权威）。

    超预算且配置了 summarize_fn 时自动压缩（compact_trigger_pct% 触发，对齐
    Claude Code auto-compact）：旧段 LLM 摘要 + 近期原文；压缩失败退回机械
    保头保尾裁剪（零 LLM 成本，绝不因压缩失败丢历史）。
    """
    try:
        if history_override is not None:
            history = history_override
        else:
            snapshot_history = chat_snapshot.load_prompt_history(thread_id)
            history = snapshot_history if snapshot_history is not None else chat_memory.get_history(thread_id)
        selected: list[tuple[int, dict]] = []
        counts = {"user": 0, "assistant": 0}
        for index in range(len(history) - 1, -1, -1):
            item = history[index]
            role = item.get("role")
            if role not in counts:
                continue
            if per_role > 0 and counts[role] >= per_role:
                continue
            # 批量采集副产品（meta.kind=plan_collect）不占用每角色历史条数，
            # 也不进入文本历史——否则 14 条图片消息会把计划卡/重要回复挤出 6 条额度。
            # 正常主动生成（persist_image 等）不带此标记，照常计入上下文。
            if role == "assistant" and (item.get("meta") or {}).get("kind") == "plan_collect":
                continue
            content = (item.get("content") or "").strip()
            if not content:
                continue
            selected.append((index, {"role": role, "content": content}))
            counts[role] += 1
            if per_role > 0 and all(count >= per_role for count in counts.values()):
                break
        items = [item for _, item in sorted(selected, key=lambda pair: pair[0])]
        if not items:
            return []

        # max_tokens<=0 表示无上限：历史全量不裁剪（有效预算已由
        # resolve_history_budget 按「min(设置, 模型窗口−输出预留)」裁决后传入）。
        if max_tokens <= 0:
            return items

        budget = max(1, max_tokens - len(items) * 4)
        costs = [estimate_tokens(item["content"]) for item in items]
        total = sum(costs)
        # 自动压缩（/compact 对齐）：达到触发百分比即压旧段为摘要；
        # 未配压缩（pct<=0 或无 summarize_fn）走原有机械路径。
        trigger = budget * compact_trigger_pct / 100.0
        if (compact_trigger_pct > 0 and summarize_fn is not None
                and len(items) >= 3 and total > trigger):
            compacted = _compact_history(items, costs, budget, summarize_fn)
            if compacted is not None:
                return compacted
        if total <= budget:
            return items

        # 2026-09-04 保头保尾窗口（成本杠杆 L2，设计 §2）：头部固定保留 keep_head 条原文
        #（前缀稳定锚），中段整体让位给尾部近期消息；被挤掉的中段折叠成一行标注挂到保留
        # 尾部最早一条（或头部末条）——不改 role/交替结构、纯机械、零 LLM 成本。
        keep_head = min(2, len(items))
        head = items[:keep_head]
        head_cost = sum(costs[:keep_head])
        rest = list(zip(items[keep_head:], costs[keep_head:]))
        if not rest:  # 极端小预算下无中段可让位：退回逐条头尾裁剪
            return [
                {**item, "content": _clip_to_token_budget(
                    item["content"], max(1, budget // len(items)))}
                for item in items
            ]
        budget_left = max(0, budget - head_cost)
        tail: list[dict] = []
        used_tail = 0
        skipped_mid = 0
        for item, cost in reversed(rest):
            if cost <= budget_left:
                tail.append(item)
                budget_left -= cost
                used_tail += cost
            elif not tail and budget_left > 0:
                # 预算连一条整条都放不下时，只允许把「最近一条」裁剪进来
                item["content"] = _clip_to_token_budget(item["content"], budget_left)
                tail.append(item)
                budget_left = 0
                used_tail = 1
            else:
                skipped_mid += 1
        tail.reverse()
        if skipped_mid:
            marker = f"\n\n…（中间 {skipped_mid} 条历史已按预算压缩，既有设定与人物关系不变）…"
            if tail:
                tail[0]["content"] = marker.lstrip("\n\n") + tail[0]["content"]
            else:
                head[-1]["content"] = head[-1]["content"] + marker
        return head + tail
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
        proxy = (ctx.get("chat_proxy", "") or "").strip()
        resolved = chat_fn(
            ctx["chat_base"], ctx["chat_key"], ctx["chat_model"],
            system, user, temperature=0.2, **({"proxy": proxy} if proxy else {}),
        )
        return (resolved or "").strip() or original
    except Exception:  # noqa: BLE001
        return original
