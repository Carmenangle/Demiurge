"""角色当前状态参考图（2026-11-12）。

锁四件事：**没有底图不许退化成文生图**、**模板没有 base_image 槽不许提交**、
**状态变了要重出（不能只看文件在不在）**、**画幅不由本模块传**（模板 Resize 硬写 576×1024）。
"""

from pathlib import Path

from app.services import character_reference, scene_reference

TEMPLATE = {
    "id": "tpl-img2img",
    "exposed": [
        {"node_id": "18", "field": "text", "binding": "prompt"},
        {"node_id": "1", "field": "text", "binding": "negative_prompt"},
        {"node_id": "100", "field": "image", "binding": "base_image"},
        {"node_id": "19", "field": "lora_name", "binding": "lora_name"},
        {"node_id": "19", "field": "strength_model", "binding": "lora_weight"},
        # 图生图的真实尺寸不在 exposed 里（Resize 节点硬写），这里显式放一个 latent 槽
        # 验证「width=0 不覆盖」。
        {"node_id": "20", "field": "width", "binding": "latent_width", "default": 1280},
        {"node_id": "20", "field": "height", "binding": "latent_height", "default": 720},
    ],
}

CARD = {
    "name": "埃吉尔",
    "scene_key": "current",
    "identity": {"appearance": "白发、龙角", "gender": "女", "base_image": ""},
    "wardrobe": "连体黑丝",
    "state_text": "埃吉尔低头看着两只叠在一起的手。",
    "scene_context": {"locale": "二楼走廊", "lighting": "lamplight"},
    "state_hash": "h1",
}


def _install_comfy(monkeypatch, *, values: dict | None = None):
    monkeypatch.setattr(
        character_reference.template_store, "get_template", lambda _id: TEMPLATE,
    )
    # 底图上传：真机走 `/upload/image`，测试只验「填进去的是上传后的相对名」。
    monkeypatch.setattr(
        character_reference.comfyui_client, "upload_image",
        lambda url, filename, data, content_type="image/png": f"uploaded_{filename}",
    )

    def fake_submit(template_id, submitted_values, *, prompt="", url="",
                    loras=None, lora_mode="single"):
        if values is not None:
            values.update(submitted_values)
            values["prompt"] = prompt
        return {"prompt_id": "pid-1"}

    monkeypatch.setattr(character_reference.workflow_submission, "submit_template", fake_submit)
    monkeypatch.setattr(
        character_reference.comfyui_client, "fetch_result",
        lambda url, prompt_id: {
            "status": "completed",
            "images": [{"filename": "c.png", "type": "output", "subfolder": ""}],
        },
    )

    def fake_download(url, filename, dest, type="output", subfolder=""):
        Path(dest).write_bytes(b"png-bytes")
        return "image/png"

    monkeypatch.setattr(character_reference.comfyui_client, "fetch_view_to_file", fake_download)


def _card(tmp_path: Path, **overrides) -> dict:
    base_image = tmp_path / "base.png"
    base_image.write_bytes(b"base")
    card = dict(CARD)
    card["identity"] = dict(CARD["identity"], base_image=str(base_image))
    card.update(overrides)
    return card


def test_filename_sanitizes_and_keeps_chinese():
    assert character_reference.character_reference_filename("埃吉尔") == "埃吉尔.png"
    assert character_reference.character_reference_filename("A/B 测试") == "A_B_测试.png"


def test_missing_base_image_fails_without_falling_back_to_txt2img(tmp_path, monkeypatch):
    _install_comfy(monkeypatch)
    card = dict(CARD, identity=dict(CARD["identity"], base_image=""))
    result = character_reference.generate_character_reference(
        card, prompt="p", template_id="tpl-img2img", url="http://x",
        out_dir=tmp_path / "out",
    )
    assert result.status == "failed"
    assert "定妆底图" in result.error


def test_missing_base_image_file_fails(tmp_path, monkeypatch):
    _install_comfy(monkeypatch)
    card = dict(CARD, identity=dict(CARD["identity"], base_image=str(tmp_path / "nope.png")))
    result = character_reference.generate_character_reference(
        card, prompt="p", template_id="tpl-img2img", url="http://x",
        out_dir=tmp_path / "out",
    )
    assert result.status == "failed"
    assert "不存在" in result.error


def test_template_without_base_image_slot_fails_before_submit(tmp_path, monkeypatch):
    _install_comfy(monkeypatch)
    monkeypatch.setattr(
        character_reference.template_store, "get_template",
        lambda _id: {"id": "txt2img", "exposed": [
            {"node_id": "18", "field": "text", "binding": "prompt"},
        ]},
    )
    submitted: list[str] = []
    monkeypatch.setattr(
        character_reference.workflow_submission, "submit_template",
        lambda template_id, values, **kwargs: submitted.append(template_id) or {"prompt_id": "x"},
    )
    result = character_reference.generate_character_reference(
        _card(tmp_path), prompt="p", template_id="txt2img", url="http://x",
        out_dir=tmp_path / "out",
    )
    assert result.status == "failed"
    assert "base_image 槽" in result.error
    assert submitted == []


def test_generate_passes_base_image_and_does_not_override_size(tmp_path, monkeypatch):
    captured: dict = {}
    _install_comfy(monkeypatch, values=captured)
    result = character_reference.generate_character_reference(
        _card(tmp_path), prompt="a prompt", template_id="tpl-img2img", url="http://x",
        out_dir=tmp_path / "out",
        style_loras=[{"name": "style.safetensors", "weight": 0.8}],
    )
    assert result.status == "generated"
    assert captured["100.image"] == "uploaded_base.png"
    assert captured["18.text"] == "a prompt"
    assert captured["19.lora_name"] == "style.safetensors"
    # width/height 传 0 ⇒ 保留模板默认值，不塞 1024×576
    assert captured["20.width"] == 1280
    assert captured["20.height"] == 720
    assert Path(result.image_path).read_bytes() == b"png-bytes"


def test_upload_failure_fails_before_submit(tmp_path, monkeypatch):
    _install_comfy(monkeypatch)
    monkeypatch.setattr(
        character_reference.comfyui_client, "upload_image",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("上传炸了")),
    )
    submitted: list[str] = []
    monkeypatch.setattr(
        character_reference.workflow_submission, "submit_template",
        lambda template_id, values, **kwargs: submitted.append(template_id) or {"prompt_id": "x"},
    )
    result = character_reference.generate_character_reference(
        _card(tmp_path), prompt="p", template_id="tpl-img2img", url="http://x",
        out_dir=tmp_path / "out",
    )
    assert result.status == "failed"
    assert "上传定妆底图" in result.error
    assert submitted == []


def test_same_state_skips(tmp_path, monkeypatch):
    _install_comfy(monkeypatch)
    out = tmp_path / "out"
    first = character_reference.generate_character_reference(
        _card(tmp_path), prompt="p", template_id="tpl-img2img", url="http://x", out_dir=out,
    )
    second = character_reference.generate_character_reference(
        _card(tmp_path), prompt="p", template_id="tpl-img2img", url="http://x", out_dir=out,
    )
    assert first.status == "generated"
    assert second.status == "skipped"


def test_state_change_regenerates_even_if_file_exists(tmp_path, monkeypatch):
    _install_comfy(monkeypatch)
    out = tmp_path / "out"
    character_reference.generate_character_reference(
        _card(tmp_path), prompt="p", template_id="tpl-img2img", url="http://x", out_dir=out,
    )
    again = character_reference.generate_character_reference(
        _card(tmp_path, state_hash="h2"), prompt="p", template_id="tpl-img2img",
        url="http://x", out_dir=out,
    )
    assert again.status == "generated"


def test_prompt_change_regenerates(tmp_path, monkeypatch):
    _install_comfy(monkeypatch)
    out = tmp_path / "out"
    character_reference.generate_character_reference(
        _card(tmp_path), prompt="p1", template_id="tpl-img2img", url="http://x", out_dir=out,
    )
    again = character_reference.generate_character_reference(
        _card(tmp_path), prompt="p2", template_id="tpl-img2img", url="http://x", out_dir=out,
    )
    assert again.status == "generated"


def test_manifest_preserves_characters_from_previous_runs(tmp_path, monkeypatch):
    """单独补跑第三张时，前两张的记录不许被整份覆盖。"""
    _install_comfy(monkeypatch)
    out = tmp_path / "out"
    ok = _card(tmp_path)
    character_reference.generate_character_references(
        [ok], prompts={"埃吉尔": "p1"}, template_id="tpl-img2img",
        url="http://x", out_dir=out,
    )
    second = _card(tmp_path, name="奥古斯特")
    manifest = character_reference.generate_character_references(
        [second], prompts={"奥古斯特": "p2"}, template_id="tpl-img2img",
        url="http://x", out_dir=out,
    )
    assert set(manifest["characters"]) == {"埃吉尔", "奥古斯特"}
    assert manifest["characters"]["埃吉尔"]["status"] == "generated"


def test_manifest_written_and_one_failure_isolated(tmp_path, monkeypatch):
    _install_comfy(monkeypatch)
    ok = _card(tmp_path)
    bad = dict(CARD, name="腓特烈", identity=dict(CARD["identity"], base_image=""))
    manifest = character_reference.generate_character_references(
        [ok, bad], prompts={"埃吉尔": "p1", "腓特烈": "p2"},
        template_id="tpl-img2img", url="http://x", out_dir=tmp_path / "out",
    )
    assert manifest["characters"]["埃吉尔"]["status"] == "generated"
    assert manifest["characters"]["腓特烈"]["status"] == "failed"
    assert (tmp_path / "out" / "manifest.json").is_file()


def test_build_template_values_width_zero_keeps_default():
    values = scene_reference.build_template_values(
        TEMPLATE, prompt="p", negative="n", width=0, height=0,
        style_loras=[], base_image="D:/x/base.png",
    )
    assert values["100.image"] == "D:/x/base.png"
    assert values["20.width"] == 1280
    assert values["20.height"] == 720


def test_build_template_values_still_overrides_when_size_given():
    values = scene_reference.build_template_values(
        TEMPLATE, prompt="p", negative="n", width=1024, height=576,
        style_loras=[],
    )
    assert values["20.width"] == 1024
    assert values["20.height"] == 576
    assert "100.image" not in values
