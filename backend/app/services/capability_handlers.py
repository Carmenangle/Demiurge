"""能力薄适配器：只做参数透传与逐条失败隔离，不藏业务、不新增执行语义。

capability_registry 里的 handler 指向这里的函数或既有 services 函数；
P2 plan_tasks 执行器未来逐 operation 分发到它们（真源见 docs/ROADMAP-AUTOPILOT.md）。
"""
from __future__ import annotations

import uuid
from typing import Any

from app.services.workflow_submission import WorkflowSubmissionError, submit_template


def submit_batch(template_id: str, variants: list[dict[str, Any]], prompt: str,
                 url: str, client_id: str = "",
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
        try:
            outcome = submit_template(template_id, values, prompt, url, client_id,
                                      loras=loras, lora_mode=lora_mode)
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


def collect_comfy_outputs(prompt_ids: list[str], comfyui_url: str, output_dir: str,
                          repo_id: str, names: list[str] | None = None,
                          prompts: list[str] | None = None,
                          timeout_seconds: int = 600,
                          embed_base: str = "", embed_key: str = "",
                          embed_model: str = "") -> dict[str, Any]:
    """委派产物采集：轮询 ComfyUI 历史取图 → 落作品文件夹 → 注册进资产库（generation RAG）。

    每个取到的图在资产库里挂 prompt（提交时的提示词）+ tags「委派计划」，
    前端资产库/摘要卡按 local-view URL 展示。阻塞轮询在执行器心跳保护下安全。
    """
    import time as _time

    from app.services import comfyui_client, repo_meta, view_urls
    from app.services.rag_backend import EmbedConfig
    from app.services import rag_store

    deadline = _time.time() + max(30, timeout_seconds)
    results: list[dict[str, Any]] = []
    for index, prompt_id in enumerate(prompt_ids):
        label = (names[index] if names and index < len(names) and str(names[index]).strip()
                 else f"output-{index + 1}")
        status = ""
        while _time.time() < deadline:
            result = comfyui_client.fetch_result(comfyui_url, prompt_id)
            status = str(result.get("status"))
            if status == "done":
                break
            if status == "not_found":
                raise RuntimeError(f"任务 {prompt_id[:8]} 在 ComfyUI 中丢失（可能已重启）")
            _time.sleep(2.0)
        if status != "done":
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
