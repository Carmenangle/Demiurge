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
    assert normalized["exposed"] == [{"node_id": "6", "field": "x"}]
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
