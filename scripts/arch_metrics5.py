"""测试面覆盖度代理 + 契约锁强度指标（只读）。

来源：复评指标脚本（T5.4 自 `_tmp/` 迁入，原样保真，仅 ROOT 改为仓库根推导）。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BE = ROOT / "backend"
APP = BE / "app"
OUT = []


def nlines(p):
    return sum(1 for _ in p.open(encoding="utf-8", errors="replace"))


# 1) services 模块 → 是否有同名/近名测试文件（覆盖代理，非真实覆盖率）
services = sorted(p.stem for p in (APP / "services").glob("*.py"))
tests = sorted((BE / "tests").glob("*.py"))
test_names = " ".join(p.stem for p in tests)
hit, miss = [], []
for s in services:
    if s in test_names:
        hit.append(s)
    else:
        miss.append(s)
OUT.append(f"services 模块 {len(services)} 个；测试文件名中出现同名 = {len(hit)}（{100*len(hit)/len(services):.0f}%）")
OUT.append(f"    未命中（无同名测试文件）{len(miss)} 个，前 25：{miss[:25]}")

# 2) 契约/形态锁特征：测试里读生产源码 + ast.parse + 断言形态
feat = {"读源码(read_text/readFileSync)": 0, "stub 掉网络(monkeypatch/httpx)": 0,
        "tripwire/形态锁(read_text+assert)": 0}
files_read_src = []
for p in tests:
    src = p.read_text(encoding="utf-8", errors="replace")
    if re.search(r"read_text\(|Path\(.*__file__\)|inspect\.getsource", src):
        files_read_src.append(p.name)
OUT.append(f"    后端「读生产源码做断言」的测试文件 = {len(files_read_src)}：{files_read_src}")

fe = ROOT / "frontend" / "src"
fe_read = [p.name for p in fe.rglob("*.test.ts*") if "readFileSync" in p.read_text(encoding="utf-8", errors="replace")]
OUT.append(f"    前端「读生产源码做断言(readFileSync)」的测试文件 = {len(fe_read)}：{fe_read}")

# 3) conftest autouse 夹具数（测试隔离强度）
cf = BE / "tests" / "conftest.py"
src = cf.read_text(encoding="utf-8", errors="replace")
auto = re.findall(r"@pytest\.fixture\(([^)]*)\)", src)
autouse = [a for a in auto if "autouse" in a]
OUT.append(f"    conftest fixture 总数 = {len(auto)}；其中 autouse = {len(autouse)}")
OUT.append(f"    conftest 行数 = {nlines(cf)}")

# 4) 前端 e2e 与 manual 套件
OUT.append("")
ro = ROOT
for pat in ("frontend/e2e-manual/*.spec.ts", "frontend/e2e/*.spec.ts", "frontend/playwright*.ts"):
    OUT.append(f"    {pat}: {len(list(ro.glob(pat)))} 个")

(ROOT / "_tmp" / "arch_metrics5.txt").write_text("\n".join(OUT), encoding="utf-8")
print("OK")
