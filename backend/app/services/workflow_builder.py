"""AI 搭工作流编排：按模式调用模型、消费图规则结果并按需落盘。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4

from app.config import BUILD_TIME_BUDGET_SEC
from app.services import node_index, workflow_build_turn, workflow_graph_rules, workflow_merge
from app.services.pathnames import safe_seg


def save_workflow(graph: dict, name: str, workflow_dir: str) -> str:
    """把画布 graph 存成 .json 到 workflowDir，返回落盘路径。

    前端保存传的是 UI(编辑器)格式（app.graph.serialize()，含 nodes/links + 布局）——
    ComfyUI 侧栏打开工作流走标准载入，只认 UI 格式；存 API prompt 格式（无 nodes 数组）
    会被解析成空白画布。本工具 workflows.parse 两种格式都能读，提交生图时再 ui_to_api。
    原样写入即可（不改结构），文件名去非法字符 + 加短 uuid 防撞。
    workflow_dir 缺失/写失败抛 ValueError。
    """
    if not workflow_dir:
        raise ValueError("未配置工作流默认读取路径（设置 → 路径 → 工作流默认读取路径）")
    base = Path(workflow_dir)
    base.mkdir(parents=True, exist_ok=True)
    stem = safe_seg((name or "ai_workflow").strip()) or "ai_workflow"
    fname = f"{stem}_{uuid4().hex[:8]}.json"
    dest = base / fname
    dest.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(dest)


# ======================================================================
# 搭建编排：检索节点 → 拼 prompt → 调模型 → 校验 → 回喂重试。
# 从路由层下沉（ARCHITECTURE：路由薄、不写循环/业务逻辑）。LLM 调用由调用方
# 注入 chat_fn（签名同 ai_common.chat），故本服务不 import routers，无循环依赖。
# ======================================================================

def _trim_catalog(packs: list[dict], per_pack: int = 1500, total: int = 12000) -> str:
    """把检索命中包拼成节点清单文本，但控量——防超大包(如 easy-use 上百节点全文)撑爆
    单次请求 prompt 触发上游 502/超时。每包正文截断到 per_pack 字符，总量到 total 字符封顶。
    接口表(interface_sheet)已另给真实口/类型，这里只需让 AI 知道有哪些节点可选，长描述可裁。"""
    parts: list[str] = []
    used = 0
    for p in packs:
        c = p.get("content", "") or ""
        if len(c) > per_pack:
            c = c[:per_pack] + f" …(该包内容过长已截断，共{len(p.get('content',''))}字符)"
        if used + len(c) > total:
            parts.append(f"…(还有 {len(packs) - len(parts)} 个相关包因篇幅省略)")
            break
        parts.append(c)
        used += len(c)
    return "\n\n".join(parts)


def _missing_hint(missing: list[str], alts: dict | None = None) -> list[str]:
    """把被拆掉的未装节点转成给用户的「推荐安装 + 本机平替」提示（作为 warnings 回前端）。
    alts: {缺失节点: [本机同类平替...]}，有平替就一并告知，让用户知道可换本机已装的。"""
    if not missing:
        return []
    lines = [f"以下节点本机没装，已从工作流里移除：{'、'.join(missing)}。"]
    if alts:
        for miss, sub in alts.items():
            if sub:
                lines.append(f"「{miss}」本机可用同类平替：{'、'.join(sub[:5])}（可让我改用这些重搭）")
    lines.append("若要装缺失的节点：点下方「去安装」按钮会跳到节点管理市场并自动搜索；搜不到就用它给的 Git 链接装。装完「同步节点库」再重搭。")
    return lines


def _extract_json(text: str) -> dict:
    """从模型输出里抠出 JSON（容错去 ```json 围栏）。失败抛 ValueError。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t
        t = t[4:] if t.lower().startswith("json") else t
        t = t.strip("`").strip()
    s, e = t.find("{"), t.rfind("}")
    if s < 0 or e < 0:
        raise ValueError("模型未返回 JSON")
    return json.loads(t[s:e + 1])


def _slim_graph_for_prompt(base: dict) -> dict:
    """把当前图瘦身成「给 AI 看的表示」：保留 id/class_type/所有连线，省掉 widget 标量值。
    治增量模式每轮塞完整图→节点多就 prompt 爆炸/超时。连线拓扑完整保留(中间插入/拆边重连靠它)，
    只把与接线无关的 widget 字面值(seed/steps/cfg/text/ckpt_name 等)替换为占位，长文本截断。
    ⚠只影响发给模型的表示，实际合并用前端另传的完整图，不受影响。"""
    slim: dict = {}
    for nid, node in (base or {}).items():
        if not isinstance(node, dict):
            slim[nid] = node
            continue
        new_inputs = {}
        for k, v in (node.get("inputs") or {}).items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], (str, int)) and isinstance(v[1], int):
                new_inputs[k] = v                       # 连线 [id, 序号]：原样保留（拓扑关键）
            elif isinstance(v, str) and len(v) > 24:
                new_inputs[k] = v[:24] + "…"             # 长文本(提示词等)截断
            else:
                new_inputs[k] = "…"                       # 其它 widget 标量：占位，接线无关
        slim[nid] = {"class_type": node.get("class_type"), "inputs": new_inputs}
    return slim


_BUILD_SYSTEM = (
    "你是资深 ComfyUI 工作流专家。根据用户需求，用你的专业判断**一次性通盘规划并生成一个"
    "完整、专业、可直接运行的** ComfyUI API prompt 格式工作流（不是片段，是从加载模型到保存输出的整条链）。\n"
    "格式：JSON 对象 {\"节点id\": {\"class_type\": \"节点名\", \"inputs\": {...}}}。\n"
    "连线规则：inputs 里某口的值若是 [\"上游节点id\", 输出序号] 表示接线；否则是 widget 字面值。\n"
    "【专业标准·务必按最佳实践搭，别偷工减料搭最基础版】：\n"
    "- 按模型架构选正确的加载方式：SDXL/SD1.5 等一体式模型用 CheckpointLoaderSimple；"
    "而 Flux、SD3、以及 Anima/UNET 分离式模型，应走 **UNETLoader(单独加载 UNET) + DualCLIPLoader/CLIPLoader(单独加载CLIP) + VAELoader(单独加载VAE)** 的分离式架构，"
    "这是这类模型的正确用法，绝不要图省事套用 checkpoint 一体式。\n"
    "- 需求里提到的每一项能力（如反推、图生图、放大、面部修复）都要在图里真实实现对应链路，不许简化掉。"
    "图生图要有 VAEEncode 把参考图编码进 latent；反推要有对应的看图出词节点链。\n"
    "- 先把必接线口(MODEL/LATENT/CONDITIONING 等大写类型)连成骨架，再填 widget，optional 口按需求接。\n"
    "【节点名以清单为准（防止写错名字），但你的专业能力不受清单限制】：\n"
    "- 本机真实接口清单只是本轮检索候选，不是完整安装清单；清单中没有某节点时，不能据此断言本机未安装。\n"
    "- 【可用节点真实接口】清单给出本机节点的**真实 class_type 和接口**，接线时口名/类型严格照它，"
    "写节点名也以清单里的真实名为准（别凭印象写 LlamaCPP/Qwen2VL 这种可能不对的名字）。\n"
    "- 若你要用的某个节点清单里没列出：先在清单里找**功能同类的替代节点**（如某种 UNET 加载器、某种反推节点）；"
    "清单里确实找不到任何能实现该能力的节点时，才省略该部分，并按你的专业判断把其余部分搭到最完整。\n"
    "- 不要因为清单没列全就退化成最简单的工作流——清单是查真实节点名用的，不是你能力的上限。\n"
    "完整性自检：\n"
    "- 整图必须有输出节点(如 SaveImage)，主链从模型加载一路连通到它，不留断链孤岛；\n"
    "- 每个节点的必接线口都要接上；用到开关/聚合节点(如 Any Switch (rgthree)，动态输入口 "
    "any_01,any_02,…)时，务必既接上游各路来源、又把输出接给下游，不能只接一头；\n"
    "只输出 JSON，不要解释、不要 markdown 代码块围栏。"
)


_MODULE_SYSTEM = (
    "你是 ComfyUI 工作流的分模块搭建器。给你【当前工作流(API格式，已搭好、冻结不可改)】和一段新需求，"
    "你只输出【本次要新增的模块节点】和它们如何接到当前工作流上，绝不重复或修改已有节点。\n"
    "输出 JSON 对象，两个字段：\n"
    '  "nodes": {"模块内临时id": {"class_type":"节点名", "inputs": {...}}}  —— 只放新增节点；\n'
    "           模块内部互连用这些临时id写 [\"临时id\", 输出序号]；接当前图的口先留空，用 anchors 表达。\n"
    '  "anchors": [ ... ]  —— 跨界连线，每项二选一方向：\n'
    '     正向(新节点某输入口 接 现有节点输出): {"module_node":"临时id","module_input":"口名","base_node":"现有id","base_output":序号}\n'
    '     反向(现有节点某输入口 改接 新节点输出): {"base_node":"现有id","base_input":"口名","module_node":"临时id","module_output":序号}\n'
    "只用【可用节点清单】里出现的节点，绝不虚构。\n"
    "控制流开关（二选一/多选一，用于「文生图还是图生图」「本地还是云端反推」这类切换）：\n"
    "- 用类型无关的开关节点（优先 Any Switch (rgthree)，它对任意类型通用——图/latent/文本/音频/视频都行）；\n"
    "- 【关键·Any Switch (rgthree) 的输入口是动态的】它在接口表里显示 in[] 没有输入口，但实际"
    "接受名为 any_01、any_02、any_03… 的输入口（按顺序编号）。你必须把要切换的**各路来源分别接到 "
    "any_01 / any_02 …**，否则开关没有上游、下游拿不到数据（这是最常见的错误！）；\n"
    "- 完整接法举例（UNET/Checkpoint 二选一给采样器）：把 UNETLoader 的 MODEL 输出接到 Any Switch 的 "
    "any_01，把 CheckpointLoaderSimple 的 MODEL 输出接到 any_02，再把 Any Switch 的输出(序号0)接到 "
    "KSampler 的 model 口。**三条线缺一不可**：两个加载器→开关(any_01/any_02)、开关→采样器。\n"
    "- 用 anchors 表达跨界连线时，module_input 就写 \"any_01\"、\"any_02\" 这样的口名；\n"
    "- 需要按开关信号选路时可配 ImpactConditionalBranch（布尔选 tt/ff 两路）。\n"
    "自检：你新增的每个开关/聚合节点，都必须既有上游(输入口接了来源)又有下游(输出被人用)，不能只接一头。\n"
    "只输出 JSON，不要解释、不要 markdown 围栏。"
)


_PLAN_SYSTEM = (
    "你是面向新手的 ComfyUI 工作流顾问。用户不熟悉节点，你要用**大白话**讲清楚方案，供他判断后决定是否执行。\n"
    "根据用户需求（和当前画布，若有）+ 给定的可用节点，输出一段**给人看的中文方案**，包含：\n"
    "1. 这个工作流是做什么的（一句话目标）；\n"
    "2. 分几步、每步大意（如：加载模型 → 写提示词 → 采样出图 → 保存），用简单序号列出；\n"
    "   —— 按模型架构给专业方案：SDXL/SD1.5 一体式用 Checkpoint 加载；Flux/SD3/Anima 等分离式模型"
    "应走 UNET 加载器 + 单独 CLIP + 单独 VAE 的分离式架构，别图省事套 checkpoint 一体式。"
    "需求提到的每项能力(反推/图生图/放大等)都要在方案里有对应步骤，别简化成最基础版。\n"
    "3. 会用到哪些关键节点，各自作用（用节点显示名+一句话，别堆术语）——正式方案里点名的节点"
    "**必须来自给定的【本机真实节点】清单**，绝不凭印象编节点名(如不要写 LlamaCPP 这种本机没有的)；\n"
    "   ——【本机真实节点】是从 ComfyUI object_info 按本轮需求筛出的已安装节点子集，不是完整安装清单；"
    "清单中没有某节点时，不能据此断言本机未安装，也不能建议用户重复安装。\n"
    "4. 若在现有画布上改，说清改动了什么、为什么这么接。\n"
    "5. 如果本轮候选没有覆盖某项能力，只能说明『本轮检索未确认到合适节点』并建议重新同步或继续检索；"
    "除非上下文另有【已确认缺失节点】清单，否则禁止输出『需安装节点』或声称节点未安装。\n"
    "要求：口语化、简短、不输出 JSON、不输出节点连线细节；全文控制在 1200 个中文字符以内，"
    "每步最多两句话，不要在结尾重复前文的切换逻辑。结尾一句『确认后我就照这个方案（只用你已装的节点）搭好写入画布。』"
)


def build_graph(chat_fn, *, base_url: str, api_key: str, model: str, proxy: str,
                cfg, need: str, comfy_url: str, workflow_dir: str, name: str,
                max_retries: int, current_graph: dict, save: bool,
                history: list[dict] | None = None) -> dict:
    """按需求自动搭工作流：检索节点→AI 生成→校验重试→落盘。返回 {ok, path, graph, errors[, warnings]}。
    need 为空抛 ValueError；ComfyUI 不可达抛 ComfyError。RAG 为空时退回本机库存。"""
    turn = workflow_build_turn.prepare(
        cfg, need=need, comfy_url=comfy_url, current_graph=current_graph,
        history=history, k=10,
    )
    candidates = turn.candidates
    object_info, packs = candidates.object_info, candidates.packs
    node_catalog = _trim_catalog(packs)

    # 接口速查表：命中包里节点的真实口名/类型，AI 据此接线不臆造
    sheet = workflow_graph_rules.interface_sheet(
        candidates.names, object_info, priority=set(candidates.named),
    )

    # 3. AI 生成 → 校验 → 有错回喂重连（有 current_graph 时在其基础上增量改，输出完整新图）
    convo = (
        f"需求：{need}\n\n【可用节点清单（文字说明）】\n{node_catalog}"
        + "\n\n【节点真实接口（务必据此接线，口名/类型不得臆造）】\n"
        + "说明：in[] 里 * 是需接线的口(带类型)、? 是可选、= 是填字面值的 widget；out[] 是输出序号:类型。\n"
        + sheet
    )
    if turn.history_text:
        convo += "\n\n" + turn.history_text
    if turn.current_graph:
        convo += (
            "\n\n【当前画布工作流(API格式)】\n"
            + json.dumps(turn.current_graph, ensure_ascii=False)
            + "\n\n请在上面这个当前画布的基础上，按新需求做增量修改（保留无关部分，只改需要变的），"
            "输出修改后的**完整** JSON。"
        )
    _deadline = time.time() + BUILD_TIME_BUDGET_SEC  # 到点不再开始新的纠错轮次
    last_errors: list[str] = []
    graph: dict = {}
    for attempt in range(max(1, max_retries)):
        if time.time() > _deadline:
            break
        reply = chat_fn(base_url, api_key, model, _BUILD_SYSTEM, convo, temperature=0.2, proxy=proxy)
        try:
            graph = _extract_json(reply)
        except (ValueError, json.JSONDecodeError) as e:
            last_errors = [f"输出解析失败：{e}"]
            convo += f"\n\n上次输出无法解析为 JSON（{e}），请只输出合法 JSON。"
            continue
        # 用了没装的节点：还有重试就回喂让 AI 换真实节点；没重试了就拆掉+提示安装
        _missing = [nid for nid, n in graph.items()
                    if isinstance(n, dict) and n.get("class_type", "") not in object_info]
        if _missing and attempt < max(1, max_retries) - 1:
            types = sorted({graph[i].get("class_type", "") for i in _missing})
            last_errors = [f"用了本机没装的节点：{'、'.join(types)}"]
            convo += ("\n\n上次用了本机没装的节点(" + "、".join(types) +
                      ")，请只用【节点真实接口】里列出的节点重搭；确实没有对应能力的节点就省略那部分。")
            continue
        graph, missing = workflow_graph_rules.split_missing_nodes(graph, object_info)
        workflow_graph_rules.fill_combo_defaults(graph, object_info)
        errors = workflow_graph_rules.validate_graph(graph, object_info)
        if not errors:
            # 结构审核（悬空/开关单边接/孤岛）——整图模式没"下一轮"，故审核问题也回喂自修
            warnings = workflow_graph_rules.audit_graph(graph, object_info)
            if warnings and attempt < max(1, max_retries) - 1:
                last_errors = warnings
                convo += ("\n\n上次工作流能通过类型校验，但结构审核发现下列问题，请修正后重新输出完整 JSON"
                          "（重点：开关节点两头都接、别留悬空节点、主链要连到输出节点）：\n" + "\n".join(warnings))
                continue
            # combo 已在校验前规整过，这里直接落盘
            path = ""
            if save:
                path = save_workflow(graph, name or need[:20], workflow_dir)
            return {"ok": True, "path": path, "graph": graph, "errors": [], "warnings": warnings + _missing_hint(missing)}
        last_errors = errors
        convo += "\n\n上次工作流有以下错误，请修正后重新输出完整 JSON：\n" + "\n".join(errors)

    return {"ok": False, "path": "", "graph": graph, "errors": last_errors}


def build_module(chat_fn, *, base_url: str, api_key: str, model: str, proxy: str,
                 cfg, need: str, comfy_url: str, current_graph: dict, max_retries: int,
                 history: list[dict] | None = None) -> dict:
    """分模块增量搭建：AI 只出新模块+锚点 → 后端 ID 安全合并进当前图 → 校验整图 → 重试。
    不落盘，返回 {ok, graph, errors[, warnings]}（graph 为合并后完整图，前端写回画布）。"""
    turn = workflow_build_turn.prepare(
        cfg, need=need, comfy_url=comfy_url, current_graph=current_graph,
        history=history, k=10,
    )
    candidates = turn.candidates
    object_info = candidates.object_info

    # 接口速查表：检索命中包里节点的**真实输入/输出口+类型**。没有它 AI 只能猜口名/类型，
    # 会选错开关节点、把 BOOLEAN 接进 IMAGE 口（这正是搭建失败的主因）。
    sheet = workflow_graph_rules.interface_sheet(
        candidates.names, object_info, max_nodes=40,
    )

    base = turn.current_graph
    # 增量模式 prompt 瘦身：①不塞冗长文字 catalog（接口表已给真实口/类型）②当前图只发结构+连线，
    # widget 标量值省略为 …（接线无关）。治"节点多就 prompt 爆炸→502/超时"。合并用前端另传的完整图。
    convo = (
        f"新需求：{need}\n\n【当前工作流(结构+连线，冻结；widget 值已省略为 …，你只需接线不用管它们)】\n"
        + json.dumps(_slim_graph_for_prompt(base), ensure_ascii=False)
        + "\n\n【可用节点及真实接口（务必据此接线，口名/类型不得臆造）】\n"
        + "说明：in[] 里 * 是需接线的口(带类型)、? 是可选、= 是填字面值的 widget；out[] 是输出序号:类型。\n"
        + sheet
    )
    if turn.history_text:
        convo += "\n\n" + turn.history_text
    _deadline = time.time() + BUILD_TIME_BUDGET_SEC  # 到点不再开始新的纠错轮次
    last_errors: list[str] = []
    merged: dict = base
    for attempt in range(max(1, max_retries)):
        if time.time() > _deadline:
            break  # 超预算：不再调模型，跳出返回已有 merged（下方按 last_errors 报）
        reply = chat_fn(base_url, api_key, model, _MODULE_SYSTEM, convo, temperature=0.2, proxy=proxy)
        try:
            out = _extract_json(reply)
        except (ValueError, json.JSONDecodeError) as e:
            last_errors = [f"输出解析失败：{e}"]
            convo += f"\n\n上次输出无法解析为 JSON（{e}），请只输出含 nodes/anchors 的合法 JSON。"
            continue
        module_nodes = out.get("nodes", {}) or {}
        anchors = out.get("anchors", []) or []
        if not module_nodes:
            last_errors = ["未输出任何新增节点"]
            convo += "\n\n上次没有输出 nodes，请给出本模块要新增的节点。"
            continue
        merged = workflow_merge.merge_module(base, module_nodes, anchors)["graph"]
        workflow_graph_rules.fill_combo_defaults(merged, object_info)
        errors = workflow_graph_rules.validate_graph(merged, object_info)
        if not errors:
            # 硬错误过了，再跑结构审核（悬空/开关单边接/孤岛）——这些"能跑但不合意图"的缺陷
            audit = workflow_graph_rules.audit_graph(merged, object_info)
            # 有审核问题且还有重试额度 → 回喂 AI 自修（附具体问题），修好再返回
            if audit and attempt < max(1, max_retries) - 1:
                last_errors = audit
                convo += ("\n\n合并后整图能通过类型校验，但结构审核发现下列问题，请修正你的 nodes/anchors "
                          "后重新输出（重点：开关节点要两头都接、别留悬空节点）：\n" + "\n".join(audit))
                continue
            # combo 已在校验前规整过；audit 作为 warnings 一并回前端（末轮仍有问题也照常写入，如实告知）
            return {"ok": True, "graph": merged, "errors": [], "warnings": audit}
        last_errors = errors
        convo += "\n\n合并后整图有以下错误，请修正你的 nodes/anchors 后重新输出：\n" + "\n".join(errors)

    return {"ok": False, "graph": merged, "errors": last_errors}


def build_direct(chat_fn, *, base_url: str, api_key: str, model: str, proxy: str,
                 cfg, need: str, comfy_url: str, current_graph: dict,
                 history: list[dict] | None = None) -> dict:
    """精简直连模式：信任强模型(Opus 等)一次到位。**只调 1 次模型**输出完整图，
    不查询重写、不 audit 自修、不整图回喂重试——避免多次串行调用在慢中转上超时。
    校验只做一遍：不通过则如实报错(附错误)，由用户看后自己改或重发，不来回折腾。
    返回 {ok, graph, errors, warnings, missing_nodes, alternatives}。"""
    turn = workflow_build_turn.prepare(
        cfg, need=need, comfy_url=comfy_url, current_graph=current_graph,
        history=history, k=12,
    )
    candidates = turn.candidates
    object_info = candidates.object_info
    sheet = workflow_graph_rules.interface_sheet(
        candidates.names, object_info, priority=set(candidates.named),
    )

    convo = (
        f"需求：{need}\n\n【可用节点及真实接口（务必据此接线，口名/类型不得臆造）】\n"
        + "说明：in[] 里 * 是需接线的口(带类型)、? 是可选、= 是填字面值的 widget；out[] 是输出序号:类型。\n"
        + sheet
    )
    if turn.current_graph:
        convo += (
            "\n\n【当前画布(API格式)】\n"
            + json.dumps(turn.current_graph, ensure_ascii=False)
            + "\n\n请在当前画布基础上按需求做增量修改，输出修改后的**完整** JSON（保留无关部分）。"
        )
    if turn.history_text:
        convo += "\n\n" + turn.history_text
    # 精简直连：最多 2 次模型调用(初次 + 1 次纠错回喂)。后端能确定性修的(combo近似值/缺widget/
    # widget误接线/幻觉节点)先自动修掉不占模型；修完仍有真结构错(如缺必填连线口)才回喂 AI 一次。
    # 这样既消化掉增量那种"错误在循环里改掉"的好处，又不学它多轮回喂拖到超时(顶多 2 次)。
    graph: dict = {}
    missing: list[str] = []
    last_errors: list[str] = []
    for attempt in range(2):
        reply = chat_fn(base_url, api_key, model, _BUILD_SYSTEM, convo, temperature=0.2, proxy=proxy)
        try:
            graph = _extract_json(reply)
        except (ValueError, json.JSONDecodeError) as e:
            last_errors = [f"模型输出无法解析为 JSON：{e}"]
            if attempt == 0:
                convo += f"\n\n上次输出无法解析为 JSON（{e}），请只输出合法 JSON。"
                continue
            return {"ok": False, "graph": {}, "errors": last_errors, "warnings": []}
        # 后端自动修：拆幻觉节点 + 规整 combo/补默认/拆错接的 widget 连线
        graph, missing = workflow_graph_rules.split_missing_nodes(graph, object_info)
        workflow_graph_rules.fill_combo_defaults(graph, object_info)
        errors = workflow_graph_rules.validate_graph(graph, object_info)
        if not errors:
            break
        last_errors = errors
        if attempt == 0:
            convo += ("\n\n上次工作流经自动修正后仍有下列错误，请修正后重新输出完整 JSON"
                      "（重点：必接线的口要接上、别把 widget 当连线）：\n" + "\n".join(errors))
            continue
        # 第 2 次仍错：如实返回，不再拖时间
        alts = node_index.suggest_alternatives(cfg, missing, object_info) if missing else {}
        return {"ok": False, "graph": graph, "errors": last_errors,
                "warnings": _missing_hint(missing, alts), "missing_nodes": missing,
                "alternatives": alts}

    alts = node_index.suggest_alternatives(cfg, missing, object_info) if missing else {}
    warnings = workflow_graph_rules.audit_graph(graph, object_info) + _missing_hint(missing, alts)
    return {"ok": True, "graph": graph, "errors": [], "warnings": warnings, "missing_nodes": missing,
            "alternatives": alts}


def build_plan(chat_fn, *, base_url: str, api_key: str, model: str, proxy: str,
               cfg, need: str, comfy_url: str, current_graph: dict,
               history: list[dict] | None = None) -> dict:
    """顾问模式：只产出给人看的中文方案文本，不生成/不改画布。返回 {plan}。
    need 为空抛 ValueError；节点清单以 ComfyUI object_info 为准。"""
    turn = workflow_build_turn.prepare(
        cfg, need=need, comfy_url=comfy_url, current_graph=current_graph,
        history=history, k=12,
    )
    candidates = turn.candidates
    packs = candidates.packs
    node_catalog = _trim_catalog(packs, per_pack=600, total=4000)

    # 顾问方案也要基于**本机真实节点名**，否则会凭训练印象编不存在的节点(如把反推写成 LlamaCPP，
    # 实际本机是 Florence2/BLIP/DeepDanbooru)。给出真实节点清单，并强约束正式方案只用它们。
    object_info = candidates.object_info
    sheet = workflow_graph_rules.interface_sheet(
        candidates.names, object_info, priority=set(candidates.named),
    )

    catalog_text = node_catalog or "RAG 本轮未命中；以下方案仅依据 ComfyUI object_info 本机库存。"
    convo = (
        f"用户需求：{need}\n\n"
        f"【安装状态事实】ComfyUI object_info 共返回 {len(object_info)} 个节点。"
        "下方接口表中的节点均已确认安装；接口表只是按需求筛选的子集，未出现不代表未安装。"
        "本轮没有提供【已确认缺失节点】，禁止建议安装或声称节点缺失。\n\n"
        f"【检索参考（不能用于判断安装状态）】\n{catalog_text}"
    )
    if sheet:
        convo += ("\n\n【本机真实节点（正式方案里点名的节点必须来自这里，别凭印象编节点名）】\n" + sheet)
    if turn.current_graph:
        convo += (
            "\n\n【当前画布(API格式)】\n"
            + json.dumps(turn.current_graph, ensure_ascii=False)
            + "\n\n请说明会在当前画布基础上做哪些增量改动。"
        )
    if turn.history_text:
        convo += "\n\n" + turn.history_text
    plan = chat_fn(base_url, api_key, model, _PLAN_SYSTEM, convo, temperature=0.4, proxy=proxy)
    return {"plan": plan}
