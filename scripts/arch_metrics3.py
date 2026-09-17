"""两处真风险点的定向核查（只读）：
1) 路由层共享模块 ai_common 的规模与性质（是否藏业务逻辑）
2) llm.chat* 里的 time.sleep 是否落在「async 调用链」上（会阻塞事件循环）

来源：复评指标脚本（T5.4 自 `_tmp/` 迁入，原样保真，仅 ROOT 改为仓库根推导）。
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"
OUT = []


def parse(f):
    return ast.parse(f.read_text(encoding="utf-8", errors="replace"))


# ---------- 1. ai_common ----------
OUT.append("===== 1. routers/ai_common.py（路由层共享模块） =====")
p = APP / "routers" / "ai_common.py"
if p.exists():
    src = p.read_text(encoding="utf-8", errors="replace")
    tree = parse(p)
    tops = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    OUT.append(f"    行数 = {sum(1 for _ in src.splitlines())}；顶层定义 {len(tops)} 个")
    for n in tops:
        kind = "async def" if isinstance(n, ast.AsyncFunctionDef) else ("def" if isinstance(n, ast.FunctionDef) else "class")
        size = getattr(n, "end_lineno", n.lineno) - n.lineno + 1
        OUT.append(f"        {kind:<9} {n.name}()  {size} 行")
    OUT.append("    前 25 行原文：")
    for i, line in enumerate(src.splitlines()[:25], 1):
        OUT.append(f"        {i:>4}| {line}")

OUT.append("")
OUT.append("===== 2. llm.py 的 sleep 与调用链形态 =====")
llm = APP / "services" / "llm.py"
tree = parse(llm)
src_lines = llm.read_text(encoding="utf-8", errors="replace").splitlines()
for n in ast.walk(tree):
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in ("chat", "chat_messages", "chat_messages_stream"):
        kind = "async def" if isinstance(n, ast.AsyncFunctionDef) else "def"
        OUT.append(f"    {kind} {n.name}()  @行 {n.lineno}")

# 全仓找 chat_messages / chat_messages_stream 的调用点，判断宿主是否 async
callers = []
for f in sorted(APP.rglob("*.py")):
    if f.name == "llm.py":
        continue
    try:
        t = parse(f)
    except SyntaxError:
        continue
    parents = {}
    for node in ast.walk(t):
        for ch in ast.iter_child_nodes(node):
            parents[ch] = node
    for node in ast.walk(t):
        if isinstance(node, ast.Call):
            fn = node.func
            nm = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if nm in ("chat_messages", "chat_messages_stream"):
                host = None
                cur = parents.get(node)
                while cur is not None:
                    if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        host = cur
                        break
                    cur = parents.get(cur)
                hk = "?"
                if isinstance(host, ast.AsyncFunctionDef):
                    hk = "ASYNC"
                elif isinstance(host, ast.FunctionDef):
                    hk = "sync"
                callers.append(f"{f.relative_to(ROOT).as_posix()}:{node.lineno} {nm}() in {getattr(host,'name','<module>')} [{hk}]")
OUT.append(f"    llm.chat_messages* 调用点 {len(callers)} 处：")
for c in callers[:20]:
    OUT.append("        " + c)
async_c = [c for c in callers if "[ASYNC]" in c]
OUT.append(f"    ⚠ 落在 async 函数内的调用点 = {len(async_c)} 处")

# 顺带：全仓 async 函数内直接调 time.sleep 的总数（真阻塞面）
OUT.append("")
OUT.append("===== 3. 全仓 async 函数内直接调 time.sleep（真阻塞面） =====")
found = 0
for f in sorted(APP.rglob("*.py")):
    src = f.read_text(encoding="utf-8", errors="replace")
    if "time.sleep(" not in src:
        continue
    try:
        t = parse(f)
    except SyntaxError:
        continue
    for node in ast.walk(t):
        if isinstance(node, ast.AsyncFunctionDef):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "sleep":
                    OUT.append(f"    {f.relative_to(ROOT).as_posix()}:{sub.lineno} in {node.name}()")
                    found += 1
OUT.append(f"    合计 = {found} 处")

(ROOT / "_tmp" / "arch_metrics3.txt").write_text("\n".join(OUT), encoding="utf-8")
print("OK")
