import httpx
import pytest
import threading

from app.services import image_gen


class _Response:
    status_code = 200

    @staticmethod
    def json():
        return {"data": [{"url": "https://img.test/result.png"}]}


def _capture_client(captured):
    class CaptureClient:
        def __init__(self, *args, **kwargs):
            captured["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            captured.update(kwargs)
            return _Response()

    return CaptureClient


def test_read_timeout标记为交付状态未知(monkeypatch):
    class TimeoutClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("The read operation timed out")

    monkeypatch.setattr(image_gen.httpx, "Client", TimeoutClient)

    with pytest.raises(image_gen.UpstreamDeliveryUnknown, match="交付状态未知.*request_id="):
        image_gen.generate("https://img.test/v1", "key", "model", "prompt")


def test_4k请求使用更长的读取等待时间(monkeypatch):
    captured = {}
    monkeypatch.setattr(image_gen.httpx, "Client", _capture_client(captured))

    image_gen.generate(
        "https://img.test/v1", "key", "gpt-image-2", "prompt",
        size="3840x2160",
    )

    timeout = captured["client_kwargs"]["timeout"]
    assert timeout.connect == 20
    assert timeout.write == 120
    assert timeout.read == 900


def test_生图模型显式代理传入http客户端(monkeypatch):
    captured = {}
    monkeypatch.setattr(image_gen.httpx, "Client", _capture_client(captured))

    image_gen.generate("https://img.test/v1", "key", "model", "prompt", proxy="http://proxy")

    assert captured["client_kwargs"]["proxy"] == "http://proxy"
    assert captured["client_kwargs"]["trust_env"] is False


def test_connect_timeout明确请求未发送(monkeypatch):
    class ConnectTimeoutClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            raise httpx.ConnectTimeout("connect timed out")

    monkeypatch.setattr(image_gen.httpx, "Client", ConnectTimeoutClient)

    with pytest.raises(image_gen.UpstreamRequestNotSent, match="请求未发送"):
        image_gen.generate("https://img.test/v1", "key", "gpt-image-2", "prompt")


def test_图生图接口504标记为交付状态未知(monkeypatch):
    class GatewayTimeoutResponse:
        status_code = 504
        text = '{"error":{"message":"图片生成超时，请稍后再试。"}}'

    class GatewayTimeoutClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return GatewayTimeoutResponse()

    monkeypatch.setattr(image_gen.httpx, "Client", GatewayTimeoutClient)
    monkeypatch.setattr(
        image_gen, "_load_image_bytes",
        lambda image: (b"png", "image.png", "image/png"),
    )

    with pytest.raises(image_gen.UpstreamDeliveryUnknown, match="504.*未返回任务编号"):
        image_gen.generate_with_images(
            "https://img.test/v1", "key", "gpt-image-2", "prompt", ["source"],
        )


def test_gpt_image发送用户选择的质量参数(monkeypatch):
    captured = {}
    monkeypatch.setattr(image_gen.httpx, "Client", _capture_client(captured))

    image_gen.generate(
        "https://img.test/v1", "key", "gpt-image-2-4k", "prompt",
        quality="medium",
    )

    assert captured["json"]["quality"] == "medium"


def test_banana编辑请求不发送不支持的质量参数(monkeypatch):
    captured = {}
    monkeypatch.setattr(image_gen.httpx, "Client", _capture_client(captured))
    monkeypatch.setattr(
        image_gen, "_load_image_bytes",
        lambda image: (b"png", "image.png", "image/png"),
    )

    image_gen.generate_with_images(
        "https://img.test/v1", "key", "nano-banana-pro", "prompt", ["source"],
        quality="high",
    )

    assert "quality" not in captured["data"]


def test_任意尺寸在安全范围内原样发送(monkeypatch):
    captured = {}
    monkeypatch.setattr(image_gen.httpx, "Client", _capture_client(captured))

    image_gen.generate(
        "https://img.test/v1", "key", "custom-image-model", "prompt",
        size="1536x192",
    )

    assert captured["json"]["size"] == "1536x192"


def test_生成请求原样发送用户提示词(monkeypatch):
    captured = {}
    monkeypatch.setattr(image_gen.httpx, "Client", _capture_client(captured))
    original = "保留这段提示词原文，包括16:9与所有构图要求"

    image_gen.generate(
        "https://img.test/v1", "key", "custom-image-model", original,
        size="3840x2160",
    )

    assert captured["json"]["prompt"] == original


def test_图生图请求原样发送用户提示词(monkeypatch):
    captured = {}
    monkeypatch.setattr(image_gen.httpx, "Client", _capture_client(captured))
    monkeypatch.setattr(
        image_gen, "_load_image_bytes",
        lambda image: (b"png", "image.png", "image/png"),
    )
    original = "只按当前文字编辑参考图，不添加系统约束"

    image_gen.generate_with_images(
        "https://img.test/v1", "key", "custom-image-model", original,
        ["source"], size="3840x2160",
    )

    assert captured["data"]["prompt"] == original


def test_蒙版编辑分别上传原图与mask字段(monkeypatch):
    captured = {}
    monkeypatch.setattr(image_gen.httpx, "Client", _capture_client(captured))
    monkeypatch.setattr(
        image_gen, "_load_image_bytes",
        lambda value: (value.encode(), "image.png", "image/png"),
    )

    image_gen.generate_with_images(
        "https://img.test/v1", "key", "gpt-image-2", "修改蒙版区域",
        ["original"], mask="alpha-mask",
    )

    assert [field for field, _file in captured["files"]] == ["image[]", "mask"]
    assert captured["files"][0][1][1] == b"original"
    assert captured["files"][1][1][1] == b"alpha-mask"


@pytest.mark.parametrize(("requested", "expected"), [
    ("3840x1644", "3840x1648"),
    ("1672x941", "1680x944"),
])
def test_非16倍数尺寸在请求上游前自动对齐(requested, expected, monkeypatch):
    captured = {}
    monkeypatch.setattr(image_gen.httpx, "Client", _capture_client(captured))

    image_gen.generate(
        "https://img.test/v1", "key", "custom-image-model", "prompt",
        size=requested,
    )

    assert captured["json"]["size"] == expected


@pytest.mark.parametrize("size", ["63x1024", "1024x3841", "bad"])
def test_非法自定义尺寸在请求上游前拒绝(size, monkeypatch):
    monkeypatch.setattr(
        image_gen.httpx, "Client",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不得请求上游")),
    )

    with pytest.raises(ValueError, match="图片"):
        image_gen.generate("https://img.test/v1", "key", "model", "prompt", size=size)


def test_生图入口已取消则不请求上游(monkeypatch):
    """2026-10-14 C2：暂停后 ReAct 可能还有后续工具调用——取消置位时不得再发起
    上游请求（防「暂停了还在扣费生成」）。"""
    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(
        image_gen.httpx, "Client",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("已取消不得请求上游")),
    )

    with pytest.raises(image_gen.UpstreamRequestNotSent, match="已取消"):
        image_gen.generate(
            "https://img.test/v1", "key", "model", "prompt", cancel_event=cancelled,
        )


def test_图生图入口已取消则不读参考图不请求上游(monkeypatch):
    """取消检查放在读参考图之前——取消后连 30MB 下载都不做。"""
    cancelled = threading.Event()
    cancelled.set()

    def boom(*_a, **_k):
        raise AssertionError("已取消不得读参考图/请求上游")

    monkeypatch.setattr(image_gen, "_load_image_bytes", boom)
    monkeypatch.setattr(
        image_gen.httpx, "Client",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("已取消不得请求上游")),
    )

    with pytest.raises(image_gen.UpstreamRequestNotSent, match="已取消"):
        image_gen.generate_with_images(
            "https://img.test/v1", "key", "model", "prompt", ["source"],
            cancel_event=cancelled,
        )


def test_生图取消事件未置位时路径不变(monkeypatch):
    """默认 None / 未置位 ⇒ 与改造前逐字节一致（照常请求上游）。"""
    captured = {}
    monkeypatch.setattr(image_gen.httpx, "Client", _capture_client(captured))
    pending = threading.Event()

    image_gen.generate(
        "https://img.test/v1", "key", "model", "prompt", cancel_event=pending,
    )

    assert captured["json"]["prompt"] == "prompt"


def test_load_image_bytes_local_view_bypasses_explicit_proxy(monkeypatch):
    """local-view 指向本机后端，即使配置了显式代理也必须绕过（Clash 无法转发 localhost → 502）。"""
    captured = {}

    class _StreamResp:
        headers = {"content-type": "image/png"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            return iter([b"pngdata"])

    class _StreamClient:
        def __init__(self, *args, **kwargs):
            captured["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return _StreamResp()

    monkeypatch.setattr(image_gen.httpx, "Client", _StreamClient)

    data, fn, mime = image_gen._load_image_bytes(
        "http://127.0.0.1:8010/api/comfyui/local-view?path=C%3A%5Ctest.png",
        proxy="http://127.0.0.1:7897",
    )

    assert data == b"pngdata"
    assert "proxy" not in captured["client_kwargs"]  # 显式代理被剥离


def test_load_image_bytes_relative_local_view_gets_local_origin(monkeypatch):
    """取图地址的 wire 形态是根相对（属主 view_urls），httpx 只吃绝对地址 ⇒
    `_load_image_bytes` 必须先用本机实际监听端口拼回 origin，否则图生图上传直接报错。"""
    captured: dict = {}

    class _StreamResp:
        headers = {"content-type": "image/png"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield b"pngdata"

    class _StreamClient:
        def __init__(self, *args, **kwargs):
            captured["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, *args, **kwargs):
            captured["url"] = url
            return _StreamResp()

    monkeypatch.setattr(image_gen.httpx, "Client", _StreamClient)
    from app.config import BACKEND_BASE_URL

    data, _fn, _mime = image_gen._load_image_bytes(
        "/api/comfyui/local-view?path=C%3A%5Ctest.png",
        proxy="http://127.0.0.1:7897",
    )

    assert captured["url"] == f"{BACKEND_BASE_URL}/api/comfyui/local-view?path=C%3A%5Ctest.png"
    assert "proxy" not in captured["client_kwargs"]  # 本机地址仍绕开代理
    assert data == b"pngdata"


def test_load_image_bytes_external_url_keeps_explicit_proxy(monkeypatch):
    """外部图片 URL 仍走显式代理（中转通路依赖它）。"""
    captured = {}

    class _StreamResp:
        headers = {"content-type": "image/jpeg"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            return iter([b"jpgdata"])

    class _StreamClient:
        def __init__(self, *args, **kwargs):
            captured["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return _StreamResp()

    monkeypatch.setattr(image_gen.httpx, "Client", _StreamClient)

    data, fn, mime = image_gen._load_image_bytes(
        "https://img.test/ref.png",
        proxy="http://127.0.0.1:7897",
    )

    assert data == b"jpgdata"
    assert captured["client_kwargs"]["proxy"] == "http://127.0.0.1:7897"


def test_build_http_client_空代理不传proxy键(monkeypatch):
    """F 刀（2026-10-24）：`**client_kwargs` dict 拆包是 77 个 mypy 错的共同根因，修法是
    直接传参；空代理必须**不传 proxy 键**（不能传 None）——local-view 绕过显式代理的语义由
    test_load_image_bytes_local_view_bypasses_explicit_proxy 锁，这里锁 helper 本身。"""
    made = []
    monkeypatch.setattr(image_gen.httpx, "Client", lambda **kw: made.append(kw))

    image_gen.build_http_client("")
    image_gen.build_http_client("http://127.0.0.1:7897", 42.0)

    assert made[0] == {"trust_env": False, "timeout": 120}
    assert made[1] == {"trust_env": False, "timeout": 42.0, "proxy": "http://127.0.0.1:7897"}
