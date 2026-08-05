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


def test_build_current_resource_pack(tmp_path):
    files = resource_pack.build(ROOT, tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))

    assert len(files) == 5
    assert manifest["embedded_regex_count"] == 33
    assert manifest["global_regex_count"] == 24
    assert (tmp_path / "preset" / "GrayWill-0.46-demiurge.json").is_file()
