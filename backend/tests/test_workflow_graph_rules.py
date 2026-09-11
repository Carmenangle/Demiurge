from app.services import workflow_graph_rules as rules


OBJECT_INFO = {
    "Model": {
        "input": {"required": {"name": [["a.safetensors", "b.safetensors"], {}]}},
        "output": ["MODEL"],
    },
    "Sampler": {
        "input": {
            "required": {
                "model": ["MODEL", {}],
                "steps": ["INT", {"default": 20}],
            },
        },
        "output": ["IMAGE"],
    },
    "Save": {
        "input": {"required": {"images": ["IMAGE", {}]}},
        "output": [],
        "output_node": True,
    },
    "Text": {"input": {"required": {}}, "output": ["STRING"]},
}


def test_fill_defaults_then_validate_complete_graph():
    graph = {
        "1": {"class_type": "Model", "inputs": {"name": "A.SAFETENSORS"}},
        "2": {"class_type": "Sampler", "inputs": {"model": ["1", 0]}},
        "3": {"class_type": "Save", "inputs": {"images": ["2", 0]}},
    }

    changed = rules.fill_combo_defaults(graph, OBJECT_INFO)

    assert changed == 2
    assert graph["1"]["inputs"]["name"] == "a.safetensors"
    assert graph["2"]["inputs"]["steps"] == 20
    assert rules.validate_graph(graph, OBJECT_INFO) == []
    assert rules.audit_graph(graph, OBJECT_INFO) == []


def test_validate_reports_real_output_type_mismatch():
    graph = {
        "1": {"class_type": "Text", "inputs": {}},
        "2": {"class_type": "Sampler", "inputs": {"model": ["1", 0], "steps": 20}},
    }

    errors = rules.validate_graph(graph, OBJECT_INFO)

    assert any("需 MODEL" in error and "输出的是 STRING" in error for error in errors)


def test_split_missing_node_disconnects_only_invalid_reference():
    graph = {
        "1": {"class_type": "MissingNode", "inputs": {}},
        "2": {"class_type": "Sampler", "inputs": {"model": ["1", 0], "steps": 20}},
    }

    clean, missing = rules.split_missing_nodes(graph, OBJECT_INFO)

    assert missing == ["MissingNode"]
    assert "1" not in clean
    assert clean["2"]["inputs"] == {"steps": 20}


def test_audit_reports_graph_without_output_and_dangling_node():
    graph = {"1": {"class_type": "Text", "inputs": {}}}

    issues = rules.audit_graph(graph, OBJECT_INFO)

    assert any("输出没有接给任何下游" in issue for issue in issues)
    assert any("没有任何输出节点" in issue for issue in issues)
