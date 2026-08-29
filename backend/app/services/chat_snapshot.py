"""前端消息流快照：按 thread_id 落盘成 JSON 文件，作为对话流的可靠真源。

与 chat_memory（langgraph checkpoint）分工不同：
  - chat_snapshot：前端显示与模型历史的真源，含工作流卡等非对话消息，入模前再筛文本。
  - chat_memory  ：仅在快照文件尚不存在时兼容旧会话，不得复活快照中已删除的消息。
前端 localStorage 仅作快取，关浏览器/清端口/换 origin 都不丢，因真源在磁盘。
"""
import json
import os
import re
import threading

from app.config import DATA_DIR
from app.services.pathnames import safe_seg

SNAP_DIR = DATA_DIR / "chat_snapshots"   # 旧位置/未配置仓库文件夹时的回退
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_REVISIONS: dict[str, int] = {}
_REVISIONS_PATH = DATA_DIR / "chat_revisions.json"


def _load_revisions() -> dict[str, int]:
    try:
        raw = json.loads(_REVISIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): int(v) for k, v in raw.items() if isinstance(v, (int, float))}


_REVISIONS.update(_load_revisions())  # 重启后恢复各 thread 最新 revision（防旧保存覆盖压缩结果）


def _persist_revisions(revisions: dict[str, int]) -> None:
    try:
        _REVISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _REVISIONS_PATH.write_text(
            json.dumps(revisions, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # 持久化失败不阻断保存，仅退回内存闸


def _thread_lock(thread_id: str) -> threading.Lock:
    key = _safe(thread_id)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _safe(thread_id: str) -> str:
    """thread_id 一般是 uuid 或 'home'，仍兜底过滤非法文件名字符（不去两端，空则 home）。"""
    return safe_seg(thread_id or "home", "home", strip=False)


def _legacy_path(thread_id: str):
    """旧落点：backend/data/chat_snapshots/<id>.json。"""
    return SNAP_DIR / f"{_safe(thread_id)}.json"


def _path(thread_id: str):
    """会话记录落点：配了"仓库文件夹"则随图片同落 <仓库文件夹>/<作品名>/chat.json，
    否则回退旧位置。thread_id == repo_id，复用 repo_meta 的作品文件夹命名（保中文）。

    惰性迁移：新位置无文件但旧位置有 → 搬过去（一次性，静默失败回退旧位置），
    让存量会话在下次打开时平滑归位，无需用户手动操作。
    """
    from app.services import repo_meta
    output_dir = repo_meta.output_dir_from_state()
    if not output_dir:
        return _legacy_path(thread_id)
    # 子仓库先从旧扁平/UUID 文件夹惰性搬到嵌套位置 <父名>/<子名>/（幂等），再取其下 chat.json
    folder = repo_meta.migrate_legacy_folder(output_dir, thread_id)
    new_path = folder / "chat.json"
    if not new_path.exists():
        legacy = _legacy_path(thread_id)
        if legacy.is_file():
            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(legacy, new_path)
            except OSError:
                return legacy  # 搬迁失败 → 继续用旧位置，不丢数据
    return new_path


def _save_unlocked(thread_id: str, messages: list) -> None:
    p = _path(thread_id)
    p.parent.mkdir(parents=True, exist_ok=True)  # 仓库文件夹或旧 SNAP_DIR，按解析结果建
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(messages, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _preserve_server_media_state(current: list, incoming: list) -> list:
    """合并同槽的服务端提交状态，防止前端旧快照把认领/结果回滚成 pending。

    只处理传入快照仍保留的同一消息与同一槽；用户已经删除的消息或槽不会复活。
    """
    current_messages = {
        item.get("id"): item for item in current
        if isinstance(item, dict) and item.get("id")
    }
    merged: list = []
    for item in incoming:
        if not isinstance(item, dict):
            merged.append(item)
            continue
        current_item = current_messages.get(item.get("id"))
        if not isinstance(current_item, dict) or not isinstance(item.get("parts"), list):
            merged.append(item)
            continue
        current_parts = {
            part.get("slotId"): part for part in (current_item.get("parts") or [])
            if isinstance(part, dict) and part.get("slotId")
        }
        next_parts: list = []
        changed = False
        for part in item["parts"]:
            server_part = current_parts.get(part.get("slotId")) if isinstance(part, dict) else None
            if not isinstance(server_part, dict) or not isinstance(part, dict):
                next_parts.append(part)
                continue
            if (server_part.get("type") in ("image", "video", "audio")
                    and server_part.get("status") == "ready"):
                next_parts.append(server_part)
                changed = True
                continue
            if part.get("type") == "media-slot" and server_part.get("type") == "media-slot":
                protected = {
                    key: server_part[key] for key in ("submissionClaim", "promptId")
                    if server_part.get(key)
                }
                if protected:
                    next_parts.append({**part, **protected})
                    changed = True
                    continue
            next_parts.append(part)
        merged.append({**item, "parts": next_parts} if changed else item)
    return merged


def save(thread_id: str, messages: list) -> None:
    """覆盖写入该 thread 的完整消息流，并与增量写串行化。"""
    with _thread_lock(thread_id):
        _save_unlocked(thread_id, messages)


def save_if_newer(thread_id: str, messages: list, revision: int) -> bool:
    """仅接受同一前端会话中更新的完整快照，阻止较早异步请求晚到后覆盖删除结果。"""
    key = _safe(thread_id)
    with _thread_lock(thread_id):
        previous = _REVISIONS.get(key)
        if previous is not None and revision <= previous:
            return False
        _save_unlocked(thread_id, _preserve_server_media_state(load(thread_id), messages))
        _REVISIONS[key] = revision
        _persist_revisions(_REVISIONS)
        return True


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


def to_prompt_history(messages: list) -> list[dict]:
    """把前端完整消息快照转成只供模型上下文使用的对话历史。

    快照是用户当前所见对话的真源；被删除的消息已不在列表中，不得从 checkpoint 复活。
    工作流卡、空流式占位等非对话结构不进 prompt；parts 仅抽文本块。
    与前端 promptHistory 对齐：状态/Toast（system）、顶层媒体气泡（工作流/Agent 产出
    的图/视频/音频）、非剧情路由（generate/video/…）的助手消息都不进剧情上下文。
    """
    history: list[dict] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        if message.get("system"):
            continue  # 状态/Toast（如「已提交到 ComfyUI…」）不进上下文
        text = (message.get("text") or "").strip()
        if not text:
            text = "\n".join(
                (part.get("text") or "").strip()
                for part in (message.get("parts") or [])
                if isinstance(part, dict) and part.get("type") == "text"
                and (part.get("text") or "").strip()
            ).strip()
        if not text:
            continue
        if role == "assistant":
            # 顶层媒体气泡（工作流/Agent 产出的图/视频/音频，带提示词文本）不进剧情上下文
            if message.get("image") or message.get("video") or message.get("audio"):
                continue
            route = message.get("route")
            if route and route not in ("roleplay", "answer"):
                continue  # 生图/视频/反推/灵感/工具等非剧情路由
        history.append({"role": role, "content": text})
    return history


def load_prompt_history(thread_id: str) -> list[dict] | None:
    """读可见快照并转成 prompt 历史；仅快照文件不存在时返回 None。

    已存在但为空的快照表示用户已删空对话，必须返回 [] 阻止 checkpoint 回退。
    已存在但损坏时也安全地返回 []，宁可不上传历史也不复活用户已删内容。
    """
    path = _path(thread_id)
    if not path.exists():
        return None
    try:
        return to_prompt_history(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return []


# 一次性迁移：旧快照（route/system 字段引入前）里没有标签的助手消息，
# 无法再靠运行时启发式判断是否剧情——按「剧情专家标签」原则回填标签：
#   - 文本像状态/Toast（含 ComfyUI/prompt_id/命令/运行状态词）→ system:true（非剧情）
#   - 文本像生成提示词（danbooru 质量标签词汇，叙事正文不会出现）→ route:"generate"（非剧情）
#   - 其余纯正文 → route:"roleplay"（剧情专家产出）
# 有媒体/卡字段或已有 route 的消息不动（天然排除或已标签化）。
# 仅对历史数据跑一次（backend/scripts/backfill_story_route_0824.py），
# 之后新消息全部走标签判定，不再依赖任何文本猜测。
_STATUS_HINTS = re.compile(
    r"ComfyUI|prompt_id|重新生图|生成完成，但没有输出|生成失败|生成较复杂|已提交到 ComfyUI|"
    r"启动失败|扮演失败|ComfyUI 未启动|正在尝试自动拉起|请先启动|请稍候 20|请等待完成|"
    r"无法保证准确重生成|选择完毕|没有已确认|没抓到画布内容|没找到名为|当前已有生成任务|"
    r"原始消息已不存在|已应用 LoRA|已生成图片|/w |/s |/find "
)
# 生成提示词强特征：danbooru 质量标签/画面词汇（剧情叙事不会用这些词）
_PROMPT_HINTS = re.compile(
    r"masterpiece|best quality|score_9|score_8|absurdres|high resolution|refined details|"
    r"nsfw|1girl|1boy|solo|ultra detailed|high contrast|amazing quality|"
    r"anime coloring|sharp focus|good anatomy|good shading|@\w+ \w+"
)


def backfill_story_tags(messages: list) -> tuple[list, dict]:
    """给无标签的旧助手文本消息回填剧情/状态/生成标签；返回 (新列表, 统计)。纯函数无 IO。"""
    changed = story = status = generate = 0
    out: list = []
    for item in messages:
        if not isinstance(item, dict):
            out.append(item)
            continue
        if item.get("route"):
            out.append(item)  # 已标签化，跳过
            continue
        role = item.get("role")
        text = str(item.get("text") or "").strip()
        if role != "assistant" or not text:
            out.append(item)
            continue
        if (item.get("system") or item.get("workflow") or item.get("inspiration")
                or item.get("portsPlan") or item.get("promptApproval") or item.get("routeChoice")
                or item.get("image") or item.get("video") or item.get("audio")):
            out.append(item)  # 有显式非剧情标记，不动
            continue
        if _STATUS_HINTS.search(text):
            out.append({**item, "system": True})
            status += 1
        elif _PROMPT_HINTS.search(text):
            out.append({**item, "route": "generate"})
            generate += 1
        else:
            out.append({**item, "route": "roleplay"})
            story += 1
        changed += 1
    return out, {"changed": changed, "story": story, "status": status, "generate": generate}


def assistant_message(mid: str, text: str, **fields) -> dict:
    """前后端共用的 assistant 消息形状唯一构造入口。

    键序固定 id/role/text/...，额外字段(image/interrupted/inspiration 等)按传入顺序附加。
    generation_store 等其它模块也应经此构造，避免各处手拼 dict 导致形状漂移。
    """
    return {"id": mid, "role": "assistant", "text": text, **fields}


def user_message(mid: str, text: str, images: list[str] | None = None) -> dict:
    """构造可由前端直接恢复的用户消息；图片仍走 ChatMessage.parts。"""
    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})
    parts.extend({"type": "image", "url": url} for url in (images or []) if url)
    message = {"id": mid, "role": "user", "text": text}
    if parts:
        message["parts"] = parts
    return message


def ensure_user_message(
    thread_id: str, mid: str, text: str, images: list[str] | None = None,
    *, before_id: str = "",
) -> bool:
    """模型启动前确保用户输入已进入权威快照；已有前端富消息时保持原样。"""
    if not mid or (not text and not images):
        return False
    with _thread_lock(thread_id):
        items = load(thread_id)
        if any(isinstance(item, dict) and item.get("id") == mid for item in items):
            return True
        message = user_message(mid, text, images)
        before_index = next((
            index for index, item in enumerate(items)
            if isinstance(item, dict) and item.get("id") == before_id
        ), -1)
        if before_index >= 0:
            items.insert(before_index, message)
        else:
            items.append(message)
        _save_unlocked(thread_id, items)
        return True


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
    """合并更新一条消息的结构化字段，不覆盖已有正文和媒体。

    若 message_id 在快照中不存在则静默返回（不追加幽灵消息）。
    """
    if not mid:
        return
    with _thread_lock(thread_id):
        items = load(thread_id)
        for i, item in enumerate(items):
            if isinstance(item, dict) and item.get("id") == mid:
                items[i] = {**item, **fields}
                _save_unlocked(thread_id, items)
                return
        # 未知 message_id → 静默返回，不追加不可见消息


def select_inspiration(thread_id: str, message_id: str, urls: list[str]) -> dict[str, object]:
    """在快照中更新灵感卡选中项：只记录选中 URL 列表，不存全量搜索结果。

    校验：只接受 http(s) URL；过滤非法协议。
    若 message_id 在快照中不存在则静默返回（不追加幽灵消息）。
    返回 {"ok": True, "selected": urls}。
    """
    safe = [u.strip() for u in urls if (u or "").strip().startswith(("http://", "https://"))]
    inspiration: dict = {}
    for item in load(thread_id):
        if isinstance(item, dict) and item.get("id") == message_id \
                and isinstance(item.get("inspiration"), dict):
            inspiration = dict(item["inspiration"])
            break
    else:
        # 未找到目标消息 → 不追加幽灵消息，静默返回
        return {"ok": True, "selected": safe}
    inspiration["selected"] = safe
    merge_fields(thread_id, message_id, inspiration=inspiration)
    return {"ok": True, "selected": safe}


def resolve_media_slot(thread_id: str, message_id: str, slot_id: str, url: str,
                       *, media_type: str = "image",
                       regeneration: dict | None = None,
                       derived_from: list | None = None) -> bool:
    """把指定消息的异步媒体槽原位替换为图片/视频；目标不存在时绝不追加新消息。
    derived_from 为派生链弱引用（M2.1 视频槽记首帧底图槽），随 ready part 落盘。"""
    if not message_id or not slot_id or not url:
        return False
    kind = "video" if media_type == "video" else ("audio" if media_type == "audio" else "image")
    with _thread_lock(thread_id):
        items = load(thread_id)
        for item_index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("id") != message_id:
                continue
            parts = item.get("parts") or []
            for part_index, part in enumerate(parts):
                if (isinstance(part, dict) and part.get("type") in ("media-slot", "image", "video", "audio")
                        and part.get("slotId") == slot_id):
                    ready = {"type": kind, "url": url, "slotId": slot_id, "status": "ready"}
                    # 音频分条元数据（角色名/序号/媒体类型提示）随槽位保留：刷新恢复后
                    # 气泡与画布楼层仍能按角色分条展示，不因服务端回填而丢失标签。
                    if isinstance(part, dict) and part.get("kind") == "audio":
                        for key in ("kind", "speaker", "seq", "total"):
                            if part.get(key) is not None:
                                ready[key] = part[key]
                    if regeneration:
                        ready["regeneration"] = regeneration
                    if derived_from:
                        ready["derivedFrom"] = derived_from
                    next_parts = list(parts)
                    next_parts[part_index] = ready
                    items[item_index] = {**item, "parts": next_parts}
                    _save_unlocked(thread_id, items)
                    return True
            return False
    return False


def bind_media_slot_prompt(thread_id: str, message_id: str, slot_id: str,
                           prompt_id: str) -> bool:
    """ComfyUI 接受任务后立刻把 prompt_id 写入快照，供刷新恢复继续轮询。"""
    if not message_id or not slot_id or not prompt_id:
        return False
    with _thread_lock(thread_id):
        items = load(thread_id)
        for item_index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("id") != message_id:
                continue
            parts = item.get("parts") or []
            for part_index, part in enumerate(parts):
                if (isinstance(part, dict) and part.get("type") == "media-slot"
                        and part.get("slotId") == slot_id):
                    next_parts = list(parts)
                    submitted = {**part, "promptId": prompt_id}
                    submitted.pop("submissionClaim", None)
                    next_parts[part_index] = submitted
                    items[item_index] = {**item, "parts": next_parts}
                    _save_unlocked(thread_id, items)
                    return True
            return False
    return False


def claim_media_slot_submission(thread_id: str, message_id: str, slot_id: str) -> bool:
    """ComfyUI 提交前原子认领 pending 槽；已认领/已提交/已完成目标拒绝。"""
    if not message_id or not slot_id:
        return False
    with _thread_lock(thread_id):
        items = load(thread_id)
        for item_index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("id") != message_id:
                continue
            parts = item.get("parts") or []
            for part_index, part in enumerate(parts):
                if not (isinstance(part, dict) and part.get("type") == "media-slot"
                        and part.get("slotId") == slot_id):
                    continue
                if part.get("promptId") or part.get("submissionClaim"):
                    return False
                next_parts = list(parts)
                next_parts[part_index] = {**part, "submissionClaim": True}
                items[item_index] = {**item, "parts": next_parts}
                _save_unlocked(thread_id, items)
                return True
            return False
    return False


def remove_media_slot(thread_id: str, message_id: str, slot_id: str) -> bool:
    """删除失败的异步媒体槽并合并相邻正文；目标不存在时不改快照。"""
    if not message_id or not slot_id:
        return False
    with _thread_lock(thread_id):
        items = load(thread_id)
        for item_index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("id") != message_id:
                continue
            found = False
            next_parts: list[dict] = []
            for part in item.get("parts") or []:
                if (isinstance(part, dict) and part.get("type") == "media-slot"
                        and part.get("slotId") == slot_id):
                    found = True
                    continue
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    text = str(part.get("text") or "")
                    if not text:
                        continue
                    if next_parts and next_parts[-1].get("type") == "text":
                        next_parts[-1]["text"] = str(next_parts[-1].get("text") or "") + text
                    else:
                        next_parts.append({**part, "text": text})
                else:
                    next_parts.append(part)
            if not found:
                return False
            updated = {**item}
            if next_parts:
                updated["parts"] = next_parts
            else:
                updated.pop("parts", None)
            items[item_index] = updated
            _save_unlocked(thread_id, items)
            return True
    return False


def append_image(thread_id: str, mid: str, image_url: str, text: str = "") -> None:
    """按 mid upsert 一条带图 assistant 消息（mid 同时回传前端，重开不重复）。"""
    upsert(thread_id, _assistant_message(mid, text or "", image=image_url))


def append_text(thread_id: str, mid: str, text: str) -> None:
    """按 mid 更新正文并保留已有媒体槽/审批等结构化字段。"""
    if not (text or "").strip():
        return
    with _thread_lock(thread_id):
        items = load(thread_id)
        for i, item in enumerate(items):
            if isinstance(item, dict) and item.get("id") == mid:
                items[i] = {**item, "role": "assistant", "text": text}
                _save_unlocked(thread_id, items)
                return
        items.append(_assistant_message(mid, text))
        _save_unlocked(thread_id, items)


def ensure_media_slot(thread_id: str, message_id: str, slot_id: str,
                      *, offset: int | None = None) -> bool:
    """确保指定正文含稳定媒体槽；重复事件幂等，消息不存在时失败关闭。"""
    if not message_id or not slot_id:
        return False
    with _thread_lock(thread_id):
        items = load(thread_id)
        for index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("id") != message_id:
                continue
            existing = item.get("parts") or []
            if any(isinstance(part, dict) and part.get("type") == "media-slot"
                   and part.get("slotId") == slot_id for part in existing):
                return True
            text = str(item.get("text") or "")
            position = len(text) if offset is None else max(0, min(len(text), int(offset)))
            parts: list[dict] = []
            if position:
                parts.append({"type": "text", "text": text[:position]})
            parts.append({"type": "media-slot", "slotId": slot_id, "status": "pending"})
            if position < len(text):
                parts.append({"type": "text", "text": text[position:]})
            items[index] = {**item, "parts": parts}
            _save_unlocked(thread_id, items)
            return True
    return False


def append_media_slot(thread_id: str, message_id: str, slot_id: str, *,
                      kind: str = "audio", speaker: str | None = None,
                      seq: int | None = None, total: int | None = None) -> bool:
    """向指定消息末尾追加一个 pending 媒体槽，保留已有 parts（不覆盖图片/视频槽）。

    与 ensure_media_slot 的差异：音频对白槽在正文最终化之后才由前端逐角色补写，
    消息可能已含图片槽（同轮先出图），必须幂等追加而非按 offset 重建 parts。
    kind/speaker/seq/total 随槽位落盘，供刷新恢复后气泡与画布楼层按角色分条展示。
    """
    if not message_id or not slot_id:
        return False
    with _thread_lock(thread_id):
        items = load(thread_id)
        for index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("id") != message_id:
                continue
            parts = list(item.get("parts") or [])
            if any(isinstance(part, dict) and part.get("type") == "media-slot"
                   and part.get("slotId") == slot_id for part in parts):
                return True
            # 纯文本消息还没有 parts：先补一条完整正文 text part，避免只渲染音频槽丢失正文
            if not parts and item.get("text"):
                parts = [{"type": "text", "text": item["text"]}]
            slot: dict = {"type": "media-slot", "slotId": slot_id, "status": "pending"}
            if kind:
                slot["kind"] = kind
            if speaker is not None:
                slot["speaker"] = speaker
            if seq is not None:
                slot["seq"] = seq
            if total is not None:
                slot["total"] = total
            parts.append(slot)
            items[index] = {**item, "parts": parts}
            _save_unlocked(thread_id, items)
            return True
    return False


def append_ready_part(thread_id: str, message_id: str, part: dict) -> bool:
    """向指定消息末尾追加一个 ready 媒体 part（如合并音频结果），保留已有 parts。

    幂等：同 slotId 的 part 已存在时直接返回 True（合并结果落盘不重复追加）。
    """
    if not message_id or not isinstance(part, dict) or not part.get("slotId"):
        return False
    slot_id = str(part["slotId"])
    with _thread_lock(thread_id):
        items = load(thread_id)
        for index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("id") != message_id:
                continue
            parts = list(item.get("parts") or [])
            if any(isinstance(p, dict) and p.get("slotId") == slot_id for p in parts):
                return True
            # 纯文本消息还没有 parts：先补一条完整正文 text part，避免媒体 part 顶掉正文
            if not parts and item.get("text"):
                parts = [{"type": "text", "text": item["text"]}]
            parts.append(part)
            items[index] = {**item, "parts": parts}
            _save_unlocked(thread_id, items)
            return True
    return False


def remove_parts_matching(thread_id: str, message_id: str,
                          predicate) -> bool:
    """删除消息中满足条件的 part（如合并后移除分条音频），保留其余 part 与正文。

    predicate 只作用于 dict part；返回是否实际删除过。删除后若 parts 为空则清空字段。
    """
    if not message_id:
        return False
    with _thread_lock(thread_id):
        items = load(thread_id)
        for index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("id") != message_id:
                continue
            original = item.get("parts") or []
            next_parts = [p for p in original
                          if not (isinstance(p, dict) and predicate(p))]
            if len(next_parts) == len(original):
                return False
            updated = {**item}
            if next_parts:
                updated["parts"] = next_parts
            else:
                updated.pop("parts", None)
            items[index] = updated
            _save_unlocked(thread_id, items)
            return True
    return False
