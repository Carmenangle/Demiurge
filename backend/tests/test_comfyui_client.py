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


def test_fetch_result_audio_output_reclassified(monkeypatch):
    """音频节点产物：outputs.audio 键（wav/mp3/flac…）→ audios；images 里的音频扩展名也归 audio。"""
    hist = {"pid": {"status": {"completed": True}, "outputs": {
        "5": {"audio": [{"filename": "voice.wav", "subfolder": "", "type": "output"}]},
        "6": {"images": [{"filename": "music.mp3", "subfolder": "", "type": "output"}]},
        "7": {"images": [{"filename": "cover.png", "subfolder": "", "type": "output"}]},
    }}}
    monkeypatch.setattr(comfyui_client, "urlopen", lambda *a, **k: _FakeResp(hist))

    r = comfyui_client.fetch_result("http://127.0.0.1:8188", "pid")

    assert [audio["filename"] for audio in r["audios"]] == ["voice.wav", "music.mp3"]
    assert [image["filename"] for image in r["images"]] == ["cover.png"]


def test_fetch_result_audio_filter_mismatch_falls_back(monkeypatch):
    """音频节点同理：filter 匹配不到 → 全量收集 audios。"""
    hist = {"pid": {"status": {"completed": True}, "outputs": {
        "24": {"audio": [{"filename": "narr.wav", "subfolder": "", "type": "output"}]},
    }}}
    monkeypatch.setattr(comfyui_client, "urlopen", lambda *a, **k: _FakeResp(hist))

    r = comfyui_client.fetch_result("http://127.0.0.1:8188", "pid", ["42"])

    assert r["status"] == "completed"
    assert [audio["filename"] for audio in r["audios"]] == ["narr.wav"]


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


def _queue_fake(url, timeout=None):
    # 历史为空、队列区分 running/pending，模拟「有没有动弹」的信号来源
    if "/queue" in url:
        return _FakeResp({
            "queue_running": [[0, {"prompt_id": "run-pid"}]],
            "queue_pending": [[0, "wait-pid"]],
        })
    return _FakeResp({})


def test_fetch_result_running_when_in_running_queue(monkeypatch):
    monkeypatch.setattr(comfyui_client, "urlopen", _queue_fake)
    r = comfyui_client.fetch_result("http://127.0.0.1:8188", "run-pid")
    assert r["status"] == "running"


def test_fetch_result_pending_when_only_queued(monkeypatch):
    monkeypatch.setattr(comfyui_client, "urlopen", _queue_fake)
    r = comfyui_client.fetch_result("http://127.0.0.1:8188", "wait-pid")
    assert r["status"] == "pending"


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


def test_fetch_result_filter_node_mismatch_falls_back_to_all_outputs(monkeypatch):
    """模板 primary_output_node_id 与实际 SaveImage 节点不一致（filter 匹配不到任何产物）
    → 兜底全量收集，避免 completed 却拿空结果（结果不进对话/资产库）。"""
    hist = {"pid": {"status": {"completed": True}, "outputs": {
        "24": {"images": [{"filename": "saved.png", "subfolder": "", "type": "output"}]},
    }}}
    monkeypatch.setattr(comfyui_client, "urlopen", lambda *a, **k: _FakeResp(hist))

    r = comfyui_client.fetch_result("http://127.0.0.1:8188", "pid", ["999"])

    assert r["status"] == "completed"
    assert [image["filename"] for image in r["images"]] == ["saved.png"]


def test_fetch_result_filter_mismatch_video_falls_back(monkeypatch):
    """视频节点同理：filter 匹配不到 → 全量收集 videos。"""
    hist = {"pid": {"status": {"completed": True}, "outputs": {
        "24": {"gifs": [{"filename": "movie.mp4", "subfolder": "", "type": "output"}]},
    }}}
    monkeypatch.setattr(comfyui_client, "urlopen", lambda *a, **k: _FakeResp(hist))

    r = comfyui_client.fetch_result("http://127.0.0.1:8188", "pid", ["42"])

    assert r["status"] == "completed"
    assert [video["filename"] for video in r["videos"]] == ["movie.mp4"]


def test_fetch_result_filter_mismatch_skips_temp_preview(monkeypatch):
    """兜底全量收集时必须过滤 temp 预览图（只返回 SaveImage 的 output 产物）。"""
    hist = {"pid": {"status": {"completed": True}, "outputs": {
        "3": {"images": [{"filename": "preview.png", "subfolder": "", "type": "temp"}]},
        "24": {"images": [{"filename": "saved.png", "subfolder": "", "type": "output"}]},
    }}}
    monkeypatch.setattr(comfyui_client, "urlopen", lambda *a, **k: _FakeResp(hist))

    r = comfyui_client.fetch_result("http://127.0.0.1:8188", "pid", ["999"])

    assert [image["filename"] for image in r["images"]] == ["saved.png"]
