from app.services import workflow_convert


def _widget_input(name: str) -> dict:
    return {"name": name, "link": None, "widget": {"name": name}}


def test_ui_to_api_uses_node_widget_metadata_when_object_info_times_out(monkeypatch):
    monkeypatch.setattr(workflow_convert, "_object_info", lambda _url: {})
    workflow = {
        "nodes": [
            {
                "id": 12,
                "type": "CLIPLoader",
                "inputs": [_widget_input("clip_name"), _widget_input("type"),
                           _widget_input("device")],
                "widgets_values": ["clip.safetensors", "krea2", "default"],
            },
            {
                "id": 14,
                "type": "UNETLoader",
                "inputs": [_widget_input("unet_name"), _widget_input("weight_dtype")],
                "widgets_values": ["model.safetensors", "default"],
            },
            {
                "id": 16,
                "type": "LatentUpscaleBy",
                "inputs": [_widget_input("upscale_method"), _widget_input("scale_by")],
                "widgets_values": ["bislerp", 1.5],
            },
            {
                "id": 17,
                "type": "Seed (rgthree)",
                "inputs": [_widget_input("seed")],
                "widgets_values": [-1, "", "", "okay"],
            },
            {
                "id": 25,
                "type": "ColorMatchV2",
                "inputs": [_widget_input("method"), _widget_input("strength"),
                           _widget_input("multithread")],
                "widgets_values": ["mkl", 0.5, True],
            },
        ],
        "links": [],
    }

    api = workflow_convert.ui_to_api(workflow, "http://127.0.0.1:8188")

    assert api["12"]["inputs"] == {
        "clip_name": "clip.safetensors", "type": "krea2", "device": "default",
    }
    assert api["14"]["inputs"] == {
        "unet_name": "model.safetensors", "weight_dtype": "default",
    }
    assert api["16"]["inputs"] == {"upscale_method": "bislerp", "scale_by": 1.5}
    assert api["17"]["inputs"] == {"seed": -1}
    assert api["25"]["inputs"] == {
        "method": "mkl", "strength": 0.5, "multithread": True,
    }


def test_new_combo_schema_is_treated_as_widget(monkeypatch):
    monkeypatch.setattr(workflow_convert, "_object_info", lambda _url: {
        "ColorMatchV2": {
            "input": {"required": {
                "image_target": ["IMAGE", {}],
                "image_ref": ["IMAGE", {}],
                "method": ["COMBO", {"options": ["mkl", "hm"]}],
                "strength": ["FLOAT", {"default": 1.0}],
                "multithread": ["BOOLEAN", {"default": True}],
            }},
        },
    })
    workflow = {
        "nodes": [{
            "id": 25, "type": "ColorMatchV2", "inputs": [],
            "widgets_values": ["mkl", 0.5, True],
        }],
        "links": [],
    }

    api = workflow_convert.ui_to_api(workflow, "http://127.0.0.1:8188")

    assert api["25"]["inputs"] == {
        "method": "mkl", "strength": 0.5, "multithread": True,
    }
