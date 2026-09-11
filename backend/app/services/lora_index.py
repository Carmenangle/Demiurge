"""LoRA 触发词编排层：扫磁盘同步 + SQLite 增删改查 + 向量库镜像。

与节点知识库的两处关键差异：
1. 节点库同步读 ComfyUI 的 /object_info（HTTP）；LoRA 必须扫磁盘，因为 LoraLoader 的
   lora_name 枚举拿不到触发词，触发词在文件里。
2. 节点库纯向量库；LoRA 主存 SQLite（精确查），向量库只作镜像。见 db.py 表注释。

沿用节点库的两条约定：进程内单例进度 + daemon 线程；`source == "manual"` 的条目同步时
不覆盖（用户手工校正过的内容优先于自动提取）。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from app.db import get_connection
from app.services import lora_scan, lora_store
from app.services.rag_backend import EmbedConfig

logger = logging.getLogger(__name__)
DEFAULT_SUGGESTED_WEIGHT = 0.8

# 同步进度（进程内单例，同一时刻只允许一个同步任务）：
#   running 是否在跑，done/total 已处理/总文件数，current 当前文件名，
#   added 新增，updated 自动更新，kept 保留手填未动，missing 标记消失，error/finished。
_PROGRESS: dict = {"running": False, "done": 0, "total": 0, "current": "",
                   "added": 0, "updated": 0, "kept": 0, "missing": 0,
                   "error": "", "finished": False}
_LOCK = threading.Lock()

def _set_progress(**kw) -> None:
    with _LOCK:
        _PROGRESS.update(kw)


def sync_progress() -> dict:
    """当前同步进度快照，供前端轮询。"""
    with _LOCK:
        return dict(_PROGRESS)


def _mirror_async(fn, *args) -> None:
    """把向量库镜像写入丢到后台线程。

    镜像只服务检索，主存是 SQLite —— 它没有理由挡在 HTTP 响应前面。同步写的话每次
    保存都要等 embedding 接口一次网络往返（实测手填要等 3-5 秒）。
    lora_store 内部已吞异常并记日志，这里失败同样不影响主流程。
    """
    threading.Thread(target=fn, args=args, daemon=True).start()


def _row_to_item(row) -> dict:
    return {
        "lora_name": row["lora_name"],
        "triggers": split_words(row["triggers"]),
        "note": row["note"],
        "suggested_weight": float(row["suggested_weight"]),
        "suggested_prompt": row["suggested_prompt"],
        "source": row["source"],
        "missing": bool(row["missing"]),
        "updated_at": row["updated_at"],
    }


def split_words(value: str) -> list[str]:
    """逗号分隔字符串 → 列表。复用 lora_service 的规范化，保持全项目一致。"""
    from app.services.lora_service import normalize_trigger_words
    return normalize_trigger_words(value or "")


def list_items() -> list[dict]:
    """列出全部条目，缺失文件排在最后、其余按名字排序。"""
    with get_connection() as conn:
        rows = conn.execute(
            "select * from lora_triggers order by missing asc, lora_name asc"
        ).fetchall()
    return [_row_to_item(r) for r in rows]


def get_triggers_map() -> dict[str, list[str]]:
    """{lora_name: 触发词}，供编排注入按文件名精确查。跳过缺失文件与空触发词。"""
    with get_connection() as conn:
        rows = conn.execute(
            "select lora_name, triggers from lora_triggers where missing = 0"
        ).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        words = split_words(r["triggers"])
        if words:
            out[r["lora_name"]] = words
    return out


def get_trigger_status_map() -> dict[str, str]:
    """返回每个现存 LoRA 的触发词确认状态。

    configured：有要机械前置的触发词；not_required：用户手动保存了空词，明确
    确认该 LoRA 无需触发词；unconfirmed：自动提取为空，尚未由用户确认。
    """
    with get_connection() as conn:
        rows = conn.execute(
            "select lora_name, triggers, source from lora_triggers where missing = 0"
        ).fetchall()
    out: dict[str, str] = {}
    for row in rows:
        if split_words(row["triggers"]):
            out[row["lora_name"]] = "configured"
        elif row["source"] == "manual":
            out[row["lora_name"]] = "not_required"
        else:
            out[row["lora_name"]] = "unconfirmed"
    return out


def get_lora_data_map() -> dict[str, dict]:
    """现存 LoRA 的触发词状态与建议权重，供模型选择器按文件名精确绑定。"""
    with get_connection() as conn:
        rows = conn.execute(
            "select lora_name, triggers, source, suggested_weight "
            "from lora_triggers where missing = 0"
        ).fetchall()
    out: dict[str, dict] = {}
    for row in rows:
        status = "configured" if split_words(row["triggers"]) else (
            "not_required" if row["source"] == "manual" else "unconfirmed"
        )
        out[row["lora_name"]] = {
            "trigger_status": status,
            "suggested_weight": float(row["suggested_weight"]),
        }
    return out


def normalize_suggested_weight(value: object) -> float:
    try:
        weight = float(value)
    except (TypeError, ValueError):
        return DEFAULT_SUGGESTED_WEIGHT
    if weight != weight:  # NaN
        return DEFAULT_SUGGESTED_WEIGHT
    return max(0.0, min(2.0, weight))


def save_item(lora_name: str, triggers: list[str], note: str = "",
              cfg: EmbedConfig | None = None, *, suggested_weight: float = DEFAULT_SUGGESTED_WEIGHT,
              suggested_prompt: str = "") -> dict:
    """用户手填/校正一条。固定标 source='manual'，此后同步不再覆盖它。"""
    # 再过一遍规范化：前端可能整条塞进来（如 `线条动漫、平涂`），
    # 前端切错不该成为最终结果 —— 后端才是权威。
    words = split_words(", ".join(t for t in triggers if t and t.strip()))
    joined = ", ".join(words)
    weight = normalize_suggested_weight(suggested_weight)
    author_prompt = (suggested_prompt or "").strip()
    now = int(time.time())
    with get_connection() as conn:
        conn.execute(
            """insert into lora_triggers
                   (lora_name, triggers, note, suggested_weight, suggested_prompt,
                    source, missing, updated_at)
               values (?, ?, ?, ?, ?, 'manual', 0, ?)
               on conflict(lora_name) do update set
                   triggers = excluded.triggers, note = excluded.note,
                   suggested_weight = excluded.suggested_weight,
                   suggested_prompt = excluded.suggested_prompt,
                   source = 'manual', updated_at = excluded.updated_at""",
            (lora_name, joined, note, weight, author_prompt, now),
        )
    if cfg is not None:
        _mirror_async(lora_store.index_lora, cfg, lora_name, words, note, "manual")
    return {"lora_name": lora_name, "triggers": words, "note": note,
            "suggested_weight": weight,
            "suggested_prompt": author_prompt,
            "source": "manual", "missing": False, "updated_at": now}


def delete_item(lora_name: str, cfg: EmbedConfig | None = None) -> None:
    """删除一条（含向量库镜像）。SQLite 同步删，镜像后台删。"""
    with get_connection() as conn:
        conn.execute("delete from lora_triggers where lora_name = ?", (lora_name,))
    if cfg is not None:
        _mirror_async(lora_store.delete_lora, cfg, lora_name)


def _existing_rows() -> dict[str, dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "select lora_name, triggers, note, source, missing from lora_triggers"
        ).fetchall()
    return {r["lora_name"]: dict(r) for r in rows}


def _do_sync(loras_dir: Path, names: list[str], cfg: EmbedConfig | None,
             full: bool) -> None:
    """逐文件提触发词并落库。full=True 时连 manual 条目一并重提（用户显式要求重建）。"""
    existing = _existing_rows()
    added = updated = kept = 0
    now = int(time.time())

    for i, name in enumerate(names):
        _set_progress(done=i, current=name)
        old = existing.get(name)
        # 手工校正过的不动，只把可能残留的 missing 标记清掉
        if old and old["source"] == "manual" and not full:
            if old["missing"]:
                with get_connection() as conn:
                    conn.execute(
                        "update lora_triggers set missing = 0 where lora_name = ?", (name,)
                    )
            kept += 1
            continue

        words, source = lora_scan.detect_triggers(loras_dir / name)
        joined = ", ".join(words)
        with get_connection() as conn:
            conn.execute(
                """insert into lora_triggers
                       (lora_name, triggers, note, source, missing, updated_at)
                   values (?, ?, '', ?, 0, ?)
                   on conflict(lora_name) do update set
                       triggers = excluded.triggers, source = excluded.source,
                       missing = 0, updated_at = excluded.updated_at""",
                (name, joined, source, now),
            )
        if old is None:
            added += 1
        else:
            updated += 1
        if cfg is not None and words:
            lora_store.index_lora(cfg, name, words, old["note"] if old else "", source)

    # 磁盘上已消失的：只标记不删，避免抹掉用户手填的内容
    gone = [n for n in existing if n not in set(names)]
    if gone:
        with get_connection() as conn:
            conn.executemany(
                "update lora_triggers set missing = 1 where lora_name = ?",
                [(n,) for n in gone],
            )

    _set_progress(done=len(names), added=added, updated=updated, kept=kept,
                  missing=len(gone))


def resolve_loras_dir(models_dir: str) -> Path:
    """models 目录 → loras 子目录。对齐 model_downloader.TYPE_DIRS 的 'lora' -> 'loras'。"""
    return Path(models_dir) / "loras"


def start_sync(models_dir: str, cfg: EmbedConfig | None = None,
               full: bool = False) -> dict:
    """启动后台同步。先同步扫目录（拿到总数好让前端显示 x/total），再开线程逐个提取。

    已有任务在跑时拒绝重复启动。目录不存在时不抛错，返回 total=0 —— 用户可能还没设路径。
    """
    with _LOCK:
        if _PROGRESS["running"]:
            return {"total": _PROGRESS["total"], "already_running": True}

    loras_dir = resolve_loras_dir(models_dir)
    names = lora_scan.scan_lora_dir(loras_dir)
    total = len(names)
    _set_progress(running=True, done=0, total=total, current="",
                  added=0, updated=0, kept=0, missing=0, error="", finished=False)

    def _run() -> None:
        try:
            _do_sync(loras_dir, names, cfg, full)
            _set_progress(running=False, finished=True, current="")
        except Exception as e:  # noqa: BLE001 后台线程兜底，错误进度里报
            logger.warning("LoRA 触发词同步失败", exc_info=True)
            _set_progress(running=False, finished=True, error=str(e), current="")

    threading.Thread(target=_run, daemon=True).start()
    return {"total": total, "already_running": False}
