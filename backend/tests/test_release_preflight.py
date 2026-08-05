import importlib.util
import subprocess
from pathlib import Path


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
    for path in ("backend/data/test.json", "userdata/chat.json", "presets/private.json", "docs/memory/private.md"):
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
