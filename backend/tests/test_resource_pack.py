import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "build_resource_pack", ROOT / "scripts" / "build_resource_pack.py"
)
assert SPEC and SPEC.loader
resource_pack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resource_pack)


def test_sanitize_recursively_removes_credentials():
    source = {
        "api_key": "private",
        "prompts": [{"content": "keep", "custom_include_headers": {"Authorization": "x"}}],
    }

    assert resource_pack.sanitize(source) == {"prompts": [{"content": "keep"}]}


def test_build_resource_pack_from_clean_fixture(tmp_path):
    source = tmp_path / "source"
    output = tmp_path / "output"
    preset = source / "presets" / "GrayWill-0.46-demiurge.json"
    regex = source / "backend" / "data" / "regex_scripts.json"
    preset.parent.mkdir(parents=True)
    regex.parent.mkdir(parents=True)
    preset.write_text(json.dumps({
        "api_key": "private",
        "extensions": {"regex_scripts": [{"name": "embedded"}]},
    }), encoding="utf-8")
    regex.write_text(json.dumps([{"name": "global"}]), encoding="utf-8")

    files = resource_pack.build(source, output)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert len(files) == 5
    assert manifest["embedded_regex_count"] == 1
    assert manifest["global_regex_count"] == 1
    built_preset = json.loads(
        (output / "preset" / "GrayWill-0.46-demiurge.json").read_text(encoding="utf-8")
    )
    assert "api_key" not in built_preset


def test_committed_resource_pack_is_complete():
    package = ROOT / "presets" / "Demiurge-presets-regex"
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["embedded_regex_count"] == 33
    assert manifest["global_regex_count"] == 24
    assert all((package / item["path"]).is_file() for item in manifest["files"])
