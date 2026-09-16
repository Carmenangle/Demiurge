#!/usr/bin/env python3
"""重算技术手册里「行数」声明 + 打印覆盖单元表所需的真值。

背景（2026-09-15 / 09-16 两次架构审计实测）：`docs/tech-manual` 各册的
「关键文件（真实路径 + 行数）」表长期手写，与真实行数**普遍差 1.5–2 倍**
（`agent_graph.py` 文档 3049 / 实际 5183 等）——是全表性陈旧，不是个别漏改。
本脚本是唯一重算口径，避免「手改一遍、下次又旧」的循环。

## 安全边界（踩过才定的三条）
1. **只改「表头含『行数』的那一列」**。同一张表里还有 `` `tests/test_x.py`（4 条） `` 这类
   **用例条数**声明——按路径+括号数字盲目替换会把「4 条」写成「517」。故按列定位。
2. **括号内容含「条 / 个 / 处 / 例」时一律跳过**（双保险）。
3. **写回走 bytes**（先探测 CRLF/LF），不用 `Path.write_text` —— 后者会归一换行，
   在 `core.autocrlf=true` 下 `git diff` 看不出来，是最容易带进提交的一类污染。

## 口径
行数 = `sum(1 for _ in open(f, encoding='utf-8', errors='replace'))`（含末行）。
⚠ 不要用 PowerShell `Get-Content | Measure-Object -Line`（不计空行，实测把 212 数成 199）。

## 已知有意不处理
`HANDOFF-PARALLEL.md` 用行内叙述体（形如「- x.py（3049，重点读…）」），
不是表格、且属交接文档，误改风险高于收益 ⇒ 跳过（脚本会打印跳过项）。

用法：
    python scripts/recount_doc_metrics.py            # 只报告差异（默认，不写盘）
    python scripts/recount_doc_metrics.py --apply    # 写回
    python scripts/recount_doc_metrics.py --facts    # 打印覆盖单元表所需真值
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TECH = ROOT / "docs" / "tech-manual"
SKIP_FILES = {"HANDOFF-PARALLEL.md"}

HEADER_HINT = "行数"
NOT_LINE_HINT = ("条", "个", "处", "例")
MIN_LINES = 10  # 任何源文件都远大于此；防「(4)」这类条数混入

DECL = re.compile(r"`(?P<path>[^`\n]+?\.(?:py|ts|tsx|js))`\s*（(?P<num>\d+)(?P<tail>[^）]*)）")


def n_lines(p: Path) -> int:
    return sum(1 for _ in p.open(encoding="utf-8", errors="replace"))


def resolve(rel: str) -> Path | None:
    """路径可能写成：仓库根相对 / backend 相对 / frontend 相对 / app 相对 / src 相对。

    兜底：文档里常只写**裸文件名**（如 `worldbook_store.py` / `characters.py`）——
    按文件名在 `backend/app` 与 `frontend/src` 下找**唯一**匹配；命中 0 个或多个则放弃
    （宁可漏改，不可改错）。
    """
    rel = rel.strip().lstrip("/")
    for base in (ROOT, ROOT / "backend", ROOT / "frontend",
                 ROOT / "backend" / "app", ROOT / "frontend" / "src"):
        cand = base / rel
        if cand.is_file():
            return cand
    if "*" in rel:
        return None
    name = Path(rel).name
    found = [p for root in (ROOT / "backend" / "app", ROOT / "frontend" / "src")
             for p in root.rglob(name) if p.is_file() and "__pycache__" not in p.parts]
    return found[0] if len(found) == 1 else None


def cell_spans(line: str) -> list[tuple[int, int]]:
    """返回一行 markdown 表格各单元格的 (start, end) 偏移（不含分隔用的 `|`）。"""
    spans, cur = [], None
    for i, ch in enumerate(line):
        if ch == "|":
            if cur is not None:
                spans.append((cur, i))
            cur = i + 1
    if cur is not None:
        spans.append((cur, len(line)))
    return spans


def is_sep_row(cells: list[str]) -> bool:
    return bool(cells) and all(c.strip() and set(c.strip()) <= set("-: ") for c in cells)


def scan(md: Path) -> tuple[list[tuple[int, int, int, str, int, int]], list[str]]:
    """返回：[(行号, 列起始, 列结束, path, 声明值, 实际值)] 与 未解析清单。"""
    hits: list[tuple[int, int, int, str, int, int]] = []
    unresolved: list[str] = []
    raw = md.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    header: list[str] | None = None
    offset = 0  # 行在 text 中的起始偏移
    for ln_no, line in enumerate(text.split("\n"), 1):
        if line.startswith("|"):
            spans = cell_spans(line)
            cells = [line[s:e] for s, e in spans]
            if is_sep_row(cells):
                offset += len(line) + 1
                continue
            if header is None:
                header = cells
            elif len(cells) == len(header):
                for idx, h in enumerate(header):
                    if HEADER_HINT not in h:
                        continue
                    s, e = spans[idx]
                    for m in DECL.finditer(line, s, e):
                        rel, declared = m.group("path"), int(m.group("num"))
                        if declared < MIN_LINES or any(t in m.group("tail") for t in NOT_LINE_HINT):
                            continue
                        f = resolve(rel)
                        if f is None:
                            unresolved.append(f"{md.name}:{ln_no} `{rel}`")
                            continue
                        actual = n_lines(f)
                        if actual != declared:
                            hits.append((ln_no, m.start(), m.end(), rel, declared, actual))
        else:
            header = None
        offset += len(line) + 1
    return hits, unresolved


def check(apply: bool) -> int:
    per_file: dict[Path, list] = {}
    unresolved: list[str] = []
    scanned = 0
    for md in sorted(TECH.rglob("*.md")):
        if md.name in SKIP_FILES:
            print(f"[跳过] {md.relative_to(ROOT).as_posix()}（行内叙述体，有意不自动改）")
            continue
        scanned += 1
        hits, unres = scan(md)
        unresolved += unres
        if hits:
            per_file[md] = hits

    total_hits = sum(len(v) for v in per_file.values())
    print(f"\n[扫描] {scanned} 篇手册；**行数列中与实际不符 {total_hits} 处**，"
          f"跨 {len(per_file)} 个文件；无法解析路径 {len(unresolved)} 处")

    if not apply:
        for md, hits in per_file.items():
            print(f"\n  {md.relative_to(ROOT).as_posix()}（{len(hits)} 处）")
            for ln_no, _s, _e, rel, d, a in hits:
                print(f"      L{ln_no}  `{rel}`  {d} → {a}")
        if unresolved:
            print("\n  [未解析路径]")
            for u in unresolved:
                print(f"      {u}")
        print("\n（未写盘；加 --apply 写回）")
        return 0

    for md, hits in per_file.items():
        text = md.read_bytes().decode("utf-8", errors="replace")
        lines = text.split("\n")
        done = 0
        # 同一行多处：从后往前替换，避免偏移失效
        for ln_no, s, e, rel, d, a in sorted(hits, key=lambda t: (t[0], -t[1])):
            line = lines[ln_no - 1]
            seg = line[s:e]
            old, new = f"（{d}", f"（{a}"
            if seg.count(old) != 1:
                print(f"   [跳过] {md.name} L{ln_no} `{rel}`（{d}：单元格内命中 {seg.count(old)} 次")
                continue
            lines[ln_no - 1] = line[:s] + seg.replace(old, new) + line[e:]
            done += 1
        md.write_bytes("\n".join(lines).encode("utf-8"))
        print(f"   已写回 {md.relative_to(ROOT).as_posix()}（{done}/{len(hits)} 处）")
    return 0


def facts() -> int:
    def cnt(pat: str, base: Path) -> int:
        return len(sorted(base.glob(pat)))

    print("== 覆盖单元表真值 ==")
    print(f"backend/tests/test_*.py           = {cnt('test_*.py', ROOT / 'backend' / 'tests')} 个文件")
    fe = ROOT / "frontend" / "src"
    fe_tests = [p for p in fe.rglob("*") if p.suffix in (".ts", ".tsx") and ".test." in p.name]
    print(f"frontend/src/**/*.test.ts(x)      = {len(fe_tests)} 个文件")
    dm = ROOT / "docs" / "memory"
    print(f"docs/memory/*.md 顶层 / 递归       = {cnt('*.md', dm)} / {len(list(dm.rglob('*.md')))} 篇")
    print(f"docs/memory/MEMORY.md 行数         = {n_lines(dm / 'MEMORY.md')}")
    il = ROOT / "backend" / ".importlinter"
    if il.is_file():
        t = il.read_bytes().decode("utf-8", errors="replace")
        print(f"backend/.importlinter 行数 / 合同数 = {n_lines(il)} 行 / "
              f"{len(re.findall(r'^\[importlinter:contract:', t, re.M))} 条")
    print(f"scripts/*.py                      = {cnt('*.py', ROOT / 'scripts')} 个")

    print("\n== ARCHITECTURE.md 分区行号（区间 = 本行 ~ 下一分区前一行）==")
    lines = (ROOT / "ARCHITECTURE.md").read_bytes().decode("utf-8", errors="replace").splitlines()
    heads = [(i + 1, ln[3:].strip()) for i, ln in enumerate(lines) if ln.startswith("## ")]
    for idx, (no, title) in enumerate(heads):
        end = (heads[idx + 1][0] - 1) if idx + 1 < len(heads) else len(lines)
        print(f"   {no:>4}–{end:<4} {title}")
    print(f"   （总行数 {len(lines)}）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="重算技术手册行数声明 / 打印覆盖单元表真值")
    ap.add_argument("--apply", action="store_true", help="写回文件（默认只报告差异）")
    ap.add_argument("--facts", action="store_true", help="只打印覆盖单元表所需真值")
    a = ap.parse_args()
    return facts() if a.facts else check(a.apply)


if __name__ == "__main__":
    sys.exit(main())
