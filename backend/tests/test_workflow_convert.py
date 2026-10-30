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


def test_object_info_超时必须覆盖云端大schema(monkeypatch):
    """云端 /object_info 常达数 MB：5 s 超时会静默回落内置表、widget 顺序错位。
    这里钉住「超时不得再调回秒级」的合同。"""
    seen: dict[str, object] = {}

    def fake_fetch(url, **kwargs):
        seen.update(kwargs)
        seen["url"] = url
        return {}

    monkeypatch.setattr(workflow_convert.comfyui_client, "fetch_object_info", fake_fetch)

    assert workflow_convert._object_info("https://comfy.example.com") == {}
    assert isinstance(seen["timeout"], (int, float))
    assert seen["timeout"] >= 30


def test_object_info_失败仍保留转换降级语义(monkeypatch):
    def boom(*args, **kwargs):
        raise workflow_convert.comfyui_client.ComfyError("timed out", 502)

    monkeypatch.setattr(workflow_convert.comfyui_client, "fetch_object_info", boom)

    assert workflow_convert._object_info("http://127.0.0.1:8188") == {}
