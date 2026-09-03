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
