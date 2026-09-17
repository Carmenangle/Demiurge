"""对上一轮统计中易误报的三项做 AST 级精确复核（只读）。

来源：复评指标脚本（T5.4 自 `_tmp/` 迁入，原样保真，仅 ROOT 改为仓库根推导）。
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"
OUT = []


def parse(f):
    return ast.parse(f.read_text(encoding="utf-8", errors="replace"))


def rel(f):
    return f.relative_to(ROOT).as_posix()


# ---------- 1. 真正的「services → routers」反向依赖（AST，排除 routers 自身与 main 装配） ----------
OUT.append("===== 1. 反向依赖精确定位（AST） =====")
layers = {
    "services": sorted((APP / "services").rglob("*.py")),
    "其他(app 顶层, 排除 main.py)": [f for f in sorted(APP.glob("*.py")) if f.name != "main.py"],
}
for layer, files in layers.items():
    hits = []
    for f in files:
        try:
            tree = parse(f)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.routers"):
                hits.append(f"{rel(f)}:{node.lineno} from {node.module} import ...")
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("app.routers"):
                        hits.append(f"{rel(f)}:{node.lineno} import {a.name}")
    OUT.append(f"[{layer}] 违规 {len(hits)} 处")
    for h in hits:
        OUT.append("    " + h)

# 路由之间互相 import（不是违规，但是耦合信号）
out_e = 0
edges = []
for f in sorted((APP / "routers").glob("*.py")):
    try:
        tree = parse(f)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        mod = getattr(node, "module", None) or ""
        if isinstance(node, ast.ImportFrom) and mod.startswith("app.routers"):
            out_e += 1
            edges.append(f"{f.name} -> {mod}")
OUT.append(f"[routers 之间互相 import] 边数 = {out_e}")
for e in edges[:12]:
    OUT.append("    " + e)

# ---------- 2. time.sleep 是否落在 async 函数内（阻塞事件循环 = 真问题） ----------
OUT.append("")
OUT.append("===== 2. time.sleep 位置与宿主函数（async 内 = 阻塞事件循环） =====")
for f in sorted((APP / "services").rglob("*.py")):
    src = f.read_text(encoding="utf-8", errors="replace")
    if "time.sleep(" not in src:
        continue
    try:
        tree = parse(f)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fn = sub.func
                name = ""
                if isinstance(fn, ast.Attribute):
                    name = f"{getattr(fn.value,'id','')}.{fn.attr}"
                if name == "time.sleep":
                    kind = "ASYNC(阻塞!)" if isinstance(node, ast.AsyncFunctionDef) else "sync"
                    OUT.append(f"    {rel(f)}:{sub.lineno} in {node.name}() → {kind}")

# ---------- 3. except Exception 分布（按目录聚合，看是否集中于 IO/外部边界） ----------
OUT.append("")
OUT.append("===== 3. except Exception 分布（按目录） =====")
agg = {}
files_with = {}
for f in sorted(APP.rglob("*.py")):
    c = len(re.findall(r"except\s+Exception", f.read_text(encoding="utf-8", errors="replace")))
    if not c:
        continue
    key = f.relative_to(APP).parts[0] if len(f.relative_to(APP).parts) > 1 else "(app 顶层)"
    agg[key] = agg.get(key, 0) + c
    files_with[key] = files_with.get(key, 0) + 1
for k in sorted(agg, key=lambda x: -agg[x]):
    OUT.append(f"    {k:<14} {agg[k]:>4} 处 / {files_with[k]} 文件")

# ---------- 4. agent_graph.py 内部结构（巨文件是否内聚） ----------
OUT.append("")
OUT.append("===== 4. 巨文件内部结构 =====")
for name, limit in ((APP / "services" / "agent_graph.py", 0),):
    tree = parse(name)
    tops = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    funcs = [n for n in tops if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    classes = [n for n in tops if isinstance(n, ast.ClassDef)]
    OUT.append(f"    agent_graph.py 顶层定义 {len(tops)} 个（函数 {len(funcs)} / 类 {len(classes)}）")
    sizes = []
    for n in funcs:
        sizes.append((getattr(n, "end_lineno", n.lineno) - n.lineno + 1, n.name))
    sizes.sort(reverse=True)
    OUT.append("    最大的 8 个函数：")
    for s, nm in sizes[:8]:
        OUT.append(f"        {s:>4} 行  {nm}()")

for name in (ROOT / "frontend" / "src" / "views" / "CanvasStageFlow.tsx",
             ROOT / "frontend" / "src" / "lib" / "useChatSession.ts"):
    txt = name.read_text(encoding="utf-8", errors="replace")
    comp = len(re.findall(r"\bfunction\s+\w+|\bconst\s+\w+\s*=\s*(?:async\s*)?\(", txt))
    hooks = len(re.findall(r"\buse(?:State|Effect|Ref|Memo|Callback|Reducer)\(", txt))
    OUT.append(f"    {name.name}: 函数/组件定义约 {comp} 个；hook 调用 {hooks} 次")

(ROOT / "_tmp" / "arch_metrics2.txt").write_text("\n".join(OUT), encoding="utf-8")
print("OK")
