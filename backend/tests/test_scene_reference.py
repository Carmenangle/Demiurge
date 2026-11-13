"""空场景环境图生成（2026-11-12）。"""

import json
from pathlib import Path

from app.services import scene_card, scene_reference

TEMPLATE = {
    "id": "tpl-scene",
    "exposed": [
        {"node_id": "18", "field": "text", "binding": "prompt"},
        {"node_id": "1", "field": "text", "binding": "negative_prompt"},
        {"node_id": "5", "field": "width", "binding": "latent_width"},
        {"node_id": "5", "field": "height", "binding": "latent_height"},
        {"node_id": "19", "field": "lora_name", "binding": "lora_name"},
        {"node_id": "19", "field": "strength_model", "binding": "lora_weight"},
    ],
}

CARD = {
    "space_type": "warehouse",
    "architecture": [
        {"item": "wooden door", "kind": "architecture", "position": "entrance",
         "evidence": "木门前铺着红毯"},
    ],
    "fixed_props": [
        {"item": "wooden platform", "kind": "fixed_prop", "position": "center",
         "evidence": "红毯铺到中央的木板台子"},
    ],
    "layout": {"entrance": "wooden door", "center": "wooden platform"},
    "lighting": {"sources": ["lamplight"], "direction": "", "quality": "dim",
                 "time": "dusk", "weather": ""},
    "palette": ["red"],
    "camera": {"shot_size": "wide", "height": "eye level", "angle": "eye level",
               "depth": "deep"},
}


def _install_comfy(monkeypatch, *, values: dict | None = None, prompt: str = ""):
    monkeypatch.setattr(
        scene_reference.template_store, "get_template", lambda _id: TEMPLATE,
    )

    def fake_submit(template_id, submitted_values, *, prompt="", url="",
                    loras=None, lora_mode="single"):
        if values is not None:
            values.update(submitted_values)
            values["prompt"] = prompt
            values["loras"] = list(loras or [])
        return {"prompt_id": "pid-1"}

    monkeypatch.setattr(scene_reference.workflow_submission, "submit_template", fake_submit)
    monkeypatch.setattr(
        scene_reference.comfyui_client, "fetch_result",
        lambda url, prompt_id: {
            "status": "completed",
            "images": [{"filename": "scene.png", "type": "output", "subfolder": ""}],
        },
    )

    def fake_download(url, filename, dest, type="output", subfolder=""):
        Path(dest).write_bytes(b"png-bytes")
        return "image/png"

    monkeypatch.setattr(scene_reference.comfyui_client, "fetch_view_to_file", fake_download)


def test_scene_reference_filename_sanitizes_scene_key():
    assert scene_reference.scene_reference_filename("scene-0") == "scene-0.png"
    assert scene_reference.scene_reference_filename("序章/仓库") == "scene.png"
    assert scene_reference.scene_reference_filename("") == "scene.png"


def test_generate_scene_reference_submits_empty_scene_prompt(monkeypatch, tmp_path):
    captured: dict = {}
    _install_comfy(monkeypatch, values=captured)
    card = {**CARD, "scene_key": "scene-0"}

    result = scene_reference.generate_scene_reference(
        card, template_id="tpl-scene", url="http://127.0.0.1:8188", out_dir=tmp_path,
        style_loras=[{"name": "flat.safetensors", "weight": 0.8}],
    )

    assert result.status == "generated"
    assert result.prompt_id == "pid-1"
    assert Path(result.image_path) == tmp_path / "scene-0.png"
    assert Path(result.image_path).read_bytes() == b"png-bytes"
    assert result.prompt.startswith("unpopulated interior, no people, no characters")
    assert "wooden platform" in result.prompt
    assert captured["18.text"] == result.prompt
    assert "people" in str(captured["1.text"])
    assert captured["5.width"] == 1024
    assert captured["5.height"] == 576
    assert captured["19.lora_name"] == "flat.safetensors"
    assert captured["19.strength_model"] == 0.8
    assert captured["loras"] == [{"name": "flat.safetensors", "weight": 0.8}]


def test_generate_scene_reference_skips_existing_image(monkeypatch, tmp_path):
    dest = tmp_path / "scene-0.png"
    dest.write_bytes(b"old")
    scene_reference._write_cache(
        dest, scene_key="scene-0", template_id="tpl-scene",
        prompt=scene_card.build_scene_prompt(CARD),
    )
    submitted: list = []
    monkeypatch.setattr(
        scene_reference.workflow_submission, "submit_template",
        lambda *args, **kwargs: submitted.append(1) or {},
    )

    result = scene_reference.generate_scene_reference(
        {**CARD, "scene_key": "scene-0"},
        template_id="tpl-scene", url="http://127.0.0.1:8188", out_dir=tmp_path,
    )

    assert result.status == "skipped"
    assert result.image_path == str(dest)
    assert dest.read_bytes() == b"old"
    assert submitted == []

    _install_comfy(monkeypatch)
    forced = scene_reference.generate_scene_reference(
        {**CARD, "scene_key": "scene-0"}, template_id="tpl-scene",
        url="http://127.0.0.1:8188", out_dir=tmp_path, force=True,
    )
    assert forced.status == "generated"
    assert dest.read_bytes() == b"png-bytes"


def test_generate_scene_reference_regenerates_when_prompt_changes(monkeypatch, tmp_path):
    """场景卡规则更新后，旧图不能只凭文件存在继续复用。"""
    dest = tmp_path / "scene-0.png"
    dest.write_bytes(b"stale")
    scene_reference._write_cache(
        dest, scene_key="scene-0", template_id="tpl-scene", prompt="stale prompt",
    )
    _install_comfy(monkeypatch)

    result = scene_reference.generate_scene_reference(
        {**CARD, "scene_key": "scene-0"},
        template_id="tpl-scene", url="http://127.0.0.1:8188", out_dir=tmp_path,
    )

    assert result.status == "generated"
    assert dest.read_bytes() == b"png-bytes"
    sidecar = json.loads(dest.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["prompt"] == result.prompt


def test_generate_scene_reference_reports_missing_template(monkeypatch, tmp_path):
    monkeypatch.setattr(scene_reference.template_store, "get_template", lambda _id: None)

    result = scene_reference.generate_scene_reference(
        {**CARD, "scene_key": "scene-0"},
        template_id="missing", url="http://127.0.0.1:8188", out_dir=tmp_path,
    )

    assert result.status == "failed"
    assert "模板不存在" in result.error


def test_generate_scene_reference_reports_submit_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        scene_reference.template_store, "get_template", lambda _id: TEMPLATE,
    )

    def boom(*args, **kwargs):
        raise RuntimeError("模板缺少原始工作流文件，无法启动")

    monkeypatch.setattr(scene_reference.workflow_submission, "submit_template", boom)

    result = scene_reference.generate_scene_reference(
        {**CARD, "scene_key": "scene-0"},
        template_id="tpl-scene", url="http://127.0.0.1:8188", out_dir=tmp_path,
    )

    assert result.status == "failed"
    assert "缺少原始工作流" in result.error


def test_generate_scene_references_backfills_key_and_writes_manifest(monkeypatch, tmp_path):
    _install_comfy(monkeypatch)
    cards = {
        "scene-0": {**CARD},
        "scene-1": {**CARD, "space_type": "bedroom"},
    }

    manifest = scene_reference.generate_scene_references(
        cards, template_id="tpl-scene", url="http://127.0.0.1:8188", out_dir=tmp_path,
    )

    assert set(manifest["scenes"]) == {"scene-0", "scene-1"}
    assert manifest["scenes"]["scene-0"]["card"]["scene_key"] == "scene-0"
    assert manifest["scenes"]["scene-1"]["card"]["scene_key"] == "scene-1"
    assert manifest["scenes"]["scene-1"]["card"]["space_type"] == "bedroom"
    on_disk = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["template_id"] == "tpl-scene"
    assert (tmp_path / "scene-0.png").exists()
