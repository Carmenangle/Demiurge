"""契约守卫（2026-09-15 P3）：跑测试不得写真实 `backend/data/` 运行态。

本文件**不自己 monkeypatch**。守的是 `tests/conftest.py` 里那组 `_isolate_*` fixture 还在
——谁把它们删掉、或把常量改回真实路径，这里立刻变红。
（P2 同款手法：`test_run_trace.py::test_测试期TRACE必须已重定向出真实日志目录`。）

为什么值得一条守卫：这类漏写在功能上完全无声（业务用例全绿），只在「跑完测试发现真实
运行态被改」时才暴露。09-10（task_progress 被清空成 `{}`）与 09-15（trace 轮转撞
safe-delete 守卫整批假失败）两次都吃过这个亏。
"""
from __future__ import annotations

from app.config import DATA_DIR
from app.services import (
    attachment_store,
    build_session_store,
    chat_snapshot,
    comfy_launcher,
    flow_context,
    procedure_skills,
    prompt_approval_store,
    rag_backend,
    scenario_lab,
    visual_asset_index,
)


def _assert_outside_data(actual, label: str) -> None:
    """断言 actual 不落在真实 DATA_DIR 之内。"""
    assert actual is not None, f"{label} 未重定向（None）"
    path = str(actual).replace("\\", "/").rstrip("/").lower()
    root = str(DATA_DIR).replace("\\", "/").rstrip("/").lower()
    assert not path.startswith(root + "/"), f"{label} 仍指向真实 data 目录：{actual}"
    assert path != root, f"{label} 仍等于真实 data 目录：{actual}"


def test_测试期各存储常量必须已重定向出真实data目录():
    _assert_outside_data(attachment_store.ATTACH_ROOT, "attachment_store.ATTACH_ROOT")
    _assert_outside_data(chat_snapshot.SNAP_DIR, "chat_snapshot.SNAP_DIR")
    _assert_outside_data(chat_snapshot._REVISIONS_PATH, "chat_snapshot._REVISIONS_PATH")
    _assert_outside_data(build_session_store.SESS_DIR, "build_session_store.SESS_DIR")
    _assert_outside_data(procedure_skills.STORE, "procedure_skills.STORE")
    _assert_outside_data(prompt_approval_store.APPROVALS_FILE, "prompt_approval_store.APPROVALS_FILE")
    _assert_outside_data(scenario_lab.SNAPSHOT_ROOT, "scenario_lab.SNAPSHOT_ROOT")
    _assert_outside_data(scenario_lab.EXPERIMENT_ROOT, "scenario_lab.EXPERIMENT_ROOT")
    _assert_outside_data(flow_context._root(), "flow_context._root()")
    _assert_outside_data(comfy_launcher.DATA_DIR, "comfy_launcher.DATA_DIR")
    # ⚠ 反向锁：`_config_path` 必须**保持**指向真实文件 —— `load_config()` 靠它读真实
    # ComfyUI 安装路径给 `audio_merge.find_ffmpeg()` 用；一旦把它也重定向到 tmp，
    # `test_audio_merge` 的 3 条「真实 ffmpeg」用例会从执行静默退化为跳过（09-15 踩过）。
    assert comfy_launcher._config_path() == DATA_DIR / "comfy_config.json", (
        "comfy_launcher._config_path() 不得被重定向（load_config 的读语义必须保留）"
    )
    # 向量库：CHROMA_DIR 被多模块冻结 + repo_meta 走调用时派生路径，逐处都要落在 tmp。
    _assert_outside_data(rag_backend.CHROMA_DIR, "rag_backend.CHROMA_DIR")
    _assert_outside_data(scenario_lab.CHROMA_DIR, "scenario_lab.CHROMA_DIR")
    _assert_outside_data(visual_asset_index.CHROMA_DIR, "visual_asset_index.CHROMA_DIR")
