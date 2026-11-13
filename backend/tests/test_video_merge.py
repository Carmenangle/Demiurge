"""全局模式视频拼接服务测试（ffmpeg 全 mock，不依赖本机二进制）。"""

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from app.services import video_merge


def _local_view(path: Path) -> str:
    return f"/api/comfyui/local-view?path={quote(str(path))}&type=output"


def test_local_path_from_media_url(tmp_path):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"video")
    assert video_merge.local_path_from_media_url(_local_view(clip)) == clip
    assert video_merge.local_path_from_media_url(str(clip)) == clip
    assert video_merge.local_path_from_media_url("https://example.com/a.mp4") is None
    assert video_merge.local_path_from_media_url("") is None


def test_merge_videos_same_params_uses_copy(monkeypatch, tmp_path):
    clips = []
    for index in range(2):
        clip = tmp_path / f"c{index}.mp4"
        clip.write_bytes(b"video")
        clips.append(clip)
    monkeypatch.setattr(video_merge, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(
        video_merge, "_probe",
        lambda _ff, _path: {"codec": "h264", "width": 720, "height": 1280,
                            "fps": 24.0, "duration": 4.0},
    )
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"merged")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(video_merge.subprocess, "run", fake_run)
    result = video_merge.merge_videos(
        [_local_view(path) for path in clips],
        output_dir=str(tmp_path), repo_id="r1", sequence_id="seq",
    )
    assert result["ok"] is True
    assert result["url"]
    assert Path(result["path"]).is_file()
    assert "-c" in calls[0] and "copy" in calls[0]
    assert any("暂不带音轨" in warning for warning in result["warnings"])


def test_merge_videos_different_params_reencodes(monkeypatch, tmp_path):
    clips = []
    for index in range(2):
        clip = tmp_path / f"c{index}.mp4"
        clip.write_bytes(b"video")
        clips.append(clip)
    probes = iter([
        {"codec": "h264", "width": 720, "height": 1280, "fps": 24.0, "duration": 4.0},
        {"codec": "hevc", "width": 1280, "height": 720, "fps": 30.0, "duration": 3.0},
        {"codec": "h264", "width": 720, "height": 1280, "fps": 24.0, "duration": 7.0},
    ])
    monkeypatch.setattr(video_merge, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(video_merge, "_probe", lambda _ff, _path: next(probes))
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"merged")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(video_merge.subprocess, "run", fake_run)
    result = video_merge.merge_videos(
        [_local_view(path) for path in clips],
        output_dir=str(tmp_path), repo_id="r1", sequence_id="seq2",
    )
    assert result["ok"] is True
    assert "libx264" in calls[0]
    assert any("scale=720:1280" in item for item in calls[0])
    assert any("重编码" in warning for warning in result["warnings"])


def test_merge_videos_is_idempotent_without_force(monkeypatch, tmp_path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"video")
    monkeypatch.setattr(video_merge, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(video_merge.repo_meta, "repo_folder", lambda out, _rid: Path(out))

    def fail_if_run(*_args, **_kwargs):
        raise AssertionError("幂等命中时不应再调 ffmpeg")

    monkeypatch.setattr(video_merge.subprocess, "run", fail_if_run)
    out_dir = tmp_path / "videos"
    out_dir.mkdir()
    (out_dir / "shot_sequence_seq3.mp4").write_bytes(b"old")
    result = video_merge.merge_videos(
        [_local_view(clip)], output_dir=str(tmp_path), repo_id="r1", sequence_id="seq3",
    )
    assert result["ok"] is True
    assert "复用" in result["warnings"][0]


def test_merge_videos_rejects_remote_and_missing_ffmpeg(monkeypatch, tmp_path):
    result = video_merge.merge_videos(
        ["https://example.com/a.mp4"], output_dir=str(tmp_path), repo_id="r1",
        sequence_id="seq4",
    )
    assert result["ok"] is False
    assert "本地成片地址" in result["error"]

    monkeypatch.setattr(video_merge, "find_ffmpeg", lambda: None)
    result = video_merge.merge_videos(
        ["/not/exist.mp4"], output_dir=str(tmp_path), repo_id="r1", sequence_id="seq5",
    )
    assert result["ok"] is False
    assert "ffmpeg" in result["error"]
