"""把模型原始流转换成可安全提前展示的正文流。"""
from __future__ import annotations

import re
from typing import Callable


_HIDDEN = {"think", "状态更新", "表格更新", "illustration"}
_UNWRAP = {"content"}
_TAG_NAME = re.compile(r"^/?\s*([^\s>/]+)")


class VisibleTextStream:
    """跨 chunk 隐藏内部控制块，并去掉正文包装标签。

    on_hidden(name, text)：隐藏块内容被消费时逐段回调（2026-08-31 晚「思考全公开」：
    think 内容实时送前端思考面板，正文流保持干净）。name 为块名（think/状态更新/…）。
    """

    def __init__(self, on_hidden: Callable[[str, str], None] | None = None) -> None:
        self._buffer = ""
        self._hidden: str | None = None
        self._on_hidden = on_hidden

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        output: list[str] = []
        while self._buffer:
            if self._hidden:
                closing = f"</{self._hidden}>"
                index = self._buffer.lower().find(closing.lower())
                if index < 0:
                    keep = max(0, len(closing) - 1)
                    hidden_part = self._buffer[:-keep] if keep else self._buffer
                    if hidden_part and self._on_hidden is not None:
                        self._on_hidden(self._hidden, hidden_part)
                    self._buffer = self._buffer[-keep:] if keep else ""
                    break
                hidden_part = self._buffer[:index]
                if hidden_part and self._on_hidden is not None:
                    self._on_hidden(self._hidden, hidden_part)
                self._buffer = self._buffer[index + len(closing):]
                self._hidden = None
                continue

            start = self._buffer.find("<")
            if start < 0:
                output.append(self._buffer)
                self._buffer = ""
                break
            if start:
                output.append(self._buffer[:start])
                self._buffer = self._buffer[start:]
            end = self._buffer.find(">")
            if end < 0:
                break
            tag = self._buffer[:end + 1]
            self._buffer = self._buffer[end + 1:]
            match = _TAG_NAME.match(tag[1:-1])
            name = match.group(1).lower() if match else ""
            closing_tag = tag.startswith("</")
            if name in _HIDDEN:
                if not closing_tag:
                    self._hidden = name
                continue
            if name in _UNWRAP:
                continue
            output.append(tag)
        return "".join(output)

    def finish(self) -> str:
        if self._hidden:
            self._buffer = ""
            return ""
        tail, self._buffer = self._buffer, ""
        return tail
