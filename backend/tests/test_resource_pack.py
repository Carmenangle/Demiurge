import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "build_resource_pack", ROOT / "scripts" / "build_resource_pack.py"
)
assert SPEC and SPEC.loader
resource_pack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resource_pack)


def test_sanitize_recursively_removes_credentials():
    source = {
        "api_key": "private",
        "prompts": [{"content": "keep", "custom_include_headers": {"Authorization": "x"}}],
    }

    assert resource_pack.sanitize(source) == {"prompts": [{"content": "keep"}]}


def test_build_resource_pack_from_clean_fixture(tmp_path):
    source = tmp_path / "source"
    output = tmp_path / "output"
    preset = source / "presets" / "GrayWill-0.46-demiurge.json"
    regex = source / "backend" / "data" / "regex_scripts.json"
    preset.parent.mkdir(parents=True)
    regex.parent.mkdir(parents=True)
    preset.write_text(json.dumps({
        "api_key": "private",
        "extensions": {"regex_scripts": [{"name": "embedded"}]},
    }), encoding="utf-8")
    regex.write_text(json.dumps([{"name": "global"}]), encoding="utf-8")

    files = resource_pack.build(source, output)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert len(files) == 5
    assert manifest["embedded_regex_count"] == 1
    assert manifest["global_regex_count"] == 1
    built_preset = json.loads(
        (output / "preset" / "GrayWill-0.46-demiurge.json").read_text(encoding="utf-8")
    )
    assert "api_key" not in built_preset


def test_committed_resource_pack_is_complete():
    package = ROOT / "presets" / "Demiurge-presets-regex"
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["embedded_regex_count"] == 33
    assert manifest["global_regex_count"] == 24
    assert all((package / item["path"]).is_file() for item in manifest["files"])


# --- ComfyUI 工作流模板包 ---

ITEM_ID = "643baff143664bf7bb1afe44d6c37470"
H3_TYPE = "MiniMaxH3ImageToVideo"


def _fixture_record(**overrides):
    """最小可用的模板记录：视频节点 + 首/尾帧接线 + 四条手标绑定（#58 后）。"""
    graph = {
        "nodes": [
            {"id": 61, "type": "LoadImage", "inputs": [], "outputs": [{"name": "IMAGE"}],
             "widgets_values": ["a.png", None]},
            {"id": 329, "type": H3_TYPE,
             "inputs": [
                 {"name": "prompt", "type": "STRING", "link": None},
                 {"name": "first_frame", "type": "IMAGE", "link": 7},
                 {"name": "last_frame", "type": "IMAGE", "link": 9},
             ],
             "outputs": [{"name": "IMAGE"}],
             "widgets_values": {"videopreview": {"params": {"cos_url": "https://x.example/a.mp4"}},
                                "length": 124}},
        ],
        "links": [[7, 61, 0, 329, 1, "IMAGE"], [9, 61, 0, 329, 2, "IMAGE"]],
    }
    record = {
        "id": ITEM_ID,
        "name": "MiniMax H3 图生视频（首帧）",
        "source_path": "D:\\Elsewhere\\backend\\data\\workflows\\x.json",
        "created_at": 1.0,
        "updated_at": 2.0,
        "preserve_loras": True,
        "node_order": ["61"],
        "input_node_ids": ["61"],
        "output_node_ids": ["329"],
        "primary_output_node_id": "329",
        "exposed": [
            {"node_id": "61", "field": "image", "binding": "base_image"},
            {"node_id": "329", "field": "prompt", "binding": "video_prompt"},
            {"node_id": "329", "field": "length", "binding": "video_duration"},
            {"node_id": "73", "field": "image", "binding": "last_frame_image"},
        ],
        "workflow_data": graph,
    }
    record.update(overrides)
    return record


def _build_from(tmp_path, record, items=resource_pack.TEMPLATE_PACKAGE_ITEMS):
    root = tmp_path / "src"
    source = root / "backend" / "data" / "templates" / f"{ITEM_ID}.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "out"
    original = resource_pack.TEMPLATE_PACKAGE_ITEMS
    resource_pack.TEMPLATE_PACKAGE_ITEMS = items
    try:
        return resource_pack.build_template_pack(root, output), output
    finally:
        resource_pack.TEMPLATE_PACKAGE_ITEMS = original


def test_sanitize_template_keeps_graph_and_strips_machine_fields(tmp_path):
    cleaned = resource_pack.sanitize_template(_fixture_record())

    assert "source_path" not in cleaned and "created_at" not in cleaned
    assert "videopreview" not in cleaned["workflow_data"]["nodes"][1]["widgets_values"]
    assert cleaned["workflow_data"]["nodes"][1]["widgets_values"]["length"] == 124
    assert cleaned["preserve_loras"] is True
    # 原记录不能被就地改脏：同一份 dict 再次清洗仍应拿到预览态
    assert "videopreview" in _fixture_record()["workflow_data"]["nodes"][1]["widgets_values"]


def test_build_template_pack_writes_record_and_bare_graph(tmp_path):
    files, output = _build_from(tmp_path, _fixture_record())
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["package"] == resource_pack.TEMPLATE_PACKAGE
    assert manifest["template_count"] == 1
    assert all((output / item["path"]).is_file() for item in manifest["files"])
    template = json.loads(
        (output / "templates" / "MiniMax_H3_图生视频_首帧.json").read_text(encoding="utf-8")
    )
    assert template["workflow_data"]["nodes"][1]["type"] == H3_TYPE
    # 裸图与记录内嵌图同源：顶层就是 nodes/links
    graph = json.loads((output / "workflows" / "MiniMax_H3_图生视频_首帧.json").read_text(encoding="utf-8"))
    assert len(graph["nodes"]) == 2 and len(graph["links"]) == 2


def test_pack_rejects_missing_manual_binding(tmp_path):
    """只装图本体恢复不了的三条绑定必须在构建时判死，而不是等换机后静默丢。"""
    record = _fixture_record()
    record["exposed"] = [f for f in record["exposed"] if f["binding"] != "video_prompt"]

    try:
        _build_from(tmp_path, record)
    except ValueError as error:
        assert "video_prompt" in str(error)
    else:
        raise AssertionError("缺少手标绑定的模板包应该构建失败")


def test_pack_rejects_ref2va_backslide(tmp_path):
    """首帧链换型是这条链的根修复；类型退回 ref2va 必须让构建失败。"""
    record = _fixture_record()
    record["workflow_data"]["nodes"][1]["type"] = "MiniMaxH3ReferenceToVideo"

    try:
        _build_from(tmp_path, record)
    except ValueError as error:
        assert "MiniMaxH3ReferenceToVideo" in str(error)
    else:
        raise AssertionError("退回 ref2va 的模板包应该构建失败")


def test_pack_rejects_dangling_link(tmp_path):
    record = _fixture_record()
    record["workflow_data"]["links"] = []

    try:
        _build_from(tmp_path, record)
    except ValueError as error:
        assert "悬空引用" in str(error)
    else:
        raise AssertionError("悬空链路应该让构建失败")


def test_host_and_denylist_gates(tmp_path):
    stray = tmp_path / "stray.json"
    stray.write_text('{"u": "https://rh-images.example/x.mp4"}', encoding="utf-8")
    try:
        resource_pack._assert_allowed_hosts(stray)
    except ValueError as error:
        assert "rh-images.example" in str(error)
    else:
        raise AssertionError("非白名单外链应该被拦")

    named = tmp_path / "named.json"
    named.write_text('{"t": "《某个真实作品标题》"}', encoding="utf-8")
    try:
        resource_pack._assert_no_private_names(named, ("某个真实作品标题",))
    except ValueError as error:
        assert "真实专名" in str(error)
    else:
        raise AssertionError("本机真实专名应该被拦")
    # 干净检出没有专名表 ⇒ 闸自然不生效，不得判红
    resource_pack._assert_no_private_names(named, ())


def test_committed_template_pack_restores_every_binding():
    """换机重装的真实恢复证据：包里的记录过一遍 _normalize，六条绑定必须齐。

    video_width/video_height 由后端按节点类型自动补，另外四条（含 #58 的
    last_frame_image）只在 exposed 里手标过。
    """
    from app.services import template_store

    package = ROOT / "presets" / resource_pack.TEMPLATE_PACKAGE
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert all((package / item["path"]).is_file() for item in manifest["files"])

    entry = manifest["templates"][0]
    record = json.loads((package / entry["template_file"]).read_text(encoding="utf-8"))
    normalized = template_store._normalize(record)
    bindings = {field["binding"] for field in normalized["exposed"] if field.get("binding")}

    assert bindings == {
        "base_image",
        "video_prompt",
        "video_duration",
        "video_width",
        "video_height",
        "last_frame_image",
    }
    assert normalized["preserve_loras"] is True
    assert normalized["primary_output_node_id"] == "67"
    video_nodes = [
        node
        for node in normalized["workflow_data"]["nodes"]
        if str(node.get("type") or "").lower() in template_store._VIDEO_SIZE_NODE_TYPES
    ]
    assert [node["type"] for node in video_nodes] == [H3_TYPE]


# --- #58：尾帧 binding 化（2026-11-04）-------------------------------------

def test_pack_rejects_missing_last_frame_link(tmp_path):
    """#58：329.last_frame 没接线＝尾帧图静默丢弃，构建必须判死。"""
    record = _fixture_record()
    last = next(s for s in record["workflow_data"]["nodes"][1]["inputs"]
                if s["name"] == "last_frame")
    last["link"] = None
    record["workflow_data"]["links"] = [
        l for l in record["workflow_data"]["links"] if l[0] != 9
    ]

    try:
        _build_from(tmp_path, record)
    except ValueError as error:
        assert "尾帧链丢失" in str(error)
    else:
        raise AssertionError("329.last_frame 未接线的模板包应该构建失败")


def test_pack_rejects_missing_last_frame_binding(tmp_path):
    """尾帧 LoadImage 的 binding 丢了，前端 lastFrameImage 就无处注入。"""
    record = _fixture_record()
    record["exposed"] = [f for f in record["exposed"]
                         if f["binding"] != "last_frame_image"]

    try:
        _build_from(tmp_path, record)
    except ValueError as error:
        assert "last_frame_image" in str(error)
    else:
        raise AssertionError("缺少 last_frame_image 绑定的模板包应该构建失败")


def test_committed_template_pack_last_frame_is_wired():
    """已提交的模板包：尾帧变量必须真被读到，且库文件与投影逐字同步。"""
    package = ROOT / "presets" / resource_pack.TEMPLATE_PACKAGE
    graph = json.loads(
        (package / "workflows" / "MiniMax_H3_图生视频_首帧.json").read_text(encoding="utf-8")
    )
    node = next(n for n in graph["nodes"] if str(n.get("id")) == "329")
    assert node["type"] == H3_TYPE
    last = next(s for s in node["inputs"] if s["name"] == "last_frame")
    assert last.get("link") is not None, "329.last_frame 未接线 ⇒ 尾帧图静默丢弃"
    link = next(l for l in graph["links"] if l[0] == last.get("link"))
    assert str(link[3]) == "329"
    src = next(n for n in graph["nodes"] if str(n.get("id")) == str(link[1]))
    assert src["type"] == "GetNode", link
    assert "尾帧图像" in str(src.get("widgets_values"))

    record = json.loads(
        (package / "templates" / "MiniMax_H3_图生视频_首帧.json").read_text(encoding="utf-8")
    )
    bound = [f for f in record["exposed"] if f.get("binding") == "last_frame_image"]
    assert len(bound) == 1
    assert str(bound[0]["node_id"]) == "73" and bound[0]["field"] == "image"

    # 本机工作流库文件是 gitignored 的运行态；干净检出没有它 ⇒ 不判红。
    lib = ROOT / "backend" / "data" / "workflows" / "MiniMax_H3_图生视频_首帧.json"
    if not lib.is_file():
        return
    lib_graph = json.loads(lib.read_text(encoding="utf-8"))
    lib_node = next(n for n in lib_graph["nodes"] if str(n.get("id")) == "329")
    lib_last = next(s for s in lib_node["inputs"] if s["name"] == "last_frame")
    assert lib_last.get("link") == last.get("link"), "库文件重导入会退回「无尾帧」"


def test_committed_template_pack_api_graph_has_both_frames():
    """#58 加码：UI 接线在≠API 提交生效。

    第 77 刀把尾帧支路 71/73/75 留成 mode=4（mute），ui_to_api 的 is_skipped
    会整条剔除 ⇒ 329.last_frame 在 API 图里根本不存在。只查 UI link 抓不到这个。
    """
    from app.services.workflow_convert import ui_to_api

    package = ROOT / "presets" / resource_pack.TEMPLATE_PACKAGE
    graph = json.loads(
        (package / "workflows" / "MiniMax_H3_图生视频_首帧.json").read_text(encoding="utf-8")
    )
    api = ui_to_api(graph, "")   # 离线展平，不查 object_info
    node = api.get("329")
    assert node and node["class_type"] == H3_TYPE
    for name in ("first_frame", "last_frame"):
        assert isinstance(node["inputs"].get(name), list), (
            f"{name} 在 API 图里缺席：尾帧支路可能仍是 mode=4 静音"
        )
    assert node["inputs"]["first_frame"] != node["inputs"]["last_frame"]
    assert "73" in api, "尾帧 LoadImage 被剔除，lastFrameImage 注入口不存在"
