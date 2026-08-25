"""video_gen 单测：URL 端点通用化（v1/v2、单复数、完整端点）+ V1.4 首帧参考图注入。"""

import pytest

from app.services import video_gen


# ===== _norm_url：适用于所有 OpenAI 兼容形态 =====

def test_norm_url_complete_endpoints_used_as_is():
    # 用户填完整端点（v1 单数 / v2 复数 / v1 复数）→ 原样使用，不猜版本
    assert video_gen._norm_url("https://ai.t8star.org/v2/videos/generations") \
        == "https://ai.t8star.org/v2/videos/generations"
    assert video_gen._norm_url("https://x.com/v1/video/generations") \
        == "https://x.com/v1/video/generations"
    assert video_gen._norm_url("https://x.com/v1/videos/generations") \
        == "https://x.com/v1/videos/generations"


def test_norm_url_version_prefix_maps_to_endpoint():
    # 填带版本前缀 → 拼对应端点（v2 → 复数 videos，v1 → 单数 video，向后兼容）
    assert video_gen._norm_url("https://ai.t8star.org/v2") \
        == "https://ai.t8star.org/v2/videos/generations"
    assert video_gen._norm_url("https://x.com/v1") \
        == "https://x.com/v1/video/generations"


def test_norm_url_bare_root_defaults_to_v1():
    # 纯站点根 → 默认 v1 布局（向后兼容；报错信息会提示填完整端点最稳）
    assert video_gen._norm_url("https://ai.t8star.org") \
        == "https://ai.t8star.org/v1/video/generations"


def test_norm_task_url_follows_same_rules():
    assert video_gen._norm_task_url("https://ai.t8star.org/v2/videos/generations", "t1") \
        == "https://ai.t8star.org/v2/videos/generations/t1"
    assert video_gen._norm_task_url("https://ai.t8star.org/v2", "t1") \
        == "https://ai.t8star.org/v2/videos/generations/t1"
    assert video_gen._norm_task_url("https://x.com", "t1") \
        == "https://x.com/v1/video/generations/t1"


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
