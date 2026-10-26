"""JSON 出口的媒体 URL 归一（`app.main._normalize_media_urls`）行为回归。

存量数据里写死了 `http://127.0.0.1:8010/api/comfyui/...`（RAG 记录、仓库封面、对话正文
里的 markdown 图），存档不迁移（用户定案「读时归一」）⇒ 只能在发往客户端前收敛。
SSE 帧的同一口径在 tests/test_sse_wire.py，本文件只测 JSON 中间件与其边界。

app.main 的测试导入闸在 tests/conftest.py 顶部（公共闸），本文件直接 import。
"""
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.testclient import TestClient

from app.main import app

# 测试专用路由挂在真 app 上：中间件是 `@app.middleware` 注册的，只有走真实 ASGI 链
# 才能证明「挂在 JSON 上、不挂在事件流与文本上」。路径带 _test 前缀，无生产调用方。
_LEGACY = "http://127.0.0.1:8010/api/comfyui/local-view?path=D%3A%5Ca.png"
_REMOTE = "https://cdn.example.com/api/comfyui/local-view?path=x"

app.add_api_route("/api/_test/egress-json", lambda: {"url": _LEGACY, "remote": _REMOTE},
                  methods=["GET"])
app.add_api_route("/api/_test/egress-text",
                  lambda: PlainTextResponse(f"图 {_LEGACY}"), methods=["GET"])
app.add_api_route("/api/_test/egress-stream",
                  lambda: StreamingResponse(iter([b"data: a\n\n"]),
                                            media_type="text/event-stream"),
                  methods=["GET"])


def _client() -> TestClient:
    # 入站三道闸里 client-IP 门只认回环对端；TestClient 默认对端是 "testclient" ⇒ 403。
    # Host 用默认 testserver（闸内白名单，浏览器永不发送）。
    return TestClient(app, client=("127.0.0.1", 51000))


def test_json_egress_collapses_loopback_and_keeps_external():
    body = _client().get("/api/_test/egress-json").json()
    assert body["url"] == "/api/comfyui/local-view?path=D%3A%5Ca.png"
    assert body["remote"] == _REMOTE          # 外部主机不动，SSRF 校验继续判假


def test_json_egress_recomputes_content_length():
    r = _client().get("/api/_test/egress-json")
    assert r.status_code == 200
    # 收敛掉的 origin 字节必须反映在 Content-Length 上，否则客户端按旧长度截断读
    assert int(r.headers["content-length"]) == len(r.content)
    assert r.headers["content-type"].startswith("application/json")


def test_non_json_egress_untouched():
    """文本与事件流不由本中间件改写：后者整体读回会把增量下发缓冲成一次性。"""
    client = _client()
    r = client.get("/api/_test/egress-text")
    assert _LEGACY in r.text

    r = client.get("/api/_test/egress-stream")
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text == "data: a\n\n"


def test_health_endpoint_still_ok_after_rebuild():
    """无一命中的 JSON 也要能原样重建返回（重建路径不能吞背景与状态）。"""
    r = _client().get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "running_jobs": 0}  # running_jobs＝comfy_pending 在途计数（M4 运行时）
