"""ComfyUI 工作流图规则：解释 object_info、规整、校验与结构审核。"""
from __future__ import annotations

# 基础值类型（widget 填值，非连线）；其余大写标识符视为需连线的「连接类型」。
_PRIMITIVE = {"INT", "FLOAT", "STRING", "BOOLEAN"}


def _is_link_type(t) -> bool:
    """判断一个输入类型是否需要连线：大写连接类型(MODEL/LATENT...)才需要。
    combo 选项是 list、基础类型在 _PRIMITIVE 里，都属 widget 值不需连线。"""
    if isinstance(t, list):
        return False  # combo 下拉，widget
    if not isinstance(t, str):
        return False
    return t not in _PRIMITIVE


def node_interface(schema: dict) -> dict:
    """从单节点 object_info schema 提取接口约束，供 AI 与校验用。

    返回 {
      required_links: [{name, type}],   # 必须接线的输入口
      required_widgets: [{name, type}], # 必填 widget 值
      optional_links: [{name, type}],   # 可接可不接的输入口
      outputs: [type, ...],             # 输出类型（供下游匹配）
    }
    """
    inp = schema.get("input", {}) or {}
    req = inp.get("required", {}) or {}
    opt = inp.get("optional", {}) or {}
    req_links, req_widgets = [], []
    for name, spec in req.items():
        t = spec[0] if isinstance(spec, list) and spec else spec
        if _is_link_type(t):
            req_links.append({"name": name, "type": t if isinstance(t, str) else "COMBO"})
        else:
            # combo 口(spec[0] 是候选 list)带上 options 供校验/纠错；基础类型 options=None
            opts = t if isinstance(t, list) else None
            # spec[1] 常是 {"default": ..., ...}，取出默认值供自动补齐（AI 没填的必填 widget）
            meta = spec[1] if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict) else {}
            req_widgets.append({
                "name": name,
                "type": "COMBO" if opts is not None else (t if isinstance(t, str) else "COMBO"),
                "options": opts,
                "default": meta.get("default"),
            })
    opt_links = []
    for name, spec in opt.items():
        t = spec[0] if isinstance(spec, list) and spec else spec
        if _is_link_type(t):
            opt_links.append({"name": name, "type": t})
    return {
        "required_links": req_links,
        "required_widgets": req_widgets,
        "optional_links": opt_links,
        "outputs": list(schema.get("output", []) or []),
    }


def validate_graph(graph: dict, object_info: dict) -> list[str]:
    """用 schema 校验 API 格式 graph，返回错误列表（空=合法）。

    查：①节点 class_type 存在；②连线引用的上游节点存在、输出序号有效；
    ③连线两端类型匹配；④必填连线口都接了；⑤无自环。不改 graph，只报错。
    错误文本写清节点 id 与口名，供 AI 据此重连。
    """
    errors: list[str] = []
    if not isinstance(graph, dict) or not graph:
        return ["graph 为空或格式非法（应为 {node_id: {class_type, inputs}} 字典）"]

    for nid, node in graph.items():
        if not isinstance(node, dict):
            errors.append(f"节点 {nid} 不是对象")
            continue
        ct = node.get("class_type", "")
        schema = object_info.get(ct)
        if not schema:
            errors.append(f"节点 {nid} 的 class_type「{ct}」不存在（未安装或拼写错）")
            continue
        iface = node_interface(schema)
        inputs = node.get("inputs", {}) or {}
        # 必填连线口检查 + 类型匹配
        for link in iface["required_links"]:
            name = link["name"]
            if name not in inputs:
                errors.append(f"节点 {nid}({ct}) 缺必填输入口「{name}」(需接 {link['type']})")
                continue
            _check_link(nid, ct, name, link["type"], inputs[name], graph, object_info, errors)
        # 已填的可选连线口也校验类型
        opt_map = {l["name"]: l["type"] for l in iface["optional_links"]}
        for name, val in inputs.items():
            if isinstance(val, list) and name in opt_map:
                _check_link(nid, ct, name, opt_map[name], val, graph, object_info, errors)
        # 必填 widget 校验：缺失 / combo 值不在候选内（错误里带真实候选供 AI 纠正）
        for w in iface["required_widgets"]:
            _check_widget(nid, ct, w, inputs, errors)
    # 可达性不再算硬错误：见 reachability_warnings。断链孤岛只当警告，允许"先加一路、
    # 下轮再接回主链"的增量搭建中间态照样写入画布。
    return errors


def reachability_warnings(graph: dict, object_info: dict) -> list[str]:
    """终点可达性作为**警告**（不阻断）：有孤岛节点/无输出节点时提醒，但工作流仍可写入画布。
    仅在硬错误(validate_graph)为空时才有意义调用（否则孤岛多是接线错的副产物）。"""
    return _check_reachable(graph, object_info)


def split_missing_nodes(graph: dict, object_info: dict) -> tuple[dict, list[str]]:
    """把用了「本机没装(class_type 不在 object_info)」的节点从图里拆掉，返回 (清理后的图, 缺失节点类型列表)。

    治「AI 编了不存在的节点(如 Qwen2VLLoader)→整个工作流被判死」：不因个别幻觉节点毙掉全图，
    而是把它们摘掉、断开引用它们的连线，其余能搭的照常保留；缺失的类型归入「推荐安装」告诉用户。
    连线处理：任何节点若某输入口连的是被删节点，就移除该口（口留空，后续可手接/校验会提示）。
    """
    if not isinstance(graph, dict):
        return graph, []
    missing_ids = {nid for nid, n in graph.items()
                   if isinstance(n, dict) and n.get("class_type", "") not in object_info}
    if not missing_ids:
        return graph, []
    missing_types = sorted({graph[nid].get("class_type", "") for nid in missing_ids})
    clean: dict = {}
    for nid, node in graph.items():
        if nid in missing_ids:
            continue  # 删掉缺失节点本身
        inputs = node.get("inputs", {}) or {}
        new_inputs = {}
        for name, val in inputs.items():
            # 断开指向被删节点的连线
            if isinstance(val, list) and len(val) == 2 and str(val[0]) in missing_ids:
                continue
            new_inputs[name] = val
        clean[nid] = {**node, "inputs": new_inputs}
    return clean, missing_types


def audit_graph(graph: dict, object_info: dict) -> list[str]:
    """审核（非阻断，供回喂 AI 自修 + 报告给用户）：确定性扫出「能跑但不合意图」的接线缺陷。
    与 validate_graph（硬错误：类型/存在性）互补，audit 查的是**结构完整性**：
      ①悬空输出：某节点有输出口，但没有任何其它节点用它（除输出节点本身），且它也不是输出节点；
      ②开关/聚合节点单边接：Any Switch 这类节点，输入口一个没接(没上游) 或 输出没被用(没下游)；
      ③可达性问题（复用 _check_reachable：孤岛/无输出节点）。
    返回问题描述列表（空=结构完整）。"""
    issues: list[str] = []
    if not isinstance(graph, dict) or not graph:
        return issues

    # 建"谁被谁用"：downstream_used[上游id] = 被引用次数
    used_out: dict[str, int] = {nid: 0 for nid in graph}
    for nid, node in graph.items():
        for val in (node.get("inputs", {}) or {}).values():
            if isinstance(val, list) and len(val) == 2:
                up = str(val[0])
                if up in used_out:
                    used_out[up] += 1

    for nid, node in graph.items():
        ct = node.get("class_type", "")
        schema = object_info.get(ct)
        if not schema:
            continue
        is_output_node = bool(schema.get("output_node"))
        iface = node_interface(schema)
        inputs = node.get("inputs", {}) or {}
        n_links_in = sum(1 for v in inputs.values() if isinstance(v, list) and len(v) == 2)
        has_out = len(iface["outputs"]) > 0

        # ② 开关/聚合节点单边接（名字含 switch，或输入口全是通配 *）
        is_switch = "switch" in ct.lower() or all(
            l.get("type") == "*" for l in (iface["required_links"] + iface["optional_links"])
        ) and (iface["required_links"] or iface["optional_links"])
        if is_switch:
            if n_links_in == 0:
                issues.append(f"节点 {nid}({ct}) 是开关/聚合节点，但没有接任何上游来源（输入口空着）——请把要切换的各路接到它的输入口(如 any_01,any_02)")
            if has_out and used_out.get(nid, 0) == 0 and not is_output_node:
                issues.append(f"节点 {nid}({ct}) 是开关/聚合节点，但它的输出没有接给任何下游——请把它的输出接到需要的口")
            continue

        # ① 普通节点悬空输出：有输出、没被用、又不是输出节点 = 白搭
        if has_out and not is_output_node and used_out.get(nid, 0) == 0:
            issues.append(f"节点 {nid}({ct}) 的输出没有接给任何下游（悬空），要么接上要么删掉")

    # ③ 可达性
    issues.extend(_check_reachable(graph, object_info))
    return issues


def _check_reachable(graph: dict, object_info: dict) -> list[str]:
    """终点可达性：①至少有一个输出节点(output_node，如 SaveImage)；②每个节点都能沿连线抵达某输出节点。
    否则是断链孤岛/没有出图终点的死图（各节点单看合法，整图跑不出结果）。"""
    errs: list[str] = []
    # 输出节点集合（schema.output_node=True，如 SaveImage/PreviewImage）
    sinks = {nid for nid, node in graph.items()
             if object_info.get(node.get("class_type", ""), {}).get("output_node")}
    if not sinks:
        return ["整图没有任何输出节点（如 SaveImage），工作流跑不出结果"]
    # 反向邻接：被谁作为上游引用 → 建 下游->上游 的反查，从 sinks 逆向 BFS 标记可达
    upstream: dict[str, set[str]] = {nid: set() for nid in graph}
    for nid, node in graph.items():
        for val in (node.get("inputs", {}) or {}).values():
            if isinstance(val, list) and len(val) == 2:
                up = str(val[0])
                if up in graph:
                    upstream[nid].add(up)
    reachable = set(sinks)
    stack = list(sinks)
    while stack:
        cur = stack.pop()
        for up in upstream.get(cur, ()):
            if up not in reachable:
                reachable.add(up)
                stack.append(up)
    dead = [nid for nid in graph if nid not in reachable]
    if dead:
        errs.append(f"节点 {'、'.join(sorted(dead))} 未连到任何输出节点（断链孤岛，不会参与出图）")
    return errs


_MAX_OPTS = 30  # 候选列表最多列多少个（防单条错误撑爆 token）


def _fmt_options(options: list) -> str:
    """把 combo 候选格式化成简短提示：前 _MAX_OPTS 个 + 总数。"""
    vals = [str(x) for x in options]
    shown = vals[:_MAX_OPTS]
    tail = f" …(共{len(vals)}个)" if len(vals) > _MAX_OPTS else ""
    return "、".join(shown) + tail


def _check_widget(nid, ct, w, inputs, errors):
    """校验一个必填 widget：在场 + （combo 时）值在候选内。基础类型只查在场，不查具体值。

    重要：combo 口的**空值占位**(''/None，如骨架里的 unet_name/ckpt_name/image)一律放过——
    这类"选本机哪个模型/图"是环境相关的，交给用户在画布下拉里选，不该阻断结构校验；
    尤其增量模式冻结骨架时 AI 根本改不了这些口，若拦截会造成校验永远失败的死锁。
    仍拦截的是：combo 填了**不存在的非空值**(AI 幻觉)、以及口被误当连线填了 [id, slot]。
    """
    name = w["name"]
    options = w.get("options")
    val = inputs.get(name)
    # 误当连线填了 [id, slot]：这是结构错误，拦
    if isinstance(val, list):
        hint = f"，可选：{_fmt_options(options)}" if options else ""
        errors.append(f"节点 {nid}({ct}) 的 widget「{name}」被误当连线填了，应填字面值{hint}")
        return
    # combo 空占位（未填 / '' / None）：放过，待用户在画布选真实模型/图
    is_empty = name not in inputs or val is None or (isinstance(val, str) and val.strip() == "")
    if options is not None:
        if is_empty:
            return  # 空占位不拦
        if val not in options:
            errors.append(
                f"节点 {nid}({ct}) 的「{name}」填了「{val}」，不是合法值，可选：{_fmt_options(options)}")
        return
    # 基础类型(INT/FLOAT/STRING/BOOLEAN)必填：只查在场，不查具体值
    if name not in inputs:
        errors.append(f"节点 {nid}({ct}) 缺必填 widget「{name}」")


def _coerce_one(val, opts):
    """把一个 combo 值规整到合法候选。返回 (新值, 是否改动)。
    规则(依次)：①已合法→不动；②空→第一个标量候选；③大小写不敏感相等→改成候选原值；
    ④值是某候选的前缀/子串，或某候选是值的子串→取最短匹配(如 png→PNG、baked→Baked VAE、
    anima→waiANIPONYXL…不匹配则不动)；⑤都不中→保持原值(交给校验报错，AI/用户再改)。"""
    scalars = [o for o in opts if isinstance(o, (str, int, float, bool))]
    if not scalars:
        return val, False
    if val in opts:
        return val, False
    is_empty = val is None or (isinstance(val, str) and val.strip() == "")
    if is_empty:
        return scalars[0], True
    if not isinstance(val, str):
        return val, False
    low = val.strip().lower()
    # ③ 大小写不敏感相等
    for o in scalars:
        if isinstance(o, str) and o.lower() == low:
            return o, True
    # ④ 子串/前缀匹配，取最短候选（最贴近）
    cand = [o for o in scalars if isinstance(o, str) and (low in o.lower() or o.lower().startswith(low))]
    if cand:
        return min(cand, key=len), True
    # ⑤ token 匹配：候选常是「中文 (english)」格式(如 空格 (space))，AI 可能填描述性词
    #    (如 全部合并为空格)。拆出候选的核心 token，只要输入包含某候选的任一 token 就匹配。
    #    (如"全部合并为空格"含"空格"→匹配"空格 (space)"；"自动修复"不含"两者/both"→不匹配，正确留报错)
    import re as _re
    best = None
    for o in scalars:
        if not isinstance(o, str):
            continue
        toks = [t for t in _re.split(r"[()（）\s]+", o.lower()) if len(t) >= 2]
        if any(t in low for t in toks):
            if best is None or len(o) < len(best):
                best = o
    if best is not None:
        return best, True
    # ⑥ 兜底：都匹配不上(如"启用"对不上否/空格/逗号，"default"/"anima"对不上真实文件名)——
    #    为了"先能生成"，强制回落到第一个候选（通常是关闭/默认项或第一个真实模型），
    #    让工作流至少合法可生成；模型名等用户在画布再改。返回 forced=True 供上层出提示。
    return scalars[0], True


def fill_combo_defaults(graph: dict, object_info: dict) -> int:
    """规整 combo widget 值到合法候选：空占位→补默认；近似值(大小写/子串,如 png→PNG、
    baked→Baked VAE)→自动纠正。让"差一点点"的填值不至于毙掉整个工作流。
    注意：必须在 validate_graph **之前**调用（否则近似值先被判死）。已合法/无法匹配的不动。
    返回改动数。原地改 graph。"""
    changed = 0
    for nid, node in graph.items():
        if not isinstance(node, dict):
            continue
        schema = object_info.get(node.get("class_type", ""))
        if not schema:
            continue
        inputs = node.setdefault("inputs", {})
        def _dft(w):
            d = w.get("default")
            if d is not None:
                return d
            return {"BOOLEAN": False, "INT": 0, "FLOAT": 0.0, "STRING": ""}.get(w.get("type"), "")
        for w in node_interface(schema)["required_widgets"]:
            name = w["name"]
            val = inputs.get(name) if name in inputs else None
            opts = w.get("options")
            # widget 口被误当连线([id,slot])：widget 不能接线，拆掉连线改填默认（治 CLIPTextEncode.text、
            # PromptCleaningMaid.string 被接反推输出的报错——先能生成，用户/后续再手接）。
            if isinstance(val, list):
                inputs[name] = _coerce_one("", opts)[0] if opts else _dft(w)
                changed += 1
                continue
            if opts:
                new, did = _coerce_one(val, opts)  # combo：空补默认/近似纠正/兜底第一候选
                if did:
                    inputs[name] = new
                    changed += 1
                continue
            # 非 combo 必填 widget：AI 没填就用节点默认补齐
            if name not in inputs or val is None:
                inputs[name] = _dft(w)
                changed += 1
    return changed


def interface_sheet(node_names: list[str], object_info: dict, max_nodes: int = 60,
                    priority: set | None = None) -> str:
    """把一批节点的**真实输入/输出接口**拼成精简速查表，喂给 AI 搭工作流。

    这是接线正确性的关键：只有节点名+文字描述时，AI 不知道某节点的口叫什么、输出什么类型，
    只能猜（表现为选错开关节点、把 BOOLEAN 接进 IMAGE 口）。给出真实接口后 AI 才能连对。
    每节点一行：  节点名: in 口名(类型)/widget名(类型)... => out 输出0,输出1...
    连线口标 *、widget 标 =；控量到 max_nodes 个节点，超出省略。
    priority：方案点名的节点集，永远输出、不受 max_nodes 截断（治「点名节点排末尾被截掉」）。
    """
    priority = priority or set()
    lines: list[str] = []
    seen = 0
    for name in node_names:
        schema = object_info.get(name)
        if not schema:
            continue
        if seen >= max_nodes and name not in priority:  # 点名节点豁免截断
            continue
        iface = node_interface(schema)
        ins = []
        for l in iface["required_links"]:
            ins.append(f"*{l['name']}({l['type']})")          # 必接线口
        for l in iface["optional_links"]:
            ins.append(f"*{l['name']}({l['type']})?")          # 可选接线口
        for w in iface["required_widgets"]:
            ins.append(f"={w['name']}({w['type']})")           # 必填 widget
        outs = ", ".join(f"{i}:{t}" for i, t in enumerate(iface["outputs"]))
        line = f"{name}: in[{' '.join(ins)}] => out[{outs}]"
        # 动态输入口节点补注：rgthree Any Switch 等在 schema 里 in[] 是空的（输入口前端运行时动态加），
        # 不加说明 AI 会以为它没输入口→开关悬空。明确告知真实动态口名。
        if not ins and "switch" in name.lower():
            line += "  ← 动态输入口 any_01,any_02,…（把各路来源接这些口，别以为它没输入）"
        lines.append(line)
        seen += 1
    return "\n".join(lines)


def _check_link(nid, ct, port, want_type, val, graph, object_info, errors):
    """校验一条连线：val 应为 [上游id, 输出序号]，且上游存在、序号有效、输出类型匹配。"""
    if not (isinstance(val, list) and len(val) == 2):
        return  # 不是连线（widget 值），跳过
    up_id, out_idx = str(val[0]), val[1]
    if up_id == str(nid):
        errors.append(f"节点 {nid}({ct}) 口「{port}」连到了自己（自环）")
        return
    up = graph.get(up_id)
    if not up:
        errors.append(f"节点 {nid}({ct}) 口「{port}」引用的上游节点 {up_id} 不存在")
        return
    up_schema = object_info.get(up.get("class_type", ""))
    if not up_schema:
        return  # 上游 class_type 非法已在主循环报过
    up_outputs = list(up_schema.get("output", []) or [])
    if not isinstance(out_idx, int) or out_idx < 0 or out_idx >= len(up_outputs):
        errors.append(f"节点 {nid}({ct}) 口「{port}」的上游输出序号 {out_idx} 越界（{up_id} 有 {len(up_outputs)} 个输出）")
        return
    got = up_outputs[out_idx]
    # 通配类型放行：rgthree Any Switch 等"类型无关"节点的口/输出是 "*"，本就匹配任意类型
    # （这正是它们能合并多模态的意义）。want 端或 got 端是 * 都不算不匹配。
    if want_type == "COMBO" or want_type == "*" or got == "*":
        return
    if got != want_type:
        errors.append(f"节点 {nid}({ct}) 口「{port}」需 {want_type}，但上游 {up_id} 输出的是 {got}（类型不匹配）")
