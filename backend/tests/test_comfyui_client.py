import json

from app.services import comfyui_client


class _FakeResp:
    def __init__(self, payload: dict):
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._data


_HIST = {
    "pid": {
        "status": {"completed": True},
        "outputs": {
            "10": {"images": [{"filename": "mid.png", "subfolder": "", "type": "output"}]},
            "20": {
                "images": [{"filename": "final.png", "subfolder": "", "type": "output"}],
                "gifs": [{"filename": "anim.gif", "subfolder": "", "type": "output"}],
            },
        },
    }
}


def _patch(monkeypatch):
    monkeypatch.setattr(comfyui_client, "urlopen", lambda *a, **k: _FakeResp(_HIST))


def test_fetch_result_no_filter_returns_all(monkeypatch):
    _patch(monkeypatch)
    r = comfyui_client.fetch_result("http://127.0.0.1:8188", "pid")
    assert {i["filename"] for i in r["images"]} == {"mid.png", "final.png"}
    assert [v["filename"] for v in r["videos"]] == ["anim.gif"]


def test_fetch_result_prefers_saved_output_over_temporary_preview(monkeypatch):
    hist = {"pid": {"status": {"completed": True}, "outputs": {
        "2": {"images": [{"filename": "first-pass.png", "subfolder": "", "type": "temp"}]},
        "24": {"images": [{"filename": "final.png", "subfolder": "", "type": "output"}]},
    }}}
    monkeypatch.setattr(comfyui_client, "urlopen", lambda *a, **k: _FakeResp(hist))

    r = comfyui_client.fetch_result("http://127.0.0.1:8188", "pid")

    assert [image["filename"] for image in r["images"]] == ["final.png"]


def test_fetch_result_filter_keeps_only_primary_node(monkeypatch):
    _patch(monkeypatch)
    r = comfyui_client.fetch_result("http://127.0.0.1:8188", "pid", ["20"])
    assert [i["filename"] for i in r["images"]] == ["final.png"]
    assert [v["filename"] for v in r["videos"]] == ["anim.gif"]


def test_fetch_result_primary_node_temp_falls_back_to_final_saved_output(monkeypatch):
    hist = {"pid": {"status": {"completed": True}, "outputs": {
        "2": {"images": [{"filename": "first-pass.png", "subfolder": "", "type": "temp"}]},
        "24": {"images": [{"filename": "second-pass.png", "subfolder": "", "type": "output"}]},
    }}}
    monkeypatch.setattr(comfyui_client, "urlopen", lambda *a, **k: _FakeResp(hist))

    r = comfyui_client.fetch_result("http://127.0.0.1:8188", "pid", ["2"])

    assert [image["filename"] for image in r["images"]] == ["second-pass.png"]


def test_gif_in_images_reclassified_as_video(monkeypatch):
    hist = {"pid": {"status": {"completed": True}, "outputs": {
        "1": {"images": [{"filename": "clip.gif", "subfolder": "", "type": "output"}]},
    }}}
    monkeypatch.setattr(comfyui_client, "urlopen", lambda *a, **k: _FakeResp(hist))
    r = comfyui_client.fetch_result("http://127.0.0.1:8188", "pid")
    assert r["images"] == []
    assert [v["filename"] for v in r["videos"]] == ["clip.gif"]


def test_fetch_result_exposes_execution_error_as_failed(monkeypatch):
    hist = {"pid": {
        "status": {
            "completed": False,
            "status_str": "error",
            "messages": [["execution_error", {
                "node_id": "42",
                "node_type": "KSamplerAdvanced",
                "exception_message": "mat1 and mat2 shapes cannot be multiplied",
            }]],
        },
        "outputs": {},
    }}
    monkeypatch.setattr(comfyui_client, "urlopen", lambda *a, **k: _FakeResp(hist))

    r = comfyui_client.fetch_result("http://127.0.0.1:8188", "pid")

    assert r["status"] == "failed"
    assert r["error"] == (
        "KSamplerAdvanced (#42): mat1 and mat2 shapes cannot be multiplied"
    )


def test_local_comfyui_http_adapters_ignore_environment_proxies():
    assert comfyui_client._NO_PROXY_HANDLER.proxies == {}
    assert comfyui_client._DIRECT_SESSION.trust_env is False


def test_full_object_info_uses_short_lived_cache(monkeypatch):
    calls = []
    comfyui_client._OBJECT_INFO_CACHE.clear()
    monkeypatch.setattr(
        comfyui_client, "urlopen",
        lambda *args, **kwargs: calls.append((args, kwargs)) or _FakeResp({"NodeA": {}}),
    )

    first = comfyui_client.fetch_object_info("http://127.0.0.1:8188")
    second = comfyui_client.fetch_object_info("http://127.0.0.1:8188")

    assert first == second == {"NodeA": {}}
    assert len(calls) == 1


def test_force_refresh_bypasses_object_info_cache(monkeypatch):
    calls = []
    comfyui_client._OBJECT_INFO_CACHE.clear()

    def open_object_info(*_args, **_kwargs):
        calls.append(None)
        return _FakeResp({f"Node{len(calls)}": {}})

    monkeypatch.setattr(comfyui_client, "urlopen", open_object_info)
    comfyui_client.fetch_object_info("http://127.0.0.1:8188")
    refreshed = comfyui_client.fetch_object_info("http://127.0.0.1:8188", force=True)

    assert refreshed == {"Node2": {}}
    assert len(calls) == 2


def test_submit_prompt_允许复杂工作流校验超过十秒(monkeypatch):
    captured = {}

    def open_prompt(_request, *, timeout):
        captured["timeout"] = timeout
        return _FakeResp({"prompt_id": "prompt-1"})

    monkeypatch.setattr(comfyui_client, "urlopen", open_prompt)

    assert comfyui_client.submit_prompt("http://127.0.0.1:8188", {"1": {}}) == "prompt-1"
    assert captured["timeout"] >= 30
