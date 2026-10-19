"""ComfyUI 在途登记（2026-10-19）：track / finish / TTL / 幂等。

这份表是为了修「换浏览器 / 清缓存后在跑的图探测不到」——它的正确性直接决定
后台活动面板会不会显示幽灵任务，故重点覆盖：去重、终态清除、30 分钟 TTL。
"""
from __future__ import annotations

import time

import pytest

from app.services import comfy_pending, task_progress_store


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    """写盘落到 tmp（禁止污染生产 data/task_progress），并从空表开始。"""
    monkeypatch.setattr(task_progress_store, "STORE_DIR", tmp_path)
    monkeypatch.setattr(comfy_pending, "_TASKS", {})
    yield


def test_track_and_snapshot():
    created = time.time() - 10
    item = comfy_pending.track("p1", "repo-a", created)
    assert item["thread_id"] == "repo-a"
    assert item["created_at"] == created
    assert [entry["prompt_id"] for entry in comfy_pending.snapshot()] == ["p1"]


def test_track_is_idempotent_and_overwrites_thread():
    comfy_pending.track("p1", "old-repo")
    comfy_pending.track("p1", "new-repo")
    items = comfy_pending.snapshot()
    assert len(items) == 1
    assert items[0]["thread_id"] == "new-repo"


def test_blank_prompt_rejected():
    with pytest.raises(ValueError):
        comfy_pending.track("  ", "repo-a")


def test_missing_created_at_defaults_to_now():
    item = comfy_pending.track("p1", "repo-a")
    assert item["created_at"] <= time.time()


def test_millisecond_created_at_is_coerced_to_seconds():
    """前端误传毫秒（1.7e12）时必须纠正，否则 TTL 永远不过期 ⇒ 面板出现僵尸条目。"""
    now = time.time()
    item = comfy_pending.track("p1", "repo-a", now * 1000)
    assert abs(item["created_at"] - now) < 5


def test_bogus_created_at_falls_back_to_now():
    now = time.time()
    assert abs(comfy_pending.track("a", "r", 0)["created_at"] - now) < 5
    assert abs(comfy_pending.track("b", "r", now + 3600)["created_at"] - now) < 5


def test_finish_removes_and_is_idempotent():
    comfy_pending.track("p1", "repo-a")
    assert comfy_pending.finish("p1") is True
    assert comfy_pending.finish("p1") is False
    assert comfy_pending.snapshot() == []


def test_finish_missing_prompt_is_safe():
    assert comfy_pending.finish("") is False
    assert comfy_pending.finish("never-tracked") is False


def test_snapshot_prunes_expired_entries():
    now = time.time()
    comfy_pending.track("old", "repo-a", now - comfy_pending.TTL_MS / 1000 - 5)
    comfy_pending.track("fresh", "repo-a", now - 5)
    assert [entry["prompt_id"] for entry in comfy_pending.snapshot()] == ["fresh"]


def test_snapshot_sorted_newest_first():
    now = time.time()
    comfy_pending.track("a", "repo-a", now - 30)
    comfy_pending.track("b", "repo-a", now - 10)
    assert [entry["prompt_id"] for entry in comfy_pending.snapshot()] == ["b", "a"]


def test_persisted_across_reload(monkeypatch):
    comfy_pending.track("p1", "repo-a")
    # 模拟进程重启：从磁盘重新加载
    reloaded = task_progress_store.load("comfy-pending")
    assert "p1" in reloaded
    assert reloaded["p1"]["thread_id"] == "repo-a"
