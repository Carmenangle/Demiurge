"""分模块增量搭建：把 AI 每轮输出的「新模块节点 + 锚点」安全合并进当前图。

核心思路：已搭部分(base)冻结不动，AI 只产出本模块的新节点与「接到现有图哪个口」的锚点，
后端做两件 AI 做不可靠的事：
  ① ID 安全重编号——模块节点 id 重映射到 base 里不冲突的新 id，模块内部连线同步改写；
  ② 按锚点接线——把模块新节点接到冻结的现有图上（正向：模块口接现有输出；反向：现有口改接模块输出）。
这样图再大，AI 也碰不到已搭好的节点，避免整图重出把已连对的部分改坏。

graph 用 API prompt 格式：{node_id(str): {class_type, inputs:{口名: 值 或 [上游id, 输出序号]}}}。
"""
from __future__ import annotations


def freeze_ids(graph: dict) -> set[str]:
    """现有图的节点 id 集合（字符串），用于合并后确认这些节点未被模块覆盖。"""
    return {str(k) for k in (graph or {})}


def _next_base_id(base: dict) -> int:
    """base 里可用的起始新 id：现有数字 id 的 max+1（非数字 id 忽略）。空图从 1 起。"""
    mx = 0
    for k in (base or {}):
        try:
            mx = max(mx, int(k))
        except (ValueError, TypeError):
            continue
    return mx + 1


def _remap_inputs(inputs: dict, id_map: dict) -> dict:
    """把一个节点 inputs 里指向模块内部的连线 [旧id, 序号] 改写为新 id；widget 值原样保留。"""
    out = {}
    for name, val in (inputs or {}).items():
        if isinstance(val, list) and len(val) == 2 and str(val[0]) in id_map:
            out[name] = [id_map[str(val[0])], val[1]]
        else:
            out[name] = val
    return out


def merge_module(base: dict, module_nodes: dict, anchors: list | None = None) -> dict:
    """把模块节点合并进 base，返回 {graph, id_map}。不改入参（深拷贝语义由调用方保证浅层安全）。

    - base: 当前冻结图 {id: {class_type, inputs}}
    - module_nodes: AI 输出的新模块节点 {模块内id: {class_type, inputs}}，其内部连线用模块内 id
    - anchors: 跨界连线列表，每项二选一方向：
        正向(模块接现有输出): {module_node, module_input, base_node, base_output}
        反向(现有口改接模块输出): {base_node, base_input, module_node, module_output}
      module_node/base_node 为对应侧的节点 id；*_input 为口名；*_output 为输出序号(int)。

    冲突处理：模块 id 一律重编号到 base 之外的新 id（即使不撞也重编，保证确定性）。
    """
    graph = {str(k): dict(v, inputs=dict(v.get("inputs", {}) or {})) for k, v in (base or {}).items()}
    anchors = anchors or []

    # 1. 给模块每个节点分配不冲突的新 id
    nid = _next_base_id(graph)
    id_map: dict[str, str] = {}
    for old in module_nodes:
        id_map[str(old)] = str(nid)
        nid += 1

    # 2. 落模块节点（内部连线改写为新 id）
    for old, node in module_nodes.items():
        new_id = id_map[str(old)]
        graph[new_id] = {
            "class_type": node.get("class_type", ""),
            "inputs": _remap_inputs(node.get("inputs", {}), id_map),
        }

    # 3. 按锚点接线
    for a in anchors:
        mn = str(a.get("module_node", ""))
        # 正向：模块节点某输入口 接 现有节点输出
        if a.get("module_input") is not None and a.get("base_node") is not None:
            new_id = id_map.get(mn)
            if new_id and new_id in graph:
                graph[new_id]["inputs"][a["module_input"]] = [str(a["base_node"]), a.get("base_output", 0)]
        # 反向：现有节点某输入口 改接 模块节点输出
        elif a.get("base_input") is not None and a.get("base_node") is not None:
            bn = str(a["base_node"])
            new_id = id_map.get(mn)
            if bn in graph and new_id:
                graph[bn]["inputs"][a["base_input"]] = [new_id, a.get("module_output", 0)]

    return {"graph": graph, "id_map": id_map}
