"""后台活动投影（2026-10-19）：下载 / 节点 / LoRA / 对话后置维护 → 统一活动条目。

本模块只读投影，测试重点：
- 各源快照字段能正确映射成统一结构（含 title 单一属主）；
- 已结束超过保留期的下载不再展示；
- 空快照不产生条目（不能凭空给面板灌噪音）。
"""
from __future__ import annotations

import time

import pytest

from app.services import activity_feed, agent_agency, lora_index, model_downloader, node_update


@pytest.fixture(autouse=True)
def _isolate_sources(monkeypatch):
    """默认四个源都空闲；各用例只覆盖自己关心的那一个，避免真实进程状态泄漏进断言。"""
    monkeypatch.setattr(model_downloader, "list_tasks", lambda: [])
    monkeypatch.setattr(node_update, "progress", lambda: {"running": False})
    monkeypatch.setattr(lora_index, "sync_progress", lambda: {"running": False})
    monkeypatch.setattr(agent_agency, "maintenance_snapshot", lambda: [])


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
    monkeypatch.setattr(agent_agency, "maintenance_snapshot", lambda: [{
        "repo_id": "repo-1", "turn": 42, "queued": 2, "started_at": time.time(),
    }])
    item = activity_feed.snapshot()[0]

    assert item["kind"] == "chat_maintenance"
    assert item["repo_id"] == "repo-1"
    assert item["progress"] == "第 42 回合 · 排队 2 个"
    assert item["target"] == ""


def test_snapshot_sorted_by_started_at_desc(monkeypatch):
    now = time.time()
    monkeypatch.setattr(model_downloader, "list_tasks", lambda: [
        {"id": "old", "status": "downloading", "kind": "model", "name": "old",
         "downloaded": 1, "total": 2, "created": now - 60, "updated_at": now},
        {"id": "new", "status": "downloading", "kind": "model", "name": "new",
         "downloaded": 1, "total": 2, "created": now, "updated_at": now},
    ])
    assert [item["label"] for item in activity_feed.snapshot()] == ["new", "old"]
