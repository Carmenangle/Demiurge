import pytest


@pytest.fixture(autouse=True)
def _isolate_process_local_model_leases():
    """进程级调度状态不能从一个测试泄漏到下一个测试。"""
    from app.services import model_lease

    model_lease._reset_for_tests()
    yield
    model_lease._reset_for_tests()
