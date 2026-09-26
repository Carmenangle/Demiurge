from app.services import template_store


def test_normalize_ids_and_legacy_without_mutating_input():
    original = {
        "input_node_ids": [1, " 2 ", "", 1],
        "output_node_ids": [3, "3", None],
        "node_order": ["2", 1, "2"],
        "prompt_node_id": 4,
        "image_node_id": " 5 ",
        "exposed": [{"node_id": 6, "field": "x"}, {"node_id": "", "field": "bad"}],
    }
    normalized = template_store._normalize(original)

    assert normalized["input_node_ids"] == ["1", "2", "4", "5"]
    assert normalized["output_node_ids"] == ["3"]
    assert normalized["node_order"] == ["2", "1"]
    assert normalized["exposed"] == [{
        "node_id": "6", "field": "x", "label": "x", "semantic": "x",
    }]
    assert original["input_node_ids"] == [1, " 2 ", "", 1]


def test_ordered_node_ids_preserves_domain_order():
    record = {
        "node_order": ["2"],
        "exposed": [{"node_id": "1"}, {"node_id": "2"}],
        "input_node_ids": ["3", "1"],
        "output_node_ids": ["4", "3"],
    }
    assert template_store.ordered_node_ids(record) == ["2", "1", "3", "4"]


def test_save_returns_same_normalized_shape_as_get(tmp_path, monkeypatch):
    monkeypatch.setattr(template_store, "TEMPLATES_DIR", tmp_path)
    saved = template_store.save_template({
        "name": "x", "input_node_ids": [1, "1"], "prompt_node_id": 2,
        "output_node_ids": [3], "exposed": [],
    })
    loaded = template_store.get_template(saved["id"])

    assert saved == loaded
    assert loaded["input_node_ids"] == ["1", "2"]
    assert loaded["output_node_ids"] == ["3"]


def test_primary_output_node_id_normalized_and_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(template_store, "TEMPLATES_DIR", tmp_path)
    # 归一：数字/带空白转非空字符串；缺省为空串
    assert template_store._normalize({"primary_output_node_id": 7})["primary_output_node_id"] == "7"
    assert template_store._normalize({})["primary_output_node_id"] == ""
    saved = template_store.save_template({"name": "x", "primary_output_node_id": " 9 "})
    assert template_store.get_template(saved["id"])["primary_output_node_id"] == "9"


def test_unique_empty_latent_is_automatically_exposed_for_media_insert():
    normalized = template_store._normalize({
        "workflow_data": {"nodes": [
            {"id": 4, "type": "EmptyLatentImage", "widgets_values": [832, 1216, 1]},
            {"id": 8, "type": "KSampler", "widgets_values": []},
        ]},
        "exposed": [{
            "node_id": "9", "field": "text", "semantic": "prompt",
            "label": "提示词", "control": "textarea", "default": "",
        }],
    })

    latent = [field for field in normalized["exposed"]
              if field.get("binding", "").startswith("latent_")]
    assert latent == [
        {"node_id": "4", "field": "width", "label": "width",
         "control": "number", "semantic": "width", "binding": "latent_width", "default": 832},
        {"node_id": "4", "field": "height", "label": "height",
         "control": "number", "semantic": "height", "binding": "latent_height", "default": 1216},
    ]


def test_multiple_empty_latents_require_explicit_semantics():
    normalized = template_store._normalize({
        "workflow_data": {"nodes": [
            {"id": 4, "type": "EmptyLatentImage", "widgets_values": [832, 1216, 1]},
            {"id": 5, "type": "EmptyLatentImage", "widgets_values": [1024, 1024, 1]},
        ]},
        "exposed": [],
    })

    assert normalized["exposed"] == []


def test_unique_api_format_empty_latent_is_automatically_exposed():
    normalized = template_store._normalize({
        "workflow_data": {
            "4": {"class_type": "EmptyLatentImage", "inputs": {
                "width": 1024, "height": 1024, "batch_size": 1,
            }},
            "8": {"class_type": "KSampler", "inputs": {}},
        },
        "exposed": [],
    })

    assert [(field["node_id"], field["semantic"], field["binding"], field["default"])
            for field in normalized["exposed"]] == [
        ("4", "width", "latent_width", 1024),
        ("4", "height", "latent_height", 1024),
    ]


def _文本载体记录() -> dict:
    """Anima AIO 全能流的真实形状：整条流没有任何 CLIPTextEncode，提示词在 String 节点里。"""
    return {"workflow_data": {"nodes": [
        {"id": 1610, "type": "String", "title": "POS Prompt", "widgets_values": ["1girl"]},
        {"id": 1611, "type": "String", "title": "NEG Prompt", "widgets_values": [""]},
        {"id": 1348, "type": "String", "title": "负面", "widgets_values": [""]},
        {"id": 1347, "type": "String", "title": "Anima首选质量词条", "widgets_values": [""]},
        {"id": 1673, "type": "String", "title": "词条临时工作区", "widgets_values": [""]},
        {"id": 1692, "type": "PrimitiveStringMultiline", "title": "画师串", "widgets_values": [""]},
    ]}}


def test_文本载体节点按标题声明binding():
    record = _文本载体记录()
    assert template_store._infer_binding(record, "1610", "String") == "prompt"
    assert template_store._infer_binding(record, "1611", "String") == "negative_prompt"
    assert template_store._infer_binding(record, "1348", "String") == "negative_prompt"
    # 标题没声明正负的载体节点一律不猜（质量词条 / 临时工作区 / 画师串）
    assert template_store._infer_binding(record, "1347", "String") == ""
    assert template_store._infer_binding(record, "1673", "String") == ""
    assert template_store._infer_binding(record, "1692", "String") == ""


def test_无cliptextencode的模板归一后正文有落点():
    # 整链验收：归一必须给 1610.String 补上 binding=="prompt"，否则 inject 的兜底分支
    # 永不命中，模型写的正文被静默丢弃、出图用的是模板里烘焙的旧文本（2026-09-26 真机）。
    normalized = template_store._normalize({
        "workflow_data": _文本载体记录()["workflow_data"],
        "exposed": [{"node_id": "1610", "field": "String", "control": "text", "default": "old"}],
        "input_node_ids": ["1610"],
    })

    assert [(f["node_id"], f["field"], f.get("binding")) for f in normalized["exposed"]] == [
        ("1610", "String", "prompt"),
    ]


def test_专用语义不被标题声明规则抢走():
    # LoRA 加载器 / 音频节点即使标题里带 prompt 字样，也仍走各自的专用 binding。
    record = {"workflow_data": {"nodes": [
        {"id": 19, "type": "LoraLoader", "title": "Positive Prompt 风格 LoRA"},
        {"id": 3, "type": "IndexTTS25EmotionVectorNode", "title": "Positive Prompt"},
    ]}}
    assert template_store._infer_binding(record, "19", "lora_name") == "lora_name"
    assert template_store._infer_binding(record, "19", "strength_model") == "lora_weight"
    assert template_store._infer_binding(record, "3", "text") == ""


def test_legacy_semantic_aliases_become_hidden_bindings_and_duplicates_are_removed():
    normalized = template_store._normalize({
        "workflow_data": {"nodes": [
            {"id": 40, "type": "LoraLoaderModelOnly", "title": "LoRA"},
            {"id": 12, "type": "EmptyLatentImage", "widgets_values": [832, 1216, 1]},
        ]},
        "exposed": [
            {"node_id": "40", "field": "strength_model", "semantic": "lora_weight", "default": 1},
            {"node_id": "12", "field": "width", "semantic": "width", "default": 832},
            {"node_id": "12", "field": "width", "semantic": "latent_width", "default": 832},
        ],
    })

    assert [(field["field"], field["semantic"], field.get("binding"))
            for field in normalized["exposed"]] == [
        ("strength_model", "strength_model", "lora_weight"),
        ("width", "width", "latent_width"),
        ("height", "height", "latent_height"),
    ]
