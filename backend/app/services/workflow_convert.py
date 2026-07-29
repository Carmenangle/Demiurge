"""把 ComfyUI UI(编辑器导出)格式工作流转换为 API(/prompt)格式。

难点：
- UI 格式的 widgets_values 是无名数组，需按节点类型的输入顺序还原成 {名: 值}。
- seed 类节点有隐藏的 control_after_generate 控件，会多占一个 widget 值，API 格式不需要它。
- 连线输入要从顶层 links 反查 [src_node_id, src_slot]。

策略：优先用运行中 ComfyUI 的 /object_info 拿到每个节点的真实输入顺序（最准），
拿不到则退回内置 WIDGET_NAMES 表。
"""
from __future__ import annotations

from app.services import comfyui_client
from app.services.workflow_parser import PASSTHROUGH_TYPES, WIDGET_NAMES

# 不进入 API 格式的隐藏/UI-only 控件名
_HIDDEN_WIDGETS = {"control_after_generate"}


def _object_info(comfy_url: str) -> dict:
    """经统一 ComfyUI HTTP Adapter 拉取，失败时保留转换降级语义。"""
    try:
        return comfyui_client.fetch_object_info(comfy_url, timeout=5)
    except comfyui_client.ComfyError:
        return {}


def _widget_names_for(ntype: str, object_info: dict) -> list[str] | None:
    """返回该节点类型按声明顺序的 widget 输入名（含被连线但仍占 widget 位的）。拿不到返回 None。"""
    info = object_info.get(ntype)
    if not info:
        return None
    required = info.get("input", {}).get("required", {})
    optional = info.get("input", {}).get("optional", {})
    names: list[str] = []
    for name, spec in {**required, **optional}.items():
        # spec[0] 是类型；列表(枚举)或基础类型才是 widget，连线型(大写类型名)不算 widget
        t = spec[0] if isinstance(spec, list) and spec else None
        if isinstance(t, list):
            names.append(name)  # 枚举下拉
        elif isinstance(t, str) and t in ("INT", "FLOAT", "STRING", "BOOLEAN"):
            names.append(name)
    return names


# 这些 widget 后面会额外跟一个 UI-only 控件值（control_after_generate），消费时要多吃一格
_SEED_LIKE = {"seed", "noise_seed", "rand_seed"}


def ui_to_api(workflow: dict, comfy_url: str = "") -> dict:
    """UI 格式 → API 格式。非 UI 格式（已是 API）原样返回。"""
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return workflow  # 已是 API 格式或无法识别

    by_id: dict[str, dict] = {str(n.get("id")): n for n in nodes if isinstance(n, dict)}

    # 1) link 反查表：link_id -> (src_node_id, src_slot)
    link_src: dict[int, tuple[str, int]] = {}
    for l in workflow.get("links", []) or []:
        if isinstance(l, list) and len(l) >= 5:
            link_src[l[0]] = (str(l[1]), l[2])

    object_info = _object_info(comfy_url) if comfy_url else {}
    # 已知非执行/纯注释节点（不参与生成，不进 API 图）。
    # 在共享的穿透集上再加 PrimitiveNode：它运行期需穿透（值已并入下游 widget）。
    _NON_EXEC = PASSTHROUGH_TYPES | {"PrimitiveNode"}

    def is_skipped(node: dict) -> bool:
        """需穿透/跳过的节点：Reroute 中转、bypass/mute(mode 2/4)、笔记类、
        以及 ComfyUI 未注册的类型（拉到 object_info 时按其清单判定）。"""
        t = node.get("type")
        if t in _NON_EXEC or node.get("mode", 0) in (2, 4):
            return True
        if object_info and t not in object_info:
            return True  # ComfyUI 里没有这个节点类型 → 不可执行，跳过
        return False

    def resolve(node_id: str, slot: int, seen: set) -> list | None:
        """把落在被跳过节点上的连线，沿其输入回溯到真实源头 [node_id, slot]。"""
        node = by_id.get(node_id)
        if node is None:
            return None
        if not is_skipped(node):
            return [node_id, slot]
        if node_id in seen:  # 防环
            return None
        seen.add(node_id)
        ntype = node.get("type")
        out_type = None
        outs = node.get("outputs") or []
        if isinstance(outs, list) and slot < len(outs):
            out_type = (outs[slot] or {}).get("type")
        # 选一个用于穿透的输入：Reroute 用唯一输入；bypass 用同类型输入
        for inp in node.get("inputs") or []:
            link = inp.get("link")
            if link is None or link not in link_src:
                continue
            if ntype == "Reroute" or out_type in (None, "*") or inp.get("type") == out_type:
                src_id, src_slot = link_src[link]
                return resolve(src_id, src_slot, seen)
        return None  # 悬空：上游已断（如整条 bypass 链）

    api: dict[str, dict] = {}

    for node in nodes:
        if not isinstance(node, dict):
            continue
        if is_skipped(node):
            continue  # 虚拟/绕过节点不提交，连线已穿透到真实源
        ntype = node.get("type", "")
        nid = str(node.get("id", ""))
        inputs: dict = {}

        # 连线输入：穿透 Reroute / bypass 回到真实源头
        # 同时记录“已是连线输入”的名字——这些名字绝不能再被 widget 值覆盖
        # （如 ImpactConditionalBranch.cond 是 BOOLEAN 连线，被 widget 覆盖会断开开关）
        linked_names: set = set()
        for inp in node.get("inputs", []) or []:
            name = inp.get("name")
            link = inp.get("link")
            if link is None:
                continue
            linked_names.add(name)
            if link in link_src:
                src_id, src_slot = link_src[link]
                resolved = resolve(src_id, src_slot, set())
                if resolved is not None:
                    inputs[name] = resolved

        # widget 输入：widgets_values 按“所有 widget 位（含被连线的）”的声明顺序排列，
        # 必须逐位对齐消费——连线位也照样占一格（跳过赋值），seed 类后面多占一格
        # control_after_generate。否则后面的值会整体错位（如 sampler_name 取到数字）。
        widgets = node.get("widgets_values")
        if isinstance(widgets, list) and widgets:
            oi_names = _widget_names_for(ntype, object_info)
            if oi_names is not None:
                # 有 object_info：按声明顺序逐位对齐
                wi = 0
                for nm in oi_names:
                    if wi >= len(widgets):
                        break
                    if nm not in linked_names:
                        inputs[nm] = widgets[wi]
                    wi += 1
                    if nm in _SEED_LIKE:
                        wi += 1  # 跳过其后的 control_after_generate UI 值
            else:
                # 退回内置表：表里含隐藏控件，按原始顺序消费并跳过隐藏名/连线名
                raw_names = WIDGET_NAMES.get(ntype, [])
                wi = 0
                for nm in raw_names:
                    if wi >= len(widgets):
                        break
                    if nm not in _HIDDEN_WIDGETS and nm not in linked_names:
                        inputs[nm] = widgets[wi]
                    wi += 1

        api[nid] = {"class_type": ntype, "inputs": inputs}

    return api
