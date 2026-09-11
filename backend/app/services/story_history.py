"""剧情模式历史瘦身·纯逻辑（0 I/O 0 LLM）：只放行上一次剧情轮的纯正文。

上下文合同（docs/PLAN-CONTEXT-SLIM.md）：剧情模式历史只进上一次剧情轮，且过滤到只剩
正文——剥离 think/status/状态更新/表格更新/encounter/骰点/插画/音频/转场块；其余更早
轮次一律不进上下文。会话快照仍是历史真源：本模块只在注入侧过滤，不改写、不删除已存历史。
"""
from __future__ import annotations

import re

# 生成侧搭车块全集（与各提取器属主一一对应）：think=思考、illustration=插画计划、
# audio=配音、transition=转场、roll=命运骰点、encounter=登场锚点、
# status=状态栏快照、状态更新=数值增量、表格更新=旧版表格块。
_TAGS = "think|illustration|audio|transition|roll|encounter|status|状态更新|表格更新"
_BLOCK_RE = re.compile(
    rf"\s*<(?P<tag>{_TAGS})\b[^>]*>[\s\S]*?</(?P=tag)>\s*", re.IGNORECASE)
_BLOCK_OPEN_TAIL_RE = re.compile(
    rf"\s*<(?:{_TAGS})\b[^>]*>[\s\S]*\Z", re.IGNORECASE)
_CONTENT_RE = re.compile(r"<content\b[^>]*>([\s\S]*?)</content>", re.IGNORECASE)


def prose_only(text: str) -> str:
    """从单条剧情回复里提取用户可见正文：优先 <content> 内层，否则剥控制块后的剩余全文。

    完整块与只开未闭的尾部块都剥（模型漏闭标签时防控制内容泄入下一轮上下文）。
    块位置以单个换行占位，剥离产生的连续空行折叠为一个段间空行。
    """
    source = text or ""
    content = _CONTENT_RE.search(source)
    if content:
        source = content.group(1)
    source = _BLOCK_RE.sub("\n", source)
    source = _BLOCK_OPEN_TAIL_RE.sub("", source)
    source = re.sub(r"\n{3,}", "\n\n", source)
    return source.strip()


def last_story_round(history: list[dict] | None) -> list[dict]:
    """取上一次剧情轮：最后一条有正文的 AI 回复（剥到纯正文）+ 它之前最近的用户输入。

    末轮被控制块占满（正文为空）时回退到上一条有正文的 AI 回复，保证注入不落空。
    开场问候（前面没有用户输入）只返回 AI 一条。空历史 → []。
    """
    items = [item for item in (history or []) if isinstance(item, dict)]
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        if (item.get("role") or "") != "assistant":
            continue
        content = prose_only(str(item.get("content") or ""))
        if not content:
            continue
        round_: list[dict] = [{"role": "assistant", "content": content}]
        for earlier in reversed(items[:index]):
            if (earlier.get("role") or "") == "user" and \
                    str(earlier.get("content") or "").strip():
                round_.insert(0, {
                    "role": "user",
                    "content": str(earlier.get("content") or "").strip(),
                })
                break
        return round_
    return []
