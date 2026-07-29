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
}


def _result(status: str, message: str, *, source: str = "") -> dict[str, object]:
    return {
        "status": status,
        "message": message,
        "source": source,
        "billable": False,
    }


def probe_remote(kind: str, base_url: str, api_key: str, model_name: str) -> dict[str, object]:
    label = _KIND_LABELS.get(kind, "模型")
    if not (base_url or "").strip():
        return _result("error", "请先填写 API URL")
    if not (model_name or "").strip():
        return _result("error", "请先填写模型名称")

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    reachable: list[str] = []
    errors: list[str] = []
    with httpx.Client(trust_env=False, timeout=12, follow_redirects=True) as client:
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
