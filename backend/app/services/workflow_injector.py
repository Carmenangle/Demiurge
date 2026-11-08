"""把用户填写的值 / 提示词注入到已转好的 API 工作流。

纯变换：不接触 ComfyUI、不做 I/O。原地改写 api 的 inputs 并返回缺失的必填输入标签，
路由层据此决定是否 422 拒绝。抽出来后可脱离 live ComfyUI 单测。
"""
from __future__ import annotations

import re

from copy import deepcopy

_TEXT_FIELDS = ("text", "string", "prompt", "positive")
_LORA_LOADERS = {"LoraLoader", "LoraLoaderModelOnly"}
# 动态槽 LoRA 加载器：槽位是 API 图里的 lora_N 对象输入，不是 schema 字段
# （ui_to_api 的 _DYNAMIC_SLOT_WIDGETS 负责把 widgets_values 展开成这一形态）。
_LORA_ADAPTERS = {"Power Lora Loader (rgthree)"}
# 可选图像绑定：尾帧由首尾帧链按需提供（尾帧生图失败的设计降级就是「只出首帧」）。
# 为空时不算缺失，并把节点连同下游连线一起摘掉——否则模板自带的默认图（如
# example.png）会悄悄成为尾帧；目标 ComfyUI 上还往往没有这张文件。
# 模板若只暴露尾帧图（没有主图），它仍是唯一图口、照旧必填。
_OPTIONAL_IMAGE_BINDINGS = {"last_frame_image"}
# 第 100 刀：Ref2VA 的角色参考图/参考音频也是「补充口」——缺图/缺音频必须摘掉源节点，
# 绝不能让模板自带的示例文件顶上来（LoadAudio 尤其危险：不摘会把示例 mp3 当对白音轨）。
_OPTIONAL_REFERENCE_IMAGE_RE = re.compile(r"reference_image(?:_\d+)?")
_OPTIONAL_REFERENCE_AUDIO_RE = re.compile(r"reference_audio(?:_\d+)?")


def _is_optional_image_binding(binding: str) -> bool:
    return binding in _OPTIONAL_IMAGE_BINDINGS or bool(
        _OPTIONAL_REFERENCE_IMAGE_RE.fullmatch(binding)
    )


def _field_binding(field: dict) -> str:
    """与前端 workflowFieldBinding 同口径：binding 优先，旧模板回退 semantic。"""
    binding = str(field.get("binding") or "").strip()
    if binding:
        return binding
    semantic = str(field.get("semantic") or "").strip()
    field_name = str(field.get("field") or "").strip()
    return semantic if semantic and semantic != field_name else ""


def _drop_unused_media_node(api: dict, node_id: str) -> None:
    """摘掉没拿到值的可选媒体节点（图片/音频），并连带收掉只因它才有输入的下游预处理节点。

    可选输入（如 MiniMax H3 的 last_frame / ref_audios）缺键即按「无此输入」执行；但模板里
    尾帧通常先经过缩放节点（73 LoadImage → 71 ImageScaleByAspectRatio → 329.last_frame），
    只删图片节点会让缩放节点缺输入、整单校验失败。这里沿下游反复删除「失去全部
    节点连线输入」的节点，再把指向它们的连线一并摘掉——329.last_frame 就是这样断开的。
    """
    if node_id not in api:
        return
    dead = {node_id}
    while True:
        grew = False
        for other_id, node in list(api.items()):
            if other_id in dead or not isinstance(node, dict):
                continue
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                continue
            lost = False
            for key, value in list(inputs.items()):
                if isinstance(value, list) and len(value) == 2 and str(value[0]) in dead:
                    del inputs[key]
                    lost = True
            if not lost:
                continue
            # 还有别的节点连线 ⇒ 这个节点不只服务这张可选图，保留（其缺失输入由模板/ComfyUI 兜底）
            if any(isinstance(value, list) and len(value) == 2 for value in inputs.values()):
                continue
            dead.add(other_id)
            grew = True
        if not grew:
            break
    for dead_id in dead:
        api.pop(dead_id, None)


def disable_all_loras(api: dict) -> None:
    """无 LoRA 模式保留图结构，但令所有 LoRA 加载器不产生影响。"""
    for node in api.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if class_type not in _LORA_LOADERS and class_type not in _LORA_ADAPTERS:
            continue
        inputs = node.setdefault("inputs", {})
        if class_type in _LORA_ADAPTERS:
            for key, value in inputs.items():
                if key.startswith("lora_") and isinstance(value, dict):
                    value["on"] = False
            continue
        inputs["strength_model"] = 0
        if class_type == "LoraLoader":
            inputs["strength_clip"] = 0


def inject_lora_stack(api: dict, anchor_node_id: str, loras: list[dict]) -> bool:
    """把 LoRA 列表串到模板现有加载器后，并把原下游改接到链尾。"""
    stack = [item for item in loras if str(item.get("name") or "").strip()]
    anchor_id = str(anchor_node_id or "")
    anchor = api.get(anchor_id)
    if not stack or not isinstance(anchor, dict):
        return False
    class_type = str(anchor.get("class_type") or "")
    anchor_inputs = anchor.setdefault("inputs", {})
    if class_type in _LORA_ADAPTERS:
        # 锚点语义与标准 loader 一致：模板在锚点上的条目被本次所选 LoRA 替换
        # （模板要保底就开 preserve_loras，提交层会整段跳过 LoRA 处理）。
        for key in [name for name in anchor_inputs if name.startswith("lora_")]:
            del anchor_inputs[key]
        for index, item in enumerate(stack, start=1):
            anchor_inputs[f"lora_{index}"] = {
                "on": True,
                "lora": str(item["name"]),
                "strength": float(item.get("weight", 0.8)),
                "strengthTwo": None,
            }
        return True
    if class_type not in _LORA_LOADERS:
        return False

    def apply_values(inputs: dict, item: dict) -> None:
        weight = float(item.get("weight", 0.8))
        inputs["lora_name"] = str(item["name"])
        inputs["strength_model"] = weight
        if class_type == "LoraLoader":
            inputs["strength_clip"] = weight

    apply_values(anchor_inputs, stack[0])
    numeric_ids = [int(node_id) for node_id in api if str(node_id).isdigit()]
    next_id = max(numeric_ids, default=0) + 1
    chain_ids = {anchor_id}
    previous_id = anchor_id
    for item in stack[1:]:
        while str(next_id) in api:
            next_id += 1
        node_id = str(next_id)
        next_id += 1
        inputs = deepcopy(anchor_inputs)
        inputs["model"] = [previous_id, 0]
        if class_type == "LoraLoader":
            inputs["clip"] = [previous_id, 1]
        apply_values(inputs, item)
        api[node_id] = {"class_type": class_type, "inputs": inputs}
        chain_ids.add(node_id)
        previous_id = node_id
    if previous_id == anchor_id:
        return True
    for node_id, node in api.items():
        if node_id in chain_ids or not isinstance(node, dict):
            continue
        for key, value in (node.get("inputs") or {}).items():
            if isinstance(value, list) and len(value) == 2 and str(value[0]) == anchor_id:
                output_index = value[1]
                if output_index == 0 or (class_type == "LoraLoader" and output_index == 1):
                    node["inputs"][key] = [previous_id, output_index]
    return True


def pick_lora_anchor(api: dict, exposed: list[dict]) -> str:
    """选 LoRA 注入锚点：模板标注优先，未标注时自动发现动态槽加载器。

    自动发现只认 _LORA_ADAPTERS——标准 loader 仍要求模板显式标注，避免在
    多加载器模板里猜错节点。优先取模型链末端（不被另一个 LoRA 加载器当作
    model/clip 源的那一个），让新 LoRA 串在既有栈之后。
    """
    explicit = next((
        str(field.get("node_id") or "") for field in exposed
        if str(field.get("node_id") or "") in api
        and (
            str(field.get("binding") or "") == "lora_name"
            or (str(field.get("semantic") or "") == "lora_name"
                and str(field.get("field") or "") != "lora_name")
        )
    ), "")
    if explicit:
        return explicit
    adapters = [
        node_id for node_id, node in api.items()
        if isinstance(node, dict) and node.get("class_type") in _LORA_ADAPTERS
    ]
    if not adapters:
        return ""
    adapter_ids = set(adapters)
    referenced = {
        str(value[0])
        for node_id in adapters
        for value in (api[node_id].get("inputs") or {}).values()
        if isinstance(value, list) and len(value) == 2 and str(value[0]) in adapter_ids
    }
    for node_id in adapters:
        if node_id not in referenced:
            return node_id
    return adapters[0]


def unsupported_lora_types(api: dict) -> list[str]:
    """API 图里出现、但注入/关闭都不认识的执行型 LoRA 节点类型（用于 422 文案）。

    文本形态的 UI 栈（class_type 含 uionly）不执行 LoRA，不列入。
    """
    found: list[str] = []
    for node in api.values():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        lowered = class_type.lower()
        if "lora" not in lowered or "uionly" in lowered:
            continue
        if class_type in _LORA_LOADERS or class_type in _LORA_ADAPTERS:
            continue
        if class_type not in found:
            found.append(class_type)
    return found


def _coerce_number(value: str):
    """字符串数字 → int/float；解析失败返回 None（调用方保留原值）。

    整数形态走 int（latent 宽高/steps），其余按 float（cfg/strength 等）。
    """
    text = value.strip()
    try:
        if text.lstrip("+-").isdigit():
            return int(text)
        return float(text)
    except ValueError:
        return None


def inject_template_values(
    api: dict,
    exposed: list[dict],
    values: dict,
    prompt: str = "",
    prompt_node_id: str = "",
) -> list[str]:
    """套用暴露字段的用户值，并可选把 prompt 注入到指定节点的文本字段。

    - 仅覆盖模板暴露的字段（node_id.field）。
    - 主图输入（control == "image"）为空 → 记入 missing；可选图口（尾帧）为空 →
      从 API 图里摘掉该节点，不算缺失。
    - prompt 非空且 prompt_node_id 命中 → 写首个常见文本字段，否则首个字符串字段。

    原地修改 api，返回 missing（缺失必填项的标签列表）。

    values 键支持三种形态（按序匹配，先复合键后别名）：
    - 复合键 "node_id.field"（前端 NodeCard 语义）；
    - binding/semantic 别名（如 "prompt"/"lora_name"——Autopilot 计划编译产出的语义参数）；
    - field 裸名（与复合键 field 相同时等效）。
    prompt 参数在 prompt_node_id 未命中时兜底注入 exposed 里 binding=="prompt" 的字段
    （模板保存编辑态时已标注提示词节点；未标注则不猜，交由 missing/原值）。
    """
    # 变体嵌套展平（2026-09-05 实锤）：模型会把批参数写成 {"name","prompt","values":
    # {"latent_width":…}} 嵌套形态，嵌套键从未进注入 → 宽高/权重静默落回模板默认
    # （720×1080 请求出 1280×720 横版图）。在注入唯一收口展平；顶层键优先。
    nested = values.get("values")
    if isinstance(nested, dict):
        merged = {key: val for key, val in values.items() if key != "values"}
        for key, val in nested.items():
            merged.setdefault(key, val)
        values = merged
    exposed_keys = {f"{f['node_id']}.{f['field']}" for f in exposed}
    # 模板是否有「必填图口」：只有可选尾帧的模板不能把尾帧当可省（它是唯一图源）。
    has_primary_image = any(
        f.get("control") == "image" and not _is_optional_image_binding(_field_binding(f))
        for f in exposed
    )
    missing: list[str] = []
    for f in exposed:
        key = f"{f['node_id']}.{f['field']}"
        node_id, field = f["node_id"], f["field"]
        val = values.get(key)
        if val is None:
            # 别名匹配：binding / semantic / field 裸名
            for alias in (f.get("binding"), f.get("semantic"), field):
                if alias and (alias in values):
                    val = values[alias]
                    break
        # 输入型（图片）为空：主图缺失拒绝启动；可选图口缺图则摘节点，绝不落回模板默认图
        binding = _field_binding(f)
        if f.get("control") == "image" and (val is None or val == ""):
            if has_primary_image and _is_optional_image_binding(binding):
                _drop_unused_media_node(api, node_id)
                continue
            missing.append(f.get("label") or field)
            continue
        # 参考音频空值：摘掉 LoadAudio（及只为它供输入的下游），不落模板默认音轨
        if (val is None or val == "") and _OPTIONAL_REFERENCE_AUDIO_RE.fullmatch(binding):
            _drop_unused_media_node(api, node_id)
            continue
        if val is None or key not in exposed_keys:
            continue
        # 数值型字段收窄：Autopilot 计划注入的宽高等参数可能是字符串数字
        # （'720'/'0.5'），原样写入会以 str 进入 ComfyUI prompt，触发节点类型
        # 校验失败/静默异常。control == "number" 一律按值形态转 int/float。
        if f.get("control") == "number" and isinstance(val, str):
            parsed = _coerce_number(val)
            if parsed is not None:
                val = parsed
        if node_id in api:
            api[node_id].setdefault("inputs", {})[field] = val

    pid = str(prompt_node_id or "")
    if not (prompt and pid and pid in api):
        # 兜底：模板未记录 prompt_node_id 时，用 exposed 里 binding=="prompt" 的字段
        target_field = next((f for f in exposed
                             if f.get("binding") == "prompt" or f.get("semantic") == "prompt"), None)
        if prompt and target_field and str(target_field.get("node_id") or "") in api:
            pid = str(target_field["node_id"])
    if prompt and pid and pid in api:
        inp = api[pid].setdefault("inputs", {})
        # 字段名大小写不敏感（原生 String 节点的输入键就是 "String"）：按 _TEXT_FIELDS 的
        # 优先级顺序取，找不到才退回「首个字符串字段」。改造前键名全小写时逐字等价。
        lowered = {str(k).lower(): k for k in inp}
        target = next((lowered[name] for name in _TEXT_FIELDS if name in lowered), None)
        if target is None:
            target = next((k for k, v in inp.items() if isinstance(v, str)), None)
        if target is not None:
            inp[target] = prompt

    return missing


def set_unique_output_prefix(api: dict, prefix: str) -> int:
    """覆写全部 SaveImage 节点的 filename_prefix 为唯一值。

    模板自带的前缀常含秒级 %date%——两个任务同秒完成时后者覆盖前者，
    导致「不同提示词、相同结果图」。prefix 需调用方保证唯一（如含 prompt_id）。
    返回覆写的节点数。
    """
    count = 0
    for node in api.values():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        if class_type.startswith("SaveImage") and "filename_prefix" in (node.get("inputs") or {}):
            node["inputs"]["filename_prefix"] = prefix
            count += 1
    return count
