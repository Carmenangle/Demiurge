"""renderer 适配器：云端直返 url、ComfyUI 提交+轮询取图、超时/丢失抛错、/view 拼接。"""
from __future__ import annotations

import pytest

from app.services import scene_renderers as sr
from app.services.scene_illustration import SceneRequest


def test_云端renderer透传config直返url(monkeypatch):
    seen = {}

    def fake_generate(base, key, model, prompt, *, size, quality):
        seen.update(base=base, model=model, prompt=prompt, size=size, quality=quality)
        return "https://img/x.png"

    monkeypatch.setattr(sr.image_gen, "generate", fake_generate)
    r = sr.cloud_renderer(sr.CloudConfig("http://api", "k", "dall-e", size="512x512", quality="low"))
    assert r(SceneRequest(prompt="她俯身")) == "https://img/x.png"
    assert seen == {"base": "http://api", "model": "dall-e", "prompt": "她俯身",
                    "size": "512x512", "quality": "low"}


def test_云端有角色底图走图生图(monkeypatch):
    seen = {}

    def fake_with_images(base, key, model, prompt, *, images, size, quality):
        seen.update(images=images, prompt=prompt)
        return "https://img/edit.png"

    monkeypatch.setattr(sr.image_gen, "generate_with_images", fake_with_images)
    cfg = sr.CloudConfig("http://api", "k", "gpt-image-1",
                         character_base_images={"爱丽丝": "/data/alice.png"})
    r = sr.cloud_renderer(cfg)
    assert r(SceneRequest(prompt="她俯身", actors=["爱丽丝"])) == "https://img/edit.png"
    assert seen == {"images": ["/data/alice.png"], "prompt": "她俯身"}


def test_云端在场角色无底图回退风格底图(monkeypatch):
    seen = {}
    monkeypatch.setattr(sr.image_gen, "generate_with_images",
                        lambda *a, **k: seen.update(images=k["images"]) or "u")
    cfg = sr.CloudConfig("http://api", "k", "gpt-image-1",
                         character_base_images={"鲍勃": "/data/bob.png"},
                         style_base_image="/data/style.png")
    sr.cloud_renderer(cfg)(SceneRequest(prompt="p", actors=["爱丽丝"]))
    assert seen == {"images": ["/data/style.png"]}  # 未命中角色 → 风格底图


def test_云端无任何底图仍纯文生图(monkeypatch):
    monkeypatch.setattr(sr.image_gen, "generate", lambda *a, **k: "https://txt/x.png")
    cfg = sr.CloudConfig("http://api", "k", "gpt-image-1")
    assert sr.cloud_renderer(cfg)(SceneRequest(prompt="p", actors=["爱丽丝"])) == "https://txt/x.png"


def test_comfy提交后轮询到完成取图(monkeypatch):
    monkeypatch.setattr(sr.workflow_submission, "submit_template",
                        lambda *a, **k: {"ok": True, "prompt_id": "pid1"})
    calls = {"n": 0}

    def fake_fetch(url, pid):
        calls["n"] += 1
        if calls["n"] < 2:
            return {"status": "pending", "images": []}
        return {"status": "completed",
                "images": [{"filename": "a.png", "subfolder": "sub", "type": "output"}]}

    monkeypatch.setattr(sr.comfyui_client, "fetch_result", fake_fetch)
    r = sr.comfy_renderer(sr.ComfyConfig("http://comfy:8188", "tpl"),
                          sleep=lambda s: None, now=lambda: 0.0)
    url = r(SceneRequest(prompt="p"))
    assert url == "http://comfy:8188/view?filename=a.png&subfolder=sub&type=output"
    assert calls["n"] == 2  # 轮询了两次


def test_comfy任务丢失抛错(monkeypatch):
    monkeypatch.setattr(sr.workflow_submission, "submit_template",
                        lambda *a, **k: {"prompt_id": "pid"})
    monkeypatch.setattr(sr.comfyui_client, "fetch_result",
                        lambda u, p: {"status": "not_found", "images": []})
    r = sr.comfy_renderer(sr.ComfyConfig("http://c", "tpl"), sleep=lambda s: None, now=lambda: 0.0)
    with pytest.raises(RuntimeError, match="丢失"):
        r(SceneRequest(prompt="p"))


def test_comfy超时抛错(monkeypatch):
    monkeypatch.setattr(sr.workflow_submission, "submit_template",
                        lambda *a, **k: {"prompt_id": "pid"})
    monkeypatch.setattr(sr.comfyui_client, "fetch_result",
                        lambda u, p: {"status": "pending", "images": []})
    clock = {"t": 0.0}

    def fake_now():
        clock["t"] += 10.0  # 每次查询推进 10s，很快越过 timeout
        return clock["t"]

    r = sr.comfy_renderer(sr.ComfyConfig("http://c", "tpl", poll_timeout=30.0),
                          sleep=lambda s: None, now=fake_now)
    with pytest.raises(TimeoutError, match="超时"):
        r(SceneRequest(prompt="p"))


def test_无promptid抛错(monkeypatch):
    monkeypatch.setattr(sr.workflow_submission, "submit_template", lambda *a, **k: {"ok": True})
    r = sr.comfy_renderer(sr.ComfyConfig("http://c", "tpl"), sleep=lambda s: None, now=lambda: 0.0)
    with pytest.raises(RuntimeError, match="prompt_id"):
        r(SceneRequest(prompt="p"))
