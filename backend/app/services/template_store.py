"""工作流模板持久化：每个模板存为一个 JSON 文件。

模板结构：
{
  "id": "uuid",
  "name": "模板名",
  "source_path": "原始 workflow 文件路径（可选）",
  "exposed": [
    {
      "node_id": "5",
      "field": "steps",
      "label": "采样步数",       # 展示用标签
      "control": "number",       # 控件类型 text/number/textarea/select/image/seed/boolean
      "semantic": "steps",       # 始终等于原工作流字段名
      "binding": "",             # 内部自动用途，不要求用户映射
      "default": 20              # 默认值
    }
  ],
  "created_at": 1700000000.0,
  "updated_at": 1700000000.0
}
"""
from __future__ import annotations

import json
import re
import time
import uuid

from pathlib import Path

from app.config import TEMPLATES_DIR
from app.services import workflow_flatten

# 纯文本载体节点（原生 String / PrimitiveString / 自定义 Text）：字段名本身没有语义
# （就是 "String"/"text"），节点类型也看不出去向，**只有节点标题**能声明它装的是什么。
# Anima AIO 全能流整条流里没有任何 CLIPTextEncode——提示词在 title="POS Prompt" 的
# String 节点里；只按节点类型判会漏掉它，正文注入静默落空（2026-09-26 真机实锤）。
_TEXT_CARRIER_TYPES = ("string", "primitive", "text")
_TEXT_CARRIER_FIELDS = frozenset({"string", "text", "value", "prompt", "positive"})
_NEG_PROMPT_TITLE_RE = re.compile(r"negative|neg[\s_-]*prompt|负面|负向|反向", re.IGNORECASE)
_POS_PROMPT_TITLE_RE = re.compile(r"positive|\bpos[\s_-]*prompt\b|正面|正向", re.IGNORECASE)
# 第 94 刀：参考视频节点的角色参考图槽（图片3、4、5…）。模板编辑器没有手选 binding 的入口，
# 可自动识别的声明只有字段名（ref_image_files 单控件）与节点标题（「参考图N」编号）。
# ⚠ 「参考底图」是图生图输入，不匹配「参考图」，不能被这条规则抢走。
_REF_IMAGE_TITLE_RE = re.compile(
    r"(?:参考图|角色参考|reference[\s_-]*image|\bref[\s_-]*image\b)\s*[_-]?(\d+)?",
    re.IGNORECASE,
)
# 视频生成节点上「以整数像素显式声明输出尺寸」的字段（2026-10-31 用户定案：
# 图片的宽高要和视频的宽高一致）。这些字段在工作流里通常由 ResolutionSelector 连线供给，
# 而画布暴露层只收无连线字段，所以按节点类型自动绑定、提交时用真实图片尺寸覆盖连线。
# 2026-11-01：H3 首帧链改走 MiniMaxH3ImageToVideo（ref2va 没有任何帧锁定入口，
# 首帧只当参考图 ⇒ 第 0 帧必然不是成图），两个类型都要认，否则宽高一致静默失效。
_VIDEO_SIZE_NODE_TYPES = ("minimaxh3referencetovideo", "minimaxh3imagetovideo")
# 第 100 刀（2026-11-08）：H3 参考视频节点（MiniMaxH3ReferenceToVideo）的图片/音频槽在
# 工作流里是**连线**输入（ref_images.ref_image_N / ref_audios.ref_audio_N），模板编辑器暴露
# 的是上游 LoadImage/LoadAudio 自己的未连线字段——字段名与标题都看不出它接在第几个槽。
# ⇒ binding 只能由**图位置**决定：图片1/2 = 首尾帧，图片3+ = 角色参考图；音频槽 N → 第 N+1 条
# 参考音频。槽名带组前缀（ref_images.ref_image_0），用 search 匹配尾段。
_REF2VA_IMAGE_SLOT_RE = re.compile(r"(?:^|\.)ref_image_(\d+)$")
_REF2VA_AUDIO_SLOT_RE = re.compile(r"(?:^|\.)ref_audio_(\d+)$")
_REF2VA_IMAGE_FIELDS = ("image", "images")
_REF2VA_AUDIO_FIELDS = ("audio",)


def _ensure_dir() -> None:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


def _path(template_id: str) -> Path:
    return TEMPLATES_DIR / f"{template_id}.json"


def _normalize_ids(values) -> list[str]:
    out: list[str] = []
    for value in values or []:
        item = str(value or "").strip()
        if item and item not in out:
            out.append(item)
    return out


def _workflow_nodes(record: dict) -> list[dict]:
    workflow = record.get("workflow_data")
    nodes = workflow.get("nodes") if isinstance(workflow, dict) else None
    if isinstance(nodes, list):
        return [node for node in nodes if isinstance(node, dict)]
    if isinstance(workflow, dict):
        return [
            {**node, "id": node_id} for node_id, node in workflow.items()
            if isinstance(node, dict) and "class_type" in node
        ]
    return []


def _link_table(raw_links) -> dict[str, tuple[str, int]]:
    """link id → (源节点 id, 源输出槽)。兼容顶层数组与 subgraph 内的字典两种形态。"""
    table: dict[str, tuple[str, int]] = {}
    for raw in raw_links or []:
        if isinstance(raw, list) and len(raw) >= 5:
            link_id, origin_id, origin_slot = raw[0], raw[1], raw[2]
        elif isinstance(raw, dict) and raw.get("id") is not None:
            link_id = raw.get("id")
            origin_id = raw.get("origin_id")
            origin_slot = raw.get("origin_slot")
        else:
            continue
        try:
            table[str(link_id)] = (str(origin_id), int(origin_slot or 0))
        except (TypeError, ValueError):
            continue
    return table


def _ui_sources_from_output(node_id: str, links: dict[str, tuple[str, int]],
                            nodes: dict[str, dict], marker: str, media_type: str,
                            seen: set[str]) -> set[str]:
    """沿同类型（IMAGE/AUDIO）连线向上游找 LoadImage/LoadAudio 源头。

    穿透缩放/拼接等预处理节点；只跟随与目标媒体同类型的输入（宽高/模型等旁支不参与）。
    """
    if node_id in seen:
        return set()
    seen.add(node_id)
    node = nodes.get(node_id)
    if not isinstance(node, dict):
        return set()
    if marker in str(node.get("type") or node.get("class_type") or "").lower():
        return {node_id}
    found: set[str] = set()
    for item in node.get("inputs") or []:
        if not isinstance(item, dict) or item.get("link") is None:
            continue
        item_type = str(item.get("type") or "").lower()
        if item_type and item_type != media_type:
            continue
        found |= _ui_sources_from_link(item["link"], links, nodes, marker, media_type, seen)
    return found


def _ui_sources_from_link(link_id, links: dict[str, tuple[str, int]],
                          nodes: dict[str, dict], marker: str, media_type: str,
                          seen: set[str]) -> set[str]:
    origin = links.get(str(link_id))
    if origin is None:
        return set()
    return _ui_sources_from_output(origin[0], links, nodes, marker, media_type, seen)


def _ui_ref2va_slot_sources(workflow: dict) -> list[tuple[str, str, int]]:
    """UI 工作流 → [(源节点 id, "image"/"audio", 槽序号 0 基)]。

    先走一次 `workflow_flatten.flatten`：Set/Get 中转（rgthree）必须线性化，否则
    LoadImage → SetNode → GetNode → ref_image_N 的链在这里断掉；subgraph 一并内联。
    """
    flattened, _ = workflow_flatten.flatten(workflow)
    nodes_list = [item for item in (flattened.get("nodes") or []) if isinstance(item, dict)]
    if not nodes_list:
        return []
    nodes = {str(item.get("id")): item for item in nodes_list}
    links = _link_table(flattened.get("links"))
    found: list[tuple[str, str, int]] = []
    for node in nodes_list:
        for item in node.get("inputs") or []:
            if not isinstance(item, dict) or item.get("link") is None:
                continue
            name = str(item.get("name") or "")
            image_match = _REF2VA_IMAGE_SLOT_RE.search(name)
            audio_match = _REF2VA_AUDIO_SLOT_RE.search(name)
            if image_match:
                sources = _ui_sources_from_link(
                    item["link"], links, nodes, "loadimage", "image", set(),
                )
                found.extend(
                    (source, "image", int(image_match.group(1))) for source in sorted(sources)
                )
            elif audio_match:
                sources = _ui_sources_from_link(
                    item["link"], links, nodes, "loadaudio", "audio", set(),
                )
                found.extend(
                    (source, "audio", int(audio_match.group(1))) for source in sorted(sources)
                )
    return found


def _api_ref2va_slot_sources(workflow: dict) -> list[tuple[str, str, int]]:
    """API 格式工作流 → [(源节点 id, "image"/"audio", 槽序号 0 基)]。"""
    nodes = {
        str(node_id): node for node_id, node in workflow.items()
        if isinstance(node, dict) and "class_type" in node
    }

    def sources(node_id: str, seen: set[str]) -> set[str]:
        if node_id in seen:
            return set()
        seen.add(node_id)
        node = nodes.get(node_id)
        if not isinstance(node, dict):
            return set()
        class_type = str(node.get("class_type") or "").lower()
        if "loadimage" in class_type or "loadaudio" in class_type:
            return {node_id}
        found: set[str] = set()
        for value in (node.get("inputs") or {}).values():
            if isinstance(value, list) and len(value) == 2:
                found |= sources(str(value[0]), seen)
        return found

    found: list[tuple[str, str, int]] = []
    for node in nodes.values():
        for key, value in (node.get("inputs") or {}).items():
            if not (isinstance(value, list) and len(value) == 2):
                continue
            name = str(key)
            image_match = _REF2VA_IMAGE_SLOT_RE.search(name)
            audio_match = _REF2VA_AUDIO_SLOT_RE.search(name)
            if image_match:
                expected, slot = "image", int(image_match.group(1))
            elif audio_match:
                expected, slot = "audio", int(audio_match.group(1))
            else:
                continue
            for source in sorted(sources(str(value[0]), set())):
                source_node = nodes.get(source) or {}
                class_type = str(source_node.get("class_type") or "").lower()
                kind = "image" if "loadimage" in class_type else (
                    "audio" if "loadaudio" in class_type else ""
                )
                if kind != expected:
                    continue
                found.append((source, kind, slot))
    return found


def _ref2va_binding(kind: str, index: int) -> str:
    """槽位语义（唯一口径）：图片1/2 = 首尾帧，图片3+ = 角色参考图（1 基）；音频槽 N → N+1。"""
    if kind == "image":
        if index == 0:
            return "first_frame_image"
        if index == 1:
            return "last_frame_image"
        return f"reference_image_{index - 1}"
    return f"reference_audio_{index + 1}"


def _ref2va_slot_bindings(record: dict) -> dict[tuple[str, str], str]:
    """(源节点 id, 媒体字段名) → Ref2VA 槽位 binding；同一节点接多个槽则弃用（无法一键两值）。"""
    workflow = record.get("workflow_data")
    if not isinstance(workflow, dict):
        return {}
    if isinstance(workflow.get("nodes"), list):
        sources = _ui_ref2va_slot_sources(workflow)
    else:
        sources = _api_ref2va_slot_sources(workflow)
    bindings: dict[tuple[str, str], str] = {}
    conflicts: set[tuple[str, str]] = set()
    for node_id, kind, index in sources:
        binding = _ref2va_binding(kind, index)
        fields = _REF2VA_IMAGE_FIELDS if kind == "image" else _REF2VA_AUDIO_FIELDS
        for field_name in fields:
            key = (node_id, field_name)
            if key in bindings and bindings[key] != binding:
                conflicts.add(key)
            bindings[key] = binding
    for key in conflicts:
        bindings.pop(key, None)
    return bindings


def _infer_binding(record: dict, node_id: str, field_name: str,
                   slot_bindings: dict[tuple[str, str], str] | None = None) -> str:
    slots = _ref2va_slot_bindings(record) if slot_bindings is None else slot_bindings
    positional = slots.get((node_id, field_name), "")
    if positional:
        return positional
    node = next(
        (item for item in _workflow_nodes(record) if str(item.get("id")) == node_id),
        {},
    )
    node_type = str(node.get("type") or node.get("class_type") or "").lower()
    title = str(node.get("title") or "").lower()
    name = field_name.lower()
    if name == "lora_name" and "lora" in node_type:
        return "lora_name"
    if name in ("strength_model", "strength_clip") and "lora" in node_type:
        return "lora_weight"
    if node_type == "emptylatentimage" and name == "width":
        return "latent_width"
    if node_type == "emptylatentimage" and name == "height":
        return "latent_height"
    if node_type in _VIDEO_SIZE_NODE_TYPES and name == "width":
        return "video_width"
    if node_type in _VIDEO_SIZE_NODE_TYPES and name == "height":
        return "video_height"
    # 第 94 刀：参考视频节点的角色参考图槽（必须先于 base_image，否则 LoadImage 永远只被标成底图）
    if name in ("ref_image_files", "reference_image_files") and "ref" in node_type:
        return "reference_image_files"
    numbered_ref = re.fullmatch(r"ref_image_(\d+)", name)
    if numbered_ref and "ref" in node_type:
        return f"reference_image_{numbered_ref.group(1)}"
    if name in ("ref_image", "reference_image"):
        return "reference_image"
    if "loadimage" in node_type and name in ("image", "images"):
        ref_title = _REF_IMAGE_TITLE_RE.search(title)
        if ref_title:
            return f"reference_image_{ref_title.group(1)}" if ref_title.group(1) else "reference_image"
        return "base_image"
    if "cliptextencode" in node_type and name == "text":
        return "negative_prompt" if any(word in title for word in ("negative", "负面", "负向")) else "prompt"
    # 文本载体节点：字段名与节点类型都判不出语义，改由**节点标题**声明（负面优先）。
    if any(mark in node_type for mark in _TEXT_CARRIER_TYPES) and name in _TEXT_CARRIER_FIELDS:
        if _NEG_PROMPT_TITLE_RE.search(title):
            return "negative_prompt"
        if _POS_PROMPT_TITLE_RE.search(title):
            return "prompt"
    return ""


def _generic_media_binding(binding: str) -> bool:
    """编辑器/标题给出的通用媒体 binding：Ref2VA 槽位语义必须能盖掉它。

    模板编辑器没有手选 binding 的入口 ⇒ 这些值全是启发式（LoadImage 未命名 → base_image、
    LoadAudio → voice_reference、标题猜出的 reference_image*），不是用户意图。真正的
    `reference_image_files`（Painter 单控件 JSON 数组）不在本集合内，绝不被槽位覆盖。
    """
    return binding in ("", "base_image", "voice_reference") or bool(
        re.fullmatch(r"reference_image(?:_\d+)?", binding)
    )


def _normalize_exposed(record: dict) -> list[dict]:
    normalized: dict[tuple[str, str], dict] = {}
    slot_bindings = _ref2va_slot_bindings(record)
    for raw in record.get("exposed") or []:
        if not isinstance(raw, dict):
            continue
        node_id = str(raw.get("node_id") or "").strip()
        field_name = str(raw.get("field") or "").strip()
        if not node_id or not field_name:
            continue
        legacy_semantic = str(raw.get("semantic") or "").strip()
        binding = str(raw.get("binding") or "").strip()
        if not binding and legacy_semantic and legacy_semantic != field_name:
            binding = legacy_semantic
        binding = binding or _infer_binding(record, node_id, field_name, slot_bindings)
        positional = slot_bindings.get((node_id, field_name), "")
        if positional and _generic_media_binding(binding):
            binding = positional
        item = {
            **raw,
            "node_id": node_id,
            "field": field_name,
            "label": field_name,
            "semantic": field_name,
        }
        if binding:
            item["binding"] = binding
        else:
            item.pop("binding", None)
        key = (node_id, field_name)
        if key not in normalized:
            normalized[key] = item
        elif binding and not normalized[key].get("binding"):
            normalized[key]["binding"] = binding
    return list(normalized.values())


def _auto_latent_fields(record: dict, exposed: list[dict]) -> list[dict]:
    """唯一 EmptyLatentImage 可无歧义绑定；多节点工作流必须由用户手动标注。"""
    if all(any(field.get("binding") == binding for field in exposed)
           for binding in ("latent_width", "latent_height")):
        return exposed
    latent_nodes = [
        node for node in _workflow_nodes(record)
        if (node.get("type") or node.get("class_type")) == "EmptyLatentImage"
    ]
    if len(latent_nodes) != 1:
        return exposed
    node = latent_nodes[0]
    node_id = str(node.get("id") or "").strip()
    if not node_id:
        return exposed
    widgets = node.get("widgets_values")
    raw_inputs = node.get("inputs")
    inputs = raw_inputs if isinstance(raw_inputs, dict) else {}
    width = widgets[0] if isinstance(widgets, list) and len(widgets) > 0 else inputs.get("width", 1024)
    height = widgets[1] if isinstance(widgets, list) and len(widgets) > 1 else inputs.get("height", 1024)
    result = [*exposed]
    for field_name, binding, default in (
        ("width", "latent_width", width), ("height", "latent_height", height),
    ):
        existing = next((field for field in result
                         if field["node_id"] == node_id and field["field"] == field_name), None)
        if existing:
            existing["binding"] = binding
        else:
            result.append({
                "node_id": node_id, "field": field_name, "label": field_name,
                "control": "number", "semantic": field_name,
                "binding": binding, "default": default,
            })
    return result


def _widget_value_by_name(node: dict, field_name: str) -> object:
    """UI 格式里 widgets_values 按「带 widget 的输入声明顺序」排列，据此按名取值。

    位置索引会认错（H3 节点第 0 位是 prompt，1344/768 才是宽高，124 是帧数），
    所以只取目标字段自己的那一位；API 格式或字段无 widget 时返回 None（仅影响展示默认值）。
    """
    names = [
        str(item.get("name") or "").lower() for item in (node.get("inputs") or [])
        if isinstance(item, dict) and isinstance(item.get("widget"), dict)
    ]
    values = node.get("widgets_values")
    if not names or not isinstance(values, list):
        return None
    index = next((i for i, name in enumerate(names) if name == field_name.lower()), -1)
    return values[index] if 0 <= index < len(values) else None


def _auto_video_size_fields(record: dict, exposed: list[dict]) -> list[dict]:
    """视频节点 width/height 自动绑定（与 _auto_latent_fields 同纪律：唯一候选才绑）。

    这两个字段在 H3 流里由 ResolutionSelector 连线供给，画布暴露层只收无连线字段
    ⇒ 用户无法手标，必须由这里补上 binding，提交层才能用真实首帧尺寸覆盖连线。

    `video_size_from_template`（2026-11-08）：分辨率由模板工作流自己决定（ResolutionSelector
    连线），不让首帧像素尺寸覆盖。用于复刻已在目标机验证通过的固定画幅模板。
    """
    if record.get("video_size_from_template"):
        return exposed
    if all(any(field.get("binding") == binding for field in exposed)
           for binding in ("video_width", "video_height")):
        return exposed
    video_nodes = [
        node for node in _workflow_nodes(record)
        if str(node.get("type") or node.get("class_type") or "").lower()
        in _VIDEO_SIZE_NODE_TYPES
    ]
    if len(video_nodes) != 1:
        return exposed
    node = video_nodes[0]
    node_id = str(node.get("id") or "").strip()
    if not node_id:
        return exposed
    result = [*exposed]
    for field_name, binding in (("width", "video_width"), ("height", "video_height")):
        existing = next((field for field in result
                         if field["node_id"] == node_id and field["field"] == field_name), None)
        if existing:
            existing["binding"] = binding
        else:
            result.append({
                "node_id": node_id, "field": field_name, "label": field_name,
                "control": "number", "semantic": field_name,
                "binding": binding, "default": _widget_value_by_name(node, field_name),
            })
    return result


def _normalize(record: dict) -> dict:
    """返回标准模板副本：节点 id 非空字符串、去重保序，并兼容旧输入字段。"""
    if not isinstance(record, dict):
        return record
    normalized = dict(record)
    inputs = _normalize_ids(record.get("input_node_ids"))
    for legacy in (record.get("prompt_node_id"), record.get("image_node_id")):
        inputs = _normalize_ids([*inputs, legacy])
    exposed = _normalize_exposed(record)
    exposed = _auto_latent_fields(record, exposed)
    exposed = _auto_video_size_fields(record, exposed)
    normalized.update({
        "exposed": exposed,
        "node_order": _normalize_ids(record.get("node_order")),
        "input_node_ids": inputs,
        "output_node_ids": _normalize_ids(record.get("output_node_ids")),
        "primary_output_node_id": str(record.get("primary_output_node_id") or "").strip(),
        "prompt_node_id": str(record.get("prompt_node_id") or "").strip(),
        "image_node_id": str(record.get("image_node_id") or "").strip(),
        # 模板自带 LoRA 是能力的一部分（如蒸馏加速 LoRA）：提交时不清零、也不接受外部插画 LoRA。
        "preserve_loras": bool(record.get("preserve_loras")),
        # 2026-11-12：可选预编译 API 快照（AIO 子图展开后修复过的扁平图）。
        # 有它时提交直接用它；没有则仍从 source_path 现场 ui_to_api（旧模板零变化）。
        "api_workflow": record.get("api_workflow"),
    })
    return normalized


def ordered_node_ids(record: dict) -> list[str]:
    """画布节点顺序：用户顺序、其余暴露节点、输入节点、输出节点。"""
    tpl = _normalize(record)
    exposed = _normalize_ids([
        *[field["node_id"] for field in tpl.get("exposed", [])],
        *[field.get("node_id") for field in record.get("exposed", []) if isinstance(field, dict)],
    ])
    return _normalize_ids([
        *tpl.get("node_order", []), *exposed,
        *tpl.get("input_node_ids", []), *tpl.get("output_node_ids", []),
    ])


def video_template_caps(record: dict | None) -> dict[str, int | bool]:
    """视频工作流模板**真能消费**什么（提示词只准声明模板真有的图片位）。

    第 106 刀（实稿发现 1）：编译层此前拿不到视频工作流模板，不知它有没有尾帧/参考图槽，
    ②/④ 照样写「图片2=尾帧、图片3=角色参考图」，而实机模板只暴露 `first_frame_image`
    ⇒ 这些图从不上传，提示词在指挥模型去用不存在的图。
    - supports_last_frame：exposed 有 `last_frame_image`。Ref2VA 槽 1 必须**有线**才推断得出
      binding（空槽不产生字段）⇒ 没有该字段即视为模板不收尾帧；
    - reference_slots：角色参考图槽数（`reference_image_N` 的最大 N；通用的
      `reference_image` / `reference_image_files` 是批量控件，记 99＝不设上限）；
    - duration：`video_duration` 字段默认值（int，缺省 0）。工作流默认是物理真值：
      前端不注入时长时成片就按它渲染，提示词的拍界必须跟它对齐。

    `record` 必须是 `get_template` / `list_templates` 返回的归一化记录。
    """
    exposed = record.get("exposed") if isinstance(record, dict) else None
    if not isinstance(exposed, list):
        return {"supports_last_frame": False, "reference_slots": 0, "duration": 0}
    supports_last = False
    slots = 0
    bulk = False
    duration = 0
    for item in exposed:
        if not isinstance(item, dict):
            continue
        binding = str(item.get("binding") or "")
        if binding == "last_frame_image":
            supports_last = True
        elif binding in ("reference_image", "reference_image_files"):
            bulk = True
        elif binding.startswith("reference_image_"):
            suffix = binding[len("reference_image_"):]
            if suffix.isdigit():
                slots = max(slots, int(suffix))
        elif binding == "video_duration" and not duration:
            try:
                duration = int(item.get("default") or 0)
            except (TypeError, ValueError):
                duration = 0
    return {
        "supports_last_frame": supports_last,
        "reference_slots": 99 if bulk else slots,
        "duration": max(0, duration),
    }


def list_templates() -> list[dict]:
    _ensure_dir()
    items: list[dict] = []
    for p in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            items.append(_normalize(json.loads(p.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    items.sort(key=lambda t: t.get("updated_at", 0), reverse=True)
    return items


def get_template(template_id: str) -> dict | None:
    p = _path(template_id)
    if not p.exists():
        return None
    try:
        return _normalize(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def save_template(data: dict, template_id: str | None = None) -> dict:
    _ensure_dir()
    now = time.time()
    if template_id:
        existing = get_template(template_id) or {}
        created = existing.get("created_at", now)
    else:
        template_id = uuid.uuid4().hex
        created = now

    # 新建模板时嵌入工作流快照，使模板独立于源文件后续修改
    workflow_data = data.get("workflow_data")
    if workflow_data is None:
        src = data.get("source_path", "")
        if src:
            try:
                from pathlib import Path as _Path
                workflow_data = json.loads(_Path(src).read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                workflow_data = None

    record = _normalize({
        "id": template_id,
        "name": data.get("name", "未命名模板"),
        "source_path": data.get("source_path", ""),
        "workflow_data": workflow_data,
        "exposed": data.get("exposed", []),
        "node_order": data.get("node_order", []),
        "description": data.get("description", ""),
        "prompt_node_id": data.get("prompt_node_id", ""),
        "image_node_id": data.get("image_node_id", ""),
        "input_node_ids": data.get("input_node_ids", []),
        "output_node_ids": data.get("output_node_ids", []),
        "primary_output_node_id": data.get("primary_output_node_id", ""),
        "preserve_loras": bool(data.get("preserve_loras", False)),
        "video_size_from_template": bool(data.get("video_size_from_template", False)),
        "api_workflow": data.get("api_workflow"),
        "created_at": created,
        "updated_at": now,
    })
    _path(template_id).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return record


def delete_template(template_id: str) -> bool:
    p = _path(template_id)
    if p.exists():
        p.unlink()
        return True
    return False
