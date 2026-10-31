"""workflow_flatten：subgraph 内联 / 提升 widget / Set-Get 线性化 / UI-only 剔除。"""
from app.services import workflow_convert, workflow_flatten


def _widget(name: str, link=None) -> dict:
    """带 widget 元数据的输入（占用 widgets_values 一位）。"""
    return {"name": name, "link": link, "widget": {"name": name}}


def _edge_ids(flat: dict) -> set:
    return {(str(e[1]), int(e[2]), str(e[3]), int(e[4])) for e in flat["links"]}


def _node_ids(flat: dict) -> set:
    return {str(n["id"]) for n in flat["nodes"]}


# ---------------------------------------------------------------- 向后兼容

def test_flatten_is_noop_for_workflow_without_frontend_structures():
    workflow = {
        "nodes": [{"id": 1, "type": "LoadImage", "inputs": [], "outputs": [],
                   "widgets_values": ["a.png"]}],
        "links": [],
    }

    flat, overlay = workflow_flatten.flatten(workflow)

    assert flat is workflow          # 同一对象 ⇒ 调用方输出逐字节不变
    assert overlay == {}
    assert workflow_flatten.needs_flatten(workflow) is False


def test_flatten_is_noop_for_api_format_workflow():
    api_format = {"1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}}

    flat, overlay = workflow_flatten.flatten(api_format)

    assert flat is api_format
    assert overlay == {}


def test_ui_to_api_keeps_old_output_for_workflow_without_subgraphs(monkeypatch):
    monkeypatch.setattr(workflow_convert, "_object_info", lambda *_a: {})
    workflow = {
        "nodes": [{
            "id": 12, "type": "CLIPLoader",
            "inputs": [_widget("clip_name"), _widget("type"), _widget("device")],
            "widgets_values": ["clip.safetensors", "krea2", "default"],
        }],
        "links": [],
    }

    api = workflow_convert.ui_to_api(workflow, "http://127.0.0.1:8188")

    assert api == {"12": {"class_type": "CLIPLoader", "inputs": {
        "clip_name": "clip.safetensors", "type": "krea2", "device": "default"}}}


# ---------------------------------------------------------------- subgraph

def _port_link(link_id: int, slot: int, target: int, target_slot: int, type_="IMAGE") -> dict:
    return {"id": link_id, "origin_id": -10, "origin_slot": slot,
            "target_id": target, "target_slot": target_slot, "type": type_}


def _out_link(link_id: int, origin: int, origin_slot: int, slot: int, type_="IMAGE") -> dict:
    return {"id": link_id, "origin_id": origin, "origin_slot": origin_slot,
            "target_id": -20, "target_slot": slot, "type": type_}


def _scale_node(node_id: int, image_link: int, out_link: int,
                widgets_values: list, extra_inputs: list | None = None) -> dict:
    return {"id": node_id, "type": "ImageScale",
            "inputs": [{"name": "image", "link": image_link},
                       *(_widget("upscale_method"), _widget("scale_by")),
                       *(extra_inputs or [])],
            "outputs": [{"name": "IMAGE", "links": [out_link]}],
            "widgets_values": widgets_values}


def _skeleton(subgraph: dict, instance_inputs: list, subgraph_id: str = "SG") -> dict:
    """A(1) --port_in--> 实例(2) --port_out--> SaveImage(3) 的最小骨架。"""
    return {
        "nodes": [
            {"id": 1, "type": "LoadImage", "inputs": [],
             "outputs": [{"name": "IMAGE", "links": [100]}], "widgets_values": ["a.png"]},
            {"id": 2, "type": subgraph_id, "inputs": instance_inputs,
             "outputs": [{"name": "IMAGE", "links": [200]}]},
            {"id": 3, "type": "SaveImage", "inputs": [{"name": "images", "link": 200}],
             "outputs": [], "widgets_values": ["p"]},
        ],
        "links": [[100, 1, 0, 2, 0, "IMAGE"], [200, 2, 0, 3, 0, "IMAGE"]],
        "definitions": {"subgraphs": [{**subgraph, "id": subgraph_id}]},
    }


def _simple_scale_subgraph() -> dict:
    return {"nodes": [_scale_node(10, 11, 12, ["nearest-exact", 2])],
            "links": [_port_link(11, 0, 10, 0), _out_link(12, 10, 0, 0)]}


def test_subgraph_instance_is_inlined_and_ports_rewired():
    workflow = _skeleton(_simple_scale_subgraph(),
                         instance_inputs=[{"name": "image", "link": 100}])

    flat, _ = workflow_flatten.flatten(workflow)

    assert _node_ids(flat) == {"1", "2~10", "3"}          # 实例节点本身已消失
    assert _edge_ids(flat) == {("1", 0, "2~10", 0), ("2~10", 0, "3", 0)}
    assert flat["definitions"] == {"subgraphs": []}


def test_nested_subgraph_is_inlined_recursively():
    inner = {"nodes": [_scale_node(30, 31, 32, ["nearest-exact", 1])],
             "links": [_port_link(31, 0, 30, 0), _out_link(32, 30, 0, 0)]}
    outer = {
        "nodes": [
            {"id": 20, "type": "INNER", "inputs": [{"name": "image", "link": 21}],
             "outputs": [{"name": "IMAGE", "links": [22]}]},
            _scale_node(23, 22, 24, ["nearest-exact", 2]),
        ],
        "links": [_port_link(21, 0, 20, 0),
                  {"id": 22, "origin_id": 20, "origin_slot": 0, "target_id": 23,
                   "target_slot": 0, "type": "IMAGE"},
                  _out_link(24, 23, 0, 0)],
    }
    workflow = _skeleton(outer, [{"name": "image", "link": 100}], subgraph_id="OUTER")
    workflow["definitions"]["subgraphs"].append({**inner, "id": "INNER"})

    flat, _ = workflow_flatten.flatten(workflow)

    assert _node_ids(flat) == {"1", "2~20~30", "2~23", "3"}
    assert _edge_ids(flat) == {
        ("1", 0, "2~20~30", 0), ("2~20~30", 0, "2~23", 0), ("2~23", 0, "3", 0),
    }


def test_ui_to_api_of_subgraph_workflow_has_no_frontend_virtual_types(monkeypatch):
    monkeypatch.setattr(workflow_convert, "_object_info", lambda *_a: {})
    workflow = _skeleton(_simple_scale_subgraph(),
                         instance_inputs=[{"name": "image", "link": 100}])

    api = workflow_convert.ui_to_api(workflow, "http://127.0.0.1:8188")

    assert set(api) == {"1", "2~10", "3"}
    assert {node["class_type"] for node in api.values()} == {
        "LoadImage", "ImageScale", "SaveImage"}
    assert api["2~10"]["inputs"] == {
        "image": ["1", 0], "upscale_method": "nearest-exact", "scale_by": 2}


# ---------------------------------------------------------------- 提升 widget

def _promoted_workflow(instance_link, instance_widgets_values) -> dict:
    """实例(2) 的 upscale_method 被提升；instance_link=None ⇒ 它是 widget（值在实例上）。"""
    subgraph = {
        "nodes": [_scale_node(10, 11, 12, ["nearest-exact", 2])],
        "links": [_port_link(11, 0, 10, 0), _port_link(13, 1, 10, 1, "COMBO"),
                  _out_link(12, 10, 0, 0)],
    }
    workflow = _skeleton(subgraph, instance_inputs=[
        {"name": "image", "link": 100},
        {"name": "upscale_method", "link": instance_link,
         "widget": {"name": "upscale_method"}},
    ])
    workflow["nodes"][1]["widgets_values"] = instance_widgets_values
    if instance_link is not None:
        # 独立上游 + 独立 link：把实例的 widget 口转成连线
        workflow["nodes"].insert(1, {
            "id": 4, "type": "Int", "inputs": [_widget("Number")],
            "outputs": [{"name": "INT", "links": [400]}], "widgets_values": [7]})
        workflow["links"].append([400, 4, 0, 2, 1, "COMBO"])
    return workflow


def test_promoted_widget_value_overrides_stale_inner_value(monkeypatch):
    monkeypatch.setattr(workflow_convert, "_object_info", lambda *_a: {})
    workflow = _promoted_workflow(instance_link=None, instance_widgets_values=["bislerp"])

    flat, overlay = workflow_flatten.flatten(workflow)
    api = workflow_convert.ui_to_api(workflow, "")

    assert overlay == {"2~10": {"upscale_method": "bislerp"}}
    # 体内节点自己是 "nearest-exact"（旧值），必须被实例值覆盖
    assert api["2~10"]["inputs"]["upscale_method"] == "bislerp"


def test_promoted_widget_that_is_linked_is_not_overridden(monkeypatch):
    monkeypatch.setattr(workflow_convert, "_object_info", lambda *_a: {})
    workflow = _promoted_workflow(instance_link=400, instance_widgets_values=["bislerp"])

    flat, overlay = workflow_flatten.flatten(workflow)
    api = workflow_convert.ui_to_api(workflow, "")

    assert overlay == {}                                  # 已连线 ⇒ 不写 overlay
    assert api["2~10"]["inputs"]["upscale_method"] == ["4", 0]


def test_promoted_widget_index_advances_past_linked_widget():
    """被转成连线的 widget 仍占 widgets_values 位，后面的提升值不能整体错位。"""
    subgraph = {
        "nodes": [_scale_node(10, 11, 12, ["nearest-exact", 2])],
        "links": [_port_link(11, 0, 10, 0), _port_link(13, 1, 10, 1, "COMBO"),
                  _port_link(14, 2, 10, 2, "COMBO"), _out_link(12, 10, 0, 0)],
    }
    workflow = _skeleton(subgraph, instance_inputs=[
        {"name": "image", "link": 100},
        {"name": "upscale_method", "link": 400, "widget": {"name": "upscale_method"}},
        {"name": "scale_by", "link": None, "widget": {"name": "scale_by"}},
    ])
    workflow["nodes"][1]["widgets_values"] = ["bislerp", 3]
    workflow["nodes"].insert(1, {
        "id": 4, "type": "Int", "inputs": [_widget("Number")],
        "outputs": [{"name": "INT", "links": [400]}], "widgets_values": [7]})
    workflow["links"].append([400, 4, 0, 2, 1, "COMBO"])

    _, overlay = workflow_flatten.flatten(workflow)

    # 第 0 格给了已连线的 upscale_method ⇒ 只剩第 1 格给 scale_by
    assert overlay == {"2~10": {"scale_by": 3}}


# ---------------------------------------------------------------- Set / Get

def test_setnode_passthrough_and_getnode_are_linearized():
    workflow = {
        "nodes": [
            {"id": 1, "type": "LoadImage", "inputs": [],
             "outputs": [{"name": "IMAGE", "links": [100]}], "widgets_values": ["a.png"]},
            {"id": 2, "type": "SetNode", "title": "Set_Result",
             "inputs": [{"name": "IMAGE", "link": 100}],
             "outputs": [{"name": "*", "links": [101]}], "widgets_values": ["Result"]},
            {"id": 3, "type": "GetNode", "title": "Get_Result",
             "inputs": [], "outputs": [{"name": "IMAGE", "links": [102]}],
             "widgets_values": ["Result"]},
            {"id": 4, "type": "SaveImage", "inputs": [{"name": "images", "link": 101}],
             "outputs": [], "widgets_values": ["p"]},
            {"id": 5, "type": "PreviewImage", "inputs": [{"name": "images", "link": 102}],
             "outputs": []},
        ],
        "links": [[100, 1, 0, 2, 0, "IMAGE"], [101, 2, 0, 4, 0, "IMAGE"],
                  [102, 3, 0, 5, 0, "IMAGE"]],
    }

    flat, _ = workflow_flatten.flatten(workflow)

    assert _node_ids(flat) == {"1", "4", "5"}             # Set/Get 均已剔除
    assert _edge_ids(flat) == {("1", 0, "4", 0), ("1", 0, "5", 0)}


def test_getnode_without_matching_setnode_drops_dangling_edge():
    workflow = {
        "nodes": [
            {"id": 3, "type": "GetNode", "title": "Get_missing",
             "inputs": [], "outputs": [{"name": "IMAGE", "links": [102]}],
             "widgets_values": ["missing"]},
            {"id": 5, "type": "PreviewImage", "inputs": [{"name": "images", "link": 102}],
             "outputs": []},
        ],
        "links": [[102, 3, 0, 5, 0, "IMAGE"]],
    }

    flat, _ = workflow_flatten.flatten(workflow)

    assert _node_ids(flat) == {"5"}
    assert flat["links"] == []


def test_ui_only_frontend_nodes_are_dropped(monkeypatch):
    monkeypatch.setattr(workflow_convert, "_object_info", lambda *_a: {})
    workflow = {
        "nodes": [
            {"id": 1, "type": "LoadImage", "inputs": [],
             "outputs": [{"name": "IMAGE", "links": [100]}], "widgets_values": ["a.png"]},
            {"id": 9, "type": "Fast Groups Bypasser (rgthree)", "inputs": [],
             "outputs": [], "widgets_values": [True]},
        ],
        "links": [],
    }

    flat, _ = workflow_flatten.flatten(workflow)
    api = workflow_convert.ui_to_api(workflow, "")

    assert workflow_flatten.needs_flatten(workflow) is True
    assert _node_ids(flat) == {"1"}
    assert set(api) == {"1"}


def test_combined_fixture_leaves_no_frontend_trace(monkeypatch):
    """综合：嵌套 subgraph + 提升 widget + Set/Get，一次走完不留前端痕迹。"""
    monkeypatch.setattr(workflow_convert, "_object_info", lambda *_a: {})
    inner = {"nodes": [_scale_node(30, 31, 32, ["nearest-exact", 1])],
             "links": [_port_link(31, 0, 30, 0), _out_link(32, 30, 0, 0)]}
    outer = {
        "nodes": [
            {"id": 20, "type": "INNER", "inputs": [{"name": "image", "link": 21}],
             "outputs": [{"name": "IMAGE", "links": [22]}]},
            _scale_node(23, 22, 24, ["nearest-exact", 2]),
        ],
        "links": [_port_link(21, 0, 20, 0),
                  {"id": 22, "origin_id": 20, "origin_slot": 0, "target_id": 23,
                   "target_slot": 0, "type": "IMAGE"},
                  _port_link(27, 1, 23, 1, "COMBO"),       # upscale_method 提升到实例
                  _out_link(24, 23, 0, 0)],
    }
    workflow = _skeleton(outer, [
        {"name": "image", "link": 100},
        {"name": "upscale_method", "link": None, "widget": {"name": "upscale_method"}},
    ], subgraph_id="OUTER")
    workflow["definitions"]["subgraphs"].append({**inner, "id": "INNER"})
    workflow["nodes"][1]["widgets_values"] = ["lanczos"]
    workflow["nodes"][2] = {"id": 3, "type": "SetNode", "title": "Set_Result",
                            "inputs": [{"name": "IMAGE", "link": 200}],
                            "outputs": [{"name": "*", "links": [300]}],
                            "widgets_values": ["Result"]}
    workflow["nodes"].append({"id": 5, "type": "SaveImage",
                              "inputs": [{"name": "images", "link": 300}],
                              "outputs": [], "widgets_values": ["p"]})
    workflow["links"].append([300, 3, 0, 5, 0, "IMAGE"])

    flat, overlay = workflow_flatten.flatten(workflow)
    api = workflow_convert.ui_to_api(workflow, "")

    assert _node_ids(flat) == {"1", "2~20~30", "2~23", "5"}
    assert _edge_ids(flat) == {("1", 0, "2~20~30", 0), ("2~20~30", 0, "2~23", 0),
                               ("2~23", 0, "5", 0)}
    assert flat["definitions"] == {"subgraphs": []}
    assert overlay == {"2~23": {"upscale_method": "lanczos"}}
    assert api["2~23"]["inputs"] == {
        "image": ["2~20~30", 0], "upscale_method": "lanczos", "scale_by": 2}
