"""后台活动投影（2026-10-19）。

「后台活动」面板原先只看得到 5 个源：仓库对话、ComfyUI 出图、统一 job 注册表、
Autopilot 计划、AI 建工作流。但下载、节点安装/更新/切内核、LoRA 触发词同步、
对话后置维护这四类长操作各自已有进度快照，协议互不相同，用户在面板里完全看不见，
只能自己切到对应设置页才知道它在跑。

本模块**只做投影**：读各子系统已有的进度快照，统一成 Activity 结构供
`GET /api/activity/background` 返回。不持有状态、不启动/取消任何任务、
不改变各子系统的生命周期——唯一写入方仍是各子系统自己。

统一字段口径：
    id          稳定标识（`<source>:<key>`），前端作 React key
    kind        活动类型，前端按 `KIND_LABEL` 映射标题
    label       展示名（文件名 / 节点名 / 固定文案）
    status      running | done | failed | cancelled
    progress    后端算好的可读进度（字节 / 对象数 / 步骤），前端不自己算
    detail      补充上下文（文件名、仓库路径等）
    error       失败原因
    target      点击跳转：设置子视图 id（models / node-manager / lora-data），空=无跳转
    repo_id     关联仓库（对话后置维护用；前端据 localStorage 补显示名）
    started_at / updated_at   epoch 秒

与 `services/jobs.py` 的分工：jobs 是「可登记、可 tick」的写入型注册表；
本模块是「只读投影」。因此**不要把本模块的输出再塞回 /jobs**，否则同一活动两个真源。
"""
from __future__ import annotations

import time

# 已结束任务在面板上留多久（与 jobs._KEEP_DONE_S 对齐）。仅对自带 updated_at 的源生效。
_KEEP_DONE_S = 120.0

KIND_LABEL = {
    "model_download": "模型下载",
    "workflow_download": "工作流下载",
    "node_update": "节点更新",
    "node_install": "节点安装",
    "comfyui_update": "ComfyUI 本体",
    "lora_sync": "LoRA 同步",
    "chat_maintenance": "对话后置维护",
}

# node_update 的 phase → 中文阶段名（只列出真实会出现的值）。
_PHASE_LABEL = {
    "resolve": "解析版本",
    "preflight": "依赖预检",
    "download": "拉取代码",
    "deps": "获取依赖清单",
    "deps-install": "安装依赖",
    "needs-confirm": "等待确认",
    "done": "收尾",
}

_DOWNLOAD_RUNNING = {"pending", "downloading"}
_DOWNLOAD_FAILED = {"error", "interrupted"}


def _download_status(raw: str) -> str:
    if raw in _DOWNLOAD_RUNNING:
        return "running"
    if raw in _DOWNLOAD_FAILED:
        return "failed"
    return "done"


def _download_items(now: float) -> list[dict]:
    """模型 / Civitai 工作流下载（共用 model_downloader 的任务表，kind 区分）。"""
    from app.services import model_downloader
    from app.services.update_progress import human_bytes

    items: list[dict] = []
    for task in model_downloader.list_tasks():
        status = _download_status(str(task.get("status") or ""))
        updated = float(task.get("updated_at") or task.get("created") or 0)
        if status != "running" and now - updated > _KEEP_DONE_S:
            continue
        total = int(task.get("total") or 0)
        done = int(task.get("downloaded") or 0)
        progress = (
            f"{human_bytes(done)} / {human_bytes(total)}" if total > 0 else human_bytes(done)
        )
        speed = int(task.get("speed_bps") or 0)
        if status == "running" and speed > 0:
            progress = f"{progress} · {human_bytes(speed)}/s"
        if status == "failed":
            progress = ""  # 失败时进度无意义，面板只显示错误
        is_workflow = str(task.get("kind") or "") == "workflow"
        items.append({
            "id": f"download:{task.get('id')}",
            "kind": "workflow_download" if is_workflow else "model_download",
            "label": str(task.get("name") or task.get("filename") or "下载任务"),
            "status": status,
            "progress": progress,
            "detail": str(task.get("filename") or ""),
            "error": str(task.get("error") or ""),
            "target": "models",
            "repo_id": "",
            "started_at": float(task.get("started_at") or task.get("created") or updated),
            "updated_at": updated,
        })
    return items


def _node_items() -> list[dict]:
    """节点安装 / 节点更新 / ComfyUI 本体更新与切内核（同一份单例进度快照）。"""
    from app.services import node_update

    snap = node_update.progress()
    if not snap.get("running"):
        return []
    task_kind = str(snap.get("task_kind") or "")
    kind = {
        "node-install": "node_install",
        "comfyui-update": "comfyui_update",
    }.get(task_kind, "node_update")
    subject = str(snap.get("subject") or "")
    if kind == "comfyui_update":
        label = "ComfyUI 本体"
    elif subject:
        label = f"节点：{subject}"
    else:
        label = "ComfyUI 节点"

    parts: list[str] = []
    objects_total = int(snap.get("objects_total") or 0)
    percent = int(snap.get("percent") or 0)
    if objects_total > 0:
        parts.append(f"{int(snap.get('objects_done') or 0)}/{objects_total} 对象")
    elif percent > 0:
        parts.append(f"{percent}%")
    note = str(snap.get("note") or snap.get("message") or "")
    if note:
        # note 是子系统自己维护的中文阶段描述（更具体），有它就不要再拼阶段名，否则重复。
        parts.append(note)
    else:
        phase = str(snap.get("phase") or "")
        if phase in _PHASE_LABEL:
            parts.append(_PHASE_LABEL[phase])

    now = time.time()
    return [{
        "id": f"node:{task_kind or 'node-update'}",
        "kind": kind,
        "label": label,
        "status": "running",
        "progress": " · ".join(parts),
        "detail": str(snap.get("repository") or snap.get("target_path") or ""),
        "error": str(snap.get("error") or ""),
        "target": "node-manager",
        "repo_id": "",
        "started_at": now,
        "updated_at": now,
    }]


def _lora_items() -> list[dict]:
    """LoRA 触发词扫描同步（进程内单例进度）。"""
    from app.services import lora_index

    snap = lora_index.sync_progress()
    if not snap.get("running"):
        return []
    total = int(snap.get("total") or 0)
    done = int(snap.get("done") or 0)
    parts = [f"{done}/{total}" if total > 0 else f"{done} 个"]
    current = str(snap.get("current") or "")
    if current:
        parts.append(f"当前：{current}")
    now = time.time()
    return [{
        "id": "lora:sync",
        "kind": "lora_sync",
        "label": "LoRA 触发词同步",
        "status": "running",
        "progress": " · ".join(parts),
        "detail": "",
        "error": str(snap.get("error") or ""),
        "target": "lora-data",
        "repo_id": "",
        "started_at": now,
        "updated_at": now,
    }]


def _maintenance_items() -> list[dict]:
    """对话后置维护（表格/信念/记忆），正文发出后仍在跑的收尾工作。"""
    from app.services import agent_agency

    items: list[dict] = []
    for entry in agent_agency.maintenance_snapshot():
        turn = int(entry.get("turn") or 0)
        parts = [f"第 {turn} 回合" if turn > 0 else "本轮"]
        queued = int(entry.get("queued") or 0)
        if queued > 1:
            parts.append(f"排队 {queued} 个")
        items.append({
            "id": f"maintenance:{entry.get('repo_id') or 'home'}",
            "kind": "chat_maintenance",
            "label": "对话后置维护",
            "status": "running",
            "progress": " · ".join(parts),
            "detail": "",
            "error": "",
            "target": "",
            "repo_id": str(entry.get("repo_id") or ""),
            "started_at": float(entry.get("started_at") or 0),
            "updated_at": time.time(),
        })
    return items


def snapshot() -> list[dict]:
    """全部外部长操作活动（不含 jobs 注册表，那一份走 GET /api/jobs）。"""
    now = time.time()
    items = [
        *_download_items(now),
        *_node_items(),
        *_lora_items(),
        *_maintenance_items(),
    ]
    for item in items:
        # 标题由后端定（KIND_LABEL 单一属主），前端不再维护第二份映射。
        item["title"] = KIND_LABEL.get(str(item.get("kind") or ""), str(item.get("kind") or ""))
    items.sort(key=lambda item: float(item.get("started_at") or item.get("updated_at") or 0),
               reverse=True)
    return items
