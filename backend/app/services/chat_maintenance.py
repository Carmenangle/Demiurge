"""对话维护：集中清理、缓存清理与压缩的保留规则和失败语义。"""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
import uuid
from dataclasses import dataclass

from app.services import agent_runner, chat_memory, chat_snapshot, rag_store, repo_meta
from app.services.pathnames import safe_seg
from app.services.rag_backend import EmbedConfig


class MaintenanceConflict(RuntimeError):
    pass


class MaintenanceFailed(RuntimeError):
    pass


class NothingToCompact(ValueError):
    pass


@dataclass(frozen=True)
class ClearCacheResult:
    removed: int


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(thread_id: str) -> threading.RLock:
    key = safe_seg(thread_id or "home", "home", strip=False)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _ensure_idle(thread_id: str) -> None:
    if agent_runner.is_running(thread_id):
        raise MaintenanceConflict("该对话仍有后台生成任务，请等待完成或先停止")


def _revision(history: list[dict]) -> str:
    raw = json.dumps(history, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _message_images(message: dict) -> list[str]:
    images: list[str] = []
    direct = message.get("image")
    if isinstance(direct, str) and direct:
        images.append(direct)
    for part in message.get("parts") or []:
        if isinstance(part, dict) and part.get("type") == "image" and part.get("url"):
            images.append(part["url"])
    return list(dict.fromkeys(images))


def _snapshot_history(snapshot: list[dict]) -> list[dict]:
    """把完整渲染快照转换为压缩用的时间序列，覆盖普通消息与结构化卡片。"""
    history: list[dict] = []
    for message in snapshot:
        if not isinstance(message, dict):
            continue
        fragments: list[str] = []
        text = (message.get("text") or "").strip()
        if text:
            fragments.append(text)
        elif message.get("parts"):
            fragments.extend(
                (part.get("text") or "").strip()
                for part in message["parts"]
                if isinstance(part, dict) and part.get("type") == "text"
                and (part.get("text") or "").strip()
            )
        workflow = message.get("workflow") or {}
        if workflow:
            status = "已完成" if workflow.get("done") else "未完成"
            fragments.append(f"[工作流卡] {workflow.get('templateName', '未命名')}（{status}）")
        inspiration = message.get("inspiration") or {}
        if inspiration:
            fragments.append("[灵感卡] " + (inspiration.get("prompt") or inspiration.get("query") or ""))
        ports_plan = message.get("portsPlan") or {}
        if ports_plan:
            fragments.append("[工作流编排] " + (ports_plan.get("summary") or ""))
        approval = message.get("promptApproval") or {}
        if approval:
            fragments.append(
                f"[提示词审批：{approval.get('status', '未知')}] "
                + (approval.get("prompt") or approval.get("originalPrompt") or "")
            )
        images = _message_images(message)
        content = "\n".join(fragment for fragment in fragments if fragment).strip()
        if not content and images:
            content = "（图片消息）"
        if content or images:
            history.append({
                "role": "user" if message.get("role") == "user" else "assistant",
                "content": content,
                "images": images,
            })
    return history


def _usable_image(url: str) -> bool:
    if not url:
        return False
    path = rag_store._local_path_of(url)
    return path is None or path.is_file()


def _latest_result_image(snapshot: list[dict], generations: list[dict]) -> str:
    for message in reversed(snapshot):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for url in reversed(_message_images(message)):
            if _usable_image(url):
                return url
    ordered = sorted(generations, key=lambda item: int(item.get("created_at", 0) or 0), reverse=True)
    for generation in ordered:
        url = generation.get("image_url") or ""
        if _usable_image(url):
            return url
    return ""


def clear(thread_id: str) -> dict:
    with _lock(thread_id):
        _ensure_idle(thread_id)
        try:
            chat_memory.clear_history(thread_id)
        except Exception as exc:
            raise MaintenanceFailed(f"清空对话失败：{exc}") from exc
    return {"ok": True}


def clear_cache(thread_id: str, output_dir: str) -> ClearCacheResult:
    with _lock(thread_id):
        _ensure_idle(thread_id)
        try:
            old_snapshot = chat_snapshot.load_strict(thread_id)
            chat_snapshot.save(thread_id, [])
        except Exception as exc:
            raise MaintenanceFailed(f"清空消息快照失败：{exc}") from exc
        try:
            chat_memory.clear_history(thread_id)
        except Exception as exc:
            try:
                chat_snapshot.save(thread_id, old_snapshot)
            except Exception:
                pass
            raise MaintenanceFailed(f"清空对话失败：{exc}") from exc

        removed = 0
        if output_dir:
            reference = repo_meta.repo_folder_path(output_dir, thread_id) / "reference"
            if reference.is_dir():
                removed = sum(1 for path in reference.iterdir() if path.is_file())
                try:
                    shutil.rmtree(reference)
                except Exception as exc:
                    raise MaintenanceFailed(f"删除参考图失败：{exc}") from exc
                if reference.exists():
                    raise MaintenanceFailed("删除参考图失败：目录仍然存在")
        return ClearCacheResult(removed=removed)


def compact(thread_id: str, llm, embed_cfg: EmbedConfig) -> dict:
    lock = _lock(thread_id)
    with lock:
        _ensure_idle(thread_id)
        try:
            history = chat_memory.get_history(thread_id)
            old_snapshot = chat_snapshot.load_strict(thread_id)
        except Exception as exc:
            raise MaintenanceFailed(f"读取对话状态失败：{exc}") from exc
        revision = _revision([history, old_snapshot])

    try:
        generations = rag_store.list_generations(thread_id, embed_cfg)
    except Exception as exc:
        raise MaintenanceFailed(f"读取生成记录失败：{exc}") from exc
    full_history = _snapshot_history(old_snapshot) or history
    result = chat_memory.summarize_history(full_history, llm, generations)
    if not result.get("ok"):
        error = result.get("error")
        if error:
            raise MaintenanceFailed(f"生成摘要失败：{error}")
        raise NothingToCompact("无可压缩内容或摘要为空")

    summary = result["summary"]
    final_image = _latest_result_image(old_snapshot, generations)
    message = {
        "id": str(uuid.uuid4()),
        "role": "assistant",
        "text": "【历史摘要】\n" + summary,
        **({"image": final_image} if final_image else {}),
    }
    checkpoint_summary = [{
        "role": "assistant",
        "content": "【历史摘要】\n" + summary,
        "images": [final_image] if final_image else [],
    }]

    with lock:
        _ensure_idle(thread_id)
        current = chat_memory.get_history(thread_id)
        current_snapshot = chat_snapshot.load_strict(thread_id)
        if _revision([current, current_snapshot]) != revision:
            raise MaintenanceConflict("对话在压缩期间已更新，请重试")
        try:
            chat_snapshot.save(thread_id, [message])
        except Exception as exc:
            raise MaintenanceFailed(f"写入摘要快照失败：{exc}") from exc
        try:
            chat_memory.replace_history(thread_id, checkpoint_summary)
        except Exception as exc:
            try:
                chat_snapshot.save(thread_id, old_snapshot)
            except Exception:
                pass
            try:
                chat_memory.replace_history(thread_id, history)
            except Exception:
                pass
            raise MaintenanceFailed(f"写入摘要对话失败：{exc}") from exc

    return {
        "ok": True,
        "summary": summary,
        "image_count": result.get("image_count", 0),
        "message": message,
    }
