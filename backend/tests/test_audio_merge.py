from pathlib import Path

from app.services import audio_merge, chat_snapshot


def test_local_path_from_url_解析local_view路径():
    url = "http://127.0.0.1:8010/api/comfyui/local-view?path=D%3A%5Ctool%5Ctest%5Ca.flac"
    # 文件不存在时返回 None（仅 local-view 且文件存在才可用）
    assert audio_merge.local_path_from_url(url) is None
    # 非 local-view 一律拒绝
    assert audio_merge.local_path_from_url("http://x/api/comfyui/view?filename=a.flac") is None
    assert audio_merge.local_path_from_url("file:///etc/passwd") is None


def test_local_path_from_url_非音频扩展名拒绝(monkeypatch, tmp_path):
    png = tmp_path / "a.png"
    png.write_bytes(b"x")
    audio_merge.local_path_from_url.cache_clear() if hasattr(audio_merge.local_path_from_url, "cache_clear") else None
    url = f"http://127.0.0.1:8010/api/comfyui/local-view?path={png.as_posix()}"
    assert audio_merge.local_path_from_url(url) is None


def test_concat_audio_调用ffmpeg_concat(monkeypatch, tmp_path):
    calls = []
    def fake_run(cmd, capture_output=True, text=True, timeout=300):
        # 读取 concat list 内容（临时目录在返回后即清理）
        list_path = cmd[cmd.index("-i") + 1]
        calls.append({"cmd": cmd, "list": Path(list_path).read_text(encoding="utf-8")})
        output = cmd[-1]
        Path(output).write_bytes(b"merged")
        class _Proc:
            returncode = 0
            stderr = ""
        return _Proc()
    monkeypatch.setattr(audio_merge.subprocess, "run", fake_run)
    out = tmp_path / "merged.flac"
    audio_merge.concat_audio(
        ["D:/tool/a.flac", "D:/tool/b.flac"], out, ffmpeg="ffmpeg",
    )
    assert calls, "应调用 subprocess.run"
    cmd = calls[0]["cmd"]
    assert cmd[0] == "ffmpeg"
    assert "-c" in cmd and "copy" in cmd
    list_content = calls[0]["list"]
    assert "a.flac" in list_content and "b.flac" in list_content
    assert list_content.index("a.flac") < list_content.index("b.flac")


def test_merge_audio_for_message_按seq排序并幂等(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    from app.services import repo_meta
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")

    for name in ("seg2.flac", "seg1.flac"):
        (tmp_path / name).write_bytes(b"x" * 10)

    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "正文", "parts": [
            {"type": "audio", "url": f"http://127.0.0.1:8010/api/comfyui/local-view?path={tmp_path.as_posix()}/seg2.flac",
             "slotId": "audio-bot-1", "status": "ready", "kind": "audio", "speaker": "A", "seq": 2, "total": 2},
            {"type": "audio", "url": f"http://127.0.0.1:8010/api/comfyui/local-view?path={tmp_path.as_posix()}/seg1.flac",
             "slotId": "audio-bot-0", "status": "ready", "kind": "audio", "speaker": "A", "seq": 1, "total": 2},
        ],
    }])

    merged_paths = []
    def fake_concat(paths, output, ffmpeg=None):
        merged_paths.append(list(paths))
        output.write_bytes(b"merged")
    monkeypatch.setattr(audio_merge, "concat_audio", fake_concat)

    url1 = audio_merge.merge_audio_for_message("thread", "bot")
    assert merged_paths and merged_paths[0][0].endswith("seg1.flac")
    assert merged_paths[0][1].endswith("seg2.flac")

    # 幂等：第二次直接返回同一 URL，不重复拼接
    merged_paths.clear()
    url2 = audio_merge.merge_audio_for_message("thread", "bot")
    assert url1 == url2
    assert merged_paths == []

    # 快照已追加 merged part（完整版）
    parts = chat_snapshot.load("thread")[0]["parts"]
    merged = [p for p in parts if p.get("slotId", "").startswith("merged-")]
    assert len(merged) == 1
    assert merged[0]["type"] == "audio" and merged[0]["speaker"] == "完整版"


def test_merge_audio_for_message_段数不足报错(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    from app.services import repo_meta
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    seg = tmp_path / "seg.flac"
    seg.write_bytes(b"x" * 10)
    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "正文", "parts": [
            {"type": "audio", "url": f"http://127.0.0.1:8010/api/comfyui/local-view?path={seg.as_posix()}",
             "slotId": "audio-bot-0", "status": "ready", "kind": "audio", "seq": 1, "total": 1},
        ],
    }])
    import pytest
    with pytest.raises(ValueError):
        audio_merge.merge_audio_for_message("thread", "bot")


def test_find_ffmpeg_在父目录python环境命中(monkeypatch, tmp_path):
    """便携版 ffmpeg 装在 <根>/python/... 而 config.path 指向 <根>/ComfyUI，
    必须搜父目录才能命中（回归：ffmpeg 定位层级错误导致拼接失败）。"""
    monkeypatch.setattr(audio_merge.shutil, "which", lambda name: None)
    # 构造 <root>/ComfyUI 主目录 + <root>/python/.../ffmpeg.exe
    root = tmp_path / "ComfyUI_aaaki"
    comfy_dir = root / "ComfyUI"
    ffmpeg_bin = root / "python" / "Lib" / "site-packages" / "imageio_ffmpeg" / "binaries" / "ffmpeg.exe"
    comfy_dir.mkdir(parents=True)
    ffmpeg_bin.parent.mkdir(parents=True)
    ffmpeg_bin.write_bytes(b"x")
    monkeypatch.setattr(
        audio_merge.comfy_launcher, "load_config",
        lambda: {"path": str(comfy_dir), "url": "", "python_path": ""},
    )

    exe = audio_merge.find_ffmpeg()
    assert exe and exe.lower().endswith("ffmpeg.exe")


def test_find_ffmpeg_无配置返回None(monkeypatch):
    monkeypatch.setattr(audio_merge.shutil, "which", lambda name: None)
    monkeypatch.setattr(audio_merge.comfy_launcher, "load_config", lambda: {"path": "", "url": ""})
    assert audio_merge.find_ffmpeg() is None
