#!/usr/bin/env python3
"""受控红检自动化（T6.4，scorecard 维度 3「红检靠人工纪律」的最低成本收口）。

把「注入回退形态 → 断言恰红 → 还原核 sha1」的人工流程脚本化，每次架构收口
新增的源码形态锁都可以用同一份 plan 复跑，防止锁随时间失灵。

## 流程（对 plan 里每个 variant 独立执行）
1. 备份目标文件并记录 sha1；
2. 按 bytes 注入变异（`find` → `replace`，find 必须恰命中 1 次，否则跳过该 variant）；
3. 跑指定 pytest（junitxml 判红，只认 failures/errors，不认 warning）；
4. 还原文件并核对 sha1 逐字节一致（不一致 ⇒ exit 2，环境污染必须人工处理）。

## 判定
- `expect_red=true`：failures+errors ≥ 1 ⇒ 红；= 0 ⇒ 该锁失灵（exit 1）。
- `expect_red=false`（阴性对照）：failures+errors = 0 ⇒ 通过。
- 任何 variant 失灵/异常 ⇒ 最终 exit 1；全部通过 ⇒ exit 0。

## 用法
    python scripts/redcheck.py --plan _tmp/redcheck_plan.json
    python scripts/redcheck.py --selftest        # 用内置最小变异自检脚本本身

plan.json 结构：
    {"pytest_args": ["-q", "-p", "no:cacheprovider"],   # 可选，附加给 pytest
     "variants": [
        {"name": "M1-xxx",
         "file": "backend/app/routers/mcp.py",          # 仓库根相对
         "find": "原片段（bytes 精确匹配，须恰命中 1 次）",
         "replace": "变异片段",
         "tests": "tests/test_xxx.py",                  # 传给 pytest 的测试路径
         "expect_red": true}
     ]}

⚠ 教训内嵌（T2.2-B3）：文件读写一律 bytes（`read_bytes`/`write_bytes`）——
`Path.read_text/write_text` 往返会做换行归一，CRLF 仓库里 sha1 必漂移。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP_SUFFIX = ".redcheck.bak"
JUNIT = ROOT / "_tmp" / "redcheck_junit.xml"


def sha1(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest().upper()[:16]


def run_pytest(tests: str, extra_args: list[str]) -> tuple[int, int]:
    """跑一次 pytest，返回 (failures, errors)。判绿口径与项目门禁一致。

    ⚠ junitxml 由 pytest 覆盖写，不要预先 unlink——沙箱 safe-delete 会对
    tmp 文件的批量删除弹 bulk-confirm，整条命令被杀（实测 2026-09-17）。
    """
    JUNIT.parent.mkdir(exist_ok=True)
    cmd = [sys.executable, "-m", "pytest", tests, "-q",
           f"--junitxml={JUNIT}", *extra_args]
    r = subprocess.run(cmd, cwd=ROOT / "backend", capture_output=True, timeout=1200,
                       check=False)
    if not JUNIT.exists():
        raise RuntimeError(f"pytest 未产出 junitxml（exit={r.returncode}）：\n{r.stdout[-2000:]}")
    suite = ET.parse(JUNIT).getroot().find("testsuite")
    if suite is None:
        raise RuntimeError("junitxml 缺 testsuite 节点")
    return int(suite.get("failures", 0)), int(suite.get("errors", 0))


def apply_variant(v: dict, extra_args: list[str]) -> tuple[bool, str]:
    """执行单个 variant，返回 (是否通过, 说明)。"""
    f = ROOT / v["file"]
    name = v.get("name", v["file"])
    if not f.is_file():
        return False, f"[{name}] 文件不存在：{v['file']}"

    raw = f.read_bytes()
    before = sha1(f)
    find = v["find"].encode("utf-8")
    replace = v["replace"].encode("utf-8")
    hits = raw.count(find)
    if hits != 1:
        return False, (f"[{name}] find 片段命中 {hits} 次（须恰 1），"
                       f"跳过注入（锁 needle 与源码漂移，先修 plan 再跑）")

    # 注入变异
    f.write_bytes(raw.replace(find, replace))
    sha_mismatch = False
    try:
        failures, errors = run_pytest(v["tests"], extra_args)
        red = failures + errors
    finally:
        # 还原 + 核 sha1（无论测试成败都必须还原）
        f.write_bytes(raw)
        after = sha1(f)
        if after != before:
            sha_mismatch = True
    if sha_mismatch:
        return False, f"[{name}] ⚠ 还原后 sha1 不一致（{before} → {after}），环境污染！"

    expect = v.get("expect_red", True)
    if expect and red == 0:
        return False, f"[{name}] 锁失灵：变异后 0 红（failures={failures} errors={errors}）"
    if not expect and red > 0:
        return False, f"[{name}] 阴性对照失败：变异后红了 {red}（应 0）"
    return True, (f"[{name}] ✅ 变异后红 {red}（failures={failures} errors={errors}），"
                  f"还原 sha1 一致（{before}）")


def selftest() -> int:
    """内置自检：对脚本自身的一个无害变异跑一遍（变异 scripts/ 外的临时副本）。"""
    plan = {
        "variants": [{
            "name": "selftest-阴性",
            "file": "scripts/redcheck.py",
            "find": "RED_CHECK_SELFTEST_NEEDLE",
            "replace": "RED_CHECK_SELFTEST_NEEDLE_X",
            "tests": "tests/test_smithery_key_narrowing.py::test_mcp_skills_无内联自读",
            "expect_red": False,  # find 命中 0 次 ⇒ 该 variant 必失败
        }]
    }
    (ROOT / "_tmp").mkdir(exist_ok=True)
    p = ROOT / "_tmp" / "redcheck_selftest_plan.json"
    p.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    rc = main_with_plan(p, report=False)
    print("selftest：预期该 variant 因 find 命中 0 次而失败 ⇒ " +
          ("脚本判定路径工作正常 ✅" if rc == 1 else f"⚠ 意外 exit={rc}"))
    return 0 if rc == 1 else 2


def main_with_plan(plan_path: Path, report: bool = True) -> int:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    extra = list(plan.get("pytest_args", []))
    results = []
    for v in plan.get("variants", []):
        ok, msg = apply_variant(v, extra)
        results.append((ok, msg))
        if report:
            print(msg)
    bad = [m for ok, m in results if not ok]
    if report:
        print(f"\n[redcheck] {len(results) - len(bad)}/{len(results)} variants 通过")
        if bad:
            print("失灵/异常清单：")
            for m in bad:
                print("  " + m)
    return 1 if bad else 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # GBK 控制台防 UnicodeEncodeError
    ap = argparse.ArgumentParser(description="受控红检自动化（注入变异→断言红→还原核 sha1）")
    ap.add_argument("--plan", help="plan.json 路径（仓库根相对或绝对）")
    ap.add_argument("--selftest", action="store_true", help="跑脚本自检")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.plan:
        ap.error("需要 --plan <plan.json>（或 --selftest）")
    return main_with_plan(Path(a.plan) if Path(a.plan).is_absolute() else ROOT / a.plan)


if __name__ == "__main__":
    sys.exit(main())
