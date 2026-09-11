"""workflow_parser 字段解析测试。

重点覆盖：ComfyUI 把 widget「转成输入槽」后同名字段去重——同名字段若不去重，
下游前端 fieldKey 与运行期注入 key（均为 节点id.字段名）会冲突，导致勾一个连带
勾另一个、提示词注入无法定位到底写哪个字段。
"""
from app.services.workflow_parser import parse_workflow


def _fields(node):
    return {f["name"]: f for f in node["fields"]}


def test_clip_text_widget_and_input_collision_dedup():
    # CLIPTextEncode：text 同时在 inputs（widget 被转成输入槽，未连线、空值）
    # 与 widgets_values（真实正向提示词）里出现。
    wf = {
        "nodes": [
            {
                "id": 18,
                "type": "CLIPTextEncode",
                "inputs": [
                    {"name": "clip", "link": 5},
                    {"name": "text", "link": None},
                ],
                "widgets_values": ["masterpiece, score_9, nsfw"],
            }
        ]
    }
    node = parse_workflow(wf)[0]
    text_fields = [f for f in node["fields"] if f["name"] == "text"]
    assert len(text_fields) == 1  # 去重后只剩一个
    assert text_fields[0]["value"] == "masterpiece, score_9, nsfw"
    assert text_fields[0]["linked"] is False
    # 真·连线字段（clip）保留且标记 linked
    assert _fields(node)["clip"]["linked"] is True


def test_linked_version_wins_over_widget():
    # text 已连线时，连线版本胜出（不可暴露），忽略陈旧的 widget 默认值。
    wf = {
        "nodes": [
            {
                "id": 1,
                "type": "CLIPTextEncode",
                "inputs": [{"name": "text", "link": 9}],
                "widgets_values": ["stale default"],
            }
        ]
    }
    text_fields = [f for f in parse_workflow(wf)[0]["fields"] if f["name"] == "text"]
    assert len(text_fields) == 1
    assert text_fields[0]["linked"] is True


def test_lora_loader_model_only_widget_names():
    # LoraLoaderModelOnly 的 widgets_values 应映射成 lora_name / strength_model，
    # 而非回退的 widget_0 / widget_1（否则用户无从标注 lora 语义）。
    wf = {
        "nodes": [
            {
                "id": 19,
                "type": "LoraLoaderModelOnly",
                "inputs": [{"name": "model", "link": 3}],
                "widgets_values": ["Krea2-真人-NSW.safetensors", 0.8],
            }
        ]
    }
    fields = {f["name"]: f for f in parse_workflow(wf)[0]["fields"]}
    assert fields["lora_name"]["value"] == "Krea2-真人-NSW.safetensors"
    assert fields["strength_model"]["value"] == 0.8
    assert "widget_0" not in fields and "widget_1" not in fields


def test_distinct_field_names_untouched():
    # 名字不同的字段一律保留，去重不误伤。
    wf = {
        "nodes": [
            {
                "id": 4,
                "type": "EmptyLatentImage",
                "inputs": [],
                "widgets_values": [1024, 768, 1],
            }
        ]
    }
    names = [f["name"] for f in parse_workflow(wf)[0]["fields"]]
    assert names == ["width", "height", "batch_size"]


def test_loadaudio_and_indextts_widget_names():
    # 音频节点 widgets_values 应映射成真实字段名（audio / text / Happy…），而非 widget_N，
    # 否则用户无从标注音频语义（参考音轨 / 台词 / 情感向量）。
    wf = {
        "nodes": [
            {
                "id": 1,
                "type": "LoadAudio",
                "inputs": [
                    {"name": "audio", "link": None},
                    {"name": "audioUI", "link": None},
                    {"name": "upload", "link": None},
                ],
                "widgets_values": ["", None, None],
            },
            {
                "id": 3,
                "type": "IndexTTS25EmotionVectorNode",
                "inputs": [
                    {"name": "reference_audio", "link": 1},
                    {"name": "text", "link": None},
                    {"name": "Happy", "link": None},
                    {"name": "Neutral", "link": None},
                ],
                "widgets_values": [
                    "你好。", "ZH", 1, "on", 0.8, 0.8, 30, 3, 10, 0, 1500, 120, 200,
                    True, 0, 0, 0, 0, 0, 0, 0, 0, 1, False, False,
                ],
            },
        ]
    }
    nodes = parse_workflow(wf)
    loadaudio = {f["name"]: f for f in nodes[0]["fields"]}
    indextts = {f["name"]: f for f in nodes[1]["fields"]}
    # LoadAudio：audio 字段正确映射，而非 widget_0
    assert "audio" in loadaudio
    assert "widget_0" not in loadaudio
    # IndexTTS：text / Happy / Neutral 映射正确，带真实值
    assert indextts["text"]["value"] == "你好。"
    assert indextts["Happy"]["value"] == 0
    assert indextts["Neutral"]["value"] == 1
    assert "widget_0" not in indextts
