"""对话维护：集中清理、缓存清理与压缩的保留规则和失败语义。"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
import uuid
from dataclasses import dataclass

from app.services import (
    agent_runner, attachment_store, chat_archive, chat_memory, chat_snapshot,
    rag_store, repo_meta,
)
from app.services.pathnames import safe_seg
from app.services.rag_backend import EmbedConfig

_LOG = logging.getLogger("uvicorn.error")


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


def _is_chat_thread(thread_id: str) -> bool:
    """私聊/群聊线程命名：<repo>__chat__<角色> / <repo>__group。"""
    return "__chat" in thread_id or "__group" in thread_id


def _clear_chat_summary(thread_id: str) -> None:
    """清空/压缩/恢复会话后让滚动摘要缓存失效；失败只留日志，不阻断主流程。"""
    try:
        from app.services import chat_context_summary
        chat_context_summary.clear_thread(thread_id)
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 缓存清理失败不影响会话维护
        _LOG.warning("清理聊天摘要缓存失败 thread=%s: %s", thread_id, exc)


def _revision(history: list[object]) -> str:
    raw = json.dumps(history, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_snapshot(thread_id: str) -> list:
    """严格读快照：读不动 ⇒ MaintenanceFailed（当成「没有历史」会把真源静默覆盖掉）。"""
    try:
        return chat_snapshot.load_strict(thread_id)
    except Exception as exc:
        raise MaintenanceFailed(f"读取消息快照失败：{exc}") from exc


def _archive(thread_id: str, snapshot: list, reason: str, history: list | None = None) -> str:
    """删除前的归档闸：**归档没成就一个字都不删**（fail-closed）。

    空会话返回 ""（没东西可丢，不是失败）。异常面刻意收宽：`chat_archive` 的 OSError（磁盘满/
    权限/目录不可建）与 JSON 不可序列化都必须落到 MaintenanceFailed，否则端点裸 500、
    而且——更糟的是——调用方可能在异常之外继续删。
    """
    try:
        return chat_archive.archive(thread_id, snapshot, reason, history)
    except Exception as exc:
        raise MaintenanceFailed(f"删除前归档对话失败：{exc}") from exc


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
            fragments.append("[灵感卡] " + (inspiration.get("content") or inspiration.get("title") or ""))
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
    path = rag_store.local_path_of(url)
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
        # 软删层：清的是 checkpoint，快照还在；归档留一份「当时的完整会话」，恢复对话取它
        _archive(thread_id, _read_snapshot(thread_id), chat_archive.REASON_CLEAR)
        try:
            chat_memory.clear_history(thread_id)
        except Exception as exc:
            raise MaintenanceFailed(f"清空对话失败：{exc}") from exc
        # B1 连带：对话线清空 = 该会话附件一起删（历史回放卡随之失效，属预期）
        try:
            attachment_store.delete_thread(thread_id)
        except Exception as exc:  # noqa: BLE001 - 附件清理失败不阻断主流程
            raise MaintenanceFailed(f"清理会话附件失败：{exc}") from exc
        _clear_chat_summary(thread_id)
    return {"ok": True}


def clear_cache(thread_id: str, output_dir: str) -> ClearCacheResult:
    with _lock(thread_id):
        _ensure_idle(thread_id)
        old_snapshot = _read_snapshot(thread_id)
        # 软删层：这一步之后快照就被整条抹掉，归档失败 ⇒ 一个文件都不动
        _archive(thread_id, old_snapshot, chat_archive.REASON_CLEAR_CACHE)
        try:
            chat_snapshot.save(thread_id, [])
        except Exception as exc:
            raise MaintenanceFailed(f"清空消息快照失败：{exc}") from exc
        try:
            chat_memory.clear_history(thread_id)
        except Exception as exc:
            try:
                chat_snapshot.save(thread_id, old_snapshot)
            except Exception as roll_exc:  # noqa: BLE001 - 回滚失败必须留痕，否则快照静默分叉
                _LOG.warning("清空对话后快照回滚失败 thread=%s: %s", thread_id, roll_exc)
            raise MaintenanceFailed(f"清空对话失败：{exc}") from exc
        # B1 连带：快照清空 = 该会话附件一起删（历史回放卡随之失效，属预期）
        try:
            attachment_store.delete_thread(thread_id)
        except Exception as exc:  # noqa: BLE001 - 附件清理失败不阻断主流程
            raise MaintenanceFailed(f"清理会话附件失败：{exc}") from exc

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
        _clear_chat_summary(thread_id)
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
    result = chat_memory.summarize_history(
        full_history, llm, generations, chat_mode=_is_chat_thread(thread_id),
    )
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
        # 软删层：压缩把整条会话换成一条摘要，原文只留摘要里那几句 ⇒ 先原样归档
        _archive(thread_id, old_snapshot, chat_archive.REASON_COMPACT, history)
        try:
            chat_snapshot.save(thread_id, [message])
        except Exception as exc:
            raise MaintenanceFailed(f"写入摘要快照失败：{exc}") from exc
        try:
            chat_memory.replace_history(thread_id, checkpoint_summary)
        except Exception as exc:
            try:
                chat_snapshot.save(thread_id, old_snapshot)
            except Exception as roll_exc:  # noqa: BLE001 - 回滚失败必须留痕，否则快照静默分叉
                _LOG.warning("压缩后快照回滚失败 thread=%s: %s", thread_id, roll_exc)
            try:
                chat_memory.replace_history(thread_id, history)
            except Exception as roll_exc:  # noqa: BLE001 - 回滚失败必须留痕，否则记忆静默分叉
                _LOG.warning("压缩后对话记忆回滚失败 thread=%s: %s", thread_id, roll_exc)
            raise MaintenanceFailed(f"写入摘要对话失败：{exc}") from exc
        _clear_chat_summary(thread_id)

    return {
        "ok": True,
        "summary": summary,
        "image_count": result.get("image_count", 0),
        "message": message,
    }


def list_archives(thread_id: str) -> list[dict]:
    """该会话可恢复的归档清单（新的在前）。纯读，不进写事务。"""
    return chat_archive.list_archives(thread_id)


def restore(thread_id: str, name: str) -> dict:
    """把某份归档整条覆盖回会话（快照真源 + checkpoint 历史）。

    恢复本身也是破坏性写入 ⇒ 动手前先把「当前状态」归档一份，压错了还能压回去。
    归档名不合法/读不动 ⇒ `ValueError`（路由按 400 回，不是服务内部故障）。
    """
    with _lock(thread_id):
        _ensure_idle(thread_id)
        try:
            payload = chat_archive.read_archive(thread_id, name)
        except chat_archive.ArchiveNameError as exc:
            raise ValueError(str(exc)) from exc
        current_snapshot = _read_snapshot(thread_id)
        _archive(thread_id, current_snapshot, chat_archive.REASON_RESTORE)
        messages = payload["messages"]
        # 归档里快照为空（当时只有 checkpoint 历史）⇒ 只回历史，不凭空造消息：
        # 现造一批 id 写快照，正是 chat_snapshot._is_wholesale_replacement 拦的那种形态。
        if messages:
            try:
                chat_snapshot.save(thread_id, messages)
            except Exception as exc:
                raise MaintenanceFailed(f"写入恢复快照失败：{exc}") from exc
        derived = _snapshot_history(messages) or payload["history"]
        if derived:
            try:
                chat_memory.replace_history(thread_id, derived)
            except Exception as exc:
                if messages:
                    try:
                        chat_snapshot.save(thread_id, current_snapshot)
                    except Exception as roll_exc:  # noqa: BLE001 - 回滚失败必须留痕，否则快照静默分叉
                        _LOG.warning("恢复对话后快照回滚失败 thread=%s: %s", thread_id, roll_exc)
                raise MaintenanceFailed(f"恢复对话记忆失败：{exc}") from exc
        _clear_chat_summary(thread_id)
    return {"ok": True, "messages": len(messages), "history": len(derived)}
