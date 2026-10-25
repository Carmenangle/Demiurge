"""静默宽捕获门禁（2026-10-24 审计改进项 8）：`except Exception` 若既不打日志、不再抛、
也无 `[EXC-保留]` 标注，即「静默失败」——不可观测（ComfyUI 600 秒空转事故的同族风险）。

口径（与项目既有 `# noqa: BLE001 [EXC-保留]` 标注文化对齐）：
- 只扫 `except Exception`（含 `except Exception as e`）；裸 `except:` 一律计入（项目无此风格）。
- 三者任一即不算静默：body 里有 `raise`；有日志调用（任意对象上的
  debug/info/warning/warn/error/exception/critical/log 属性调用，覆盖 `_LOG.x`/`logger.x`/
  `logging.x`）；except 行或 body 首行源码含 `[EXC-保留]` 或 `noqa: BLE001`。
- 棘轮语义：`--check` 只在「某文件计数上升」或「出现基线外新文件」时失败；下降提示刷新基线。

用法（backend/ 下）：
  python scripts/check_silent_except.py                     # 打印计数表
  python scripts/check_silent_except.py --write-baseline    # 写/刷新基线
  python scripts/check_silent_except.py --check             # 门禁（对 scripts/silent_except_baseline.json）
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
import warnings
from pathlib import Path

LOG_ATTRS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
MARKERS = ("EXC-保留", "noqa: BLE001")
BASELINE_PATH = Path(__file__).with_name("silent_except_baseline.json")


def _has_logging_call(body: list[ast.stmt]) -> bool:
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LOG_ATTRS:
                return True
    return False


def _has_raise(body: list[ast.stmt]) -> bool:
    return any(isinstance(node, ast.Raise) for stmt in body for node in ast.walk(stmt))


def _has_marker(source_lines: list[str], handler: ast.ExceptHandler) -> bool:
    lines = [handler.lineno - 1]
    if handler.body:
        lines.append(handler.body[0].lineno - 1)
    return any(any(marker in source_lines[i] for marker in MARKERS) for i in lines if 0 <= i < len(source_lines))


def scan_file(path: Path) -> list[int]:
    """返回该文件所有静默宽捕获的行号。语法错误按空处理（ruff/mypy 另有门禁）。"""
    try:
        source = path.read_text(encoding="utf-8")
        with warnings.catch_warnings():
            # 待修文件里的 invalid escape（SyntaxWarning）与本门禁无关，别污染输出
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    lines = source.splitlines()
    silent: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        bare_broad = node.type is None or (
            isinstance(node.type, ast.Name) and node.type.id == "Exception"
        )
        if not bare_broad:
            continue
        if _has_raise(node.body) or _has_logging_call(node.body) or _has_marker(lines, node):
            continue
        silent.append(node.lineno)
    return silent


def scan_tree(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        hits = scan_file(path)
        if hits:
            counts[path.relative_to(root.parent).as_posix()] = len(hits)
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def compare(current: dict[str, int], baseline: dict[str, int]) -> list[str]:
    problems = []
    for f, n in current.items():
        base = baseline.get(f, 0)
        if n > base:
            problems.append(f"回归：{f} 静默捕获 {base} → {n}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-baseline", action="store_true", help="把当前计数写进基线文件")
    parser.add_argument("--check", action="store_true", help="棘轮门禁：任一文件计数上升即失败")
    parser.add_argument("--root", default="app", help="扫描根（默认 app/）")
    args = parser.parse_args(argv)

    counts = scan_tree(Path(args.root))
    total = sum(counts.values())
    for f, n in counts.items():
        print(f"{n:4d}  {f}")
    print(f"总计 {total} 处静默宽捕获（{len(counts)} 个文件）")

    if args.write_baseline:
        BASELINE_PATH.write_text(
            json.dumps({"counts": counts, "total": total}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"基线已写入 {BASELINE_PATH}")
        return 0
    if args.check:
        if not BASELINE_PATH.is_file():
            print(f"缺少基线文件 {BASELINE_PATH}，先跑 --write-baseline")
            return 1
        data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        baseline = {k: int(v) for k, v in data.get("counts", {}).items()}
        problems = compare(counts, baseline)
        if problems:
            print("\n".join(problems))
            print("门禁失败：新增静默宽捕获必须打日志、上抛，或标注 [EXC-保留] 说明理由")
            return 1
        improved = [f for f, b in baseline.items() if counts.get(f, 0) < b]
        if improved:
            print(f"计数下降（建议刷新基线 --write-baseline）：{', '.join(improved)}")
        print("门禁通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
