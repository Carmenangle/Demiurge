"""SSE 出口帧归一：存档/历史里的绝对回环取图地址，发往客户端前必须收敛成根相对。

发射侧（view_urls）已改根相对；这里治的是**存量**——旧快照与旧消息正文里已经写死了
`http://127.0.0.1:8010/...`，不迁移存档（用户定案「读时归一」），只在出口收敛。
"""
import asyncio

from app.services import sse


def _body(events) -> str:
    response = sse.sse_response(lambda: events)

    async def collect() -> str:
        return "".join([chunk async for chunk in response.body_iterator])

    return asyncio.run(collect())


def test_image_frame_collapses_legacy_loopback_origin():
    legacy = "http://127.0.0.1:8010/api/comfyui/local-view?path=D%3A%5Ca.png"
    body = _body([{"image": legacy}])
    assert '"url": "/api/comfyui/local-view?path=D%3A%5Ca.png"' in body
    assert "127.0.0.1" not in body


def test_view_endpoint_frame_collapses_too():
    legacy = "http://localhost:8013/api/comfyui/view?filename=a.png&type=output"
    body = _body([{"video": legacy}])
    assert "/api/comfyui/view?filename=a.png&type=output" in body
    assert "localhost" not in body


def test_external_host_in_frame_is_left_alone():
    url = "https://cdn.example.com/api/comfyui/local-view?path=x"
    assert url in _body([{"image": url}])


def test_comfyui_own_address_not_collapsed():
    """只收敛自家两个取图端点；ComfyUI 自身地址留给调用方判断。"""
    assert "http://127.0.0.1:8188/view?filename=a.png" in _body(
        [{"trace": "http://127.0.0.1:8188/view?filename=a.png"}])


def test_error_frame_normalized_and_done_kept():
    def boom():
        raise RuntimeError("取图失败 http://127.0.0.1:8010/api/comfyui/local-view?path=x")
        yield {}  # pragma: no cover

    response = sse.sse_response(boom)

    async def collect() -> str:
        return "".join([chunk async for chunk in response.body_iterator])

    body = asyncio.run(collect())
    assert "/api/comfyui/local-view?path=x" in body
    assert "127.0.0.1" not in body
    assert body.endswith("data: [DONE]\n\n")
