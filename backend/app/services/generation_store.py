"""生成产出持久化：统一负责留存、入 RAG 和写对话快照。"""
import base64
import logging
import re
import time
import uuid
from collections import OrderedDict
from pathlib import Path

import httpx

from app.services import (
    chat_memory, chat_snapshot, image_store, rag_store, repo_meta, view_urls,
)
from app.services.rag_backend import EmbedConfig
from app.services.pathnames import safe_seg

_LOG = logging.getLogger("uvicorn.error")
_WORKFLOW_NAMESPACE = uuid.UUID("98f86310-70e7-4bc2-9b7a-7655140560b0")


class _BoundedKeySet:
    """有界去重集合：记「已写记忆的工作流」防重复，超上限按 FIFO 淘汰最旧键。
    取代原模块级 set（只增不减，长期运行慢泄漏）。淘汰的旧键极不可能再 finalize，安全。"""

    def __init__(self, cap: int = 2048) -> None:
        self._cap = cap
        self._d: "OrderedDict[str, None]" = OrderedDict()

    def __contains__(self, key: str) -> bool:
        return key in self._d

    def add(self, key: str) -> None:
        self._d[key] = None
        self._d.move_to_end(key)
        while len(self._d) > self._cap:
            self._d.popitem(last=False)

    def clear(self) -> None:
        self._d.clear()


_MEMORY_DONE = _BoundedKeySet()

# 远程图片/视频下载上限，防超大/恶意直链把 response.content 一次性读爆内存。
_MAX_IMAGE_BYTES = 30 * 1024 * 1024   # 30 MB
_MAX_VIDEO_BYTES = 200 * 1024 * 1024  # 200 MB


def _download_capped(url: str, timeout: int, max_bytes: int) -> bytes:
    """流式下载并在超过 max_bytes 时中止，避免全量读入超大响应。超限抛 ValueError。"""
    with httpx.Client(trust_env=False, timeout=timeout) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"远程文件超过 {max_bytes // (1024 * 1024)}MB 上限，已中止下载")
                chunks.append(chunk)
    return b"".join(chunks)


def _save_remote_image(url: str, output_dir: str, repo_id: str = "home") -> str:
    """Agent 云图容错留存；失败回退原地址，不阻断已完成的生成。"""
    if not output_dir or not url:
        return url
    try:
        if url.startswith("data:"):
            header, b64 = url.split(",", 1)
            data = base64.b64decode(re.sub(r"\s+", "", b64))
            ext = "png"
            if "image/" in header:
                ext = header.split("image/")[1].split(";")[0] or "png"
        else:
            data = _download_capped(url, timeout=120, max_bytes=_MAX_IMAGE_BYTES)
            tail = safe_seg(Path(url.split("?")[0]).name)
            ext = tail.rsplit(".", 1)[1] if "." in tail else "png"
        base = repo_meta.repo_folder(output_dir, repo_id)
        name = image_store._next_seq_name(base, ext)
        dest = base / name
        dest.write_bytes(data)
        return view_urls.local_view(str(dest))
    except Exception:
        return url


def _index_with_retry(repo_id: str, cfg: EmbedConfig, prompt: str,
                      tags: str = "", image_url: str = "") -> bool:
    for attempt in range(3):
        try:
            rag_store.index_generation(repo_id, cfg, prompt, tags, image_url)
            return True
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("index_generation 失败(第%d次) repo=%s img=%s: %s",
                         attempt + 1, repo_id, image_url, exc)
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
    return False


def persist_image(thread_id: str, repo_id: str, prompt: str, image_url: str,
                   output_dir: str, embed_base: str, embed_key: str,
                   embed_model: str, regeneration: dict | None = None) -> dict:
    """Agent 图片：容错留存后入库并追加快照，返回前后端共用身份。"""
    shown = _save_remote_image(image_url, output_dir, repo_id)
    mid = str(uuid.uuid4())
    cfg = EmbedConfig(embed_base, embed_key, embed_model)
    _index_with_retry(repo_id, cfg, prompt, image_url=shown)
    try:
        chat_snapshot.upsert(
            thread_id,
            chat_snapshot.assistant_message(
                mid, "", image=shown,
                **({"regeneration": regeneration} if regeneration else {}),
            ),
        )
    except Exception as exc:  # noqa: BLE001  失败不阻断返回，但要留痕（否则丢快照无声）
        _LOG.warning("persist_image 写快照失败 thread=%s mid=%s: %s", thread_id, mid, exc)
    return {"id": mid, "url": shown,
            **({"regeneration": regeneration} if regeneration else {})}


def _save_remote_video(url: str, output_dir: str, repo_id: str = "home") -> str:
    """Agent 云视频容错留存；失败回退原地址，不阻断已完成的生成。"""
    if not output_dir or not url:
        return url
    try:
        if url.startswith("data:"):
            header, b64 = url.split(",", 1)
            data = base64.b64decode(re.sub(r"\s+", "", b64))
            ext = "mp4"
            if "video/" in header:
                ext = header.split("video/")[1].split(";")[0] or "mp4"
            name = f"{uuid.uuid4().hex}.{safe_seg(ext)}"
        else:
            data = _download_capped(url, timeout=300, max_bytes=_MAX_VIDEO_BYTES)
            tail = safe_seg(Path(url.split("?")[0]).name)
            name = tail if "." in tail else f"{uuid.uuid4().hex}.mp4"
        dest = repo_meta.repo_folder(output_dir, repo_id) / name
        dest.write_bytes(data)
        return view_urls.local_view(str(dest))
    except Exception:
        return url


def persist_video(thread_id: str, repo_id: str, prompt: str, video_url: str,
                  output_dir: str) -> dict:
    """Agent 视频：容错留存后追加快照，返回前后端共用身份 {id,url}。"""
    shown = _save_remote_video(video_url, output_dir, repo_id)
    mid = str(uuid.uuid4())
    try:
        chat_snapshot.upsert(thread_id, chat_snapshot.assistant_message(mid, "", video=shown))
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("persist_video 写快照失败 thread=%s mid=%s: %s", thread_id, mid, exc)
    return {"id": mid, "url": shown}


def _workflow_key(thread_id: str, prompt_id: str, image: dict | None = None) -> str:
    base = f"workflow:{thread_id}:{prompt_id}"
    if image is None:
        return f"{base}:text"
    return ":".join((base, str(image.get("type", "output")),
                     str(image.get("subfolder", "")), str(image.get("filename", ""))))


def _message_id(key: str) -> str:
    return str(uuid.uuid5(_WORKFLOW_NAMESPACE, key))


def _extract_tags(prompt: str, base_url: str, api_key: str, model: str) -> str:
    if not prompt.strip() or not base_url or not model:
        return ""
    try:
        from app.services import llm  # service 层直接调 llm，不经 routers（守分层）
        system = (
            "你是标签提取助手。把给定的绘画提示词切分成 4-8 个简短关键词标签，"
            "覆盖主体、风格、场景、光影等要点。中文提示词输出中文标签。"
            "只输出标签本身，用英文逗号分隔，不要解释、不要编号、不要换行。"
        )
        out = llm.chat(base_url, api_key, model, system, prompt, temperature=0.2)
        return ",".join(t.strip() for t in re.split(r"[,，;；\n]+", out) if t.strip())
    except Exception:
        return ""


def finalize_workflow_batch(
    *, thread_id: str, repo_id: str, prompt_id: str, prompt: str,
    images: list[dict], output_dir: str, comfyui_url: str,
    embed_base: str, embed_key: str, embed_model: str,
    chat_base: str = "", chat_key: str = "", chat_model: str = "",
    videos: list[dict] | None = None,
    regeneration: dict | None = None,
) -> dict:
    """持久化一批已完成的 ComfyUI 产出；单图/单视频阶段失败不会阻断其他产出。
    videos 与 images 同结构({filename,subfolder,type})，消息以 video 字段承载。"""
    videos = videos or []
    if not thread_id or not repo_id or not prompt_id:
        raise ValueError("thread_id、repo_id 和 prompt_id 不能为空")
    if not images and not videos and not prompt.strip():
        raise ValueError("生成结果没有图片、视频或文字")

    durable = repo_id != "home"
    cfg = EmbedConfig(embed_base, embed_key, embed_model)
    tags = _extract_tags(prompt, chat_base, chat_key, chat_model) if durable else ""
    messages: list[dict] = []
    results: list[dict] = []

    for index, image in enumerate(images):
        key = _workflow_key(thread_id, prompt_id, image)
        mid = _message_id(key)
        persisted = indexed = snapshotted = False
        errors: list[str] = []
        shown = ""
        if durable:
            try:
                path = image_store.save_local(
                    output_dir, repo_id,
                    filename=str(image.get("filename", "")),
                    subfolder=str(image.get("subfolder", "")),
                    type=str(image.get("type", "output")),
                    url=comfyui_url,
                    idempotency_key=key,
                )
                shown = view_urls.local_view(path)
                persisted = True
            except Exception:  # 保留在线图，不影响本批其他产出
                errors.append("persist")
        if not shown:
            shown = view_urls.remote_view(
                filename=str(image.get("filename", "")),
                type=str(image.get("type", "output")),
                subfolder=str(image.get("subfolder", "")),
                comfyui_url=comfyui_url,
            )

        message = chat_snapshot.assistant_message(
            mid, prompt if index == 0 else "", image=shown,
            **({"regeneration": regeneration} if regeneration else {}),
        )
        messages.append(message)
        if durable:
            indexed = _index_with_retry(repo_id, cfg, prompt, tags, shown)
            if not indexed:
                errors.append("index")
            try:
                chat_snapshot.upsert(thread_id, message)
                snapshotted = True
            except Exception:
                errors.append("snapshot")
        results.append({
            "key": key, "message_id": mid, "display_url": shown,
            "persisted": persisted, "indexed": indexed,
            "snapshotted": snapshotted, "errors": errors,
        })

    for index, video in enumerate(videos):
        key = _workflow_key(thread_id, prompt_id, video)
        mid = _message_id(key)
        persisted = snapshotted = False
        errors: list[str] = []
        shown = ""
        if durable:
            try:
                path = image_store.save_local(
                    output_dir, repo_id,
                    filename=str(video.get("filename", "")),
                    subfolder=str(video.get("subfolder", "")),
                    type=str(video.get("type", "output")),
                    url=comfyui_url,
                    idempotency_key=key,
                )
                shown = view_urls.local_view(path)
                persisted = True
            except Exception:
                errors.append("persist")
        if not shown:
            shown = view_urls.remote_view(
                filename=str(video.get("filename", "")),
                type=str(video.get("type", "output")),
                subfolder=str(video.get("subfolder", "")),
                comfyui_url=comfyui_url,
            )
        # 首个产物（无图时）承载提示词文本
        head_text = prompt if (index == 0 and not images) else ""
        message = chat_snapshot.assistant_message(mid, head_text, video=shown)
        messages.append(message)
        if durable:
            try:
                chat_snapshot.upsert(thread_id, message)
                snapshotted = True
            except Exception:
                errors.append("snapshot")
        results.append({
            "key": key, "message_id": mid, "display_url": shown,
            "persisted": persisted, "indexed": False,
            "snapshotted": snapshotted, "errors": errors,
        })

    if not images and not videos and prompt.strip():
        mid = _message_id(_workflow_key(thread_id, prompt_id))
        message = chat_snapshot.assistant_message(mid, prompt)
        messages.append(message)
        indexed = snapshotted = False
        errors: list[str] = []
        if durable:
            indexed = _index_with_retry(repo_id, cfg, prompt, tags)
            if not indexed:
                errors.append("index")
            try:
                chat_snapshot.upsert(thread_id, message)
                snapshotted = True
            except Exception:
                errors.append("snapshot")
        results.append({
            "key": _workflow_key(thread_id, prompt_id), "message_id": mid,
            "display_url": "", "persisted": False, "indexed": indexed,
            "snapshotted": snapshotted, "errors": errors,
        })

    if durable:
        memory_key = _workflow_key(thread_id, prompt_id)
        if memory_key not in _MEMORY_DONE:
            try:
                chat_memory.append_message(
                    thread_id, "assistant", prompt,
                    [message["image"] for message in messages if message.get("image")] or None,
                )
                _MEMORY_DONE.add(memory_key)
            except Exception as exc:  # noqa: BLE001  记忆写入失败不阻断返回，但要留痕
                _LOG.warning("finalize_workflow_batch 写记忆失败 thread=%s prompt_id=%s: %s",
                             thread_id, prompt_id, exc)

    complete = durable and all(not item["errors"] for item in results)
    return {"prompt_id": prompt_id, "durable": durable, "messages": messages,
            "images": results, "complete": complete}


def persist_inspiration(thread_id: str, query: str, prompt: str,
                        tags: list[str], sources: list[dict]) -> dict:
    card = {"id": str(uuid.uuid4()), "query": query, "prompt": prompt,
            "tags": tags, "sources": sources}
    try:
        chat_snapshot.upsert(thread_id, chat_snapshot.assistant_message(
            card["id"], "",
            inspiration={"query": query, "prompt": prompt,
                         "tags": tags, "sources": sources},
        ))
    except Exception as exc:  # noqa: BLE001  灵感卡写快照失败不阻断返回，但要留痕
        _LOG.warning("persist_inspiration 写快照失败 thread=%s: %s", thread_id, exc)
    return card


def persist_text(thread_id: str, message_id: str, text: str,
                 interrupted: bool = False) -> None:
    if not (text or "").strip() or not message_id:
        return
    try:
        if interrupted:
            chat_snapshot.upsert(
                thread_id,
                chat_snapshot.assistant_message(message_id, text, interrupted=True),
            )
        else:
            chat_snapshot.append_text(thread_id, message_id, text)
    except Exception as exc:  # noqa: BLE001  文本落盘失败不阻断收尾，但要留痕
        _LOG.warning("persist_text 落盘失败 thread=%s mid=%s: %s", thread_id, message_id, exc)


def persist_prompt_approval(thread_id: str, approval: dict) -> None:
    message_id = str(approval.get("messageId") or "")
    if not message_id:
        return
    try:
        chat_snapshot.merge_fields(thread_id, message_id, promptApproval=approval)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("persist_prompt_approval 落盘失败 thread=%s mid=%s: %s",
                     thread_id, message_id, exc)


def persist_route_choice(thread_id: str, route_choice: dict) -> None:
    message_id = str(route_choice.get("messageId") or "")
    if not message_id:
        return
    try:
        chat_snapshot.merge_fields(thread_id, message_id, routeChoice=route_choice)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("persist_route_choice 落盘失败 thread=%s mid=%s: %s",
                     thread_id, message_id, exc)
