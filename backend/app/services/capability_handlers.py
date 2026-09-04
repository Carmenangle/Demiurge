"""能力薄适配器：只做参数透传与逐条失败隔离，不藏业务、不新增执行语义。

capability_registry 里的 handler 指向这里的函数或既有 services 函数；
P2 plan_tasks 执行器未来逐 operation 分发到它们（真源见 docs/ROADMAP-AUTOPILOT.md）。
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from app.services.workflow_submission import WorkflowSubmissionError, submit_template


def list_templates() -> dict[str, Any]:
    """列出全部工作流模板（dict 形态，产出键 templates，供 inputs_from 点引用）。"""
    from app.services import template_store
    return {"templates": template_store.list_templates()}


def read_exposed_fields(template_id: str) -> dict[str, Any]:
    """读取单个模板的 exposed 字段定义；模板不存在时抛错，不静默返回 null。"""
    from app.services import template_store
    template = template_store.get_template(template_id)
    if template is None:
        raise ValueError(f"模板不存在：{template_id}")
    return {"template": template}


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
        template_id = _resolve_template_id(str(values.get("template_id") or template_id))
        values["template_id"] = template_id
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
        unresolved = _resolve_lora_in_values(values)
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
            if unresolved:
                outcome["lora_unresolved"] = unresolved
            results.append({"index": index, "ok": True,
                            "prompt_id": outcome.get("prompt_id"),
                            "prompt": step_prompt})
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


def _ordered_submit_items(submit_result: dict[str, Any] | list[Any] | None) -> list[dict[str, Any]]:
    """把 submit 产物摊平成「与提交顺序一致」的逐条结果（供 collect 按位取回提示词）。

    submit_batch 返回整包 {"submitted":…, "results":[{index, ok, prompt_id, prompt}, …]}：
    每条实际使用的提示词在 results[i]，不在整包顶层；submit_template 单条/列表形态原样摊平。
    """
    if isinstance(submit_result, dict):
        inner = submit_result.get("results")
        if isinstance(inner, list):
            return [item for item in inner if isinstance(item, dict)]
        return [submit_result]
    if isinstance(submit_result, list):
        flat: list[dict[str, Any]] = []
        for item in submit_result:
            if not isinstance(item, dict):
                continue
            inner = item.get("results")
            if isinstance(inner, list):
                flat.extend(sub for sub in inner if isinstance(sub, dict))
            else:
                flat.append(item)
        return flat
    return []


def collect_comfy_outputs(prompt_ids: list[str] | None = None, comfyui_url: str = "",
                          output_dir: str = "", repo_id: str = "",
                          submit_result: dict[str, Any] | None = None,
                          names: list[str] | None = None,
                          prompts: list[str] | None = None,
                          timeout_seconds: int = 600) -> dict[str, Any]:
    """智能编造产物采集：轮询 ComfyUI 历史取图 → 落作品文件夹 → 注册进资产库（generation RAG）。

    每个取到的图在资产库里挂 prompt（提交时的提示词）+ tags「智能编造计划」，
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
    # 总超时 = max(调用方给的 timeout_seconds, 每张 300s × 张数)——ComfyUI 串行
    # 出图每张可能 2-4 分钟，14 张共享 600s 必然超时（2026-09-02 实锤）。
    deadline = _time.time() + max(30, timeout_seconds, len(ids) * 300)
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
                # 单个任务丢失只隔离该条，不中止整批采集（其余图照常入库）
                results.append({"prompt_id": prompt_id, "label": label, "ok": False,
                                "detail": "任务在 ComfyUI 中丢失（可能已重启）"})
                continue
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
        # 图片消息内容 = 套装名称 + 完整提示词（优先 prompts 参数，其次 submit_result
        # 里该变体实际使用的 prompt，最后回退套装名）。
        _prompt_text = str(prompts[index] if prompts and index < len(prompts) else "")
        if not _prompt_text and submit_result:
            # submit_batch 整包形态：prompt 在 results[i]（顶层只有 submitted/failed），
            # 直接 [submit_result][index].get("prompt") 会取到整包/空 dict → 提示词丢失，
            # 消息只剩套装名（2026-09-04 唐柚 14 套画像实锤）。先摊平成逐条结果再按位取。
            _items = _ordered_submit_items(submit_result)
            _item = _items[index] if index < len(_items) and isinstance(_items[index], dict) else {}
            _prompt_text = str(_item.get("prompt") or "")
        if not _prompt_text:
            _prompt_text = str(label)
        _message_text = f"{label}\n\n{_prompt_text}" if _prompt_text != label else label
        # 每张图作为独立消息写进对话快照（图片+名称+提示词，像 /w 工作流结果一样），
        # 刷新对话即可逐张看到；thread_id = repo_id（计划所属会话）。
        try:
            from app.services import chat_snapshot as _cs
            # meta.kind=plan_collect：批量采集副产品，不占每角色历史条数
            _cs.upsert(repo_id, _cs.assistant_message(
                str(uuid.uuid4()), _message_text, image=shown,
                meta={"kind": "plan_collect"}))
        except Exception:  # noqa: BLE001 - 快照追加失败不影响采集主流程
            pass
        try:
            rag_store.index_generation(
                repo_id, EmbedConfig(embed_base, embed_key, embed_model),
                prompt=_prompt_text,
                tags="智能编造计划", image_url=shown, media_type="image")
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


def _resolve_lora_in_values(values: dict) -> str | None:
    """values["lora_name"] 近似名归一为真实文件（精确名原样保留；strength 缺省补建议权重）。

    未匹配时返回上报提示（submit 结果附 lora_unresolved），不静默丢弃。
    """
    name = values.get("lora_name")
    if not isinstance(name, str) or not name.strip():
        return None
    hit = lora_resolve(name)
    if hit.get("matched") and hit["file"] != name:
        values["lora_name"] = hit["file"]
        if hit.get("suggested_weight") is not None and "strength_model" not in values:
            values["strength_model"] = hit["suggested_weight"]
        return None
    if not hit.get("matched"):
        return f"LoRA「{name}」未能匹配本机文件，本次提交未挂该 LoRA"
    return None


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

# ── 智能编造 Agent 通用创作能力（P3，全作品域内落盘）────────────────────────
# 安全边界：写入一律用「base=作品目录」由 submit_task 环境归一注入，不接受模型给
# 任意 base/绝对路径；路径域由 plan_validator（写类绝对路径）与执行期租约兜底。

def write_text_file(path: str, content: str, overwrite: bool = False) -> dict[str, Any]:
    """写 UTF-8 文本文件（写类 durable，路径域与租约由计划链路强制）。"""
    from pathlib import Path as _Path

    if not _Path(path).is_absolute():
        raise ValueError("仅接受绝对路径（相对路径不做隐式解析）")
    target = _Path(path).expanduser()
    if target.is_dir():
        raise ValueError("目标路径是目录，拒绝写入")
    if target.is_file() and not overwrite:
        raise ValueError(f"目标文件已存在，未授权覆盖：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content or "", encoding="utf-8")
    return {"path": str(target), "bytes": len((content or "").encode("utf-8"))}


def list_dir(path: str, max_entries: int = 200) -> dict[str, Any]:
    """列目录（readonly）：只返回名称/类型/大小，不返回文件内容。"""
    from pathlib import Path as _Path

    if not _Path(path).is_absolute():
        raise ValueError("仅接受绝对路径（相对路径不做隐式解析）")
    target = _Path(path).expanduser()
    if not target.is_dir():
        raise ValueError(f"目录不存在：{path}")
    entries = []
    for item in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        try:
            st = item.stat()
            entries.append({"name": item.name, "type": "dir" if item.is_dir() else "file",
                            "size": st.st_size if item.is_file() else None})
        except OSError:
            continue
        if len(entries) >= max(1, min(max_entries, 500)):
            break
    return {"path": str(target), "count": len(entries), "entries": entries}


def _normalize_worldbook_patch(patch: dict[str, Any],
                               warnings: list[str]) -> dict[str, Any] | None:
    """把模型给的条目归一成世界书 5 字段（content/comment/keys/constant/enabled）。

    - `key`/`name` 等单数写法 → keys 单元素数组；keys 为 str → [str]；
    - 白名单外的杂字段（key/name/title/index…）一律丢弃，避免脏字段进快照；
    - 缺 comment/keys 记 warning（不阻断写入，但调用方可见待补）。
    返回 None 表示没有任何有效内容字段，条目被跳过。
    """
    entry: dict[str, Any] = {}
    for field in ("content", "comment", "constant", "enabled"):
        if field in patch:
            entry[field] = patch[field]
    raw_keys = patch.get("keys")
    if raw_keys is None:
        alt = patch.get("key") if patch.get("key") not in (None, "") else patch.get("name")
        raw_keys = [alt] if isinstance(alt, str) else (list(alt) if isinstance(alt, list) else None)
    if isinstance(raw_keys, str):
        raw_keys = [raw_keys]
    if raw_keys:
        entry["keys"] = [str(k) for k in raw_keys if str(k).strip()]
    content = str(entry.get("content") or "").strip()
    if not content and not entry.get("keys"):
        return None
    if not entry.get("keys"):
        warnings.append("条目 content 缺 keys——模型写了不存在的单数字段？归一后仍为空")
    if not str(entry.get("comment") or "").strip():
        warnings.append(f"条目 {str(entry.get('keys') or ['?'])[:20]} 缺 comment"
                        "（视觉画像需『角色卡·<名>』前缀，keys 决定命中）")
    return entry


def _entry_match_keys(item: dict[str, Any]) -> list[str]:
    """存量条目匹配用 keys（兼容历史脏条目用单数 key/name 写入的情况）。"""
    raw = item.get("keys")
    if raw is None:
        alt = item.get("key") if item.get("key") not in (None, "") else item.get("name")
        raw = alt
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(k) for k in raw if str(k).strip()]
    return []


def upsert_repo_worldbook(base: str, repo_id: str,
                          entries: list[dict[str, Any]]) -> dict[str, Any]:
    """向作品世界书快照 upsert 条目（durable）。快照不存在则创建骨架。"""
    from app.services import worldbook_edit, worldbook_store

    if not (base and repo_id):
        raise ValueError("作品目录与 repo_id 由执行环境归一注入，不接受空值")
    book = worldbook_store.read_repo_snapshot(base, repo_id)
    if book is None:
        book = {"entries": []}
        snap = worldbook_store.repo_snapshot_path(base, repo_id)
        snap.parent.mkdir(parents=True, exist_ok=True)
    applied = 0
    skipped = 0
    warnings: list[str] = []
    indexed = {str(item.get("index")): item for item in worldbook_edit.list_entries(book)}
    for patch in entries or []:
        if not isinstance(patch, dict):
            skipped += 1
            continue
        norm = _normalize_worldbook_patch(patch, warnings)
        if norm is None:
            skipped += 1
            continue
        keys = norm.get("keys") or []
        comment = str(norm.get("comment") or "").strip()
        hit = next((item for item in indexed.values()
                    if ((comment and str(item.get("comment") or "").strip() == comment)
                        or (keys and any(
                            k in _entry_match_keys(item) for k in keys)))), None)
        if hit is not None:
            if worldbook_edit.update_entry(book, int(hit["index"]), norm):
                applied += 1
        else:
            worldbook_edit.add_entry(book, norm)
            applied += 1
    if applied:
        worldbook_store.save_repo_snapshot(base, repo_id, book)
    return {"repo_id": repo_id, "applied": applied, "skipped": skipped,
            "warnings": warnings}


def upsert_repo_character(base: str, card: dict[str, Any]) -> dict[str, Any]:
    """把 JSON 角色卡归一后写入作品目录（<base>/<卡名>/card.json，durable）。"""
    from app.services import character_card, character_store

    if not base:
        raise ValueError("作品目录由执行环境归一注入，不接受空值")
    if not isinstance(card, dict):
        raise ValueError("card 必须是角色卡 JSON 对象")
    normalized = character_card.normalize_card(card)
    summary = character_store.save_card(base, normalized, overwrite=True)
    return dict(vars(summary))


def create_repo_doc(base: str, rel_path: str, content: str,
                    overwrite: bool = False) -> dict[str, Any]:
    """在作品目录 docs/ 下创建 Markdown 文档（durable，拒绝越界路径）。"""
    from pathlib import Path as _Path

    if not base:
        raise ValueError("作品目录由执行环境归一注入，不接受空值")
    raw = (rel_path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise ValueError("rel_path 必须是相对作品目录的路径")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise ValueError("rel_path 不合法（拒绝 .. 穿越）")
    if not raw.lower().endswith(".md"):
        raise ValueError("仅支持创建 .md 文档")
    root = _Path(base).expanduser().resolve() / "docs"
    target = (root / _Path(*parts)).resolve()
    if not target.is_relative_to(root):
        raise ValueError("文档路径越出作品 docs/ 目录")
    if target.exists() and not overwrite:
        raise FileExistsError(target.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content or "", encoding="utf-8")
    return {"path": str(target), "bytes": len((content or "").encode("utf-8"))}


def edit_text_file(path: str, old_str: str, new_str: str,
                   replace_all: bool = False) -> dict[str, Any]:
    """按 str_replace 语义修改 UTF-8 文本文件（改代码/配置用）。"""
    from pathlib import Path as _Path

    if not _Path(path).is_absolute():
        raise ValueError("仅接受绝对路径")
    target = _Path(path).expanduser()
    if target.is_dir():
        raise ValueError("目标路径是目录，拒绝编辑")
    if not target.is_file():
        raise ValueError(f"文件不存在：{path}")
    text = target.read_text(encoding="utf-8")
    count = text.count(old_str)
    if count == 0:
        raise ValueError("old_str 在文件中不存在，请先读取文件确认内容")
    if count > 1 and not replace_all:
        raise ValueError(f"old_str 命中 {count} 处，请提供更长的上下文使其唯一，或 replace_all=true")
    updated = text.replace(old_str, new_str) if replace_all else text.replace(old_str, new_str, 1)
    target.write_text(updated, encoding="utf-8")
    return {"path": str(target), "replaced": count if replace_all else 1,
            "chars_before": len(text), "chars_after": len(updated)}


def run_shell(command: str, cwd: str = "", timeout_seconds: int = 60) -> dict[str, Any]:
    """在指定工作目录执行一条命令行（durable，审批/租约强制）。"""
    import subprocess
    from pathlib import Path as _Path

    if not (cwd or "").strip():
        raise ValueError("cwd 必须显式指定为绝对路径（工作区/作品目录）")
    workdir = _Path(cwd).expanduser()
    if not workdir.is_absolute() or not workdir.is_dir():
        raise ValueError(f"cwd 不是有效目录：{cwd}")
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(workdir), timeout=max(1, min(timeout_seconds, 300)),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        return {"exit_code": -1, "timed_out": True,
                "stdout": (exc.stdout or "")[:4000], "stderr": (exc.stderr or "")[:4000]}
    return {"exit_code": proc.returncode, "timed_out": False,
            "stdout": (proc.stdout or "")[:8000], "stderr": (proc.stderr or "")[:8000]}

def instantiate_recipe(recipe_id: str, output_dir: str = "", repo_id: str = "",
                       param_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """固化流程预设重放（durable）：整条配方作为新计划投执行队列。

    output_dir 必须等于配置真源（repo_meta），模型/客户端不得指定任意目录；
    重放计划的 durable/expensive 步骤照常走 plan_tasks 审批/配额闸门。
    """
    from app.services import plan_tasks as _plan_tasks, repo_meta as _repo_meta

    truth = _repo_meta.output_dir_from_state()
    if not truth or output_dir != truth:
        raise ValueError("output_dir 必须是当前配置的仓库根目录（环境归一注入，不接受外部指定）")
    return _plan_tasks.instantiate_recipe(
        recipe_id, output_dir=truth, repo_id=repo_id,
        param_overrides=param_overrides if isinstance(param_overrides, dict) else None)


def _dir_from_state(key: str) -> str:
    """从 user_state settings 读目录配置（characterDir/worldbookDir 等运行态真源）。"""
    from app.config import DATA_DIR

    try:
        st = json.loads((DATA_DIR / "user_state.json").read_text(encoding="utf-8"))
        return str((st.get("settings") or {}).get(key) or "")
    except (OSError, json.JSONDecodeError):
        return ""


def import_source_card(path: str, overwrite: bool = False,
                       extract_worldbook: bool = False) -> dict[str, Any]:
    """导入一张 ST/通用角色卡（PNG tEXt 内嵌或 JSON）到角色卡源库（durable）。

    源库目录 = 后端配置 characterDir（运行态真源，不经模型参数）。ST 卡原生兼容
    （TavernCard V1/V2/V3）；PNG 是二进制，file.read_text 读不了，由本 handler 读字节。
    extract_worldbook=True 时把内嵌世界书外拆到配置的 worldbookDir 并从卡剥离。
    """
    from pathlib import Path as _Path

    from app.services import character_card, character_store

    raw_path = str(path or "").strip().strip('"')
    if not raw_path:
        raise ValueError("path 必须是卡文件（PNG/JSON）的绝对路径")
    target = _Path(raw_path).expanduser()
    if not target.is_file():
        raise ValueError(f"卡文件不存在：{raw_path}")
    base = _dir_from_state("characterDir")
    if not base:
        raise ValueError("请先在设置中配置角色卡文件夹（characterDir）")
    raw = target.read_bytes()
    try:
        card = character_card.parse_card_bytes(raw, target.name)
    except character_card.CardParseError as exc:
        raise ValueError(f"卡解析失败：{exc}") from exc
    is_png = raw.startswith(character_card.PNG_SIGNATURE) or target.suffix.lower() == ".png"
    try:
        character_store.save_card(base, card, avatar=raw if is_png else None,
                                  overwrite=overwrite)
    except FileExistsError as exc:
        raise ValueError(f"源库已存在同名卡「{exc}」；确认后用 overwrite=true 重导") from exc
    worldbook_extracted = False
    if extract_worldbook:
        wb_dir = _dir_from_state("worldbookDir")
        if wb_dir:
            character_store.extract_embedded_worldbook(base, card.name, wb_dir)
            worldbook_extracted = True
    return {"name": card.name, "card_dir": str(_Path(base) / card.name),
            "avatar_saved": bool(is_png), "worldbook_extracted": worldbook_extracted}


def migrate_scan_source(path: str) -> dict[str, Any]:
    """只读扫描一张 ST/通用卡或独立世界书/预设/正则，产出迁移体检报告（readonly）。

    第二套固定流程（机械+LLM 转写）的机械前置：解析入料 → 剥离不可用字段的检测 →
    逐条目标注待转写点（注入位语义/constant 越权/keys 质量/渲染层/运行时表格/
    容器/首条非空/视觉锚点前缀），供 LLM 按规范 §4.5 判断转写。
    不写任何文件、不改任何目录；落盘由转写产物经既有能力完成。
    """
    from app.services import st_migration

    try:
        return st_migration.analyze_source(str(path))
    except st_migration.MigrationScanError as exc:
        raise ValueError(str(exc)) from exc


# ── 固化02 脚本辅助层（novel.*）：小说预处理机械工具薄适配 ──────────────────
# 逻辑真源 backend/app/services/novel_tools.py；本组只做参数归一与错误转换。

def novel_extract_epub(src: str, out_txt: str | None = None,
                       work_dir: str | None = None,
                       book_name: str | None = None) -> dict[str, Any]:
    """抽取 epub 全文为分章文本落盘（固化02 脚本辅助层 T1，reversible）。

    epub 源可在作品外（只读）；输出路径二选一：
    - `out_txt` 显式给全路径（必须落在作品域/临时工作区内，由执行环境归一注入）；
    - 或给 `work_dir`（作品根）：自动落 `<work_dir>/_prep/<书名>.full.txt`，
      `<书名>` 缺省取 epub 文件名（去 .epub 扩展）。
    两者都不给 → ValueError（不让 Agent 手拼 _prep/ 相对路径）。
    产出用「===== <章节> =====」标记，供 novel.survey/charfacts 复用；抽取后
    禁止再整本读全文。
    """
    from pathlib import Path

    from app.services import novel_tools

    if not out_txt:
        if not work_dir:
            raise ValueError("out_txt 与 work_dir 至少要给一个（给 work_dir 自动落 _prep/<书名>.full.txt）")
        book = (book_name or Path(str(src)).stem).strip()
        out_txt = str(Path(str(work_dir)) / "_prep" / f"{book}.full.txt")
    try:
        return novel_tools.extract_epub(str(src), str(out_txt))
    except novel_tools.NovelToolError as exc:
        raise ValueError(str(exc)) from exc


def novel_survey(full_txt: str, top_names: int = 60) -> dict[str, Any]:
    """只读清点分章全文：章节标题 / 称呼后缀候选名词频 / 红线词计数（readonly）。

    产物是候选名单与章节锚点，供 Agent 与用户确认转写范围；不写任何文件。
    """
    from app.services import novel_tools

    try:
        return novel_tools.survey_fulltext(str(full_txt), top_names=int(top_names or 60))
    except novel_tools.NovelToolError as exc:
        raise ValueError(str(exc)) from exc


def novel_charfacts(full_txt: str, names: list[str],
                    out_dir: str | None = None,
                    work_dir: str | None = None,
                    mode: str = "top_n", max_paras: int = 40) -> dict[str, Any]:
    """按名单从全文切素材段，逐名落 <out_dir>/<name>.txt（固化02 脚本辅助层 T3）。

    mode: top_n = 全书前 N 段完整段落；anchor = 首·中·末 320 字锚点窗口。
    输出目录二选一：`out_dir` 显式给（须在作品域内）；或给 `work_dir`（作品根）
    自动落 `<work_dir>/_prep/charfacts/`——两者都不给 → ValueError。
    素材是中间产物不是条目；模型只读素材文件后经 worldbook.upsert_repo 写条目。
    """
    from pathlib import Path

    from app.services import novel_tools

    if not isinstance(names, list) or not names:
        raise ValueError("names 必须是候选名单（非空 list），先跑 novel.survey 拿词频再人工筛")
    if not out_dir:
        if not work_dir:
            raise ValueError("out_dir 与 work_dir 至少要给一个（给 work_dir 自动落 _prep/charfacts/）")
        out_dir = str(Path(str(work_dir)) / "_prep" / "charfacts")
    try:
        return novel_tools.charfacts(str(full_txt), names, str(out_dir),
                                     mode=str(mode or "top_n"),
                                     max_paras=int(max_paras or 40))
    except novel_tools.NovelToolError as exc:
        raise ValueError(str(exc)) from exc


def novel_scan_anonymity(entries: list[dict[str, Any]] | None = None,
                         protagonist_names: list[str] | None = None,
                         repo_id: str = "", base: str = "") -> dict[str, Any]:
    """落盘匿名/红线机械扫描（固化02 §3.6，readonly）：主角名/单花括号/硬禁词。

    entries = 世界书快照条目或角色卡条目 list；protagonist_names 需含姓/名/爱称/
    带后缀形式（如 ["沈栖","栖栖"]），由 LLM 从原作提取给出。passed=False 阻断交付，
    语义判断（台词爱称第二遍）仍由 LLM 兜底。

    取数约定（2026-09-04）：entries 给显式 list 优先；不给时若给了 repo_id/base
    （base 由执行环境归一注入作品根），机械读作品世界书快照取 entries —— 这样
    approval 计划里只需写 protagonist_names，执行期再读快照，闸门步骤编得进计划。
    """
    from app.services import novel_tools, worldbook_store

    if protagonist_names is None:
        protagonist_names = []
    if not isinstance(protagonist_names, list) or not protagonist_names:
        raise ValueError("protagonist_names 必须是主角名清单（含爱称粒度）")
    if entries is None or not isinstance(entries, list):
        entries = None
    if entries is None:
        if not (base and repo_id):
            raise ValueError("未给 entries，且缺 repo_id/base 无法读作品世界书快照——"
                             "显式传 entries，或给 repo_id（base 由环境注入）自动读快照")
        snap = worldbook_store.read_repo_snapshot(str(base), str(repo_id)) or {}
        entries = list(snap.get("entries") or [])
        if not entries:
            raise ValueError(f"作品世界书快照（{repo_id}）里没有条目可扫描")
    if not isinstance(entries, list):
        raise ValueError("entries 必须是条目 list")
    try:
        return novel_tools.scan_anonymity(entries, protagonist_names)
    except novel_tools.NovelToolError as exc:
        raise ValueError(str(exc)) from exc


def knowledge_load_doc(name: str) -> dict[str, Any]:
    """按名拉取固化技能/知识全文（readonly）。

    固化技能（frontmatter 带 skill）按触发场景按需装载：命中 whenToUse 时调用本
    能力拉全文照执行（目录注入只给一行触发描述）；无 frontmatter 的普通知识文档
    由注入常驻，无需调用。
    """
    from app.services import agent_knowledge

    raw = str(name or "").strip()
    if not raw:
        raise ValueError("name 必须是知识文档名（如「固化02-小说转合集卡规范」）")
    try:
        return agent_knowledge.read_doc(raw)
    except FileNotFoundError as exc:
        raise ValueError(f"知识文档不存在：{raw}") from exc
