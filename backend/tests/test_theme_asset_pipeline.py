import importlib.util
from pathlib import Path

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
PIPELINE_PATH = ROOT / "scripts" / "theme_asset_pipeline.py"
SPEC = importlib.util.spec_from_file_location("theme_asset_pipeline", PIPELINE_PATH)
assert SPEC and SPEC.loader
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)


def _targets(theme: str) -> set[str]:
    plan = pipeline.load_plan(ROOT / "scripts" / "theme_assets" / f"{theme}.json")
    return {step["target"] for step in plan["steps"]}


@pytest.mark.parametrize("theme", ["bright", "eye-care", "night", "green", "gray"])
def test_theme_manifests_cover_shared_frontend_slots(theme):
    targets = _targets(theme)
    for family in ("main", "secondary"):
        for state in ("default", "hover", "pressed", "disabled"):
            assert f"controls/{theme}/button-{family}-{state}.png" in targets
    for message in ("assistant", "user", "system"):
        assert f"controls/{theme}/message-{message}.png" in targets
    for composer in ("top", "handle", "divider"):
        assert f"controls/{theme}/composer-{composer}.png" in targets


def test_theme_entry_scripts_only_select_a_manifest():
    for theme in ("bright", "eye_care", "green", "night", "gray"):
        path = ROOT / "scripts" / f"process_{theme}_theme_assets.py"
        source = path.read_text(encoding="utf-8")
        assert "main_for_manifest" in source
        assert len(source.splitlines()) <= 8


def test_night_manifest_preserves_precut_alpha_sources():
    plan = pipeline.load_plan(ROOT / "scripts" / "theme_assets" / "night.json")
    transparent_sources = {
        "night夜风流线角饰图集.png",
        "night按钮状态表面图集.png",
        "night消息表面纹理图集.png",
        "night空状态记录册徽记.png",
    }
    matching = [
        step for step in plan["steps"]
        if step.get("source") in transparent_sources
    ]

    assert matching
    assert all(step.get("preprocess") == "clean_alpha" for step in matching)


def test_pipeline_generates_crop_and_referenced_atlas(tmp_path):
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for x in range(16, 48):
        for y in range(20, 44):
            image.putpixel((x, y), (255, 255, 255, 255))
    image.save(source_dir / "source.png")
    plan = {
        "theme": "test",
        "steps": [
            {
                "op": "png", "id": "part", "source": "source.png",
                "target": "controls/test/part.png", "visible": True, "padding": 0,
            },
            {
                "op": "atlas", "target": "controls/test/atlas.png", "size": [128, 64],
                "items": [{"ref": "part", "box": [0, 0, 128, 64]}],
            },
        ],
    }

    outputs = pipeline.process_plan(plan, source_dir, output_dir)

    assert len(outputs) == 2
    assert Image.open(outputs[0]).size == (32, 24)
    assert Image.open(outputs[1]).size == (128, 64)


def test_manifest_rejects_output_escape_and_forward_reference():
    with pytest.raises(ValueError, match="安全相对路径"):
        pipeline.validate_plan({
            "theme": "bad", "steps": [
                {"op": "png", "source": "x.png", "target": "../outside.png"},
            ],
        })
    with pytest.raises(ValueError, match="尚未生成"):
        pipeline.validate_plan({
            "theme": "bad", "steps": [
                {"op": "atlas", "target": "atlas.png", "size": [1, 1],
                 "items": [{"ref": "later", "box": [0, 0, 1, 1]}]},
            ],
        })
