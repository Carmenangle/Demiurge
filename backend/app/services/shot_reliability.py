"""分镜模拟的可靠性助手：重试 + 截断 JSON 续写。

与剧情模式同口径（`llm.chat_messages` 的 transient 退避、`roleplay_turn` 的
截断续写），但不发网络请求：所有调用由调用方注入，便于离线模拟与单测。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

# 与 `llm._is_transient` 同源的关键词：只对上游临时故障重试，4xx 不白烧调用。
_TRANSIENT_CUES = (
    "502", "503", "504", "upstream", "timeout", "timed out",
    "connection", "connect", "proxy", "temporarily",
)


def is_transient(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(cue in text for cue in _TRANSIENT_CUES)


def call_with_retry(
    fn: Callable[[], str],
    *,
    attempts: int = 3,
    backoff: tuple[float, ...] = (0.0, 0.0, 0.0),
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> str:
    """临时故障退避重试；非临时错误立即失败。"""
    last: BaseException | None = None
    total = max(1, attempts)
    for index in range(total):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — 由下方分类决定是否重试
            last = exc
            if index >= total - 1 or not is_transient(exc):
                break
            if on_retry is not None:
                on_retry(index + 1, exc)
            time.sleep(backoff[min(index, len(backoff) - 1)])
    raise RuntimeError(f"调用失败（{total} 次）：{last}") from last


def _balanced(text: str) -> bool:
    """括号/引号是否闭合：截断的 JSON 一定不闭合；闭合后才值得尝试解析。"""
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_string


def json_complete(text: str) -> bool:
    """结构完整（可解析或至少括号闭合）即可停止续写。"""
    try:
        json.loads(text)
        return True
    except Exception:  # noqa: BLE001 — 解析失败时用括号闭合兜底
        return _balanced(text)


def complete_json(
    partial: str,
    continue_fn: Callable[[str], str],
    *,
    max_attempts: int = 3,
    min_new_chars: int = 1,
) -> str:
    """截断 JSON 续写：把已有片段交给 `continue_fn`，拼到闭合为止。"""
    text = partial
    for _ in range(max(0, max_attempts)):
        if json_complete(text):
            return text
        addition = continue_fn(text) or ""
        if len(addition.strip()) < min_new_chars:
            break
        text += addition
    return text
