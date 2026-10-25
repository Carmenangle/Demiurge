"""SSE 响应封装：把「事件迭代器」包成 text/event-stream 响应。

三个流式端点（image-agent / chat / support）此前各写一遍相同的信封：
逐事件 json.dumps → 出错发 {error} → 末尾 [DONE]。现在统一走这里。

wire 格式（与前端 api/sse.ts openSSE 对齐）：每事件一行 `data: <json>\n\n`，收尾 `data: [DONE]`。
payload 统一由 chat_stream_protocol 编码为版本化判别联合。
"""
import json
from collections.abc import Mapping
from typing import Callable, Iterable, Iterator

from fastapi.responses import StreamingResponse

from app.services import chat_stream_protocol, view_urls

import logging

_LOG = logging.getLogger("uvicorn.error")


def sse_response(events: Callable[[], Iterable[Mapping[str, object]]]) -> StreamingResponse:
    """events 是「返回事件 dict 可迭代对象」的工厂（惰性，进入流式后才求值）。
    迭代中抛异常 → 发一条 {error}；无论如何末尾补 [DONE]。

    帧内媒体 URL 出口归一（view_urls.normalize_wire）：流里带的是存档/历史里的绝对回环
    取图地址时，收敛成根相对再发。不这么做，非本机客户端拿到的就是它够不着的地址。
    json.dumps 不转义 `:` 与 `/`，正则锚定的形态在帧文本里逐字保持，故可串级改写。
    """
    def frame(payload: chat_stream_protocol.ChatStreamEvent) -> str:
        return f"data: {view_urls.normalize_wire(json.dumps(payload, ensure_ascii=False))}\n\n"

    def gen() -> Iterator[str]:
        try:
            for ev in events():
                wire_event = chat_stream_protocol.encode_event(ev)
                if wire_event is not None:
                    yield frame(wire_event)
        except Exception as e:  # 网络/鉴权/模型错误统一回传，前端展示
            _LOG.debug("sse::gen 兜底异常: %s", e)
            yield frame(chat_stream_protocol.error_event(str(e)))
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
