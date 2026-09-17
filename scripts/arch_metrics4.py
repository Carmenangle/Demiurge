"""核查：FastAPI `async def` 端点体内是否直接调用「同步阻塞」服务（会占住事件循环）。
只读。判定面取三类高置信阻塞入口：llm.chat*、requests.*、time.sleep、subprocess.run。

来源：复评指标脚本（T5.4 自 `_tmp/` 迁入，原样保真，仅 ROOT 改为仓库根推导）。
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"
OUT = []

# 建立「同步阻塞」函数白名单（模块内点号名）
BLOCK = {
    "chat_messages", "chat_messages_stream", "chat",           # services.llm
    "run", "check_output", "Popen",                            # subprocess
    "sleep",                                                   # time
    "get", "post", "request",                                  # requests（httpx sync 同名，需看导入）
}

out = []
async_endpoints = 0
for f in sorted((APP / "routers").rglob("*.py")):
    src = f.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        # 只算端点（被装饰器标记为路由的）
        decs = [ast.unparse(d) for d in node.decorator_list]
        is_ep = any(("router." in d or ".get(" in d or ".post(" in d) for d in decs)
        if is_ep:
            async_endpoints += 1
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            nm = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            base = ast.unparse(fn.value) if isinstance(fn, ast.Attribute) else ""
            if nm in BLOCK and base.split(".")[-1] in ("llm", "requests", "httpx", "time", "subprocess", "sp"):
                tag = "ENDPOINT" if is_ep else "helper"
                out.append(f"{f.relative_to(ROOT).as_posix()}:{sub.lineno} {base}.{nm}() in async {node.name}() [{tag}]")

OUT.append(f"routers 内 async 端点（带路由装饰器）= {async_endpoints} 个")
OUT.append(f"async 函数内命中「同步阻塞入口」= {len(out)} 处")
for o in out:
    OUT.append("    " + o)

# 再补一个更有用的口径：routers 里端点总数 与 async 端点占比
tot_ep = 0
for f in sorted((APP / "routers").rglob("*.py")):
    src = f.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decs = [ast.unparse(d) for d in node.decorator_list]
            if any(("router." in d or ".get(" in d or ".post(" in d or ".delete(" in d or ".put(" in d) for d in decs):
                tot_ep += 1
OUT.append("")
OUT.append(f"routers 端点总数 = {tot_ep}（async 端点 {async_endpoints} 个，占 {100*async_endpoints/max(1,tot_ep):.0f}%）")

# 同步端点（def）数量：FastAPI 会跑在线程池，是「不阻塞循环」的证据
sync_ep = tot_ep - async_endpoints
OUT.append(f"同步端点（def，FastAPI 自动线程池）= {sync_ep} 个")

(ROOT / "_tmp" / "arch_metrics4.txt").write_text("\n".join(OUT), encoding="utf-8")
print("OK")
