import pytest


@pytest.fixture(autouse=True)
def _isolate_process_local_model_leases():
    """进程级调度状态不能从一个测试泄漏到下一个测试。"""
    from app.services import model_lease

    model_lease._reset_for_tests()
    yield
    model_lease._reset_for_tests()


@pytest.fixture(autouse=True)
def _isolate_capability_leases(tmp_path, monkeypatch):
    """能力租约测试走临时文件，绝不碰真实 backend/data/capability_leases.json。

    grant/revoke 的 _save_persisted 与 _reset_for_tests 的 unlink 都作用于模块级
    LEASE_FILE；若指向真实运行态文件，全量测试会累积写入并在 unlink 时被安全删除
    钩子判成「批量删除」抛 SystemExit(1) 整批挂（2026-09-03 实锤 28 例 setup 级失败）。
    重定向到 tmp_path + 每测试前后清内存租约，同时满足 CI 封闭化合同（测试禁止
    依赖/污染 backend/data 私有数据）。
    """
    from app.services import capability_sandbox

    monkeypatch.setattr(capability_sandbox, "LEASE_FILE",
                        tmp_path / "capability_leases.json")
    capability_sandbox._reset_for_tests()
    yield
    capability_sandbox._reset_for_tests()


@pytest.fixture(autouse=True)
def _isolate_task_progress_store(tmp_path, monkeypatch):
    """任务进度快照一律落 tmp，绝不碰真实 backend/data/task_progress/*.json（2026-09-10）。

    所有命名空间文件（`fabric_checkpoints` / `plan_recipes` / `recipe_offers` /
    `downloads` / `plan_tasks`…）都经 `task_progress_store._path` 落同一个模块级
    `STORE_DIR`。此前只靠各用例自己 `monkeypatch.setattr(task_progress_store,
    "STORE_DIR", tmp_path)`，于是**新写的生产写入路径一旦没被显式重定向就会漏**：
    09-10 全量测试实锤——真实 `fabric_checkpoints.json` 被覆盖成 `{}`（若用户正停在
    fabric 审批断点上，跑一次测试就把断点抹了），且 `recipe_offers.json` 落进一条
    output_dir 指向已删除 pytest 临时目录的脏询问。

    这里统一收口，新存储不必再自带隔离样板；用例要断言别的目录仍可自行覆盖本绑定。
    """
    from app.services import task_progress_store

    monkeypatch.setattr(task_progress_store, "STORE_DIR", tmp_path / "task_progress")


@pytest.fixture(autouse=True)
def _block_os_folder_reveal(monkeypatch):
    """测试期间绝不真的弹出资源管理器（2026-09-10 事故）。

    `reveal_in_folder` 在本机 win32 下会 `subprocess.Popen(["explorer", ...])`——跑一次
    全量测试就真弹一个窗口（指向 pytest 临时目录）。此前只有一个用例自己 monkeypatch，
    而且**打错了目标**：`routers/artifacts.py` 是 `from ... import reveal_in_folder` 的
    独立绑定，改服务模块属性碰不到路由里的引用，于是那个用例既「假绿」（只断言 body
    非空）又真弹窗。这里把两处绑定一起换成降级桩（False = 非 Windows/失败），用例要断言
    别的行为请自行覆盖对应绑定。
    """
    from app.services import collection_artifacts
    from app.routers import artifacts as artifacts_router

    def _never_reveal(path, select=True):
        return False

    monkeypatch.setattr(collection_artifacts, "reveal_in_folder", _never_reveal)
    monkeypatch.setattr(artifacts_router, "reveal_in_folder", _never_reveal)
