"""P4 第一批形态锁：业务数据路径的「纯吞」必须留痕，禁止退化回 `pass`。

审计正本：docs/memory/except-exception-audit-2026-09-16.md（2026-09-16 AST 全量 435 处，
其中 91 处纯吞、T3.3 前复扫 69 处；本锁钉住 C1–C7 与 T3.2/T3.3/T3.4 追加项，**46 个函数级目标**）。
这些路径吞掉异常会让快照/记忆/索引/审批状态**静默分叉**，或让用户可见面降级
（如 `_visible_roleplay_text` 剥离失败 ⇒ 内部控制块泄漏），因此处理体必须至少留一条日志/遥测痕迹。

刻意不锁（A/B 档 best-effort，有意设计）：遥测包装器（`run_trace.emit`/`sink`）、
清理路径（interrupt/租约 revoke/taskkill）、探活 `is_up`、`visual_ci._extract_json` 等。
新增同模式收口时往 TARGETS 里加即可。

批次 4（2026-09-17）追加：10 处 C 档留痕（capability_handlers / image_agent / worldbook /
narrative_ci / workflow_builder / model_downloader / output_path_migration）＋ 4 个 A/B 档模块
（comfy_launcher / comfyui_client / model_lease / roleplay_turn）的 `[EXC-保留]` 标注注册。
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
    ("services/agency_scene_comfy.py", "_extract_video_action_plan"),   # 防拦截预设未生效（2026-10-23 随 comfy 域迁出）
    ("services/agent_agency.py", "_visible_roleplay_text"),       # 控制块（生图/状态/表格）泄漏
    ("services/agent_context_assembly.py", "_card_source"),       # 卡来源降级为 character_dir
    ("services/agent_context_assembly.py", "_agent_system"),      # system 缺「生图提示词写法」
    ("services/agent_retrieval.py", "_reindex_retrieval_tables"),  # 索引与表格数据分叉
    # T3.3 批次 2（LangGraph 编排层，2026-09-17）：失败 = 编译上下文/任务登记/记忆静默降级
    ("services/agent_graph_nodes.py", "analyze_node"),              # 缺自定义风格写法指引
    ("services/agent_graph_plan.py", "_pc_collect_attachments"),   # 缺附件路径/LoRA 清单/模板清单
    ("services/agent_graph_plan.py", "_pc_design_discussion_reply"),  # 任务会话缺本轮登记
    ("services/agent_graph_plan.py", "_pc_register_delegation"),   # 任务会话缺委派登记
    ("services/agent_graph_nodes.py", "answer_node"),               # 本轮回答缺 chat_facts 块
    ("services/agent_graph.py", "stream_multi_agent"),        # 附图本地路径未注入
    ("services/agent_fabric.py", "_fabric_knowledge_history"),  # 历史缺产物指针/规范文档
    ("services/agent_fabric.py", "_fabric_finalize"),         # 缺版本快照/产物登记
    ("services/agent_fabric.py", "_recipe_offer_answer"),     # 邀约记录未删，可能重复弹出
    # T3.3 批次 3（编排 / LLM / 元数据层，2026-09-17）：失败 = 用量统计、断点、改名迁移、
    # 检索融合、卡归属静默降级
    ("services/llm.py", "chat_messages"),              # 用量统计缺失（on_usage / on_retry 回调）
    ("services/llm.py", "chat_messages_stream"),       # 同链流式（on_usage / on_finish 回调）
    ("services/repo_meta.py", "artifact_domains"),     # 产物域缺该作品自有卡目录
    ("services/repo_meta.py", "write_repo_marker"),    # 文件夹归属标记缺失（改名迁移失配）
    ("services/repo_meta.py", "_rewrite_paths"),       # 库内旧路径残留 ⇒ 断图
    ("services/plan_compiler.py", "compile_plan"),     # variants / prev_batch 回填静默丢失
    ("services/rag_store.py", "search_generations"),   # 检索融合缺视觉一路
    ("services/rag_store.py", "_remove_local_image"),  # 磁盘残留孤儿图
    # T3.3 批次 4（零散站点，2026-09-17）：失败 = 快照留档 / 句柄登记 / 外部工具与技能装载 /
    # 世界书稠密召回 / 文风诊断 / 编译器提示 / 模型描述 / 资产索引回滚 静默降级
    ("services/capability_handlers.py", "collect_comfy_outputs"),   # 计划采集产物少一条留档
    ("services/capability_handlers.py", "create_repo_doc"),         # flow_context 少一条文档句柄
    ("services/image_agent.py", "_build"),                          # system 缺 MCP 工具/自定义技能
    ("services/worldbook.py", "_retrieve"),                         # 本轮世界书少 dense 召回一路
    ("services/narrative_ci.py", "evaluate"),                       # 本轮少 prose_style 诊断
    ("services/workflow_builder.py", "build_direct"),               # 缺 vram/model_gaps 编译提示
    ("services/model_downloader.py", "_hf_info"),                   # 模型描述留空
    ("services/output_path_migration.py", "_update_index"),         # 索引回滚失败残留半更新
    ("services/output_path_migration.py", "_rollback_index"),       # 同上（显式回滚 helper）
    # ⚠ `services/fabric_loop.py::run_loop` **刻意不在表内**：同函数内 791/1227 已留痕，
    #   但 890/965 是审计**改判保留**（整段机械闸门块：不收窄、也不加日志，见审计报告
    #   「审计后复核改判保留」）⇒ 该函数必然含纯吞，进 TARGETS 只会永红。
    #   该文件的保护改由下方「判据三 ②」的标注注册表承担。
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
    assert len(TARGETS) >= 46, (
        f"TARGETS 只剩 {len(TARGETS)} 条（下限 46）——锁的保护面缩水了；"
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


# ---------------------------------------------------------------------------
# 判据三（T3.3 批次 3）：「分类标注」与「实现」必须自洽
# ---------------------------------------------------------------------------
# 背景：`fabric_loop.run_loop` 这类函数**同函数混档** —— 一部分站点是业务数据路径（留痕），
# 一部分是审计改判的有意兜底（保留，不加日志）。函数级锁（TARGETS）的判据是「函数内不得有
# 任何纯吞」，对这种函数永远为红；改用**源码标注即注册表**：把两档都写在 `except` 行上，
# 让锁去校验「标注与实现一致」。与 routers 的判据二同源（读代码的人不必猜是有意还是写漏）。

MARK_LOG = "[EXC-留痕]"
MARK_KEEP = "[EXC-保留]"
# 模块 → 该模块 `except Exception` 处数下限（防空转）
ANNOTATED_MODULES: dict[str, int] = {
    # 同函数混档的首个样本：见审计报告「审计后复核改判保留」与 T3.3 批次 3
    "services/fabric_loop.py": 16,
    # T3.3 批次 4：A/B 档 best-effort（审计「明确保留」/ 清理路径）——所在模块的全部纯吞
    # 都必须把「有意不记日志」写在源码上
    "services/comfy_launcher.py": 5,     # taskkill 清理、日志句柄关闭
    "services/comfyui_client.py": 12,    # 探活 is_up、取消 interrupt 两处
    "services/model_lease.py": 4,        # 低优租约释放（失败由 remaining_conflicts 复查兜底）
    "services/roleplay_turn.py": 7,      # 自愈限时 extra、流式提示 notify
}
APP_FILE_FLOOR = 40      # 实测 232 个 .py
MARK_LOG_FLOOR = 40      # 全仓 [EXC-留痕] 实测 44 处
LOG_METHODS = {"warning", "error", "exception", "critical", "info", "debug"}


def _log_annotated(src: str, filename: str = "<src>") -> list[ast.ExceptHandler]:
    """返回 `except` 行带 `[EXC-留痕]` 标注的 handler。"""
    lines = src.split("\n")
    return [n for n in ast.walk(ast.parse(src, filename=filename))
            if isinstance(n, ast.ExceptHandler) and MARK_LOG in lines[n.lineno - 1]]


def _keep_offenders(src: str) -> list[str]:
    """返回**纯吞但未标 `[EXC-保留]`** 的站点（`行号: 原文`）—— 有意兜底必须写在源码上。"""
    lines = src.split("\n")
    return [f"{n.lineno}: {lines[n.lineno - 1].strip()}"
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.ExceptHandler) and _is_bare_exc(n.type) and _is_trivial(n)
            and MARK_KEEP not in lines[n.lineno - 1]]


def _placeholder_offenders(h: ast.ExceptHandler) -> list[str]:
    """留痕 handler 内「logger 调用传了额外参数、格式串却没有 `%`」的站点。

    这类调用既不报错也不打日志：logging 自己做 `msg % args` 抛 TypeError，随后被
    `handleError` 吞成 `--- Logging error ---` ⇒ **留痕等于没留**（本轮自查踩到）。
    """
    bad: list[str] = []
    for call in ast.walk(h):
        if not isinstance(call, ast.Call) or len(call.args) < 2:
            continue
        fn = call.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
        if name not in LOG_METHODS:
            continue
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and "%" not in first.value:
            bad.append(f"{call.lineno}: {name}({first.value[:40]!r}, <args>) 缺 % 占位符")
    return bad


def test_留痕标注必须真留痕且占位符齐备():
    files = sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)
    assert len(files) >= APP_FILE_FLOOR, f"app 扫描面只剩 {len(files)} 个文件 —— 锁空转了"
    marked = 0
    silent_offenders: dict[str, list[str]] = {}
    placeholder_offenders: dict[str, list[str]] = {}
    for p in files:
        rel = p.relative_to(APP).as_posix()
        src = p.read_text(encoding="utf-8")
        if MARK_LOG not in src:      # 只解析带标注的文件：省时，也避免把无关源码的
            continue                 # SyntaxWarning（如正则串里的 `\w`）算进本用例
        for h in _log_annotated(src, rel):
            marked += 1
            if _is_trivial(h):
                silent_offenders.setdefault(rel, []).append(str(h.lineno))
            bad = _placeholder_offenders(h)
            if bad:
                placeholder_offenders.setdefault(rel, []).append("；".join(bad))
    assert marked >= MARK_LOG_FLOOR, (
        f"全仓只剩 {marked} 处 [EXC-留痕]（下限 {MARK_LOG_FLOOR}）——标注被删/挪走会让本判据空转"
    )
    assert not silent_offenders, (
        "带 [EXC-留痕] 标注却仍是纯吞（标注与实现矛盾）：标注了留痕就必须真的留痕，"
        "否则请改标 [EXC-保留] 并说明为何有意不记日志。\n"
        + "\n".join(f"  {k}: {v}" for k, v in silent_offenders.items())
    )
    assert not placeholder_offenders, (
        "留痕调用传了额外参数但格式串没有 `%` 占位符 —— logging 会抛 TypeError 并被自身吞成"
        " `--- Logging error ---`，日志实际打不出来（留痕等于没留）。\n"
        + "\n".join(f"  {k}: {v}" for k, v in placeholder_offenders.items())
    )


def test_混合档模块的纯吞必须显式标注保留():
    """`ANNOTATED_MODULES` 内：**纯吞**的 `except Exception` 必须带 `[EXC-保留]`。

    纯吞只可能是「有意兜底且不加日志」，那就必须把这句话写进源码；写漏（或标注被抹掉）
    这里会红 —— 这是 `run_loop` 那种混档函数唯一上得了的锁。
    """
    for rel, floor in sorted(ANNOTATED_MODULES.items()):
        src = (APP / rel).read_text(encoding="utf-8")
        handlers = [n for n in ast.walk(ast.parse(src, filename=rel))
                    if isinstance(n, ast.ExceptHandler) and _is_bare_exc(n.type)]
        assert len(handlers) >= floor, (
            f"{rel} 的 except Exception 只剩 {len(handlers)} 处（下限 {floor}）——锁的保护面缩水了"
        )
        bad = _keep_offenders(src)
        assert not bad, (
            f"{rel} 出现**未标注**的纯吞 except Exception —— 有意兜底请标注 [EXC-保留]"
            f"（并说明为何不加日志）；业务数据路径请改为 [EXC-留痕] 并真留日志。\n"
            + "\n".join(f"  {b}" for b in bad)
        )


def test_分类标注判据自身有牙():
    """锁的锁：三个方向都要抓得到、且不误伤合法形态。"""
    fake_log = "try:\n    f()\nexcept Exception:  # [EXC-留痕] 说留痕\n    pass\n"
    assert any(_is_trivial(h) for h in _log_annotated(fake_log)), "① 抓不到「标注留痕却是纯吞」"
    real_log = ("try:\n    f()\nexcept Exception as e:  # [EXC-留痕] 真留痕\n"
                "    _LOG.warning('x: %s', e)\n")
    assert not any(_is_trivial(h) for h in _log_annotated(real_log)), "① 误伤真留痕"
    assert not _log_annotated("try:\n    f()\nexcept Exception:\n    pass\n"), \
        "① 误伤「未标注」的站点（把它当成留痕站点了）"
    bad_call = ("try:\n    f()\nexcept Exception as e:  # [EXC-留痕] 假留痕\n"
                "    _LOG.warning('没有占位符', e)\n")
    assert _placeholder_offenders(_log_annotated(bad_call)[0]), "①b 抓不到「传参但格式串无 %」"
    good_call = ("try:\n    f()\nexcept Exception as e:  # [EXC-留痕] 真留痕\n"
                 "    _LOG.warning('有占位符: %s', e)\n")
    assert not _placeholder_offenders(_log_annotated(good_call)[0]), "①b 误伤合法留痕"
    assert _keep_offenders("try:\n    f()\nexcept Exception:\n    pass\n"), \
        "② 抓不到「未标注的纯吞」"
    assert not _keep_offenders("try:\n    f()\nexcept Exception:  # [EXC-保留] 有意兜底\n    pass\n"), \
        "② 误伤 [EXC-保留] 标注"
