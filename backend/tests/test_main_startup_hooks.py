"""main.py 运行期装配合同（2026-10-24 卫生刀后形态：lifespan 单一装配点）。

事故一（2026-10-24 之前）：ComfyUI 自动启动曾在看门狗重构里被摘掉
`@app.on_event("startup")`——函数还在、`LAF_COMFY_AUTOSTART` 判断也还在，但没有任何
启动钩子调用它 ⇒ 用户看到「ComfyUI 不像以前那样随应用启动」。
事故二（卫生刀改造本身）：装配点从三个 startup 钩子收拢为 `_lifespan`、模块级
`start_worker()` 副作用一并消除；新的事故形态是「_lifespan 定义了但没传给 FastAPI」
或「某个 worker/看护调用从装配点脱落」。

本测试按 AST 钉住新接线（不 import main，避免建库/起后台线程副作用）。
"""
import ast
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "app" / "main.py"


def _tree() -> ast.Module:
    return ast.parse(MAIN.read_text(encoding="utf-8"))


def _find_fn(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"main.py 缺少 {name}")


def _called_names(fn) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def test_lifespan被真实传给FastAPI():
    # 事故形态一：函数存在但没人调用。lifespan 不进 FastAPI 构造＝装配点整个脱落。
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "FastAPI":
            assert any(k.arg == "lifespan" for k in node.keywords), \
                "FastAPI(...) 必须接收 lifespan=_lifespan，否则运行期装配不会发生"
            break
    else:
        raise AssertionError("main.py 缺少 FastAPI(...) 构造")


def test_worker与看护设施都在lifespan装配点():
    calls = _called_names(_find_fn(_tree(), "_lifespan"))
    for must in (
        "start_workflow_build_worker",
        "start_chat_agent_queue_worker",
        "start_plan_task_worker",
        "start_watchdog",  # comfy_watchdog.start_watchdog
        "install_connection_reset_filter",
    ):
        assert must in calls, f"装配点缺少 {must}"
    assert "Thread" in calls, "ComfyUI autostart 线程必须在装配点拉起"


def test_autostart线程体保留看门狗登记():
    # 2026-10-23 短板②：autostart() 返回值被丢弃 ⇒ 启动失败无声。线程体必须消费返回值。
    calls = _called_names(_find_fn(_tree(), "_autostart_comfyui"))
    assert {"autostart", "note_owned", "note_autostart_failed"} <= calls


def test_import无worker副作用():
    # 卫生刀合同：import main 不再起线程/起 worker——模块级不得残留 start_*_worker() 调用
    tree = _tree()
    module_level_calls = [
        n for n in tree.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
    ]
    for node in module_level_calls:
        f = node.value.func
        name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
        assert not name.startswith("start_"), f"模块级 {name}() 调用必须收进 lifespan"
