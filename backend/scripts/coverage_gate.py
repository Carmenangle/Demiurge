"""覆盖率软门禁：全量 pytest 带 --cov 跑一遍，TOTAL 不得低于基线（scorecard P5）。

用法（在 backend/ 下，用项目 venv）：
    ./.venv/Scripts/python.exe scripts/coverage_gate.py
        完整模式：全量测试 + 覆盖率采集 + 基线比对（约 5-6 分钟，供定期/CI 用）。
        junitxml 与 coverage 数据落 ../_tmp/，判绿口径同主门禁（failures/errors=0）。

    ./.venv/Scripts/python.exe scripts/coverage_gate.py --check ../_tmp/t4_gate_cov.json
        快检模式：只读既有 coverage json 做基线比对（秒级），供跑完全量后复核。

为什么是脚本而不是 pyproject addopts：全量带 cov 比不带慢一倍以上，把 --cov 塞进
addopts 会让每次普通跑测试都付这笔钱；门禁按需跑。先软（默认 exit 0 只报告）后硬
（--enforce 低于基线 exit 2，供 CI 接入）。

环境坑（2026-09-17 实测）：coverage 默认把数据写 backend/.coverage，沙箱 safe-delete
守卫会拦它的 erase/combine ⇒ 必须 COVERAGE_FILE 外置（本脚本已内置）。

⚠ 带 --cov 跑全量时 warnings 会从 ~5 涨到数百：覆盖率插桩改变 GC 时机，把
chat_memory 等进程级单例的 sqlite 连接（设计上进程生命周期不关）的 ResourceWarning
提前到用例间释放。这是插桩伪影不是回归 —— 判绿只看 junitxml 的 failures/errors/skipped，
warnings 不作为门禁项（同日对比实测：test_agent_dispatch 单跑 0 → 带 cov 7）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# 基线：scorecard 附录 A（P5 实测 75%）。T4.1 复测 75.01%，T4.3 补测后抬升。
# 调整基线时同步更新 docs/memory/arch-review-scorecard-2026-09-16.md 附录 A。
BASELINE = 75.0

BACKEND = Path(__file__).resolve().parent.parent
JUNIT = BACKEND.parent / "_tmp" / "coverage_gate_junit.xml"
COV_FILE = BACKEND.parent / "_tmp" / "coverage_gate.coverage"
COV_JSON = BACKEND.parent / "_tmp" / "coverage_gate_cov.json"


def _total_from_json(path: Path) -> tuple[float, int, int]:
    """从 coverage json 取 TOTAL 百分比与语句数。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    s = data["totals"]
    return s["percent_covered"], s["num_statements"], s["missing_lines"]


def check_only(cov_json: Path) -> int:
    pct, stmts, miss = _total_from_json(cov_json)
    verdict = "OK" if pct >= BASELINE else "BELOW"
    print(f"coverage TOTAL = {pct:.2f}% ({stmts} stmts / {miss} miss) | "
          f"baseline {BASELINE:.0f}% -> {verdict} (source: {cov_json})")
    if pct < BASELINE:
        print("覆盖率低于基线。先看哪里掉了：`coverage report --show-missing`，"
              "确认是删测试/删分支还是新模块未补测。")
        return 2
    return 0


def full_run(enforce: bool) -> int:
    import os

    env = dict(os.environ)
    env["COVERAGE_FILE"] = str(COV_FILE)
    env["PYTHONUTF8"] = "1"
    cmd = [
        str(BACKEND / ".venv" / "Scripts" / "python.exe"), "-m", "pytest", "tests",
        "--junitxml", str(JUNIT),
        "--cov", "app", "--cov-report", "json:" + str(COV_JSON), "-q",
    ]
    print("running:", " ".join(cmd[3:]), f"(COVERAGE_FILE={COV_FILE})")
    proc = subprocess.run(cmd, cwd=BACKEND, env=env)
    if proc.returncode not in (0, 1):
        # 沙箱收尾清 tmp 被拦时 pytest 退出码可能为 1；>=2 才当测试真失败处理
        print(f"pytest exit={proc.returncode}（>=2 视为真失败，先修测试再谈覆盖率）")
        return proc.returncode

    junit = JUNIT.read_text(encoding="utf-8")
    import re

    m = re.search(r'<testsuite name="pytest" errors="(\d+)" failures="(\d+)" '
                  r'skipped="(\d+)" tests="(\d+)"', junit)
    if not m:
        print("无法解析 junitxml，请人工核对", JUNIT)
        return 1
    errors, failures, skipped, tests = (int(m.group(i)) for i in range(1, 5))
    print(f"junitxml 四数 = {tests} / {failures} / {errors} / {skipped}")
    if failures or errors or skipped:
        print("存在失败/跳过：覆盖率数字在测试不齐时无意义，先修测试。")
        return 1

    rc = check_only(COV_JSON)
    if rc == 2 and not enforce:
        print("软门禁（--enforce 可升级为硬门禁）：本次不阻断。")
        return 0
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", type=Path, default=None,
                    help="只读校验既有 coverage json（跳过跑测试）")
    ap.add_argument("--enforce", action="store_true",
                    help="低于基线时 exit 2（默认软门禁 exit 0 只报告）")
    args = ap.parse_args()
    if args.check:
        return check_only(args.check)
    return full_run(args.enforce)


if __name__ == "__main__":
    sys.exit(main())
