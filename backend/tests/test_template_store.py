from app.routers import workflows
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


def test_preserve_loras_默认关闭且可持久化(tmp_path, monkeypatch):
    monkeypatch.setattr(template_store, "TEMPLATES_DIR", tmp_path)
    # 没写过的老模板按关闭处理（保持「空 LoRA 栈清零残留 LoRA」的既有语义）
    assert template_store._normalize({})["preserve_loras"] is False
    saved = template_store.save_template({"name": "x", "preserve_loras": True})
    assert template_store.get_template(saved["id"])["preserve_loras"] is True
    assert template_store.list_templates()[0]["preserve_loras"] is True


def test_旧前端保存模板时省略preserve_loras不清除已存开关(tmp_path, monkeypatch):
    """保存请求缺席该字段 = 不表态（旧前端/旧 dist 不知道它），不得按 False 落盘抹掉能力开关。"""
    monkeypatch.setattr(template_store, "TEMPLATES_DIR", tmp_path)
    saved = template_store.save_template({"name": "video", "preserve_loras": True})

    omitted = workflows.update_template(saved["id"], workflows.TemplateRequest(name="video2"))
    assert omitted["preserve_loras"] is True

    explicit = workflows.update_template(
        saved["id"], workflows.TemplateRequest(name="video3", preserve_loras=False),
    )
    assert explicit["preserve_loras"] is False


def test_video_size_from_template关闭宽高自动绑定(tmp_path, monkeypatch):
    """2026-11-08：分辨率交给模板工作流自己决定（ResolutionSelector 连线）。

    真机实锤：首帧 1056×1536 经自动 binding 覆盖选择器后按 1.62MP 采样，
    与云端手工验证通过的 832×1504 配置不一致。该标记为真时不得自动补宽高。
    """
    normalized = template_store._normalize(
        _视频模板记录(_H3_NODE, video_size_from_template=True)
    )
    assert [f for f in normalized["exposed"]
            if str(f.get("binding", "")).startswith("video_")] == []

    monkeypatch.setattr(template_store, "TEMPLATES_DIR", tmp_path)
    saved = template_store.save_template({
        "name": "固定画幅视频", "video_size_from_template": True,
        "workflow_data": {"nodes": [_H3_NODE]},
    })
    assert saved["video_size_from_template"] is True
    assert template_store.get_template(saved["id"])["video_size_from_template"] is True


def test_旧前端保存模板时省略video_size_from_template不清除标记(tmp_path, monkeypatch):
    """模板编辑器不认识该标记；缺席保存不得静默恢复首帧尺寸覆盖。"""
    monkeypatch.setattr(template_store, "TEMPLATES_DIR", tmp_path)
    saved = template_store.save_template({
        "name": "固定画幅视频", "video_size_from_template": True,
        "workflow_data": {"nodes": [_H3_NODE]},
    })

    omitted = workflows.update_template(saved["id"], workflows.TemplateRequest(name="v2"))
    assert omitted["video_size_from_template"] is True

    explicit = workflows.update_template(
        saved["id"],
        workflows.TemplateRequest(name="v3", video_size_from_template=False),
    )
    assert explicit["video_size_from_template"] is False


# ---------------------------------------------------------------------------
# 视频节点 width/height 自动暴露（2026-10-31 用户定案「图片的宽高要和视频的宽高一致」）
# H3 流的宽高由 ResolutionSelector 连线供给，而画布暴露层只收「无连线」字段
# ⇒ 用户在前端手标不出这两键，提交层就没有落点。必须由 _auto_video_size_fields
# 按节点类型补 binding，前端才能把首帧真实尺寸写进视频节点、盖掉那条连线。
# ---------------------------------------------------------------------------
_H3_NODE = {
    "id": 329, "type": "MiniMaxH3ReferenceToVideo",
    # widget 声明顺序与 widgets_values 一一对位：第 0 位是 prompt，1344/768 才是宽高
    "inputs": [
        {"name": "prompt", "type": "STRING", "widget": {"name": "prompt"}},
        {"name": "width", "type": "INT", "widget": {"name": "width"}, "link": 553},
        {"name": "height", "type": "INT", "widget": {"name": "height"}, "link": 554},
        {"name": "length", "type": "INT", "widget": {"name": "length"}},
    ],
    "widgets_values": ["", 1344, 768, 124],
}


def _视频模板记录(*nodes, **over):
    return {"workflow_data": {"nodes": list(nodes) or [_H3_NODE]}, **over}


def test_唯一视频节点自动补上宽高binding():
    normalized = template_store._normalize(_视频模板记录(_H3_NODE))

    assert [f for f in normalized["exposed"] if f.get("binding", "").startswith("video_")] == [
        {"node_id": "329", "field": "width", "label": "width",
         "control": "number", "semantic": "width", "binding": "video_width", "default": 1344},
        {"node_id": "329", "field": "height", "label": "height",
         "control": "number", "semantic": "height", "binding": "video_height", "default": 768},
    ]
    # 补出来的节点必须进 ordered_node_ids，否则注入层按序取不到落点
    assert "329" in template_store.ordered_node_ids(normalized)


def test_两个视频节点时不猜唯一候选纪律():
    second = {**_H3_NODE, "id": 330}
    normalized = template_store._normalize(_视频模板记录(_H3_NODE, second))

    assert [f.get("binding") for f in normalized["exposed"]
            if str(f.get("binding", "")).startswith("video_")] == []


def test_已手标宽高binding时不重复追加():
    exposed = [
        {"node_id": "329", "field": "width", "control": "number", "binding": "video_width"},
        {"node_id": "329", "field": "height", "control": "number", "binding": "video_height"},
    ]
    normalized = template_store._normalize(_视频模板记录(_H3_NODE, exposed=[dict(f) for f in exposed]))

    assert len(normalized["exposed"]) == 2
    assert [(f["field"], f["binding"]) for f in normalized["exposed"]] == [
        ("width", "video_width"), ("height", "video_height"),
    ]


def test_api格式视频节点同样绑上宽高():
    """后端提交走 API 格式：class_type 认得出视频节点，宽高才有落点（默认值缺展示位不影响）。"""
    normalized = template_store._normalize({
        "workflow_data": {"329": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {"prompt": "", "width": 1344, "height": 768, "length": 124},
        }},
    })

    assert [(f["field"], f["binding"]) for f in normalized["exposed"]
            if str(f.get("binding", "")).startswith("video_")] == [
        ("width", "video_width"), ("height", "video_height"),
    ]


def test_首帧链换型后宽高binding仍落在唯一视频节点():
    """问题3（2026-11-01）：首帧底图改接 MiniMaxH3ImageToVideo 的真 first_frame 口
    （ref2va 只有参考图通道，第 0 帧必然不是成图）。换型同时必须保住 2026-10-31
    定案的「图片宽高＝视频宽高」——按类型自动补的 video_width/video_height 不能因为
    类型名换了就静默消失。槽位形态照抄改后模板：连接型输入在前，widget 输入按序占位。
    """
    i2v_node = {
        "id": 329, "type": "MiniMaxH3ImageToVideo",
        "inputs": [
            {"name": "clip", "type": "CLIP", "link": 545},
            {"name": "vae", "type": "VAE", "link": 546},
            {"name": "first_frame", "type": "IMAGE", "shape": 7, "link": 548},
            {"name": "last_frame", "type": "IMAGE", "shape": 7},
            {"name": "prompt", "type": "STRING", "widget": {"name": "prompt"}, "link": 566},
            {"name": "width", "type": "INT", "widget": {"name": "width"}, "link": 553},
            {"name": "height", "type": "INT", "widget": {"name": "height"}, "link": 554},
            {"name": "length", "type": "INT", "widget": {"name": "length"}, "link": 555},
        ],
        "widgets_values": ["", 1344, 768, 124],
    }
    normalized = template_store._normalize(_视频模板记录(i2v_node))

    assert [(f["node_id"], f["field"], f["binding"], f["default"]) for f in normalized["exposed"]
            if str(f.get("binding", "")).startswith("video_")] == [
        ("329", "width", "video_width", 1344),
        ("329", "height", "video_height", 768),
    ]
    assert "329" in template_store.ordered_node_ids(normalized)


def test_图像模板不因此多出宽高键():
    """零回归：EmptyLatentImage 仍只走 latent_* 口径，视频宽高绑定不得顺手污染图片模板。"""
    normalized = template_store._normalize({
        "workflow_data": {"nodes": [
            {"id": 4, "type": "EmptyLatentImage", "widgets_values": [576, 1024, 1]},
        ]},
    })

    assert [f["binding"] for f in normalized["exposed"]] == ["latent_width", "latent_height"]


def _参考图记录() -> dict:
    return {"workflow_data": {"nodes": [
        {"id": 90, "type": "PainterMiniMaxRefToVideo6", "title": "Ref2VA"},
        {"id": 81, "type": "LoadImage", "title": "参考图2"},
        {"id": 82, "type": "LoadImage", "title": "角色参考图"},
        {"id": 61, "type": "LoadImage", "title": "首帧底图"},
        {"id": 62, "type": "LoadImage", "title": "参考底图"},
    ]}}


def test_参考视频节点按字段名与标题识别参考图槽():
    """第 94 刀：图片3、4、5… = 角色参考图。模板编辑器没有手选 binding 的入口，
    只能靠字段名（ref_image_files 单控件）与节点标题（参考图N）把槽位标出来。"""
    record = _参考图记录()
    assert template_store._infer_binding(record, "90", "ref_image_files") == "reference_image_files"
    assert template_store._infer_binding(record, "81", "image") == "reference_image_2"
    assert template_store._infer_binding(record, "82", "image") == "reference_image"
    # 主底图 / 图生图输入不能被参考图标题规则抢走
    assert template_store._infer_binding(record, "61", "image") == "base_image"
    assert template_store._infer_binding(record, "62", "image") == "base_image"


# ---------------------------------------------------------------------------
# 第 100 刀：Ref2VA 槽位 binding 由**图位置**决定（不是字段名/标题）
# 真实形态：LoadImage → 缩放 → SetNode/GetNode → ref_images.ref_image_N；
# LoadAudio → Set/Get → ref_audios.ref_audio_N。模板编辑器只暴露 LoadImage/LoadAudio
# 自己的字段（image/audio），通用推断会给 base_image / voice_reference ⇒ 必须按槽位覆盖。
# ---------------------------------------------------------------------------
def _h3_ref2va_workflow() -> dict:
    """图片槽 0/1/2 + 音频槽 0/1，全部经 rgthree Set/Get 中转（照抄真实模板拓扑）。"""
    nodes = [
        {"id": 108, "type": "MiniMaxH3ReferenceToVideo", "inputs": [
            {"name": "ref_images.ref_image_0", "type": "IMAGE", "link": 10},
            {"name": "ref_images.ref_image_1", "type": "IMAGE", "link": 11},
            {"name": "ref_images.ref_image_2", "type": "IMAGE", "link": 12},
            {"name": "ref_images.ref_image_3", "type": "IMAGE", "link": None},
            {"name": "ref_audios.ref_audio_0", "type": "AUDIO", "link": 13},
            {"name": "ref_audios.ref_audio_1", "type": "AUDIO", "link": 14},
            {"name": "prompt", "type": "STRING", "widget": {"name": "prompt"}},
        ], "widgets_values": [""]},
        {"id": 97, "type": "LoadImage", "inputs": [
            {"name": "image", "type": "COMBO", "widget": {"name": "image"}},
        ]},
        {"id": 99, "type": "ImageScale", "inputs": [
            {"name": "image", "type": "IMAGE", "link": 1},
            {"name": "scale", "type": "FLOAT", "widget": {"name": "scale"}},
        ]},
        {"id": 98, "type": "SetNode", "widgets_values": ["图像1"],
         "inputs": [{"name": "IMAGE", "type": "IMAGE", "link": 2}]},
        {"id": 95, "type": "GetNode", "widgets_values": ["图像1"]},
        {"id": 101, "type": "LoadImage", "inputs": [
            {"name": "image", "type": "COMBO", "widget": {"name": "image"}},
        ]},
        {"id": 104, "type": "SetNode", "widgets_values": ["图像2"],
         "inputs": [{"name": "IMAGE", "type": "IMAGE", "link": 3}]},
        {"id": 96, "type": "GetNode", "widgets_values": ["图像2"]},
        {"id": 132, "type": "LoadImage", "inputs": [
            {"name": "image", "type": "COMBO", "widget": {"name": "image"}},
        ]},
        {"id": 131, "type": "SetNode", "widgets_values": ["图像3"],
         "inputs": [{"name": "IMAGE", "type": "IMAGE", "link": 4}]},
        {"id": 133, "type": "GetNode", "widgets_values": ["图像3"]},
        {"id": 138, "type": "LoadAudio", "inputs": [
            {"name": "audio", "type": "COMBO", "widget": {"name": "audio"}},
        ]},
        {"id": 139, "type": "SetNode", "widgets_values": ["参考音频1"],
         "inputs": [{"name": "AUDIO", "type": "AUDIO", "link": 5}]},
        {"id": 143, "type": "GetNode", "widgets_values": ["参考音频1"]},
        {"id": 154, "type": "LoadAudio", "inputs": [
            {"name": "audio", "type": "COMBO", "widget": {"name": "audio"}},
        ]},
        {"id": 155, "type": "SetNode", "widgets_values": ["参考音频2"],
         "inputs": [{"name": "AUDIO", "type": "AUDIO", "link": 6}]},
        {"id": 156, "type": "GetNode", "widgets_values": ["参考音频2"]},
    ]
    links = [
        [1, 97, 0, 99, 0, "IMAGE"],
        [2, 99, 0, 98, 0, "IMAGE"],
        [10, 95, 0, 108, 0, "IMAGE"],
        [3, 101, 0, 104, 0, "IMAGE"],
        [11, 96, 0, 108, 1, "IMAGE"],
        [4, 132, 0, 131, 0, "IMAGE"],
        [12, 133, 0, 108, 2, "IMAGE"],
        [5, 138, 0, 139, 0, "AUDIO"],
        [13, 143, 0, 108, 4, "AUDIO"],
        [6, 154, 0, 155, 0, "AUDIO"],
        [14, 156, 0, 108, 5, "AUDIO"],
    ]
    return {"nodes": nodes, "links": links}


def test_Ref2VA槽位按图位置绑定图片与音频():
    """图片1/2 = 首尾帧，图片3 = 第 1 张角色参考图；音频槽 N → reference_audio_{N+1}。"""
    record = {"workflow_data": _h3_ref2va_workflow()}
    assert template_store._ref2va_slot_bindings(record) == {
        ("97", "image"): "first_frame_image",
        ("97", "images"): "first_frame_image",
        ("101", "image"): "last_frame_image",
        ("101", "images"): "last_frame_image",
        ("132", "image"): "reference_image_1",
        ("132", "images"): "reference_image_1",
        ("138", "audio"): "reference_audio_1",
        ("154", "audio"): "reference_audio_2",
    }


def test_Ref2VA槽位binding覆盖编辑器通用默认():
    """编辑器发来 base_image / voice_reference（模板编辑器没有手选 binding 的入口）⇒ 槽位优先。"""
    normalized = template_store._normalize({
        "workflow_data": _h3_ref2va_workflow(),
        "exposed": [
            {"node_id": "97", "field": "image", "control": "image", "binding": "base_image"},
            {"node_id": "101", "field": "image", "control": "image", "binding": "base_image"},
            {"node_id": "132", "field": "image", "control": "image", "binding": "reference_image"},
            {"node_id": "138", "field": "audio", "control": "text", "binding": "voice_reference"},
            {"node_id": "154", "field": "audio", "control": "text", "binding": "voice_reference"},
            {"node_id": "108", "field": "prompt", "control": "textarea", "binding": "prompt"},
        ],
    })
    bindings = {(f["node_id"], f["field"]): f.get("binding") for f in normalized["exposed"]}
    assert bindings[("97", "image")] == "first_frame_image"
    assert bindings[("101", "image")] == "last_frame_image"
    assert bindings[("132", "image")] == "reference_image_1"
    assert bindings[("138", "audio")] == "reference_audio_1"
    assert bindings[("154", "audio")] == "reference_audio_2"
    assert bindings[("108", "prompt")] == "prompt"


def test_Ref2VA同一张图接两个槽时不猜():
    """一张图同时接图片1和图片2 ⇒ 两个槽都要它，一键一值做不到，宁可回到通用 binding。"""
    workflow = {
        "nodes": [
            {"id": 108, "type": "MiniMaxH3ReferenceToVideo", "inputs": [
                {"name": "ref_images.ref_image_0", "type": "IMAGE", "link": 10},
                {"name": "ref_images.ref_image_1", "type": "IMAGE", "link": 11},
            ]},
            {"id": 97, "type": "LoadImage", "inputs": [
                {"name": "image", "type": "COMBO", "widget": {"name": "image"}},
            ]},
            {"id": 98, "type": "SetNode", "widgets_values": ["图像1"],
             "inputs": [{"name": "IMAGE", "type": "IMAGE", "link": 1}]},
            {"id": 95, "type": "GetNode", "widgets_values": ["图像1"]},
            {"id": 94, "type": "GetNode", "widgets_values": ["图像1"]},
        ],
        "links": [
            [1, 97, 0, 98, 0, "IMAGE"],
            [10, 95, 0, 108, 0, "IMAGE"],
            [11, 94, 0, 108, 1, "IMAGE"],
        ],
    }
    bindings = template_store._ref2va_slot_bindings({"workflow_data": workflow})
    assert bindings == {}


def test_API格式Ref2VA槽位同样按图位置绑定():
    record = {"workflow_data": {
        "108": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
            "ref_images.ref_image_0": ["97", 0],
            "ref_images.ref_image_2": ["132", 0],
            "ref_audios.ref_audio_0": ["138", 0],
        }},
        "97": {"class_type": "LoadImage", "inputs": {"image": "first.png"}},
        "132": {"class_type": "LoadImage", "inputs": {"image": "ref.png"}},
        "138": {"class_type": "LoadAudio", "inputs": {"audio": "voice.mp3"}},
    }}
    assert template_store._ref2va_slot_bindings(record) == {
        ("97", "image"): "first_frame_image",
        ("97", "images"): "first_frame_image",
        ("132", "image"): "reference_image_1",
        ("132", "images"): "reference_image_1",
        ("138", "audio"): "reference_audio_1",
    }


# ── 第 106 刀 · 视频工作流模板能力（实稿发现 1/2）────────────────────────────

def test_video_template_caps_real_ref2va_only_first_frame_and_duration():
    """实机 Ref2VA 模板只暴露 首帧 + 提示词 + 时长(默认10) ⇒ 无尾帧、无参考图槽、10 秒。

    真实模板（0a891…）实测 exposed 仅 3 条：`97 image binding=first_frame_image`、
    `333 text binding=video_prompt`、`84 value binding=video_duration default=10`。
    编译层据此才不该声明「图片2=尾帧、图片3=角色参考图」（会被前端静默丢掉）。
    """
    record = {"exposed": [
        {"node_id": "97", "field": "image", "binding": "first_frame_image"},
        {"node_id": "333", "field": "text", "binding": "video_prompt"},
        {"node_id": "84", "field": "value", "binding": "video_duration", "default": 10},
    ]}
    assert template_store.video_template_caps(record) == {
        "supports_last_frame": False, "reference_slots": 0, "duration": 10,
    }


def test_video_template_caps_reference_slots_take_max_n():
    record = {"exposed": [
        {"node_id": "1", "field": "image", "binding": "last_frame_image"},
        {"node_id": "2", "field": "image", "binding": "reference_image_1"},
        {"node_id": "3", "field": "image", "binding": "reference_image_3"},
        {"node_id": "4", "field": "image", "binding": "reference_image_x"},
    ]}
    caps = template_store.video_template_caps(record)
    assert caps["supports_last_frame"] is True
    assert caps["reference_slots"] == 3      # 只认数字后缀，取最大 N


def test_video_template_caps_bulk_reference_control_means_unbounded():
    """批量控件（reference_image / reference_image_files）⇒ 99＝不设上限。"""
    for binding in ("reference_image", "reference_image_files"):
        caps = template_store.video_template_caps(
            {"exposed": [{"node_id": "1", "field": "image", "binding": binding}]})
        assert caps["reference_slots"] == 99, binding


def test_video_template_caps_missing_or_broken_record_is_all_zero():
    assert template_store.video_template_caps(None) == {
        "supports_last_frame": False, "reference_slots": 0, "duration": 0,
    }
    assert template_store.video_template_caps({}) == {
        "supports_last_frame": False, "reference_slots": 0, "duration": 0,
    }
    # duration 非数字 ⇒ 0（不猜）；其余字段类型异常不炸
    caps = template_store.video_template_caps(
        {"exposed": [{"node_id": "1", "binding": "video_duration", "default": "ten"}, None]})
    assert caps["duration"] == 0
