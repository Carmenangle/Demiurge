"""后台活动投影（2026-10-19）：下载 / 节点 / LoRA / 对话后置维护 → 统一活动条目。

本模块只读投影，测试重点：
- 各源快照字段能正确映射成统一结构（含 title 单一属主）；
- 已结束超过保留期的下载不再展示；
- 空快照不产生条目（不能凭空给面板灌噪音）。
"""
from __future__ import annotations

import time

import pytest

from app.services import (
    activity_feed,
    agency_maintenance,
    lora_index,
    model_downloader,
    node_update,
    shell_delivery,
)


@pytest.fixture(autouse=True)
def _isolate_sources(monkeypatch):
    """默认各源都空闲；各用例只覆盖自己关心的那一个，避免真实进程状态泄漏进断言。"""
    monkeypatch.setattr(model_downloader, "list_tasks", lambda: [])
    monkeypatch.setattr(node_update, "progress", lambda: {"running": False})
    monkeypatch.setattr(lora_index, "sync_progress", lambda: {"running": False})
    monkeypatch.setattr(agency_maintenance, "maintenance_snapshot", lambda: [])
    # 补齐账本来自**另一个进程**写在 DATA_DIR 下的文件：不钉住它，本机真有一份在跑就全乱。
    monkeypatch.setattr(shell_delivery, "read_ledger", lambda now=None: None)


def test_download_items_map_bytes_and_kind(monkeypatch):
    now = time.time()
    monkeypatch.setattr(model_downloader, "list_tasks", lambda: [{
        "id": "t1", "status": "downloading", "kind": "model", "name": "flux1-dev.safetensors",
        "downloaded": 5 * 1024 * 1024, "total": 10 * 1024 * 1024, "speed_bps": 2 * 1024 * 1024,
        "filename": "flux1-dev.safetensors", "error": "", "created": now, "updated_at": now,
    }])
    items = activity_feed.snapshot()

    assert len(items) == 1
    item = items[0]
    assert item["kind"] == "model_download" and item["title"] == "模型下载"
    assert item["status"] == "running"
    assert item["progress"] == "5.0 MB / 10.0 MB · 2.0 MB/s"
    assert item["target"] == "models"


def test_workflow_download_distinguished(monkeypatch):
    now = time.time()
    monkeypatch.setattr(model_downloader, "list_tasks", lambda: [{
        "id": "t2", "status": "done", "kind": "workflow", "name": "mini.safetensors",
        "downloaded": 1024, "total": 1024, "filename": "mini.json",
        "created": now, "updated_at": now,
    }])
    items = activity_feed.snapshot()

    assert items[0]["kind"] == "workflow_download"
    assert items[0]["title"] == "工作流下载"
    assert items[0]["status"] == "done"


def test_finished_download_expires_after_keep_window(monkeypatch):
    stale = time.time() - activity_feed._KEEP_DONE_S - 5
    monkeypatch.setattr(model_downloader, "list_tasks", lambda: [{
        "id": "t3", "status": "done", "kind": "model", "name": "old",
        "downloaded": 1, "total": 1, "created": stale, "updated_at": stale,
    }])
    assert activity_feed.snapshot() == []


def test_failed_download_drops_progress(monkeypatch):
    now = time.time()
    monkeypatch.setattr(model_downloader, "list_tasks", lambda: [{
        "id": "t4", "status": "error", "kind": "model", "name": "bad",
        "downloaded": 100, "total": 200, "error": "网络中断",
        "created": now, "updated_at": now,
    }])
    item = activity_feed.snapshot()[0]

    assert item["status"] == "failed"
    assert item["progress"] == ""
    assert item["error"] == "网络中断"


def test_node_snapshot_maps_task_kind(monkeypatch):
    monkeypatch.setattr(node_update, "progress", lambda: {
        "running": True, "task_kind": "node-install", "subject": "ComfyUI-Manager",
        "phase": "deps-install", "percent": 0, "objects_total": 0,
        "note": "安装依赖…", "error": "",
    })
    items = activity_feed.snapshot()

    assert len(items) == 1
    assert items[0]["kind"] == "node_install"
    assert items[0]["title"] == "节点安装"
    assert items[0]["label"] == "节点：ComfyUI-Manager"
    assert items[0]["progress"] == "安装依赖…"
    assert items[0]["target"] == "node-manager"


def test_comfyui_update_gets_its_own_label(monkeypatch):
    monkeypatch.setattr(node_update, "progress", lambda: {
        "running": True, "task_kind": "comfyui-update", "subject": "ComfyUI",
        "phase": "download", "percent": 42, "objects_total": 0, "note": "",
    })
    item = activity_feed.snapshot()[0]

    assert item["kind"] == "comfyui_update"
    assert item["label"] == "ComfyUI 本体"
    assert item["progress"] == "42% · 拉取代码"


def test_node_idle_produces_nothing():
    assert activity_feed.snapshot() == []


def test_lora_sync_activity(monkeypatch):
    monkeypatch.setattr(lora_index, "sync_progress", lambda: {
        "running": True, "done": 3, "total": 9, "current": "foo.safetensors", "error": "",
    })
    item = activity_feed.snapshot()[0]

    assert item["kind"] == "lora_sync"
    assert item["progress"] == "3/9 · 当前：foo.safetensors"
    assert item["target"] == "lora-data"


def test_maintenance_activity_carries_repo(monkeypatch):
    monkeypatch.setattr(agency_maintenance, "maintenance_snapshot", lambda: [{
        "repo_id": "repo-1", "turn": 42, "queued": 2, "started_at": time.time(),
    }])
    item = activity_feed.snapshot()[0]

    assert item["kind"] == "chat_maintenance"
    assert item["repo_id"] == "repo-1"
    assert item["progress"] == "第 42 回合 · 排队 2 个"
    assert item["target"] == ""


def test_表格整理与纪要索引在后台活动可见(monkeypatch):
    """2026-10-20 用户实锤：「点击整理索引时后台任务还是未看到有任务」。

    `manual_table_fill` 每批都会更新进度，但过去只被
    `GET /tables/manual-fill/progress` 按**单仓库**轮询，后台活动面板无从枚举
    ⇒ 这个要跑十几批、几分钟的长操作在面板里完全不可见。
    """
    from app.services import manual_table_fill

    manual_table_fill._FILL_PROGRESS.clear()
    manual_table_fill.set_fill_progress("/out|repo-chron", 3, 11)
    try:
        items = [i for i in activity_feed.snapshot() if i["kind"] == "table_fill"]
    finally:
        manual_table_fill._FILL_PROGRESS.clear()

    assert len(items) == 1
    item = items[0]
    assert item["title"] == "表格整理 / 纪要索引"
    assert item["progress"] == "第 3/11 批"
    assert item["repo_id"] == "repo-chron"
    assert item["status"] == "running"


def test_表格整理结束后不再出现在活动列表(monkeypatch):
    """`clear_fill_progress` 在 `finally` 里 ⇒ 跑完/失败都不留僵尸条目。"""
    from app.services import manual_table_fill

    manual_table_fill._FILL_PROGRESS.clear()
    manual_table_fill.set_fill_progress("/out|repo-x", 1, 2)
    manual_table_fill.clear_fill_progress("/out|repo-x")

    assert [i for i in activity_feed.snapshot() if i["kind"] == "table_fill"] == []


def test_snapshot_sorted_by_started_at_desc(monkeypatch):
    now = time.time()
    monkeypatch.setattr(model_downloader, "list_tasks", lambda: [
        {"id": "old", "status": "downloading", "kind": "model", "name": "old",
         "downloaded": 1, "total": 2, "created": now - 60, "updated_at": now},
        {"id": "new", "status": "downloading", "kind": "model", "name": "new",
         "downloaded": 1, "total": 2, "created": now, "updated_at": now},
    ])
    assert [item["label"] for item in activity_feed.snapshot()] == ["new", "old"]


# —— 第八源：桌面壳后台补齐可选层（P3-b，跨进程账本）——————————————————————————

def _ledger(now: float, **overrides) -> dict:
    value = {
        "layer": "rag", "label": "RAG 增强层", "state": "running", "stage": "下载分件",
        "done_bytes": 5 * 1024 * 1024, "total_bytes": 10 * 1024 * 1024,
        "speed_bps": 2 * 1024 * 1024, "started_at": now - 8, "updated_at": now, "error": "",
    }
    value.update(overrides)
    return value


def test_壳补齐进度投影成一条运行中活动(monkeypatch):
    """验收口径「面板显示 RAG 下载中并给出速率」的读侧证据（写侧由自检第 17/19 项盯）。"""
    now = time.time()
    monkeypatch.setattr(shell_delivery, "read_ledger", lambda moment=None: _ledger(now))
    item = activity_feed.snapshot()[0]

    assert item["kind"] == "runtime_delivery" and item["title"] == "运行时补齐"
    assert item["label"] == "RAG 增强层"
    assert item["status"] == "running"
    assert item["progress"] == "5.0 MB / 10.0 MB · 2.0 MB/s"
    assert item["detail"] == "下载分件"
    assert item["target"] == ""


def test_补齐成功后条目转终态并保留速率位(monkeypatch):
    now = time.time()
    monkeypatch.setattr(shell_delivery, "read_ledger", lambda moment=None: _ledger(
        now, state="done", stage="已提交", done_bytes=10 * 1024 * 1024, speed_bps=0))
    item = activity_feed.snapshot()[0]

    assert item["status"] == "done"
    assert item["progress"] == "10.0 MB / 10.0 MB"
    assert item["detail"] == "已提交"


def test_补齐失败只显示错误不显示进度(monkeypatch):
    now = time.time()
    monkeypatch.setattr(shell_delivery, "read_ledger", lambda moment=None: _ledger(
        now, state="failed", done_bytes=3 * 1024 * 1024, error="RAG 增强层未通过启动自检"))
    item = activity_feed.snapshot()[0]

    assert item["status"] == "failed"
    assert item["progress"] == ""
    assert item["error"] == "RAG 增强层未通过启动自检"


def test_补齐结束超过保留期就不再显示(monkeypatch):
    """与下载同源口径：终态条目只留 `_KEEP_DONE_S`，否则面板会永远挂着一条上次补齐。"""
    stale = time.time() - activity_feed._KEEP_DONE_S - 5
    monkeypatch.setattr(shell_delivery, "read_ledger", lambda moment=None: _ledger(
        stale, state="done", updated_at=stale))

    assert activity_feed.snapshot() == []


def test_读侧判定陈旧时面板不产生条目(monkeypatch):
    """陈旧规则住在 `shell_delivery`（30 秒无更新 ⇒ 写入方已不在）⇒ 这里必须拿不到条目。"""
    monkeypatch.setattr(shell_delivery, "read_ledger", lambda moment=None: None)

    assert activity_feed.snapshot() == []
