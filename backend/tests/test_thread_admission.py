"""线程准入测试：抢占登记、所有权校验、协作取消、活动状态生命周期。
零重依赖，独立于 langgraph/langchain，可单独收集。"""
import threading

import pytest

from app.services import thread_admission as ta


def _fresh():
    """清空全局准入表，避免用例间串扰。"""
    with ta._lock:
        ta._active.clear()


def test_admit_rejects_same_thread():
    _fresh()
    ta.admit("t", threading.Event())
    with pytest.raises(ta.RunAlreadyActive):
        ta.admit("t", threading.Event())


def test_admit_different_threads_independent():
    _fresh()
    a = ta.admit("a", threading.Event())
    ta.admit("b", threading.Event())
    assert ta.is_active("a") and ta.is_active("b")
    ta.release(a)
    assert not ta.is_active("a")
    assert ta.is_active("b")


def test_release_allows_readmit():
    _fresh()
    first = ta.admit("t", threading.Event())
    ta.release(first)
    assert not ta.is_active("t")
    # 释放后可再次登记同一 thread
    ta.admit("t", threading.Event())
    assert ta.is_active("t")


def test_stale_release_does_not_evict_new_admission():
    _fresh()
    stale = ta.admit("t", threading.Event())
    ta.release(stale)
    current = ta.admit("t", threading.Event())
    ta.release(stale)  # 旧登记的 release 不应误删新登记
    assert ta.is_active("t")
    assert ta._active["t"] is current


def test_request_cancel_hits_active_run():
    _fresh()
    ev = threading.Event()
    ta.admit("t", ev)
    assert ta.request_cancel("t") is True
    assert ev.is_set()


def test_request_cancel_misses_when_idle():
    _fresh()
    assert ta.request_cancel("missing") is False


def test_is_active_lifecycle():
    _fresh()
    assert ta.is_active("t") is False
    admission = ta.admit("t", threading.Event())
    assert ta.is_active("t") is True
    ta.release(admission)
    assert ta.is_active("t") is False


def test_concurrent_admit_only_one_wins():
    _fresh()
    winners: list[bool] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        try:
            ta.admit("t", threading.Event())
            winners.append(True)
        except ta.RunAlreadyActive:
            winners.append(False)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert winners.count(True) == 1
