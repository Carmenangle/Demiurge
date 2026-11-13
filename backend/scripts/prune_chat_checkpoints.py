"""存量聊天 checkpoint 瘦身（2026-11-12 P6-C）。

checkpoint 每轮保存整段 state，用户上传的 data URI 大图会被重复写进每个版本，
实测单线程可到 3 GB。本脚本做三件事（必须停应用后运行，checkpoints.db 单写者）：

1. 读出每个线程的最新历史，经 `chat_memory.rewrite_history` 重写一遍
   （写入时 data URI 自动外置成文件 + local-view 地址，不再内联）；
2. 每线程只保留最新 `--keep` 个 checkpoint（旧版本只是 LangGraph 时间线）；
3. `--vacuum` 时回收磁盘。

用法：
  .\\.venv\\Scripts\\python.exe scripts/prune_chat_checkpoints.py --keep 3 --vacuum
先看现状可加 `--dry-run`。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import CHECKPOINT_DB  # noqa: E402
from app.services import chat_memory  # noqa: E402


def _stats() -> list[tuple[str, int, int]]:
    conn = sqlite3.connect(str(CHECKPOINT_DB))
    try:
        rows = conn.execute(
            "SELECT thread_id, COUNT(*), SUM(LENGTH(checkpoint)) "
            "FROM checkpoints GROUP BY thread_id ORDER BY 3 DESC",
        ).fetchall()
    finally:
        conn.close()
    return [(str(thread), int(count), int(size or 0)) for thread, count, size in rows]


def _db_size() -> int:
    try:
        return CHECKPOINT_DB.stat().st_size
    except OSError:
        return 0


def _vacuum() -> None:
    """关掉 chat_memory 单例连接后 VACUUM，避免持有事务导致 database is locked。"""
    saver = chat_memory._saver
    if saver is not None:
        try:
            saver.conn.close()
        finally:
            chat_memory._saver = None
    conn = sqlite3.connect(str(CHECKPOINT_DB), isolation_level=None)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="聊天 checkpoint 存量瘦身")
    parser.add_argument("--keep", type=int, default=3, help="每线程保留的最新 checkpoint 数")
    parser.add_argument("--vacuum", action="store_true", help="完成后 VACUUM 回收磁盘")
    parser.add_argument("--dry-run", action="store_true", help="只打印现状，不写入")
    args = parser.parse_args()

    before = _db_size()
    stats = _stats()
    print(f"db={CHECKPOINT_DB} size={before / 1024 / 1024:.1f} MiB threads={len(stats)}")
    for thread, count, size in stats:
        print(f"  {thread}: checkpoints={count} bytes={size / 1024 / 1024:.1f} MiB")
    if args.dry_run:
        return 0

    for thread, _count, _size in stats:
        history = chat_memory.get_history(thread)
        if history:
            chat_memory.rewrite_history(thread, history)
        removed = chat_memory.prune_thread(thread, keep=args.keep)
        print(f"rewritten {thread}: messages={len(history)} pruned={removed}")

    if args.vacuum:
        _vacuum()

    after = _db_size()
    print(f"done: {before / 1024 / 1024:.1f} -> {after / 1024 / 1024:.1f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
