import json

from app.services import task_progress_store


def test_progress_store_round_trip_and_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(task_progress_store, "STORE_DIR", tmp_path)
    tasks = {
        "old": {"updated_at": 1, "name": "旧任务"},
        "new": {"updated_at": 2, "name": "新任务"},
    }
    task_progress_store.save("downloads", tasks, limit=1)
    assert task_progress_store.load("downloads") == {
        "new": {"updated_at": 2, "name": "新任务"},
    }
    assert json.loads((tmp_path / "downloads.json").read_text(encoding="utf-8"))["new"]["name"] == "新任务"


def test_测试期STORE_DIR必须已重定向出真实数据目录():
    """契约守卫（2026-09-10）：**本测试不自己 monkeypatch**。

    全量测试的隔离靠 `tests/conftest.py::_isolate_task_progress_store`（autouse）。
    一旦有人删掉那个 fixture，或让某个命名空间绕开 `task_progress_store` 直写真实
    目录，本用例立刻失败——它守的是「跑测试不得改写用户运行态快照」这条红线：
    09-10 实锤真实 `fabric_checkpoints.json` 被全量测试覆盖成 `{}`（会抹掉用户正停在
    fabric 审批断点上的续跑状态）、`recipe_offers.json` 落进脏询问。
    """
    from app.config import DATA_DIR

    real = (DATA_DIR / "task_progress").resolve()
    current = task_progress_store.STORE_DIR.resolve()
    assert current != real, "STORE_DIR 仍指向真实 task_progress 目录：测试会污染用户数据"
    assert real not in current.parents, "STORE_DIR 落在真实 task_progress 目录之下"


def test_running_task_becomes_interrupted_after_restart():
    tasks = {"task": {"status": "downloading", "running": True, "speed_bps": 10}}
    assert task_progress_store.mark_interrupted(
        tasks, running_statuses={"pending", "downloading"},
    ) is True
    assert tasks["task"]["status"] == "error"
    assert tasks["task"]["phase"] == "interrupted"
    assert tasks["task"]["running"] is False
    assert "重启" in tasks["task"]["error"]


def test_terminal_task_is_preserved():
    tasks = {"task": {"status": "done", "phase": "done"}}
    assert task_progress_store.mark_interrupted(tasks, running_statuses={"pending"}) is False
    assert tasks["task"] == {"status": "done", "phase": "done"}


def test_save_按created_at排序并截掉最旧(monkeypatch, tmp_path):
    """回归（2026-09-10）：配方对象只有 `created_at`（既无 updated_at 也无 created）。

    此前排序键只读 `updated_at`/`created` → 键恒 `0` → 稳定排序退化成**插入序（最旧在前）**，
    且超 `limit` 时 `[:limit]` 截掉的是**最新**的配方——直接打到「固化流程预设」清单的顺序与容量。
    """
    monkeypatch.setattr(task_progress_store, "STORE_DIR", tmp_path)
    tasks = {
        "r1": {"created_at": 100, "name": "第一条"},
        "r2": {"created_at": 300, "name": "第三条"},
        "r3": {"created_at": 200, "name": "第二条"},
    }
    task_progress_store.save("plan_recipes", tasks, limit=2)
    # 最新在前（r2=300 → r3=200），被截掉的是最旧的 r1
    assert list(task_progress_store.load("plan_recipes")) == ["r2", "r3"]
