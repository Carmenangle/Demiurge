"""架构评分用量化指标采集（只读，不写仓库）。

口径：行数 = sum(1 for _ in open(...))，含末行（与技术手册口径一致）。

来源：复评指标脚本（2026-09-16 复评留档，T5.4 自 `_tmp/` 迁入，
见 docs/memory/arch-review-scorecard-2026-09-16.md「复评建议」）。
下次复评重跑本组脚本（arch_metrics*.py），重点五个数字：
① agent_graph.py 行数与最大函数行数；② 两个前端巨物行数；③ except Exception 处数；
④ 技术手册行数表重算（scripts/recount_doc_metrics.py）；⑤ 覆盖率 TOTAL（backend/scripts/coverage_gate.py）。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = []


def nlines(p: Path) -> int:
    try:
        return sum(1 for _ in p.open(encoding="utf-8", errors="replace"))
    except OSError:
        return 0


def sec(title):
    OUT.append("")
    OUT.append(f"===== {title} =====")


def listing(name, files, top=12):
    rows = sorted(((nlines(f), f) for f in files), key=lambda t: -t[0])
    total = sum(n for n, _ in rows)
    OUT.append(f"--- {name}: {len(rows)} 文件 / {total} 行 / 均值 {total//max(1,len(rows))} 行")
    for n, f in rows[:top]:
        OUT.append(f"    {n:>6}  {f.relative_to(ROOT).as_posix()}")
    return total, rows


sec("A. 后端规模")
be = ROOT / "backend"
app = be / "app"
app_all = sorted(app.rglob("*.py"))
routers = sorted((app / "routers").glob("*.py"))
services = sorted((app / "services").glob("*.py"))
tests = sorted((be / "tests").glob("*.py"))

t_all, rows_all = listing("app/**/*.py 全部", app_all, top=18)
t_r, _ = listing("app/routers", routers, top=6)
t_s, _ = listing("app/services", services, top=6)
t_t, _ = listing("tests/*.py", tests, top=6)

OUT.append("")
OUT.append(f"routes/services 行数比 = {t_r}/{t_s} = {t_r/max(1,t_s):.3f}（越小越『路由薄』）")
OUT.append(f"测试/生产 行数比 = {t_t}/{t_all} = {t_t/max(1,t_all):.3f}")
OUT.append(f"services 模块数 = {len(services)}，routers 模块数 = {len(routers)}")

big = [(n, f) for n, f in rows_all if n >= 1000]
OUT.append(f"app 下 ≥1000 行的文件数 = {len(big)}；≥2000 行 = {len([1 for n,_ in rows_all if n>=2000])}")
OUT.append(f"app 下 ≤50 行的文件数 = {len([1 for n,_ in rows_all if n<=50])}")

sec("B. 测试规模")
cnt_tests = 0
for f in tests:
    txt = f.read_text(encoding="utf-8", errors="replace")
    cnt_tests += len(re.findall(r"^\s*def test_", txt, re.MULTILINE))
OUT.append(f"tests 文件数 = {len(tests)}；def test_ 计数 = {cnt_tests}（与 junitxml 2821 对照）")

sec("C. 前端规模")
fe = ROOT / "frontend" / "src"
fe_all = [f for f in sorted(fe.rglob("*")) if f.suffix in (".ts", ".tsx")]
prod = [f for f in fe_all if ".test." not in f.name and ".spec." not in f.name]
tst = [f for f in fe_all if ".test." in f.name or ".spec." in f.name]
fe_src_t, fe_rows = listing("src 生产代码 (.ts/.tsx 去测试)", prod, top=14)
fe_t_t, _ = listing("src 测试代码", tst, top=0 if False else 4)

for sub in ("components", "lib", "views", "api", "stores", "hooks"):
    d = fe / sub
    if d.is_dir():
        fs = [f for f in sorted(d.rglob("*")) if f.suffix in (".ts", ".tsx") and ".test." not in f.name]
        tt = sum(nlines(f) for f in fs)
        OUT.append(f"--- src/{sub}: {len(fs)} 文件 / {tt} 行")

cnt_fe = 0
for f in tst:
    txt = f.read_text(encoding="utf-8", errors="replace")
    cnt_fe += len(re.findall(r"^\s*(?:it|test)\(", txt, re.MULTILINE))
OUT.append(f"前端测试文件 = {len(tst)}；it/test 计数 = {cnt_fe}（与 1021 例对照）")
contract = [f for f in fe_all if ".contract.test." in f.name]
OUT.append(f"前端 contract 测试文件 = {len(contract)}: {[f.name for f in contract]}")

sec("D. 架构纪律硬指标")
def count(pattern, files, flags=0):
    hits = 0
    files_hit = []
    rx = re.compile(pattern, flags)
    for f in files:
        c = len(rx.findall(f.read_text(encoding="utf-8", errors="replace")))
        if c:
            hits += c
            files_hit.append(f"{f.relative_to(ROOT).as_posix()}:{c}")
    return hits, files_hit

h, fs = count(r"from app\.routers|import app\.routers", app_all)
OUT.append(f"services/其他 反向依赖 routers = {h} {fs}  (期望 0)")
h, fs = count(r"^\s*except\s*:", app_all, re.MULTILINE)
OUT.append(f"裸 except: = {h} {fs}")
h, fs = count(r"except Exception", app_all)
OUT.append(f"except Exception 计数 = {h}（分布于 {len(fs)} 文件）")
h, fs = count(r"\bTODO\b|\bFIXME\b|\bXXX\b", app_all)
OUT.append(f"TODO/FIXME/XXX（后端 app）= {h} {fs}")
h, fs = count(r"^\s*print\(", app_all, re.MULTILINE)
OUT.append(f"生产代码 print( = {h} {fs}")
h, fs = count(r"time\.sleep\(", app_all)
OUT.append(f"生产代码 time.sleep( = {h} {fs}")
h, fs = count(r"from typing import|import typing", app_all)
OUT.append(f"typing import 面（类型标注强度代理）= {h} 文件")

sec("E. 文档规模")
docs = ROOT / "docs"
d_all = sorted(docs.rglob("*.md"))
d_t, d_rows = listing("docs/**/*.md", d_all, top=10)
tm = sorted((docs / "tech-manual").rglob("*.md")) if (docs / "tech-manual").is_dir() else []
dm = sorted((docs / "memory").glob("*.md")) if (docs / "memory").is_dir() else []
OUT.append(f"docs/tech-manual: {len(tm)} 篇 / {sum(nlines(f) for f in tm)} 行")
OUT.append(f"docs/memory: {len(dm)} 篇 / {sum(nlines(f) for f in dm)} 行")
for name in ("ARCHITECTURE.md", "CONTEXT.md", "MEMORY.md", "AGENTS.md", "README.md"):
    p = ROOT / name
    if p.exists():
        OUT.append(f"{name}: {nlines(p)} 行")

sec("F. 一致性快照")
OUT.append(f"app 总行数 {t_all} / tests 总行数 {t_t} / 前端生产 {fe_src_t}")

(ROOT / "_tmp" / "arch_metrics.txt").write_text("\n".join(OUT), encoding="utf-8")
print("OK")
