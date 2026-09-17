"""P4 第一批形态锁：业务数据路径的「纯吞」必须留痕，禁止退化回 `pass`。

审计正本：docs/memory/except-exception-audit-2026-09-16.md（2026-09-16 AST 全量 435 处，
其中 91 处纯吞、T3.3 前复扫 69 处；本锁钉住 C1–C7 与 T3.2/T3.3 追加项，**29 个函数级目标**）。
这些路径吞掉异常会让快照/记忆/索引/审批状态**静默分叉**，或让用户可见面降级
（如 `_visible_roleplay_text` 剥离失败 ⇒ 内部控制块泄漏），因此处理体必须至少留一条日志/遥测痕迹。

刻意不锁（A/B 档 best-effort，有意设计）：遥测包装器（`run_trace.emit`/`sink`）、
清理路径（interrupt/租约 revoke/taskkill）、探活 `is_up`、`visual_ci._extract_json` 等。
新增同模式收口时往 TARGETS 里加即可。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

# (相对 app 的路径, 函数名) —— 函数内所有 except Exception 处理体都不得是纯吞
TARGETS: list[tuple[str, str]] = [
    # C1 快照/记忆回滚：失败 = 快照与记忆静默分叉（违反「会话快照=历史真源」）
    ("services/chat_maintenance.py", "clear_cache"),
    ("services/chat_maintenance.py", "compact"),
    # C2 部分回复/对话记忆落盘：失败 = 刷新丢正文、记忆漏记
    ("services/agent_runner.py", "persist_partial"),
    ("services/agent_runner.py", "worker"),
    # C3 散卡入库时内嵌世界书外拆：失败 = 卡数据静默缺失
    ("services/character_store.py", "scan_loose_cards"),
    # C4 节点包/表格行索引增删：失败 = 索引与库漂移，检索质量无声退化
    ("services/node_store.py", "index_node_pack"),
    ("services/node_store.py", "delete_node_pack"),
    ("services/node_store.py", "index_node_chunks"),
    ("services/rag_store.py", "index_table_rows"),
    ("services/rag_store.py", "dedup_generations"),
    ("services/rag_store.py", "prune_missing_generations"),
    # C5 审批记录清理：失败 = 残留审批可能被误再次审批
    ("services/generation_approval.py", "_clear_after_success"),
    # C6/C7 进度发布与取消信号：低危但失败无诊断
    ("services/plan_tasks.py", "_publish_progress"),
    ("services/chat_agent_queue.py", "cancel"),
    # C4 续（T3.2）：/rag/list 的顺带播种 best-effort 属主 —— 失败 = 系统指令静默缺索引。
    # 原先这段兜底**写在路由里**（`routers/rag.py:130` 纯吞），T3.2 下沉到服务侧并留痕。
    ("services/rag_store.py", "seed_system_docs_best_effort"),
    # T3.3 批次 1（agent 辅助层，2026-09-17）：失败 = 用户可见面降级或上下文静默缺失
    ("services/agent_agency.py", "_extract_video_action_plan"),   # 防拦截预设未生效
    ("services/agent_agency.py", "_visible_roleplay_text"),       # 控制块（生图/状态/表格）泄漏
    ("services/agent_context_assembly.py", "_card_source"),       # 卡来源降级为 character_dir
    ("services/agent_context_assembly.py", "_agent_system"),      # system 缺「生图提示词写法」
    ("services/agent_retrieval.py", "_reindex_retrieval_tables"),  # 索引与表格数据分叉
    # T3.3 批次 2（LangGraph 编排层，2026-09-17）：失败 = 编译上下文/任务登记/记忆静默降级
    ("services/agent_graph.py", "analyze_node"),              # 缺自定义风格写法指引
    ("services/agent_graph.py", "_pc_collect_attachments"),   # 缺附件路径/LoRA 清单/模板清单
    ("services/agent_graph.py", "_pc_design_discussion_reply"),  # 任务会话缺本轮登记
    ("services/agent_graph.py", "_pc_register_delegation"),   # 任务会话缺委派登记
    ("services/agent_graph.py", "answer_node"),               # 本轮回答缺 chat_facts 块
    ("services/agent_graph.py", "stream_multi_agent"),        # 附图本地路径未注入
    ("services/agent_fabric.py", "_fabric_knowledge_history"),  # 历史缺产物指针/规范文档
    ("services/agent_fabric.py", "_fabric_finalize"),         # 缺版本快照/产物登记
    ("services/agent_fabric.py", "_recipe_offer_answer"),     # 邀约记录未删，可能重复弹出
]


def _is_bare_exc(t: ast.expr | None) -> bool:
    if isinstance(t, ast.Name):
        return t.id == "Exception"
    return isinstance(t, ast.Attribute) and t.attr == "Exception"


def _is_trivial(handler: ast.ExceptHandler) -> bool:
    """处理体只由 pass / 常量表达式（docstring、Ellipsis）构成 = 纯吞。"""
    return all(
        isinstance(s, ast.Pass)
        or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
        for s in handler.body
    )


def test_目标注册表非空且模块都存在():
    """防空转①：目标表被删空/改小，或路径写错 ⇒ 整条锁静默失效。"""
    assert len(TARGETS) >= 29, (
        f"TARGETS 只剩 {len(TARGETS)} 条（下限 29）——锁的保护面缩水了；"
        f"确属重构请同步下调，但不得删到失去意义"
    )
    missing = [rel for rel, _ in TARGETS if not (APP / rel).exists()]
    assert not missing, f"目标模块不存在（路径写错会让该条目标空转）: {missing}"


def test_判据自身有牙():
    """防空转②（锁的锁）：判据必须抓得到纯吞形态、且不误伤留痕（防判据写错导致永久绿灯）。"""
    def _h(src: str) -> bool:
        tree = ast.parse(src)
        return any(isinstance(n, ast.ExceptHandler) and _is_bare_exc(n.type) and _is_trivial(n)
                   for n in ast.walk(tree))

    assert _h("try:\n    f()\nexcept Exception:\n    pass\n"), "判据抓不到 `except Exception: pass`"
    assert _h("try:\n    f()\nexcept Exception:\n    ...\n"), "判据抓不到 `except Exception: ...`"
    assert _h('try:\n    f()\nexcept Exception:\n    """best effort"""\n'), "判据抓不到「仅 docstring」"
    assert not _h("try:\n    f()\nexcept Exception as e:\n    _LOG.warning('x', e)\n"), "判据误伤「留痕」"


@pytest.mark.parametrize("rel, fname", TARGETS)
def test_no_silent_except_exception(rel: str, fname: str) -> None:
    tree = ast.parse((APP / rel).read_text(encoding="utf-8"), filename=rel)
    fns = [n for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fname]
    assert fns, f"{rel} 里找不到函数 {fname}"
    silent: list[str] = []
    for fn in fns:
        for node in ast.walk(fn):
            if isinstance(node, ast.ExceptHandler) and _is_bare_exc(node.type) \
                    and _is_trivial(node):
                silent.append(f"{fn.name}:{node.lineno}")
    assert not silent, \
        f"{rel}:{fname} 出现纯吞 except Exception（业务数据路径必须留痕，见审计报告）: {silent}"
