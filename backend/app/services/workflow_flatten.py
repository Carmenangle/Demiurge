"""前端图展平：subgraph 实体内联 + 提升 widget 下传 + rgthree Set/Get 线性化 + UI-only 剔除。

ComfyUI 的 ``/prompt`` 只接受**扁平 API 图**，而编辑器导出的工作流可以是嵌套
subgraph + rgthree 变量节点 + 纯前端节点。前端 ``graphToPrompt()`` 会在提交前完成
这些展开；后端若不做同样的事，就会把前端虚拟类型当真实节点提交，
症状为 ``missing_node_type: Node 'SetNode' not found``（或 subgraph UUID 未注册）。

本模块只做「UI 格式 → 扁平 UI 格式」，**不产出 API 格式**；产出仍交由
:func:`app.services.workflow_convert.ui_to_api` 消费，保证 widgets_values → inputs
的语义只有一个属主。

两个关键约定：

1. **端口**（subgraph）：体内 ``origin_id == -10`` 表示「本 subgraph 的输入端口
   ``origin_slot``」；``target_id == -20`` 表示「本 subgraph 的输出端口
   ``target_slot``」。实例节点在父层，端口连线两端分别落在父层与体内。
2. **提升 widget**：subgraph 内部节点的 widget 可被「提升」到实例上——值存在**实例
   自己的** ``widgets_values`` 里，体内节点保留的是**旧值**。前端提交前会用实例值
   覆盖体内同名 widget。本模块把这份覆盖收集成 overlay（扁平节点 id → {widget 名: 值}），
   由 :func:`ui_to_api` 落地。

向后兼容：工作流不含 subgraph / Set/Get / 前端专属节点时，:func:`flatten` 原样返回
同一对象，调用方输出逐字节不变。
"""
from __future__ import annotations

import copy

SET_NODE = "SetNode"
GET_NODE = "GetNode"
# 只在 ComfyUI 前端存在、后端无实现、必须由本模块剔除的类型。
# （Note / MarkdownNote / Reroute / PrimitiveNode 由 ui_to_api 的 PASSTHROUGH_TYPES 负责。）
_UI_ONLY_DROP = {SET_NODE, GET_NODE,
                 "Fast Groups Bypasser (rgthree)", "Fast Groups Muter (rgthree)"}
_IN_PORT, _OUT_PORT = "-10", "-20"


def needs_flatten(workflow: dict) -> bool:
    """是否含前端专属结构（需要展平）。无则调用方必须原样走老路径。"""
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return False
    if (workflow.get("definitions") or {}).get("subgraphs"):
        return True
    return any(isinstance(n, dict) and str(n.get("type")) in _UI_ONLY_DROP for n in nodes)


def _norm_link(raw) -> dict | None:
    """统一 link 表示：顶层 ``links`` 是数组，subgraph 内 ``links`` 是 dict。"""
    if isinstance(raw, list) and len(raw) >= 5:
        return {"id": raw[0], "origin_id": str(raw[1]), "origin_slot": int(raw[2] or 0),
                "target_id": str(raw[3]), "target_slot": int(raw[4] or 0),
                "type": raw[5] if len(raw) > 5 else "*"}
    if isinstance(raw, dict):
        return {"id": raw.get("id"), "origin_id": str(raw.get("origin_id")),
                "origin_slot": int(raw.get("origin_slot") or 0),
                "target_id": str(raw.get("target_id")),
                "target_slot": int(raw.get("target_slot") or 0),
                "type": raw.get("type") or "*"}
    return None


def _var_key(node: dict) -> str:
    """rgthree Set/Get 的配对键 = 变量名。

    ⚠ 不是 title：SetNode 的 title 是 ``Set_Result`` 而 GetNode 是 ``Get_Result``，
    两者配不上；真正的键在 ``widgets_values[0]``（都是 ``Result``）。
    """
    wv = node.get("widgets_values")
    if isinstance(wv, list) and wv and isinstance(wv[0], str) and wv[0].strip():
        return wv[0].strip()
    title = str(node.get("title") or "").strip()
    for prefix in ("Set_", "Get_"):
        if title.startswith(prefix):
            return title[len(prefix):]
    return title


class _Graph:
    """可变图：``nodes``(id → node) + ``links``(id → edge)；link 表是唯一连线真源。"""

    def __init__(self, nodes: list, links: list):
        self.nodes: dict[str, dict] = {}
        for node in nodes or []:
            if isinstance(node, dict):
                self.nodes[str(node.get("id"))] = copy.deepcopy(node)
        self.links: dict = {}
        self.overlay: dict[str, dict] = {}      # 扁平节点 id → {widget 名: 提升值}
        self._seq = 0
        for raw in links or []:
            edge = _norm_link(raw)
            if edge is None or edge["id"] is None:
                continue
            self.links[edge["id"]] = edge
            if isinstance(edge["id"], int) and edge["id"] > self._seq:
                self._seq = edge["id"]

    def new_link(self, origin_id, origin_slot, target_id, target_slot, type_="*") -> str:
        self._seq += 1
        link_id = "L%d" % self._seq
        self.links[link_id] = {"id": link_id, "origin_id": str(origin_id),
                               "origin_slot": int(origin_slot),
                               "target_id": str(target_id), "target_slot": int(target_slot),
                               "type": type_}
        return link_id

    def _incident(self, node_id: str) -> list:
        return [edge for edge in self.links.values()
                if edge["origin_id"] == node_id or edge["target_id"] == node_id]

    def splice(self, instance_id: str, body_nodes: list, body_links: list,
               body_overlay: dict, prefix: str, instance_node: dict) -> None:
        """把（已递归展平的）subgraph 体内联进本图，替换实例节点 ``instance_id``。"""
        idmap: dict[str, str] = {}
        for node in body_nodes:
            raw_id = str(node.get("id"))
            new_id = "%s~%s" % (prefix, raw_id)
            idmap[raw_id] = new_id
            cloned = copy.deepcopy(node)
            cloned["id"] = new_id
            self.nodes[new_id] = cloned
        for key, values in (body_overlay or {}).items():   # 递归层级的 overlay 重新加前缀
            self.overlay["%s~%s" % (prefix, key)] = dict(values)

        incoming = {edge["target_slot"]: edge for edge in self._incident(instance_id)
                    if edge["target_id"] == instance_id}
        outgoing: dict[int, list] = {}
        for edge in self._incident(instance_id):
            if edge["origin_id"] == instance_id:
                outgoing.setdefault(edge["origin_slot"], []).append(edge)
        self.nodes.pop(instance_id, None)
        for edge in self._incident(instance_id):
            self.links.pop(edge["id"], None)

        # 端口 → 体内 (节点 raw id, 输入槽)；连线改写与提升 widget 落点都用它
        ports: dict[int, tuple] = {}
        for edge in body_links:
            if edge["origin_id"] == _IN_PORT:
                ports[edge["origin_slot"]] = (edge["target_id"], edge["target_slot"])
        self._collect_promoted(instance_node, ports, idmap)

        for edge in body_links:
            origin_id, origin_slot = edge["origin_id"], edge["origin_slot"]
            target_id, target_slot = edge["target_id"], edge["target_slot"]

            if target_id == _OUT_PORT:          # 体内结果 → 实例输出端口 → 扇出下游
                if origin_id == _IN_PORT:       # 输入直通输出（罕见）
                    upstream = incoming.get(origin_slot)
                    if upstream is None:
                        continue
                    src = (upstream["origin_id"], upstream["origin_slot"])
                elif origin_id in idmap:
                    src = (idmap[origin_id], origin_slot)
                else:
                    continue
                for carried in outgoing.get(target_slot, []):
                    self.new_link(src[0], src[1], carried["target_id"],
                                  carried["target_slot"], edge["type"])
                continue

            if origin_id == _IN_PORT:           # 实例输入端口 → 上游真实源
                upstream = incoming.get(origin_slot)
                if upstream is None:
                    continue                    # 该输入未连（widget 口即属此类）⇒ 本边丢
                origin_id, origin_slot = upstream["origin_id"], upstream["origin_slot"]
            elif origin_id in idmap:
                origin_id = idmap[origin_id]
            else:
                continue                        # 引用体内节点之外的 id（非法）⇒ 丢
            if target_id not in idmap:
                continue
            self.new_link(origin_id, origin_slot, idmap[target_id], target_slot, edge["type"])

    def _collect_promoted(self, instance_node: dict, ports: dict, idmap: dict) -> None:
        """实例上被提升的 widget 值 → overlay（覆盖体内节点同名 widget 的旧值）。

        ``widgets_values`` 按「带 widget 元数据的实例输入」出现顺序一一对应，且
        **被转成连线的 widget 仍占位**（与普通节点同规则）⇒ 下标总是推进；但已连线的
        口不写 overlay（值以连线为准，否则会把上游链打断）。
        落点名取**体内节点自己的输入名**：实例侧名字可能被 UI 去重加后缀
        （如 ``lllite_name_1``），直接用它会在 API 图里造出节点不认识的输入。
        """
        widget_values = instance_node.get("widgets_values")
        if not isinstance(widget_values, list) or not widget_values:
            return
        cursor = 0
        for index, item in enumerate(instance_node.get("inputs") or []):
            if not isinstance(item, dict) or not isinstance(item.get("widget"), dict):
                continue
            if cursor >= len(widget_values):
                break
            value, cursor = widget_values[cursor], cursor + 1
            if item.get("link") is not None:
                continue                        # 该 widget 已被转成连线 ⇒ 值以连线为准
            target = ports.get(index)
            if target is None:
                continue
            flat_id = idmap.get(str(target[0]))
            body_node = self.nodes.get(flat_id) if flat_id else None
            if body_node is None:
                continue
            body_inputs = body_node.get("inputs") or []
            name = ""
            if target[1] < len(body_inputs) and isinstance(body_inputs[target[1]], dict):
                name = str(body_inputs[target[1]].get("name") or "")
            if not name:
                name = str(item.get("name") or item["widget"].get("name") or "").strip()
            if name:
                self.overlay.setdefault(flat_id or "", {})[name] = value  # flat_id 为 None 时上方已 continue

    def linearize_set_get(self) -> None:
        """rgthree Set/Get → 把 Set/Get 的下游直连到该变量的真实源头。

        ⚠ 两侧都要重定向：GetNode 的下游自不必说；rgthree 的 **SetNode 也有直通输出**
        （output0 == 其 input0），其下游同样必须改接到真实源头，否则 SetNode 被剔除时
        会连带删掉那条边（症状：Context 少一个输入槽）。
        """
        src_of_var: dict[str, tuple] = {}
        for node_id, node in self.nodes.items():
            if str(node.get("type")) != SET_NODE:
                continue
            for edge in self.links.values():
                if edge["target_id"] == node_id:
                    src_of_var[_var_key(node)] = (edge["origin_id"], edge["origin_slot"])
                    break
        redirect = {node_id: src_of_var.get(_var_key(node))
                    for node_id, node in self.nodes.items()
                    if str(node.get("type")) in (SET_NODE, GET_NODE)}
        for link_id, edge in list(self.links.items()):
            if edge["origin_id"] in redirect:
                target = redirect[edge["origin_id"]]
                if target is None:
                    self.links.pop(link_id, None)     # 无对应源 ⇒ 悬空
                    continue
                edge["origin_id"], edge["origin_slot"] = target[0], target[1]

    def drop_ui_only(self) -> None:
        for node_id in [nid for nid, node in self.nodes.items()
                        if str(node.get("type")) in _UI_ONLY_DROP]:
            self.nodes.pop(node_id, None)
            self.overlay.pop(node_id, None)
            for edge in self._incident(node_id):
                self.links.pop(edge["id"], None)

    def to_ui(self) -> dict:
        """重建 ``node.inputs[].link`` / ``outputs[].links``，输出扁平 UI 工作流片段。"""
        for node in self.nodes.values():
            for item in node.get("inputs") or []:
                if isinstance(item, dict):
                    item["link"] = None
            for item in node.get("outputs") or []:
                if isinstance(item, dict):
                    item["links"] = []
        links: list = []
        seq = 0
        for edge in self.links.values():
            seq += 1
            links.append([seq, edge["origin_id"], edge["origin_slot"],
                          edge["target_id"], edge["target_slot"], edge["type"]])
            target_node = self.nodes.get(edge["target_id"])
            target_inputs = (target_node or {}).get("inputs") or []
            if (edge["target_slot"] < len(target_inputs)
                    and isinstance(target_inputs[edge["target_slot"]], dict)):
                target_inputs[edge["target_slot"]]["link"] = seq
            source_node = self.nodes.get(edge["origin_id"])
            source_outputs = (source_node or {}).get("outputs") or []
            if (edge["origin_slot"] < len(source_outputs)
                    and isinstance(source_outputs[edge["origin_slot"]], dict)):
                source_outputs[edge["origin_slot"]]["links"].append(seq)
        return {"nodes": list(self.nodes.values()), "links": links,
                "definitions": {"subgraphs": []}}


def _flat_def(subgraph: dict, subs: dict, stack: tuple) -> tuple[list, list, dict]:
    """递归展平一个 subgraph 定义 → (nodes, links, overlay)；端口仍保留 -10/-20 符号。"""
    graph = _Graph(subgraph.get("nodes") or [], subgraph.get("links") or [])
    for node_id, node in list(graph.nodes.items()):
        node_type = str(node.get("type"))
        inner = subs.get(node_type)
        if inner is None or node_type in stack:      # 防自引用/环
            continue
        body_nodes, body_links, body_overlay = _flat_def(inner, subs, stack + (node_type,))
        graph.splice(node_id, body_nodes, body_links, body_overlay, node_id, node)
    return list(graph.nodes.values()), list(graph.links.values()), dict(graph.overlay)


def flatten(workflow: dict) -> tuple[dict, dict]:
    """UI 工作流 → ``(扁平 UI 工作流, 提升 widget overlay)``。

    不含前端结构时原样返回（同一对象、overlay 为空），调用方输出逐字节不变。
    """
    if not needs_flatten(workflow):
        return workflow, {}
    subs = {str(item.get("id")): item
            for item in (workflow.get("definitions") or {}).get("subgraphs") or []
            if isinstance(item, dict)}
    graph = _Graph(workflow.get("nodes") or [], workflow.get("links") or [])
    for instance_id, instance_node in list(graph.nodes.items()):
        subgraph = subs.get(str(instance_node.get("type")))
        if subgraph is None:
            continue
        body_nodes, body_links, body_overlay = _flat_def(
            subgraph, subs, (str(instance_node.get("type")),))
        graph.splice(instance_id, body_nodes, body_links, body_overlay,
                     instance_id, instance_node)
    graph.linearize_set_get()
    graph.drop_ui_only()
    flat = graph.to_ui()
    result = copy.deepcopy(workflow)                 # 保留 version/id/groups 等其余顶层键
    result["nodes"] = flat["nodes"]
    result["links"] = flat["links"]
    result["definitions"] = {"subgraphs": []}
    return result, graph.overlay
