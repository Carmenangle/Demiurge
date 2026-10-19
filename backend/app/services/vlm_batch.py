"""批量视觉描述（VLM）后台任务（2026-10-18）。

为什么搬进后端：原先「自动描述缺失资产」在浏览器里循环跑——
  ① 只覆盖「当前画廊已加载的条目」，全库 318 张要靠用户手动翻页触发；
  ② 切页/刷新/关标签页即断，已完成的不丢但剩下的全没；
  ③ 本地 8B VLM 单张数秒，318 张要盯十几分钟，浏览器端没有任何持久化进度。
搬进后端后：登记为 job（GET /jobs），「后台活动」面板显示 X/N + 进度条，可协作式取消。

设计要点：
- **VLM 三元组由前端透传**（唯一真源仍是设置里的视觉模型配置）。后端不重复解析
  local/remote 两态，避免两处口径漂移（此前 activeVlmChat 改过一次代理口径）。
- **图片从本机磁盘直读**（rag_store.local_path_of 带 pictures/videos/audios 双回退），
  不再绕浏览器「后端代理 URL → dataURI」那一圈，省一次网络往返。
- 单张失败只记数、不中断其余；并发 3（本地 Ollama 吃得下，又不挤爆显存）。
"""
from __future__ import annotations

import base64
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.services import jobs

# 与「反推提示词」共用同一套系统提示（ai_text.describe_image 同源，避免两处口径漂移）。
REVERSE_SYSTEM = (
    "你是图像反推助手。仔细观察用户提供的图片，输出适合 Stable Diffusion / ComfyUI "
    "使用的英文正向提示词，用逗号分隔的 Danbooru 风格标签或短语，涵盖主体、人物特征、"
    "服饰、动作、画风、光影、构图、画质。只输出提示词本身，不要解释、不要引号、不要换行。"
)

DEFAULT_HINT = (
    "用一段简洁中文客观描述图片中的人物外观、服装、动作、场景、构图和画风；"
    "不要写提示词，不要解释。"
)

_MAX_SIDE = 1024
CONCURRENCY = 3


def downscale_datauri(u: str, max_side: int = _MAX_SIDE) -> str:
    """把 dataURI 图缩到长边 ≤ max_side 再以 JPEG 重编码后返回。

    为什么必须缩：视觉模型按像素计 token——一张 3.5MB 原图（≈1500px+）编码成 ~4150
    token，会超出小上下文 VLM 的上限（qwen3vl:8b 走 Ollama 仅 4096 ctx ⇒ 直接 400，
    前端看到 502）。缩到长边 1024 后 token 大幅下降、还能加速推理；大 ctx 模型同样受益。
    非 dataURI（http 直链）或解码失败则原样返回交给模型侧。
    """
    if not isinstance(u, str) or not u.startswith("data:"):
        return u
    try:
        from PIL import Image

        _header, b64 = u.split(",", 1)
        img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001 [EXC-留痕] 解码失败原样发，让模型侧报真实错
        return u


def file_to_datauri(path: Path | str, max_side: int = _MAX_SIDE) -> str:
    """本机图片文件 → 缩过的 dataURI。读失败返回空串（调用方跳过）。"""
    try:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return ""
        mime = "image/jpeg"
        suffix = p.suffix.lower()
        if suffix in {".png"}:
            mime = "image/png"
        elif suffix in {".webp"}:
            mime = "image/webp"
        elif suffix in {".bmp"}:
            mime = "image/bmp"
        elif suffix in {".gif"}:
            mime = "image/gif"
        raw = p.read_bytes()
        return downscale_datauri(f"data:{mime};base64," + base64.b64encode(raw).decode(), max_side)
    except Exception:  # noqa: BLE001
        return ""


def describe_datauri(vlm: dict, data_uri: str, hint: str = "") -> str:
    """单张图 → 描述文本。失败抛异常（调用方记数跳过）。"""
    from app.services import llm as _llm
    from langchain_core.messages import HumanMessage, SystemMessage

    if not data_uri:
        raise ValueError("空图片")
    model = _llm.build_model(
        str(vlm.get("base_url") or ""), str(vlm.get("api_key") or ""),
        str(vlm.get("model") or ""), 0.3, False,
        proxy=str(vlm.get("proxy") or ""),
    )
    content: list = [{"type": "text", "text": hint or DEFAULT_HINT}]
    content += [{"type": "image_url", "image_url": {"url": downscale_datauri(data_uri)}}]
    resp = model.invoke([SystemMessage(content=REVERSE_SYSTEM), HumanMessage(content=content)])
    return _llm.flatten_content(resp.content).strip()


def missing_description_assets(repo_ids: list[str]) -> list[dict]:
    """列出「缺描述」的图片资产（排除回收站；全量，不受分页限制）。"""
    from app.services import asset_store

    rows = asset_store.list_assets(repo_ids, media_type="image")
    return [r for r in rows if not str(r.get("description") or "").strip()]


def run_describe_missing(job_id: str, repo_ids: list[str], cfg, vlm: dict,
                         hint: str = "", concurrency: int = CONCURRENCY) -> dict:
    """批量补描述（后台线程里跑）。逐张 tick，可协作式取消。返回 {ok, failed, total}。"""
    from app.services import rag_store

    targets = missing_description_assets(repo_ids)
    jobs.set_total(job_id, len(targets))
    if not targets:
        jobs.set_detail(job_id, "没有缺描述的资产")
        jobs.finish(job_id, "done")
        return {"ok": 0, "failed": 0, "total": 0}

    ok = failed = 0

    def _one(row: dict) -> bool:
        data_uri = ""
        local = rag_store.local_path_of(str(row.get("image_url") or ""))
        if local is not None:
            data_uri = file_to_datauri(local)
        if not data_uri:
            return False
        text = describe_datauri(vlm, data_uri, hint)
        if not text:
            return False
        return rag_store.set_generation_description(
            str(row.get("id")), str(row.get("repo_id")), cfg, text,
        )

    def _finish(status: str, error: str = "") -> None:
        """收尾统一写 detail（成功/失败条数）——面板与前端完成 toast 都读它。
        失败条目在网格里仍显示「无描述」，没有这个计数用户无法判断还剩多少没补上。"""
        jobs.set_detail(job_id, f"成功 {ok} / 失败 {failed}")
        jobs.finish(job_id, status, error)

    try:
        with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(targets)))) as pool:
            # 逐张提交 + 边收边 tick；每收一张就查一次取消信号（协作式）
            futures = [pool.submit(_one, row) for row in targets]
            for fut in futures:
                if jobs.should_cancel(job_id):
                    break
                try:
                    if fut.result():
                        ok += 1
                    else:
                        failed += 1
                except Exception:  # noqa: BLE001 单张失败不中断其余
                    failed += 1
                finally:
                    jobs.tick(job_id)
        _finish("cancelled" if jobs.should_cancel(job_id) else "done")
    except Exception as exc:  # noqa: BLE001
        _finish("failed", str(exc))
        raise
    return {"ok": ok, "failed": failed, "total": len(targets)}
