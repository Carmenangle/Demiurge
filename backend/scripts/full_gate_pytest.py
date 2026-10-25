"""全量 pytest 门禁：跑前/跑后快照 `app`/`tests`/`scripts` 全部 `.py` 与三个配置的 sha256，跑批期间被改写即判本轮无效。

2026-10-25 的教训：全量跑到一半，另一个会话正在写 `tests/` 里一个未跟踪文件，产出
11 条红（traceback 行号偏 2 行、模块级常量求值成空串），落定后复跑即全绿。
「跑门禁时不要并发写」是纪律、不可验证；这里把它固化成机器判据。

退出码：
  0 = 用例全绿且跑批期间源码未变
  1 = 有 pytest 失败/错误
  2 = 跑批期间源码被改写 ⇒ 本轮结论作废，独占资源后重跑

用法（在 backend 目录）：
  .venv/Scripts/python.exe scripts/full_gate_pytest.py [--junit ../_tmp/junit-full.xml]
      [--timeout 7200] [-- 透传给 pytest 的参数]
 junit 默认落在仓库根 `_tmp/`（已 gitignore），不往工作树里丢产物。
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
# scripts/ 也进面：tests 里有三个模块按路径加载它（test_hardcode_guard 用
# `from scripts.check_hardcode import scan`；test_check_silent_except /
# test_installer_release 用 importlib 载入 scripts/*.py）。不盯就等于留了一扇
# 「能改结果却查不出」的门。宁可过宽：误作废只赔一轮重跑，漏作废赔一个假绿。
WATCHED_DIRS = ("app", "tests", "scripts")
WATCHED_FILES = ("pytest.ini", "pyproject.toml", "mypy_files.txt")


def snapshot() -> dict[str, str]:
    files: dict[str, str] = {}
    for name in WATCHED_DIRS:
        root = BACKEND / name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            key = path.relative_to(BACKEND).as_posix()
            files[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in WATCHED_FILES:
        path = BACKEND / name
        if path.is_file():
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def changed_keys(before: dict[str, str], after: dict[str, str]) -> list[str]:
    touched = set(before) ^ set(after)
    touched |= {name for name in before.keys() & after.keys() if before[name] != after[name]}
    return sorted(touched)


def junit_summary(path: Path) -> str:
    if not path.is_file():
        return "junit 报告缺失"
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return f"junit 报告不可读：{exc}"
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, 0) or 0)
    return " ".join(f"{key}={totals[key]}" for key in ("tests", "failures", "errors", "skipped"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--junit", default="../_tmp/junit-full.xml")
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("pytest_args", nargs="*", help="透传给 pytest 的额外参数")
    args = parser.parse_args(argv)

    junit = (BACKEND / args.junit).resolve()
    junit.parent.mkdir(parents=True, exist_ok=True)
    before = snapshot()
    print(f"[gate] 快照 {len(before)} 个文件，开始 pytest（junit→{junit}）", flush=True)

    command = [sys.executable, "-m", "pytest", "-q", f"--junitxml={junit}", *args.pytest_args]
    started = time.time()
    try:
        code = subprocess.call(command, cwd=BACKEND, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f"[gate] 超时 {args.timeout}s，本轮判失败", flush=True)
        return 1
    elapsed = time.time() - started

    changed = changed_keys(before, snapshot())
    print(f"[gate] pytest 退出码 {code}，耗时 {elapsed:.1f}s", flush=True)
    print(f"[gate] {junit_summary(junit)}", flush=True)
    if changed:
        print("[gate] 🔴 跑批期间这些文件被改写 ⇒ 本轮结论作废，独占资源重跑：", flush=True)
        for name in changed:
            print(f"[gate]   - {name}", flush=True)
        return 2
    print("[gate] 源码在跑批期间未变，结论有效", flush=True)
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
