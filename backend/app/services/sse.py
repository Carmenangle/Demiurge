"""SSE 响应封装：把「事件迭代器」包成 text/event-stream 响应。

三个流式端点（image-agent / chat / support）此前各写一遍相同的信封：
逐事件 json.dumps → 出错发 {error} → 末尾 [DONE]。现在统一走这里。

wire 格式（与前端 api/sse.ts openSSE 对齐）：每事件一行 `data: <json>\n\n`，收尾 `data: [DONE]`。
payload 统一由 chat_stream_protocol 编码为版本化判别联合。
"""
import json
from typing import Callable, Iterable, Iterator

from fastapi.responses import StreamingResponse

from app.services import chat_stream_protocol


def sse_response(events: Callable[[], Iterable[dict]]) -> StreamingResponse:
    """events 是「返回事件 dict 可迭代对象」的工厂（惰性，进入流式后才求值）。
    迭代中抛异常 → 发一条 {error}；无论如何末尾补 [DONE]。"""
    def gen() -> Iterator[str]:
        try:
            for ev in events():
                wire_event = chat_stream_protocol.encode_event(ev)
                if wire_event is not None:
                    yield f"data: {json.dumps(wire_event, ensure_ascii=False)}\n\n"
        except Exception as e:  # 网络/鉴权/模型错误统一回传，前端展示
            error = chat_stream_protocol.error_event(str(e))
            yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
