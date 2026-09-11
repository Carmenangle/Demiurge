"""设置页模型测试：远程只做无计费目录探测，本地执行最小推理。"""
from __future__ import annotations

from pathlib import Path

import httpx

from app.services import ai_provider_service, rag_backend, reranker


_KIND_LABELS = {
    "chat": "对话模型",
    "image": "生图模型",
    "video": "视频模型",
    "embedding": "Embedding 模型",
    "vlm": "视觉大模型",
}


def _result(status: str, message: str, *, source: str = "") -> dict[str, object]:
    return {
        "status": status,
        "message": message,
        "source": source,
        "billable": False,
    }


def probe_remote(kind: str, base_url: str, api_key: str, model_name: str,
                 proxy: str = "") -> dict[str, object]:
    label = _KIND_LABELS.get(kind, "模型")
    if not (base_url or "").strip():
        return _result("error", "请先填写 API URL")
    if not (model_name or "").strip():
        return _result("error", "请先填写模型名称")

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    reachable: list[str] = []
    errors: list[str] = []
    client_kwargs = {"trust_env": False, "timeout": 12, "follow_redirects": True}
    if proxy:
        client_kwargs["proxy"] = proxy
    with httpx.Client(**client_kwargs) as client:
        for url in ai_provider_service.candidate_model_urls(base_url):
            try:
                response = client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                errors.append(str(exc))
                continue
            reachable.append(f"{url} ({response.status_code})")
            if response.status_code in (401, 403):
                return _result("error", f"{label}接口可达，但鉴权失败（HTTP {response.status_code}）", source=url)
            if not response.is_success:
                continue
            try:
                payload = response.json()
            except ValueError:
                return _result("warning", f"{label}接口可达，但模型目录没有返回 JSON，无法无费用确认模型", source=url)
            data = payload if isinstance(payload, list) else (
                payload.get("data", []) if isinstance(payload, dict) else []
            )
            names = [
                str(item if isinstance(item, str) else item.get("id", ""))
                for item in data if isinstance(item, (str, dict))
            ]
            names = [name for name in names if name]
            if model_name in names:
                return _result("success", f"连接与鉴权成功，模型目录包含 {model_name}", source=url)
            if names:
                return _result("warning", f"连接与鉴权成功，但模型目录未列出 {model_name}", source=url)
            return _result("warning", f"{label}接口可达，但模型目录为空，无法确认 {model_name}", source=url)

    if reachable:
        return _result(
            "warning",
            f"{label}服务可达，但供应商未提供可用的 /models 目录；为避免扣费，未调用生成或推理接口",
            source="；".join(reachable),
        )
    detail = errors[-1] if errors else "连接失败"
    return _result("error", f"无法连接{label}服务：{detail}")


def probe_local_embedding(model_dir: str) -> dict[str, object]:
    path = Path(model_dir).expanduser() if model_dir else None
    if path is None or not path.is_dir():
        return _result("error", f"本地嵌入模型目录不存在：{model_dir or '未填写'}")
    complete, missing = rag_backend.local_model_files_status(path)
    if not complete:
        return _result("error", "本地嵌入模型缺少文件：" + "、".join(missing))
    try:
        vector = rag_backend.embed_query(
            rag_backend.EmbedConfig(model_dir=str(path.resolve()), mode="local"),
            "本地模型测试",
        )
    except Exception as exc:  # noqa: BLE001
        return _result("error", f"本地嵌入模型加载或推理失败：{exc}")
    if not vector:
        return _result("error", "本地嵌入模型已加载，但没有返回向量")
    return _result("success", f"本地文件完整，最小推理成功（向量维度 {len(vector)}）")

def probe_local_reranker(model_dir: str) -> dict[str, object]:
    effective_dir = rag_backend.EmbedConfig(reranker_dir=model_dir).reranker_dir
    ok, message = reranker.probe_model(effective_dir)
    return _result("success" if ok else "error", message)


def probe_local_vlm(gguf_path: str) -> dict[str, object]:
    """本地视觉大模型：校验文件存在 + 可解析 + 具备视觉能力 + 硬件适配检测。

    不做推理（避免加载大模型），但会结合设备显存判断"能否可行"：
    显存足够 → ok；可部分卸载 → partial_offload；不足 → low / cpu_only。
    """
    from app.services import gguf_importer

    path = Path(gguf_path).expanduser() if gguf_path else None
    if path is None or not path.is_file():
        return _result("error", f"本地模型文件不存在：{gguf_path or '未填写'}")
    meta = gguf_importer.parse_gguf(path)
    if meta is None:
        return _result("error", f"无法解析模型元数据：{path.name}")
    if meta.kind != "model":
        return _result("error", f"{path.name} 不是主模型（kind={meta.kind}），请选择主模型文件")
    if not (meta.is_vision or meta.has_vision_encoder):
        return _result(
            "warning",
            f"{path.name} 未检测到视觉能力（架构 {meta.architecture}）；VLM 需要支持图片输入的模型",
        )
    # 硬件适配：显存够不够、能否导入 Ollama 运行
    fit = gguf_importer.fit_hardware(meta)
    level = fit.get("level", "unknown")
    if level == "ok":
        status = "success"
        hint = "，可在当前硬件直接运行"
    elif level == "partial_offload":
        status = "warning"
        hint = "，当前显存偏紧需部分卸载（可考虑 Q4_K_M 量化档）"
    elif level == "cpu_only":
        status = "warning"
        hint = "，无可用 GPU，仅 CPU 可运行（速度较慢）"
    else:  # low
        status = "error"
        hint = "，当前硬件无法运行，请选择更小参数量或更低量化档"
    return _result(
        status,
        f"模型有效：{meta.architecture} {meta.parameters_b}B {meta.quant}，具备视觉能力"
        + hint
        + f"；模型约需 {fit.get('total_needed_mib', 0) / 1024:.1f}GB 显存"
        + (f"（可用 {fit.get('device', {}).get('available_mib', 0) / 1024:.1f}GB）" if fit.get("device") else ""),
    )
