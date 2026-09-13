"""task_session_store：任务会话登记（上下文合同 P1，2026-09-11）。

存储隔离由 `tests/conftest.py::_isolate_task_progress_store`（autouse）统一
重定向到 tmp，不碰真实 backend/data/task_progress/。
"""
from app.services import task_session_store


def test_保存与读取():
    entry = task_session_store.save({
        "task_type": "design_discussion",
        "intent": "跟我讨论各服装的生图提示词",
        "thread_id": "t1",
        "output_dir": "/out",
        "materials": [{"file_id": "f1", "name": "服装搭配参考分析.md"}],
    })
    got = task_session_store.active(thread_id="t1")
    assert got is not None and got["task_type"] == "design_discussion"
    assert got["created_intent"] == "跟我讨论各服装的生图提示词"
    assert entry["id"] == "task:t1"


def test_接续登记保留首链意图并合并材料():
    """接续轮重发登记：created_intent 保留首次任务句；materials 按 file_id 并集。"""
    task_session_store.save({
        "task_type": "design_discussion",
        "intent": "跟我讨论各服装的生图提示词",
        "thread_id": "t1",
        "materials": [{"file_id": "f1", "name": "文档A.md"}],
    })
    task_session_store.save({
        "task_type": "design_discussion",
        "intent": "不用ComfyUI的提示词,我要适配gpt的描述",  # 接续短句
        "thread_id": "t1",
        "materials": [{"file_id": "f2", "name": "参考图.png"}],
    })
    got = task_session_store.active(thread_id="t1")
    assert got["intent"] == "不用ComfyUI的提示词,我要适配gpt的描述"
    assert got["created_intent"] == "跟我讨论各服装的生图提示词"   # 首链任务句不丢
    fids = [m["file_id"] for m in got["materials"]]
    assert fids == ["f1", "f2"]                                    # 并集保序
    # 重复 fileId 不重复计入
    task_session_store.save({
        "task_type": "design_discussion", "intent": "继续",
        "thread_id": "t1", "materials": [{"file_id": "f2", "name": "参考图.png"}],
    })
    assert len(task_session_store.active(thread_id="t1")["materials"]) == 2


def test_产物路径回写跨轮累积且保留原任务类型():
    """fabric done 回写 product_paths：跨轮并集累积；已有任务（讨论/委派）类型不被覆盖。"""
    task_session_store.save({"task_type": "design_discussion", "intent": "讨论服装",
                             "thread_id": "t4"})
    task_session_store.save({
        "task_type": "design_discussion",  # _fabric_finalize 用 _prev 的 task_type 原样回传
        "intent": "把场景1的服装出成文档",
        "thread_id": "t4",
        "product_paths": ["D:/out/场景1服装.md"],
    })
    task_session_store.save({
        "task_type": "design_discussion", "intent": "再补场景2",
        "thread_id": "t4",
        "product_paths": ["D:/out/场景2服装.md", "D:/out/场景1服装.md"],  # 重复路径去重
    })
    got = task_session_store.active(thread_id="t4")
    assert got["task_type"] == "design_discussion"                 # 类型不被回写覆盖
    assert got["product_paths"] == ["D:/out/场景1服装.md", "D:/out/场景2服装.md"]


def test_过期与清除():
    task_session_store.save({"task_type": "design_discussion", "intent": "讨论",
                             "thread_id": "t2"})
    # 未过期可读；TTL 校验靠 updated_at 判定，直接把时间拨回去验证过期路径
    entry = task_session_store.active(thread_id="t2")
    assert entry is not None
    import time as _t
    from app.services import task_progress_store
    tasks = task_progress_store.load(task_session_store.NAMESPACE)
    tasks["task:t2"]["updated_at"] = _t.time() - task_session_store.TTL_SECONDS - 1
    task_progress_store.save(task_session_store.NAMESPACE, tasks)
    assert task_session_store.active(thread_id="t2") is None       # 过期不劫持路由
    # clear
    task_session_store.save({"task_type": "design_discussion", "intent": "讨论",
                             "thread_id": "t3"})
    task_session_store.clear("t3")
    assert task_session_store.active(thread_id="t3") is None
    # 空 thread_id 不炸
    assert task_session_store.active(thread_id="") is None
