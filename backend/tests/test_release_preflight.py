import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "release_preflight", ROOT / "scripts" / "release_preflight.py"
)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def test_release_preflight_current_tree_is_closed():
    assert preflight.validate(ROOT) == []
    assert preflight.offline_npm_dependency_error(ROOT) is None


@pytest.mark.skipif(sys.platform != "win32", reason="vendor/pip 是 Windows wheel 集")
def test_windows_offline_dependency_tree_is_closed():
    assert preflight.offline_dependency_error(ROOT) is None


def test_release_preflight_detects_missing_css_asset(tmp_path):
    css = tmp_path / "styles.css"
    public = tmp_path / "public"
    public.mkdir()
    css.write_text('a { background: url("/controls/test/missing.png"); }', encoding="utf-8")
    assert preflight.missing_css_assets(css, public) == ["controls/test/missing.png"]


def test_release_preflight_strips_requirement_extras_and_comments(tmp_path):
    requirements = tmp_path / "requirements.txt"
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    requirements.write_text("uvicorn[standard]\nrank_bm25 # sparse\n", encoding="utf-8")
    (vendor / "uvicorn-1.0-py3-none-any.whl").touch()
    (vendor / "rank_bm25-0.2-py3-none-any.whl").touch()
    assert preflight.missing_vendor_distributions(requirements, vendor) == []


def test_release_preflight_detects_ignored_release_file(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    ignored = tmp_path / "frontend" / "public" / "controls" / "green" / "button.png"
    ignored.parent.mkdir(parents=True)
    ignored.touch()
    (tmp_path / ".gitignore").write_text("frontend/public/controls/green/\n", encoding="utf-8")

    assert preflight.ignored_release_paths(tmp_path, {ignored}) == [
        "frontend/public/controls/green/button.png"
    ]
