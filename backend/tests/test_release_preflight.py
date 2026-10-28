import importlib.util
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "release_preflight", ROOT / "scripts" / "release_preflight.py"
)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def test_release_preflight_current_tree_is_safe():
    assert preflight.validate(ROOT) == []


def test_private_directories_are_ignored():
    for path in (
        "backend/data/test.json",
        "userdata/chat.json",
        "presets/private.json",
        "docs/memory/private.md",
        "docs/handoffs/private.md",
        "docs/合集卡分析.md",
        "HANDOFF-2026-09-12-prompt-cache.md",
    ):
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", path], cwd=ROOT, check=False
        )
        assert result.returncode == 0, path


def test_release_preflight_detects_missing_css_asset(tmp_path):
    css = tmp_path / "styles.css"
    public = tmp_path / "public"
    public.mkdir()
    css.write_text('a { background: url("/controls/missing.png"); }', encoding="utf-8")

    assert preflight.missing_css_assets(css, public) == ["controls/missing.png"]


def test_release_preflight_detects_secret_candidate(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    fake_token = "sk-" + "1234567890abcdefghijklmnop"
    (tmp_path / "README.md").write_text(
        f"token={fake_token}", encoding="utf-8"
    )

    paths = preflight.candidate_paths(tmp_path)
    assert paths == [tmp_path / "README.md"]
    assert preflight._contains_secret(paths[0])


def test_bin_ban_spares_tracked_shell_fixture(tmp_path):
    """.bin 禁令针对模型权重；桌面壳签名夹具例外，但其它 .bin 仍必须被拦。"""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    fixture = tmp_path / "desktop" / "tauri" / "testdata" / "updater-fixture.bin"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(b"fixture\n")
    stray = tmp_path / "models" / "weights.bin"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"weights")

    errors = preflight.validate(tmp_path)

    assert "禁止上传的运行文件: models/weights.bin" in errors
    assert not [e for e in errors if "updater-fixture.bin" in e]


def test_private_name_denylist_is_ignored(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("backend/data/\n", encoding="utf-8")
    denylist = tmp_path / "backend" / "data" / "privacy_denylist.txt"
    denylist.parent.mkdir(parents=True)
    denylist.write_text(
        "# 注释行\n\n某个很长的真实作品标题\n某个真实作品标题\n某个真实作品标题\n",
        encoding="utf-8",
    )

    assert preflight.privacy_denylist(tmp_path) == ["某个很长的真实作品标题", "某个真实作品标题"]
    assert preflight.candidate_paths(tmp_path) == [tmp_path / ".gitignore"]
    assert subprocess.run(
        ["git", "check-ignore", "-q", "--", str(denylist)], cwd=tmp_path, check=False
    ).returncode == 0


def test_private_name_leak_blocks_upload_surface(tmp_path):
    leaked = tmp_path / "ARCHITECTURE.md"
    leaked.write_text("作品《某个真实作品标题》的世界书注入", encoding="utf-8")
    clean = tmp_path / "README.md"
    clean.write_text("作品《虚构占位标题》的世界书注入", encoding="utf-8")

    errors = preflight.private_name_leaks(
        tmp_path, [leaked, clean], ["某个真实作品标题"]
    )

    assert errors == ["用户真实专名出现在上传面: ARCHITECTURE.md → 某个真实作品标题"]


def test_current_tree_has_no_private_name():
    names = preflight.privacy_denylist(ROOT)
    if not names:
        # 专名表是 gitignored 本机运行态文件；CI 干净检出没有它，闸自然不生效（不得因此判红）。
        pytest.skip("本机缺少 backend/data/privacy_denylist.txt，无法校验真实专名")
    assert preflight.private_name_leaks(ROOT, preflight.candidate_paths(ROOT), names) == []
