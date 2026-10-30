import json

from app.services import comfyui_client, url_guard


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


# ===== submit_prompt 响应丢失救回（2026-08-29「提交了任务也出了图却无回填」） =====


def test_submit_prompt_rescues_task_when_response_lost_but_queued(monkeypatch):
    """POST /prompt 超时但任务已入队（请求实际到达 ComfyUI）→ 救回 prompt_id 而非报错。"""
    calls: list[str] = []

    captured: dict = {}

    def fake(url, timeout=None):
        u = getattr(url, "full_url", url)
        calls.append(u)
        if "/prompt" in u:
            # 响应在 ComfyUI 接收任务后被掐断；先记下提交时预生成的 prompt_id
            captured["pid"] = json.loads(url.data.decode("utf-8"))["prompt_id"]
            raise TimeoutError("timed out")
        # 队列响应动态回显提交的 id（真实 ComfyUI 收到的就是这个 id）
        return _FakeResp({"queue_running": [[0, {"prompt_id": captured.get("pid")}]], "queue_pending": []})

    monkeypatch.setattr(comfyui_client, "urlopen", fake)
    pid = comfyui_client.submit_prompt("http://127.0.0.1:8188", {"1": {}})
    assert pid
    assert calls[-1].endswith("/queue")  # 救回走了队列确认
    assert any("/queue" in u for u in calls)  # 救回走了队列确认


def test_submit_prompt_raises_when_response_lost_and_not_queued(monkeypatch):
    """超时且队列/历史都没有该任务 → 照常报错（真实提交失败）。"""
    def fake(url, timeout=None):
        if "/prompt" in getattr(url, "full_url", url):
            raise TimeoutError("timed out")
        return _FakeResp({})

    monkeypatch.setattr(comfyui_client, "urlopen", fake)
    try:
        comfyui_client.submit_prompt("http://127.0.0.1:8188", {"1": {}})
        raise AssertionError("should raise")
    except comfyui_client.ComfyError:
        pass


def test_submit_prompt_uses_client_generated_id(monkeypatch):
    """请求体带客户端预生成的 prompt_id（救回判定的前提），正常响应优先用服务端 id。"""
    seen: dict = {}

    def fake(url, timeout=None):
        if "/prompt" in getattr(url, "full_url", url):
            req = url  # urlopen 收到 Request 对象
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResp({"prompt_id": "server-pid"})
        return _FakeResp({})

    monkeypatch.setattr(comfyui_client, "urlopen", fake)
    pid = comfyui_client.submit_prompt("http://127.0.0.1:8188", {"1": {}})
    assert pid == "server-pid"
    assert isinstance(seen["body"].get("prompt_id"), str) and seen["body"]["prompt_id"]


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


# ── 第 59 刀（队列感知停顿）：任务排队时附队列活性 —— ComfyUI 有任务在执行 ⇒
#    队列在推进，前端停顿守卫据此豁免深队列等待，不再误杀批量生图。


def test_第59刀_排队且队列在推进时pending附queue_busy_true(monkeypatch):
    def fake_urlopen(req, *a, **k):
        url = str(getattr(req, "full_url", req))
        if "/history/" in url:
            return _FakeResp({})                       # 不在 history ⇒ 转查队列
        return _FakeResp({"queue_running": [{"prompt_id": "other"}],
                          "queue_pending": [{"prompt_id": "pid"}]})

    monkeypatch.setattr(comfyui_client, "urlopen", fake_urlopen)

    r = comfyui_client.fetch_result("http://127.0.0.1:8188", "pid")

    assert r["status"] == "pending"
    assert r["queue_busy"] is True


def test_第59刀_排队但队列空转时queue_busy_false(monkeypatch):
    def fake_urlopen(req, *a, **k):
        url = str(getattr(req, "full_url", req))
        if "/history/" in url:
            return _FakeResp({})
        return _FakeResp({"queue_running": [], "queue_pending": [{"prompt_id": "pid"}]})

    monkeypatch.setattr(comfyui_client, "urlopen", fake_urlopen)

    r = comfyui_client.fetch_result("http://127.0.0.1:8188", "pid")

    assert r["status"] == "pending"
    assert r["queue_busy"] is False


def test_提交超时后要在宽限窗口内轮询确认接单(monkeypatch):
    """2026-10-20 实锤（19:12:18 提交 / 19:13:10 失败，共 52s）：

    大型工作流在 ComfyUI 里做节点校验/模型加载时，`POST /prompt` 的响应会晚于
    客户端 30s 超时——请求其实已被受理、稍后照常出图。旧实现只在超时**那一刻**
    查一次队列：此刻任务还没进 queue ⇒ 查不到 ⇒ 判「提交失败」⇒ 前端删槽
    ⇒ 图生成了、却无处回填、也进不了资产库（用户实锤「明明出了图却没插入」）。

    现在改为宽限窗口内轮询：只要窗口内任一次探测到接单，就必须救回 prompt_id。
    """
    probes: list[float] = []

    def fake_prompt_accepted(url, prompt_id, timeout=8):
        probes.append(timeout)
        # 前两次「还没进队列」，第三次才被受理 —— 模拟校验耗时
        return len(probes) >= 3

    monkeypatch.setattr(comfyui_client, "_prompt_accepted", fake_prompt_accepted)
    monkeypatch.setattr(comfyui_client, "PROMPT_ACCEPT_POLL_INTERVAL", 0.0)

    assert comfyui_client._await_prompt_accepted("http://127.0.0.1:8188", "pid") is True
    assert len(probes) == 3
    # 轮询用短探测超时，否则单轮就把整个窗口吃光
    assert all(t == comfyui_client.PROMPT_ACCEPT_PROBE_TIMEOUT for t in probes)


def test_宽限窗口用尽仍未接单判失败(monkeypatch):
    """窗口内始终没接单 ⇒ 如实判失败（不无限等）。"""
    monkeypatch.setattr(comfyui_client, "_prompt_accepted", lambda *a, **k: False)
    monkeypatch.setattr(comfyui_client, "PROMPT_ACCEPT_POLL_INTERVAL", 0.0)
    monkeypatch.setattr(comfyui_client, "PROMPT_ACCEPT_GRACE_SECONDS", 0.05)

    assert comfyui_client._await_prompt_accepted("http://127.0.0.1:8188", "pid") is False


class TestIsUpDefaultPort:
    """云端 ComfyUI（2026-10-27）：https 缺省端口是 443，不是 8188。"""

    def _probe(self, monkeypatch, url: str) -> tuple[str, int]:
        seen: dict[str, tuple] = {}

        def fake_conn(address, timeout=None):
            seen["address"] = address
            raise OSError("closed")

        def fake_urlopen(*args, **kwargs):
            raise OSError("down")

        monkeypatch.setattr(comfyui_client.socket, "create_connection", fake_conn)
        monkeypatch.setattr(comfyui_client, "urlopen", fake_urlopen)
        assert comfyui_client.is_up(url) is False
        return seen["address"]

    def test_https_缺省端口探443(self, monkeypatch):
        monkeypatch.setattr(url_guard, "_COMFYUI_EXTRA", ["comfy.example.com"])
        assert self._probe(monkeypatch, "https://comfy.example.com") == ("comfy.example.com", 443)

    def test_http_缺省端口仍探8188(self, monkeypatch):
        assert self._probe(monkeypatch, "http://127.0.0.1") == ("127.0.0.1", 8188)

    def test_显式端口优先于协议缺省(self, monkeypatch):
        monkeypatch.setattr(url_guard, "_COMFYUI_EXTRA", ["comfy.example.com"])
        assert self._probe(monkeypatch, "https://comfy.example.com:8443") == (
            "comfy.example.com", 8443)

    def test_tcp_能连即视为已启动(self, monkeypatch):
        class _Sock:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(
            comfyui_client.socket, "create_connection", lambda *a, **k: _Sock())

        def boom(*args, **kwargs):
            raise AssertionError("TCP 已通不应再走 HTTP 探测")

        monkeypatch.setattr(comfyui_client, "urlopen", boom)
        assert comfyui_client.is_up("http://127.0.0.1:8188") is True


class _StreamResp:
    """流式响应替身：read(n) 逐块吐出，模拟 /view 大文件。"""

    def __init__(self, chunks: list[bytes], ctype: str = "video/mp4"):
        self._chunks = list(chunks)
        self.headers = {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


def test_fetch_view_to_file_流式落盘不整块读内存(monkeypatch, tmp_path):
    seen: list[str] = []

    def fake_urlopen(url, *a, **k):
        seen.append(url)
        return _StreamResp([b"chunk-1", b"chunk-2", b""])

    monkeypatch.setattr(comfyui_client, "urlopen", fake_urlopen)
    dest = tmp_path / "out.mp4"

    ctype = comfyui_client.fetch_view_to_file(
        "http://127.0.0.1:8188", "v.mp4", str(dest), type="output", subfolder="sub")

    assert ctype == "video/mp4"
    assert dest.read_bytes() == b"chunk-1chunk-2"
    assert "filename=v.mp4" in seen[0] and "subfolder=sub" in seen[0]


def test_fetch_view_to_file_白名单外主机仍按400拒绝(tmp_path):
    dest = tmp_path / "x.bin"

    try:
        comfyui_client.fetch_view_to_file("http://evil.example.com", "a.png", str(dest))
    except comfyui_client.ComfyError as exc:
        assert exc.status == 400
    else:
        raise AssertionError("白名单外主机必须被拒")
    assert not dest.exists()
