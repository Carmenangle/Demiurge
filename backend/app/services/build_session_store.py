"""AI 搭工作流的「搭建会话」持久化：进度保存 + 多开。

痛点：AIBuildView 的进度（左栏对话 msgs + 右栏画布图）原本全在内存，切走页面/刷新/
重启 ComfyUI（装完新节点必重启）都会丢——尤其顾问模式推荐装节点后重启，搭到一半全没。

一个会话 = 一次搭建任务：{id, name, msgs[], graph(API格式), skeleton_id, updated_at}。
- 进度保存：msgs 和当前画布 graph 都落盘，重启后重新 load graph 回画布 + 恢复对话。
- 多开：多个命名会话并存，可切换/新建/删除，互不干扰。
每会话一个 JSON 文件，沿用 chat_snapshot 的落盘思路（原子写）。
"""
import json
import os
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

from app.config import DATA_DIR
from app.services.pathnames import safe_seg

SESS_DIR = DATA_DIR / "build_sessions"
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _path(sess_id: str):
    return SESS_DIR / f"{safe_seg(sess_id, 'x', strip=False)}.json"


@contextmanager
def _session_lock(sess_id: str):
    """同一会话的读改写在多线程、多进程间串行化。"""
    key = str(_path(sess_id))
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    with thread_lock:
        SESS_DIR.mkdir(parents=True, exist_ok=True)
        lock_path = _path(sess_id).with_suffix(".lock")
        with lock_path.open("a+b") as lock_file:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                while True:
                    try:
                        getattr(msvcrt, "locking")(
                            lock_file.fileno(), getattr(msvcrt, "LK_LOCK"), 1
                        )
                        break
                    except OSError:
                        time.sleep(0.05)
            else:
                import importlib
                fcntl = importlib.import_module("fcntl")
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                lock_file.seek(0)
                if os.name == "nt":
                    getattr(msvcrt, "locking")(
                        lock_file.fileno(), getattr(msvcrt, "LK_UNLCK"), 1
                    )
                else:
                    import importlib
                    fcntl = importlib.import_module("fcntl")
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _write_session(data: dict) -> None:
    p = _path(data["id"])
    tmp = p.with_name(f"{p.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    finally:
        if tmp.exists():
            tmp.unlink()


def list_sessions() -> list[dict]:
    """列出全部会话的元信息（不含 msgs/graph 大字段），按更新时间倒序。"""
    if not SESS_DIR.exists():
        return []
    out = []
    for p in SESS_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({
            "id": d.get("id", p.stem),
            "name": d.get("name", "未命名"),
            "updated_at": d.get("updated_at", 0),
            "node_count": len(d.get("graph", {})) if isinstance(d.get("graph"), dict) else 0,
            "msg_count": len(d.get("msgs", [])),
        })
    out.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
    return out


def get_session(sess_id: str) -> dict | None:
    """读取单个会话完整内容（含 msgs + graph），不存在返回 None。"""
    p = _path(sess_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_session(sess_id: str, name: str, msgs: list, graph: dict, skeleton_id: str = "") -> dict:
    """保存/覆盖会话。sess_id 为空则新建一个 id。返回会话元信息（含 id）。"""
    SESS_DIR.mkdir(parents=True, exist_ok=True)
    sid = (sess_id or "").strip() or uuid4().hex
    with _session_lock(sid):
        incoming_msgs = list(msgs or [])
        incoming_graph = graph or {}
        existing = get_session(sid)
        if existing:
            incoming_task_ids = {
                msg.get("task_id") for msg in incoming_msgs if isinstance(msg, dict)
            }
            unseen_task_msgs = [
                msg for msg in existing.get("msgs", [])
                if isinstance(msg, dict) and msg.get("task_id")
                and msg.get("task_id") not in incoming_task_ids
            ]
            incoming_msgs.extend(unseen_task_msgs)
            if any(msg.get("task_graph_applied") for msg in unseen_task_msgs):
                incoming_graph = existing.get("graph") or incoming_graph
        data = {
            "id": sid,
            "name": (name or "").strip() or "未命名工作流",
            "msgs": incoming_msgs,
            "graph": incoming_graph,
            "skeleton_id": skeleton_id or "",
            "updated_at": int(time.time() * 1000),
        }
        _write_session(data)
    return {"id": sid, "name": data["name"], "updated_at": data["updated_at"]}


def apply_task_result(session_id: str, task_id: str, mode: str, need: str,
                      result: dict | None = None, error: str = "") -> bool:
    """把后台任务结果幂等写入会话；会话已删除时不重新创建。"""
    with _session_lock(session_id):
        session = get_session(session_id)
        if session is None:
            return False
        raw_msgs = session.get("msgs")
        msgs: list[object] = raw_msgs if isinstance(raw_msgs, list) else []
        if any(isinstance(msg, dict) and msg.get("task_id") == task_id for msg in msgs):
            return True

        result = result or {}
        msg: dict[str, object] = {
            "id": uuid4().hex,
            "task_id": task_id,
            "role": "assistant",
        }
        if error:
            msg["text"] = f"请求失败：{error}"
        elif mode == "plan":
            plan = str(result.get("plan", "")).strip()
            msg.update({
                "text": plan,
                "pendingNeed": (
                    f"{need.strip()}\n\n【已和用户确认的搭建方案，请严格照此搭建】\n{plan}"
                ),
                "planText": plan,
                "planOriginalNeed": need,
            })
        else:
            missing = result.get("missing_nodes") or []
            alternatives = result.get("alternatives") or {}
            graph = result.get("graph")
            if result.get("ok") and isinstance(graph, dict) and graph:
                session["graph"] = graph
                msg["text"] = "已把工作流写入右侧画布，你可以继续发送修改要求。"
                msg["task_graph_applied"] = True
            else:
                errors = result.get("errors") or []
                detail = "\n".join(str(item) for item in errors) or "未知错误"
                msg["text"] = f"没能生成合法工作流：\n{detail}"
            if missing:
                msg["missingNodes"] = missing
            if alternatives:
                msg["alternatives"] = alternatives
            msg["retryNeed"] = need

        msgs.append(msg)
        session["msgs"] = msgs
        session["updated_at"] = int(time.time() * 1000)
        _write_session(session)
        return True


def delete_session(sess_id: str) -> bool:
    """删除会话文件。不存在返回 False。"""
    with _session_lock(sess_id):
        p = _path(sess_id)
        if not p.exists():
            return False
        p.unlink()
        return True
