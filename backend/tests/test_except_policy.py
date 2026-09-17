"""P4 / T3.1 试点形态锁：纯逻辑·存储层模块内**禁止纯吞** `except Exception`。

正本：`docs/memory/except-exception-audit-2026-09-16.md`（§4 处置建议 · T3.1「按目录试点」）。

判据与审计脚本**同口径** —— 「纯吞」＝ 处理体只由 `pass` / 常量表达式（docstring、`Ellipsis`）
构成。与既有锁的分工：
- `test_silent_except_logging.py` 钉**函数级**（C1–C7 共 14 处业务数据路径）；
- 本锁钉**模块级**范围 —— 试点模块无外部边界（纯逻辑 / 本地存储），因此任何被吞掉的异常都
  意味着**静默分叉**（索引与库漂移、产物与记录不一致），不留痕不可接受。

有意**不在**试点范围（A/B 档 best-effort，见审计报告 §3）：遥测包装器（`run_trace.emit`/`sink`）、
清理路径（interrupt / 租约 revoke / taskkill）、探活 `is_up`、以及 `comfyui_client` /
`agent_graph` 等外部边界模块 —— 它们的兜底降级是设计的一部分。

纪律：
1. 新增同模式收口时往 `PILOT_MODULES` 加模块即可（**只做加法**）；
2. 计数是**下限**（防空转）——新增 handler 不必改，**删除 handler 到低于下限会红**，
   说明锁的保护面缩水了；确属重构请同步下调，但**不得下调到 0**（那等于把锁关掉）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

# 试点模块（相对 app 的路径）→ 期望扫描到的 `except Exception` handler 数下限
PILOT_MODULES: dict[str, int] = {
    # store 层：节点包/表格行索引增删失败 = 索引与库漂移、检索质量无声退化
    "services/node_store.py": 11,
    "services/character_store.py": 4,
    # 产物持久化：写盘失败被吞 = 产物与生成记录静默不一致
    "services/generation_store.py": 30,
    "services/artifact_versions.py": 2,
    # 事实 / 世界书仓储
    "services/chat_facts.py": 2,
    "services/worldbook_store.py": 1,
}


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


def _scan(src: str) -> tuple[list[ast.ExceptHandler], list[str]]:
    """返回（模块内全部 `except Exception`，其中纯吞的 行号 列表）。"""
    tree = ast.parse(src)
    handlers = [n for n in ast.walk(tree)
                if isinstance(n, ast.ExceptHandler) and _is_bare_exc(n.type)]
    silent = [str(h.lineno) for h in handlers if _is_trivial(h)]
    return handlers, silent


def test_试点注册表非空且模块都存在():
    assert len(PILOT_MODULES) >= 5, "试点范围被改小到失去意义"
    missing = [rel for rel in PILOT_MODULES if not (APP / rel).exists()]
    assert not missing, f"试点模块不存在（路径写错会让整把锁空转）: {missing}"
    assert all(n > 0 for n in PILOT_MODULES.values()), \
        "handler 下限为 0 = 该模块的锁已空转（扫描不到任何被约束的代码）"


@pytest.mark.parametrize("rel, floor", sorted(PILOT_MODULES.items()))
def test_纯逻辑存储层禁止纯吞_except_Exception(rel: str, floor: int):
    src = (APP / rel).read_text(encoding="utf-8")
    handlers, silent = _scan(src)
    # 防空转：先证明该模块确实还有被本规则约束的代码
    assert len(handlers) >= floor, (
        f"{rel} 的 except Exception 只剩 {len(handlers)} 处（下限 {floor}）——"
        f"锁的保护面缩水了；确属重构请同步下调 PILOT_MODULES（不得改成 0）"
    )
    assert not silent, (
        f"{rel} 出现纯吞 except Exception（无外部边界的纯逻辑·存储层必须留痕或重抛，"
        f"见审计报告 §4）；行号 {silent}"
    )


def test_判据自身有牙():
    """锁的锁：判据必须抓得到纯吞形态、且不误伤留痕/重抛（防判据写错导致永久绿灯）。"""
    _, silent_pass = _scan("try:\n    f()\nexcept Exception:\n    pass\n")
    assert silent_pass, "判据抓不到 `except Exception: pass`"
    _, silent_ellipsis = _scan("try:\n    f()\nexcept Exception:\n    ...\n")
    assert silent_ellipsis, "判据抓不到 `except Exception: ...`"
    _, silent_doc = _scan('try:\n    f()\nexcept Exception:\n    """best effort"""\n')
    assert silent_doc, "判据抓不到「仅 docstring」的纯吞"
    _, ok_log = _scan("try:\n    f()\nexcept Exception as e:\n    _LOG.warning('x', e)\n")
    assert not ok_log, "判据误伤「留痕」写法"
    _, ok_raise = _scan("try:\n    f()\nexcept Exception:\n    raise\n")
    assert not ok_raise, "判据误伤「重抛」写法"
