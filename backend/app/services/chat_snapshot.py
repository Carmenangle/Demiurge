"""前端消息流快照：按 thread_id 落盘成 JSON 文件，作为对话流的可靠真源。

与 chat_memory（langgraph 多轮记忆）分工不同：
  - chat_memory  ：喂给模型的对话上下文，只含「对话类」消息。
  - chat_snapshot：前端完整渲染用的消息流，含工作流卡、反推卡等非对话消息。
前端 localStorage 仅作快取，关浏览器/清端口/换 origin 都不丢，因真源在磁盘。
"""
import json
import threading

from app.config import DATA_DIR
from app.services.pathnames import safe_seg

SNAP_DIR = DATA_DIR / "chat_snapshots"
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _thread_lock(thread_id: str) -> threading.Lock:
    key = _safe(thread_id)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _safe(thread_id: str) -> str:
    """thread_id 一般是 uuid 或 'home'，仍兜底过滤非法文件名字符（不去两端，空则 home）。"""
    return safe_seg(thread_id or "home", "home", strip=False)


def _path(thread_id: str):
    return SNAP_DIR / f"{_safe(thread_id)}.json"


def _save_unlocked(thread_id: str, messages: list) -> None:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(thread_id)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(messages, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def save(thread_id: str, messages: list) -> None:
    """覆盖写入该 thread 的完整消息流，并与增量写串行化。"""
    with _thread_lock(thread_id):
        _save_unlocked(thread_id, messages)


def load_strict(thread_id: str) -> list:
    """严格读取快照；文件损坏或 I/O 失败直接抛出，供维护事务判断失败。"""
    p = _path(thread_id)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def load(thread_id: str) -> list:
    """读取该 thread 的消息流，无则返回空列表。"""
    try:
        return load_strict(thread_id)
    except Exception:
        return []


def assistant_message(mid: str, text: str, **fields) -> dict:
    """前后端共用的 assistant 消息形状唯一构造入口。

    键序固定 id/role/text/...，额外字段(image/interrupted/inspiration 等)按传入顺序附加。
    generation_store 等其它模块也应经此构造，避免各处手拼 dict 导致形状漂移。
    """
    return {"id": mid, "role": "assistant", "text": text, **fields}


_assistant_message = assistant_message  # 兼容内部旧调用名


def upsert(thread_id: str, msg: dict) -> None:
    """按 msg["id"] 写入快照：已存在则替换该条，否则追加。

    前后端用同一消息 id（前端生成 botId/后端生成图片 mid 回传前端），无论谁后写
    都幂等去重——避免「前端保存半截文本 + 后端追加完整文本」产生重复气泡。
    读-改-写非原子，本场景单写者足够。
    """
    mid = msg.get("id")
    with _thread_lock(thread_id):
        items = load(thread_id)
        for i, it in enumerate(items):
            if isinstance(it, dict) and it.get("id") == mid:
                items[i] = msg
                _save_unlocked(thread_id, items)
                return
        items.append(msg)
        _save_unlocked(thread_id, items)


def merge_fields(thread_id: str, mid: str, **fields) -> None:
    """合并更新一条消息的结构化字段，不覆盖已有正文和媒体。"""
    if not mid:
        return
    with _thread_lock(thread_id):
        items = load(thread_id)
        for i, item in enumerate(items):
            if isinstance(item, dict) and item.get("id") == mid:
                items[i] = {**item, **fields}
                _save_unlocked(thread_id, items)
                return
        items.append(assistant_message(mid, "", **fields))
        _save_unlocked(thread_id, items)


def append_image(thread_id: str, mid: str, image_url: str, text: str = "") -> None:
    """按 mid upsert 一条带图 assistant 消息（mid 同时回传前端，重开不重复）。"""
    upsert(thread_id, _assistant_message(mid, text or "", image=image_url))


def append_text(thread_id: str, mid: str, text: str) -> None:
    """按 mid upsert 一条纯文本 assistant 消息（后台生成完成时落盘，mid=前端 botId）。"""
    if not (text or "").strip():
        return
    upsert(thread_id, _assistant_message(mid, text))
