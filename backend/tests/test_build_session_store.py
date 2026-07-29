"""搭建会话持久化测试：进度保存 + 多开 + 恢复往返（断线/重启后不丢进度的真源）。
用 tmp_path 隔离落盘目录，零重依赖可独立收集。"""
import json

import pytest

from app.services import build_session_store as bss


@pytest.fixture(autouse=True)
def _isolate_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(bss, "SESS_DIR", tmp_path)


def test_save_new_generates_id_and_meta():
    meta = bss.save_session("", "文生图", [{"role": "user"}], {"1": {}}, "sk")
    assert meta["id"]
    assert meta["name"] == "文生图"
    assert isinstance(meta["updated_at"], int) and meta["updated_at"] > 0


def test_save_defaults_blank_name():
    meta = bss.save_session("", "   ", [], {})
    assert meta["name"] == "未命名工作流"


def test_save_get_roundtrip_preserves_graph():
    graph = {"1": {"class_type": "KSampler", "inputs": {"seed": 42}}}
    msgs = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "ok"}]
    meta = bss.save_session("", "n", msgs, graph, "sk1")
    got = bss.get_session(meta["id"])
    assert got["graph"] == graph          # 恢复到画布须完全一致
    assert got["msgs"] == msgs
    assert got["skeleton_id"] == "sk1"


def test_save_overwrites_same_id():
    bss.save_session("fixed", "v1", [], {"1": {}})
    bss.save_session("fixed", "v2", [], {"1": {}, "2": {}})
    got = bss.get_session("fixed")
    assert got["name"] == "v2"
    assert len(got["graph"]) == 2


def test_get_missing_returns_none():
    assert bss.get_session("nope") is None


def test_get_corrupt_returns_none(tmp_path):
    (tmp_path / "bad.json").write_text("{ not json", encoding="utf-8")
    assert bss.get_session("bad") is None


def test_list_sessions_meta_only_and_sorted(monkeypatch):
    times = iter([1000, 2000, 3000])
    monkeypatch.setattr(bss.time, "time", lambda: next(times) / 1000)
    bss.save_session("a", "A", [{"x": 1}], {"1": {}})
    bss.save_session("b", "B", [{"x": 1}, {"y": 2}], {"1": {}, "2": {}})
    bss.save_session("c", "C", [], {})

    sessions = bss.list_sessions()
    assert [s["id"] for s in sessions] == ["c", "b", "a"]   # updated_at 倒序
    b = next(s for s in sessions if s["id"] == "b")
    assert b["node_count"] == 2 and b["msg_count"] == 2
    assert "msgs" not in b and "graph" not in b            # 不含大字段


def test_list_empty_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(bss, "SESS_DIR", tmp_path / "absent")
    assert bss.list_sessions() == []


def test_list_skips_corrupt_files(tmp_path):
    bss.save_session("ok", "OK", [], {"1": {}})
    (tmp_path / "broken.json").write_text("<<<", encoding="utf-8")
    ids = [s["id"] for s in bss.list_sessions()]
    assert ids == ["ok"]                                   # 损坏文件被跳过


def test_delete_existing_and_missing():
    bss.save_session("gone", "G", [], {})
    assert bss.delete_session("gone") is True
    assert bss.get_session("gone") is None
    assert bss.delete_session("gone") is False


def test_save_is_atomic_no_tmp_left(tmp_path):
    bss.save_session("x", "X", [], {"1": {}})
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []                                 # 原子替换后无残留 .tmp
    assert json.loads((tmp_path / "x.json").read_text(encoding="utf-8"))["id"] == "x"


def test_task_result_updates_graph_and_appends_message_once():
    bss.save_session("task-session", "T", [{"role": "user", "text": "生成"}], {"old": {}})
    result = {
        "ok": True,
        "graph": {"1": {"class_type": "SaveImage", "inputs": {}}},
        "missing_nodes": ["MissingNode"],
        "alternatives": {"MissingNode": ["LocalNode"]},
    }

    assert bss.apply_task_result("task-session", "task-1", "direct", "生成", result=result)
    assert bss.apply_task_result("task-session", "task-1", "direct", "生成", result=result)

    session = bss.get_session("task-session")
    assert session["graph"] == result["graph"]
    task_msgs = [msg for msg in session["msgs"] if msg.get("task_id") == "task-1"]
    assert len(task_msgs) == 1
    assert task_msgs[0]["missingNodes"] == ["MissingNode"]


def test_task_plan_and_failure_are_persisted_without_recreating_deleted_session():
    bss.save_session("plan", "P", [], {})
    assert bss.apply_task_result(
        "plan", "task-plan", "plan", "做一个工作流", result={"plan": "先加载模型"},
    )
    plan_msg = bss.get_session("plan")["msgs"][-1]
    assert plan_msg["planText"] == "先加载模型"
    assert "已和用户确认的搭建方案" in plan_msg["pendingNeed"]

    assert bss.apply_task_result("plan", "task-error", "direct", "继续", error="模型超时")
    assert bss.get_session("plan")["msgs"][-1]["text"] == "请求失败：模型超时"
    assert bss.apply_task_result("missing", "task", "direct", "继续", result={}) is False


def test_stale_frontend_save_does_not_erase_unseen_task_result():
    old_graph = {"old": {}}
    new_graph = {"new": {}}
    bss.save_session("race", "R", [{"role": "user", "text": "生成"}], old_graph)
    bss.apply_task_result(
        "race", "task-race", "direct", "生成", result={"ok": True, "graph": new_graph},
    )

    bss.save_session("race", "R", [{"role": "user", "text": "生成"}], old_graph)
    saved = bss.get_session("race")
    assert saved["graph"] == new_graph
    assert [msg.get("task_id") for msg in saved["msgs"]].count("task-race") == 1
