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


def test_图像模板不因此多出宽高键():
    """零回归：EmptyLatentImage 仍只走 latent_* 口径，视频宽高绑定不得顺手污染图片模板。"""
    normalized = template_store._normalize({
        "workflow_data": {"nodes": [
            {"id": 4, "type": "EmptyLatentImage", "widgets_values": [576, 1024, 1]},
        ]},
    })

    assert [f["binding"] for f in normalized["exposed"]] == ["latent_width", "latent_height"]
