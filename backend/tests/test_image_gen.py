import httpx
import pytest

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
