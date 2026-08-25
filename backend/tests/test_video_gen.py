"""video_gen 单测：URL 由用户决定（原样使用，不猜版本/单复数）+ V1.4 首帧参考图注入。"""

import pytest

from app.services import video_gen


# ===== _norm_url：URL 靠用户决定，代码原样使用 =====

def test_norm_url_used_as_is_no_guessing():
    # 无论 v1/v2、单数/复数、自定义路径——用户填什么就是最终提交地址
    assert video_gen._norm_url("https://ai.t8star.org/v2/videos/generations") \
        == "https://ai.t8star.org/v2/videos/generations"
    assert video_gen._norm_url("https://x.com/v1/video/generations") \
        == "https://x.com/v1/video/generations"
    assert video_gen._norm_url("https://api.seedance.tv/v1") \
        == "https://api.seedance.tv/v1"
    assert video_gen._norm_url("https://x.com/v1/videos/generations") \
        == "https://x.com/v1/videos/generations"
    assert video_gen._norm_url("https://x.com/custom/path") \
        == "https://x.com/custom/path"
    assert video_gen._norm_url("") == ""


def test_norm_task_url_appends_task_id_to_user_url():
    assert video_gen._norm_task_url("https://ai.t8star.org/v2/videos/generations", "t1") \
        == "https://ai.t8star.org/v2/videos/generations/t1"
    assert video_gen._norm_task_url("https://api.seedance.tv/v1", "t1") \
        == "https://api.seedance.tv/v1/t1"
    assert video_gen._norm_task_url("https://x.com/v1/videos/generations", "t1") \
        == "https://x.com/v1/videos/generations/t1"


# ===== _to_data_uri：参考图归一 =====

def test_to_data_uri_passthrough_and_local_file(tmp_path):
    assert video_gen._to_data_uri("data:image/png;base64,AAAA") == "data:image/png;base64,AAAA"
    f = tmp_path / "a.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\x0a")
    uri = video_gen._to_data_uri(str(f))
    assert uri.startswith("data:image/png;base64,")
    assert "iVBORw0KGgo" in uri  # PNG 魔数的 base64


def test_to_data_uri_rejects_unreadable_input():
    with pytest.raises(RuntimeError):
        video_gen._to_data_uri("not-a-uri-nor-a-file")


# ===== generate：V1.4 首帧参考图注入 =====

def test_generate_injects_image_field(monkeypatch):
    calls: dict = {}

    class FakeResp:
        def __init__(self, body):
            self.status_code = 200
            self._body = body

        def json(self):
            return self._body

    class FakeClient:
        def __init__(self, **kwargs):
            calls["kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            calls["url"] = url
            calls["json"] = json
            return FakeResp({"id": "t1"})

        def get(self, url, headers):
            return FakeResp({"status": "succeeded", "video_url": "http://example.com/v.mp4"})

    monkeypatch.setattr(video_gen.httpx, "Client", FakeClient)

    # 带首帧图 → payload 注入 image（data URI 原样透传）
    out = video_gen.generate(
        "https://ai.t8star.org/v2/videos/generations", "k", "m", "一只猫",
        image="data:image/png;base64,AAAA",
    )
    assert out == "http://example.com/v.mp4"
    assert calls["url"] == "https://ai.t8star.org/v2/videos/generations"
    assert calls["json"]["image"] == "data:image/png;base64,AAAA"

    # 不带图 → payload 无 image（纯文生视频）
    video_gen.generate("https://x.com/v1/video/generations", "k", "m", "一只猫")
    assert "image" not in calls["json"]
