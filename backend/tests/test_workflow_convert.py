from app.services import workflow_convert


def _widget_input(name: str) -> dict:
    return {"name": name, "link": None, "widget": {"name": name}}


def test_ui_to_api_uses_node_widget_metadata_when_object_info_times_out(monkeypatch):
    monkeypatch.setattr(workflow_convert, "_object_info", lambda *_a: {})
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
    monkeypatch.setattr(workflow_convert, "_object_info", lambda *_a: {
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
    """**无类型清单**时的全量兜底路径：云端 /object_info 常达数 MB，5 s 会静默回落内置表、
    widget 顺序错位，所以这条路径的超时不得调回秒级。（转换链路已改逐类型探测，不再走这里。）"""
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
    # 逐类型路径同样「拉不到就空」：转换降级、不抛给提交线程
    assert workflow_convert._object_info("http://127.0.0.1:8188", ("KSampler",)) == {}


def test_转换链路不再拉全量object_info(monkeypatch):
    """回归钉（2026-10-31 实锤）：本机全量清单 4474 类 / 9.4 MB，冷缓存实测 92.9~115.4 s，
    对 30 s 上限必然失败 ⇒ 每次生图提交先白等一整个超时、转换仍降级到内置表。
    必须只按图里用到的类型逐个问（首问 22.15 s 焐热服务端，其后每个 0.00~0.02 s）。"""
    calls: list[str] = []

    def fake_fetch(url, node="", **kwargs):
        calls.append(node or "<全量>")
        return {node: {"input": {"required": {"text": ["STRING", {"default": ""}]}}}}

    monkeypatch.setattr(workflow_convert.comfyui_client, "fetch_object_info", fake_fetch)
    workflow_convert.comfyui_client._OBJECT_INFO_TYPE_CACHE.clear()
    workflow = {
        "nodes": [
            {"id": 1, "type": "CLIPTextEncode", "inputs": [], "widgets_values": ["提示词"]},
            {"id": 2, "type": "Note", "inputs": [], "widgets_values": ["注释"]},
        ],
        "links": [],
    }

    api = workflow_convert.ui_to_api(workflow, "http://127.0.0.1:8188")

    assert calls == ["CLIPTextEncode"]  # 只问用到的类型，纯注释节点不问，更没有 <全量>
    assert api["1"]["inputs"] == {"text": "提示词"}


def test_确认未注册才跳过_探测没问到不得跳过(monkeypatch):
    """三档语义落到转换端：未注册（200 空对象 → None）才穿透跳过；探测失败（缺键＝未知）
    必须保留节点——否则一次网络抖动就把好节点当废节点吃掉、连线整条断掉。"""
    def fake_fetch(url, node="", **kwargs):
        if node == "GhostNode":
            return {}  # 真实 ComfyUI 对未注册类型回 200 空对象
        if node == "SlowNode":
            raise workflow_convert.comfyui_client.ComfyError("timed out", 502)
        return {node: {"input": {"required": {}}}}

    monkeypatch.setattr(workflow_convert.comfyui_client, "fetch_object_info", fake_fetch)
    workflow_convert.comfyui_client._OBJECT_INFO_TYPE_CACHE.clear()

    def _graph(ghost: str) -> dict:
        return {
            "nodes": [
                {"id": 1, "type": "LoadImage", "inputs": [],
                 "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [9]}]},
                {"id": 2, "type": ghost,
                 "inputs": [{"name": "image", "type": "IMAGE", "link": 9}],
                 "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [10]}]},
                {"id": 3, "type": "SaveImage",
                 "inputs": [{"name": "images", "type": "IMAGE", "link": 10}], "outputs": []},
            ],
            "links": [[9, 1, 0, 2, 0, "IMAGE"], [10, 2, 0, 3, 0, "IMAGE"]],
        }

    skipped = workflow_convert.ui_to_api(_graph("GhostNode"), "http://127.0.0.1:8188")
    assert "2" not in skipped  # 确认跑不动 → 穿透
    assert skipped["3"]["inputs"]["images"] == ["1", 0]

    kept = workflow_convert.ui_to_api(_graph("SlowNode"), "http://127.0.0.1:8188")
    assert kept["2"]["class_type"] == "SlowNode"  # 没问到 → 不判死
    assert kept["3"]["inputs"]["images"] == ["2", 0]


def test_dict_widgets_values_进入api输入(monkeypatch):
    """VHS_VideoCombine 等自定义节点把 widgets_values 存成 {名: 值}：
    按名字直接落进 API 输入；纯前端的 videopreview 必须剔除，已连线键不被覆盖。"""
    monkeypatch.setattr(workflow_convert, "_object_info", lambda *_a: {})
    workflow = {
        "nodes": [{
            "id": 67,
            "type": "VHS_VideoCombine",
            "inputs": [
                {"name": "images", "type": "IMAGE", "link": 561},
                {"name": "frame_rate", "type": "FLOAT", "link": None, "widget": {"name": "frame_rate"}},
            ],
            "widgets_values": {
                "frame_rate": 24, "loop_count": 0, "filename_prefix": "H3_I2V",
                "format": "video/h264-mp4", "pix_fmt": "yuv420p10le", "crf": 19,
                "save_metadata": False, "trim_to_audio": False, "pingpong": False,
                "save_output": True,
                "videopreview": {"hidden": False, "params": {"frame_rate": 24}},
            },
        }, {
            "id": 5, "type": "SaveImage", "inputs": [],
            "widgets_values": ["ComfyUI"],
        }],
        "links": [[561, 5, 0, 67, 0, "IMAGE"]],
    }

    api = workflow_convert.ui_to_api(workflow, "")

    assert api["67"]["inputs"] == {
        "images": ["5", 0],
        "frame_rate": 24, "loop_count": 0, "filename_prefix": "H3_I2V",
        "format": "video/h264-mp4", "pix_fmt": "yuv420p10le", "crf": 19,
        "save_metadata": False, "trim_to_audio": False, "pingpong": False,
        "save_output": True,
    }


def test_dict_widgets_values有schema时仍保留format动态子控件(monkeypatch):
    """object_info 顶层 required 里没有 crf/pix_fmt/save_metadata（它们按 format 动态生成），
    schema 存在时也不能按它收窄把这些值丢掉；纯前端的 videopreview 仍要剔除。"""
    monkeypatch.setattr(workflow_convert, "_object_info", lambda *_a: {
        "VHS_VideoCombine": {
            "input": {"required": {
                "images": ["IMAGE"],
                "frame_rate": ["FLOAT", {"default": 8}],
                "format": [["video/h264-mp4"], {}],
            }},
        },
    })
    workflow = {
        "nodes": [{
            "id": 67, "type": "VHS_VideoCombine",
            "inputs": [
                {"name": "images", "type": "IMAGE", "link": None},
                {"name": "crf", "type": "INT", "link": None, "widget": {"name": "crf"}},
                {"name": "pix_fmt", "type": "COMBO", "link": None, "widget": {"name": "pix_fmt"}},
                {"name": "save_metadata", "type": "BOOLEAN", "link": None, "widget": {"name": "save_metadata"}},
            ],
            "widgets_values": {
                "frame_rate": 24, "format": "video/h264-mp4",
                "crf": 19, "pix_fmt": "yuv420p10le", "save_metadata": False,
                "videopreview": {"hidden": True},
            },
        }],
        "links": [],
    }

    api = workflow_convert.ui_to_api(workflow, "http://127.0.0.1:8188")

    assert api["67"]["inputs"] == {
        "frame_rate": 24, "format": "video/h264-mp4",
        "crf": 19, "pix_fmt": "yuv420p10le", "save_metadata": False,
    }
