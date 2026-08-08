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
