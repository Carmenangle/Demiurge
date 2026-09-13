"""video_gen 单测：URL 由用户决定（原样使用）+ 发送参数参照图像模型（JSON 文生 / multipart 图生）。"""

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


# ===== 发送参数：参照图像模型 =====
# 文生视频 generate → JSON（json=payload，Content-Type: application/json）
# 图生视频 generate_with_images → multipart（data=payload + files=image[]，不设 Content-Type）

class _FakeResp:
    def __init__(self, body, status_code=200):
        self.status_code = status_code
        self.text = ""
        self._body = body

    def json(self):
        return self._body


class _FakeClient:
    """记录 post/get 调用；post 返回任务 id，get 返回成功视频 URL。"""

    def __init__(self, calls: dict, **kwargs):
        self.calls = calls
        self.calls["client_kwargs"] = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers, **kwargs):
        self.calls["post"] = {"url": url, "headers": headers, **kwargs}
        return _FakeResp({"id": "task-1"})

    def get(self, url, headers):
        self.calls["get"] = {"url": url, "headers": headers}
        return _FakeResp({"status": "succeeded", "video_url": "http://example.com/v.mp4"})


def _fake_client(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(video_gen.httpx, "Client", lambda **kw: _FakeClient(calls, **kw))
    return calls


def test_generate_submits_json_payload(monkeypatch):
    calls = _fake_client(monkeypatch)
    out = video_gen.generate("https://ai.t8star.org/v2/videos/generations", "k", "m", "一只猫")
    assert out == "http://example.com/v.mp4"
    post = calls["post"]
    assert post["url"] == "https://ai.t8star.org/v2/videos/generations"
    assert post["json"] == {"model": "m", "prompt": "一只猫", "size": "1024x1024"}
    assert post["headers"]["Content-Type"] == "application/json"
    assert "files" not in post
    # 异步轮询地址 = 用户端点 + /task_id
    assert calls["get"]["url"] == "https://ai.t8star.org/v2/videos/generations/task-1"


def test_generate_with_images_submits_multipart_like_image_gen(monkeypatch):
    calls = _fake_client(monkeypatch)
    out = video_gen.generate_with_images(
        "https://x.com/v2/videos/generations", "k", "m", "猫在奔跑",
        ["data:image/png;base64,AAAA"],
    )
    assert out == "http://example.com/v.mp4"
    post = calls["post"]
    assert post["url"] == "https://x.com/v2/videos/generations"
    # form 字段与图像模型一致：model/prompt/size，无 image 内联键
    assert post["data"] == {"model": "m", "prompt": "猫在奔跑", "size": "1024x1024"}
    # 图片走 multipart image[] 同名多图
    assert post["files"][0][0] == "image[]"
    _name, blob, mime = post["files"][0][1]
    assert blob == b"\x00\x00\x00"  # "AAAA" base64 解码
    assert mime == "image/png"
    # multipart 不设 Content-Type（httpx 自动带 boundary）
    assert "Content-Type" not in post["headers"]


def test_generate_with_images_supports_multiple_images(monkeypatch):
    calls = _fake_client(monkeypatch)
    video_gen.generate_with_images(
        "https://x.com/v2/videos/generations", "k", "m", "p",
        ["data:image/png;base64,AAAA", "data:image/png;base64,AAAB"],
    )
    files = calls["post"]["files"]
    assert len(files) == 2
    assert all(f[0] == "image[]" for f in files)


def test_generate_with_images_requires_images(monkeypatch):
    _fake_client(monkeypatch)
    with pytest.raises(ValueError, match="至少一张参考图"):
        video_gen.generate_with_images("https://x.com/v2/videos/generations", "k", "m", "p", [])
