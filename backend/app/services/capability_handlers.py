"""能力薄适配器：只做参数透传与逐条失败隔离，不藏业务、不新增执行语义。

capability_registry 里的 handler 指向这里的函数或既有 services 函数；
P2 plan_tasks 执行器未来逐 operation 分发到它们（真源见 docs/ROADMAP-AUTOPILOT.md）。
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from app.services.workflow_submission import WorkflowSubmissionError, submit_template


def submit_batch(template_id: str, variants: list[dict[str, Any]], prompt: str = "",
                 url: str = "", client_id: str = "", lora_name: str = "",
                 loras: list[dict[str, Any]] | None = None,
                 lora_mode: str = "single") -> dict[str, Any]:
    """同模板多变体批量提交：每个变体一次 submit_template，单条失败隔离不中断整批。

    ComfyUI 自身 FIFO 排队；本函数只负责逐条入队并回带逐条结果。
    """
    results: list[dict[str, Any]] = []
    for index, values in enumerate(variants):
        if not isinstance(values, dict):
            results.append({"index": index, "ok": False, "detail": "变体值必须是对象"})
            continue
        values["template_id"] = _resolve_template_id(str(values.get("template_id") or template_id))
        # 顶层 lora_name 下发到缺省变体（用户指定 LoRA 而模型只在顶层写时）
        if lora_name and not values.get("lora_name"):
            values["lora_name"] = lora_name
        # 变体 LoRA 走 loras 参数链（values 注入会被 disable_all_loras 清除）
        var_loras = loras
        lora_ref = values.pop("lora_name", None)
        if lora_ref and not var_loras:
            hit = lora_resolve(str(lora_ref))
            if hit.get("matched"):
                weight = values.pop("strength_model", None) or hit.get("suggested_weight") or 0.9
                var_loras = [{"name": hit["file"], "weight": float(weight)}]
        _resolve_lora_in_values(values)
        # 变体级 prompt 覆盖：prompt/positive_prompt 优先于共享 prompt（逐套装不同提示词用）
        step_prompt = str(values.get("prompt") or values.get("positive_prompt")
                          or prompt or "").strip()
        if not step_prompt:
            results.append({"index": index, "ok": False,
                            "detail": "缺少 prompt（共享与变体级均未提供）"})
            continue
        try:
            outcome = submit_template(template_id, values, step_prompt, url, client_id,
                                      loras=var_loras, lora_mode=lora_mode)
            results.append({"index": index, "ok": True,
                            "prompt_id": outcome.get("prompt_id")})
        except WorkflowSubmissionError as exc:
            results.append({"index": index, "ok": False, "detail": str(exc.detail)})
    return {
        "submitted": sum(1 for item in results if item["ok"]),
        "failed": sum(1 for item in results if not item["ok"]),
        "results": results,
    }


def read_text_file(path: str, max_chars: int = 20000) -> dict[str, Any]:
    """受控只读文本文件（Autopilot file.read_text 能力的薄适配）。

    安全边界：仅 UTF-8 文本、字符数上限、拒绝二进制；目录列举/写操作不存在。
    越出作品域的读取由执行器在执行前走 capability_sandbox 租约授权（审批卡明示）。
    """
    from pathlib import Path as _Path

    target = _Path(path).expanduser()
    if not _Path(path).is_absolute():
        raise ValueError("仅接受绝对路径（相对路径不做隐式解析）")
    if not target.is_file():
        raise ValueError(f"文件不存在：{path}")
    data = target.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("仅支持 UTF-8 文本文件（拒绝二进制）") from exc
    if len(text) > max_chars:
        text = text[:max_chars]
    return {"path": str(target), "text": text, "truncated": len(text) >= max_chars}


def _embed_config_from_state() -> tuple[str, str, str]:
    """从 user_state（gitignored 运行态真源）读嵌入配置；密钥不经模型产出参数。"""
    from app.config import DATA_DIR

    try:
        st = json.loads((DATA_DIR / "user_state.json").read_text(encoding="utf-8"))
        embed = (st.get("settings") or {}).get("embedModel") or {}
        return (str(embed.get("baseUrl") or ""), str(embed.get("apiKey") or ""),
                str(embed.get("modelName") or ""))
    except (OSError, json.JSONDecodeError):
        return "", "", ""


def collect_comfy_outputs(prompt_ids: list[str] | None = None, comfyui_url: str = "",
                          output_dir: str = "", repo_id: str = "",
                          submit_result: dict[str, Any] | None = None,
                          names: list[str] | None = None,
                          prompts: list[str] | None = None,
                          timeout_seconds: int = 600) -> dict[str, Any]:
    """委派产物采集：轮询 ComfyUI 历史取图 → 落作品文件夹 → 注册进资产库（generation RAG）。

    每个取到的图在资产库里挂 prompt（提交时的提示词）+ tags「委派计划」，
    前端资产库/摘要卡按 local-view URL 展示。阻塞轮询在执行器心跳保护下安全。
    """
    import time as _time

    from app.services import comfyui_client, repo_meta, view_urls
    from app.services.rag_backend import EmbedConfig
    from app.services import rag_store

    embed_base, embed_key, embed_model = _embed_config_from_state()

    # prompt_ids 可由 inputs_from 链接的 submit 产出推导；submit_result 兼容
    # submit_batch（results 数组）/ submit_template（顶层 prompt_id）/ 其列表三种形态
    # submit_result 链接值优先（编译期写入的 prompt_ids 可能是占位符）
    ids: list[str] = []
    if submit_result:
        items = submit_result if isinstance(submit_result, list) else [submit_result]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("prompt_id"):
                ids.append(str(item["prompt_id"]))
            for r in item.get("results") or []:
                if r.get("ok") and r.get("prompt_id"):
                    ids.append(str(r["prompt_id"]))
    if not ids:
        ids = [str(x) for x in (prompt_ids or [])]
    if not ids:
        raise ValueError("collect 缺少 prompt_ids（或 inputs_from 提供的 submit_result）")
    deadline = _time.time() + max(30, timeout_seconds)
    results: list[dict[str, Any]] = []
    prompt_ids = ids
    for index, prompt_id in enumerate(prompt_ids):
        label = (names[index] if names and index < len(names) and str(names[index]).strip()
                 else f"output-{index + 1}")
        status = ""
        while _time.time() < deadline:
            result = comfyui_client.fetch_result(comfyui_url, prompt_id)
            status = str(result.get("status"))
            if status in ("done", "completed"):
                break
            if status == "not_found":
                raise RuntimeError(f"任务 {prompt_id[:8]} 在 ComfyUI 中丢失（可能已重启）")
            _time.sleep(2.0)
        if status not in ("done", "completed"):
            raise RuntimeError(f"等待任务 {prompt_id[:8]} 出图超时（{timeout_seconds}s）")
        images = result.get("images") or []
        if not images:
            results.append({"prompt_id": prompt_id, "label": label, "ok": False,
                            "detail": "任务完成但没有图片产物"})
            continue
        first = images[0]
        data, _content_type = comfyui_client.fetch_view(
            comfyui_url, first["filename"], type=first.get("type", "output"),
            subfolder=first.get("subfolder", ""))
        base = repo_meta.repo_folder(output_dir, repo_id)
        dest = base / f"{len(results) + 1:02d}-{uuid.uuid4().hex[:8]}.png"
        dest.write_bytes(data)
        shown = view_urls.local_view(str(dest))
        try:
            rag_store.index_generation(
                repo_id, EmbedConfig(embed_base, embed_key, embed_model),
                prompt=(prompts[index] if prompts and index < len(prompts) else label),
                tags="委派计划", image_url=shown, media_type="image")
        except Exception as exc:  # noqa: BLE001 - 入库失败不丢文件
            results.append({"prompt_id": prompt_id, "label": label, "ok": True,
                            "file": str(dest), "url": shown,
                            "rag_indexed": False, "detail": str(exc)})
            continue
        results.append({"prompt_id": prompt_id, "label": label, "ok": True,
                        "file": str(dest), "url": shown, "rag_indexed": True})
    return {"collected": sum(1 for r in results if r.get("ok")), "results": results}


def _resolve_template_id(template_id: str) -> str:
    """template_id 查不到时按名称包含匹配归一（容忍「模板」后缀等修饰）。"""
    from app.services import template_store
    if template_store.get_template(template_id) is not None:
        return template_id
    cleaned = template_id.replace("模板", "").strip()
    for t in template_store.list_templates():
        if (t["id"] == template_id or t["id"].startswith(template_id)
                or t.get("name") == cleaned or cleaned in t.get("name", "")):
            return t["id"]
    return template_id


def _resolve_lora_in_values(values: dict) -> None:
    """values["lora_name"] 近似名归一为真实文件（精确名原样保留；strength 缺省补建议权重）。"""
    name = values.get("lora_name")
    if not isinstance(name, str) or not name.strip():
        return
    hit = lora_resolve(name)
    if hit.get("matched") and hit["file"] != name:
        values["lora_name"] = hit["file"]
        if hit.get("suggested_weight") is not None and "strength_model" not in values:
            values["strength_model"] = hit["suggested_weight"]


def lora_resolve(query: str) -> dict[str, Any]:
    """模糊解析 LoRA：名称/触发词 → 真实文件（ComfyUI 本机枚举 + lora_index 元数据）。

    匹配序：精确文件名 → 去扩展名精确 → 触发词命中 → 子串（双向，最长命中优先）。
    返回 {file, matched_by, trigger_words, suggested_weight}；匹配不到返回 candidates 摘要。
    """
    from app.services import comfyui_client, lora_index

    query_clean = (query or "").strip()
    if not query_clean:
        raise ValueError("缺少 LoRA 查询词")
    query_lower = query_clean.lower()
    installed: list[str] = []
    try:
        from app.services import comfy_launcher
        info = comfyui_client.fetch_object_info(comfy_launcher.load_config()["url"])
        installed = list(info.get("LoraLoader", {}).get("input", {})
                         .get("required", {}).get("lora_name", [])[0])
    except Exception:  # noqa: BLE001 - ComfyUI 离线时退回 lora_index 元数据
        installed = []
    meta = {item["lora_name"]: item for item in lora_index.list_items()}
    catalog = sorted(set(installed) | set(meta.keys()))
    if query_lower in {c.lower() for c in catalog}:
        file = next(c for c in catalog if c.lower() == query_lower)
        return _lora_hit(file, "exact", meta)
    stems = {c.rsplit(".", 1)[0].lower(): c for c in catalog}
    if query_lower in stems:
        return _lora_hit(stems[query_lower], "exact_name", meta)
    for c in catalog:
        item = meta.get(c) or {}
        if any(query_lower == t.lower() for t in item.get("triggers", [])):
            return _lora_hit(c, "trigger", meta)
    # token 级模糊：查询词与文件名/触发词按 token 交叉命中（「QRQ 风格」→ krea2_QRQ_韩漫风）
    import re as _re
    tokens = [t for t in _re.split(r"[\s_\-,.]+", query_lower) if len(t) >= 2]
    scored: list[tuple[int, int, str]] = []
    for c in catalog:
        stem = c.lower().rsplit(".", 1)[0]
        triggers = [t.lower() for t in (meta.get(c) or {}).get("triggers", [])]
        hits = sum(1 for t in tokens
                   if any(t in target for target in [stem, *triggers]))
        if hits:
            scored.append((hits, len(stem), c))
    if scored:
        scored.sort(key=lambda x: (-x[0], -x[1]))
        top_score = scored[0][0]
        ties = [c for h, _l, c in scored if h == top_score]
        if len(ties) > 1:  # 歧义：列出候选让用户/agent选择，不猜
            return {"matched": False, "query": query_clean, "reason": "ambiguous",
                    "candidates": [_lora_hit(c, "fuzzy_token", meta) for c in ties[:8]]}
        return _lora_hit(ties[0], "fuzzy_token", meta)
    contains_hits = sorted(
        (c for c in catalog
         if query_lower in c.lower() or c.lower().rsplit(".", 1)[0] in query_lower),
        key=lambda c: -len(c))
    if contains_hits:
        return _lora_hit(contains_hits[0], "substring", meta)
    return {"matched": False, "query": query_clean,
            "candidates": catalog[:20]}


def _lora_hit(file: str, matched_by: str, meta: dict) -> dict[str, Any]:
    item = meta.get(file) or {}
    return {"matched": True, "file": file, "matched_by": matched_by,
            "trigger_words": item.get("triggers", []),
            "suggested_weight": item.get("suggested_weight")}


def lora_list() -> dict[str, Any]:
    """本机 LoRA 全目录（文件名+触发词+建议权重+备注），供 agent 列给用户选择。"""
    from app.services import comfyui_client, lora_index

    meta = {item["lora_name"]: item for item in lora_index.list_items()}
    try:
        from app.services import comfy_launcher
        info = comfyui_client.fetch_object_info(comfy_launcher.load_config()["url"])
        installed = list(info.get("LoraLoader", {}).get("input", {})
                         .get("required", {}).get("lora_name", [])[0])
    except Exception:  # noqa: BLE001 - ComfyUI 离线退回元数据
        installed = sorted(meta.keys())
    return {"count": len(installed), "loras": [
        {"file": name, "triggers": (meta.get(name) or {}).get("triggers", []),
         "suggested_weight": (meta.get(name) or {}).get("suggested_weight"),
         "note": (meta.get(name) or {}).get("note", "")}
        for name in sorted(installed)]}
