"""Android 启动器：在 Chaquopy 里拉起真实后端 app.main:app。

启动约束（顺序不能变）：
  1. 先写环境变量与数据目录，再 import app.*——config.py 在 import 期就把
     DATA_DIR / *_DIR 定死，app.main 的 import 尾部还会直接 init_db()。
  2. frontend dist 由 MainActivity 解包后用 LAF_FRONTEND_DIST 传进来（同一路径挂两次
     会 404，故真实出服从 app.main 挂的 "/"）。
  3. ComfyUI 是 PC 独占：LAF_COMFY_AUTOSTART=0；看门狗在没有托管进程时全程 no-op。
  4. 安全（M4）：令牌由 Java 壳层生成并传进来，必填；端口 8010 被占用时退到
     18010–18099；LAF_ALLOW_REMOTE 一律清除，移动壳永不放开远端。
"""
from __future__ import annotations

import os
import random
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

PORT = 8010
FALLBACK_PORTS = range(18010, 18100)
_started = False
_info: dict = {}


def _port_free(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _choose_port(preferred: int = PORT) -> int:
    """默认 8010；被占用就在 18010–18099 里随机挑一个空闲端口。

    顺序随机（SystemRandom）而不是从小往大扫：固定回落顺序等于把「换到哪个端口」
    变成可预测信息，顺手把探测面收窄。检测与真正 listen 之间有极小 TOCTOU 窗口，
    最坏是 uvicorn 启动失败并在 boot-error.txt 留痕。
    """
    if _port_free(preferred):
        return preferred
    candidates = list(FALLBACK_PORTS)
    random.SystemRandom().shuffle(candidates)
    for port in candidates:
        if _port_free(port):
            return port
    raise RuntimeError("没有可用的回环端口（8010 / 18010–18099 全被占用）")


def get_port() -> int:
    """壳层在 start() 之后读这里拼 WebView/健康检查地址（端口不再由 Java 写死）。"""
    return int(_info.get("port", PORT))



def _pkg_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception as exc:  # 诊断字段，不参与启动判定
        return f"<missing: {exc}>"


def _prepare(data_root: Path, frontend_dir: Path, port: int, token: str) -> dict:
    data_dir = data_root / "data"
    tmp_dir = data_root / "tmp"
    for sub in ("", "workflows", "templates", "assets", "thumbs", "chroma"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(tmp_dir)

    # 安全合同：移动壳永不放开远端（request_guard 的 allow_remote 逃生门只给桌面用）
    os.environ.pop("LAF_ALLOW_REMOTE", None)
    os.environ.update(
        {
            "LAF_DATA_DIR": str(data_dir),
            "LAF_COMFY_AUTOSTART": "0",
            "LAF_COMFY_EXT_DIR": str(data_root / "comfyui-ext"),
            "LAF_RUNTIME_PORT": str(port),
            "LAF_LOCAL_TOKEN": token,
            "TIKTOKEN_CACHE_DIR": str(data_root / "tiktoken-cache"),
            "TMPDIR": str(tmp_dir),
            "HOME": str(data_root),
        }
    )
    frontend_ok = bool(frontend_dir) and frontend_dir.is_dir()
    if frontend_ok:
        os.environ["LAF_FRONTEND_DIST"] = str(frontend_dir)
    os.chdir(data_dir)
    (data_root / "tiktoken-cache").mkdir(exist_ok=True)
    return {
        "data_dir": str(data_dir),
        "frontend_dir": str(frontend_dir),
        "frontend_ok": frontend_ok,
        "port": port,
    }


def _auth_info() -> dict:
    """诊断字段只报形态，绝不回显令牌本身（probe 会进诊断导出）。"""
    try:
        from app.services import local_auth

        return {
            "enabled": bool(local_auth.env_token()),
            "header": local_auth.HEADER_NAME,
            "cookie": local_auth.COOKIE_NAME,
            "bootstrap": local_auth.BOOTSTRAP_PATH,
        }
    except Exception as exc:  # 导入期诊断（此时 app.main 可能还没 import）
        return {"enabled": False, "error": f"{type(exc).__name__}: {exc}"}


def probe() -> dict:
    info = dict(_info)
    info.update(
        {
            "started": _started,
            "port": get_port(),
            "auth": _auth_info(),
            "python": sys.version.split()[0],
            "sqlite": sqlite3.sqlite_version,
            "executable": sys.executable,
            "prefix": sys.prefix,
            "cwd": os.getcwd(),
            "packages": {
                name: _pkg_version(name)
                for name in (
                    "fastapi", "pydantic", "pydantic-core", "uvicorn", "chromadb",
                    "chromadb-rust-bindings", "langchain", "langchain-core",
                    "langgraph", "langchain-chroma", "openai", "jiter", "tiktoken",
                    "orjson", "ormsgpack", "rpds-py", "uuid-utils", "numpy",
                    "grpcio", "protobuf", "pillow", "httpx", "langchain-mcp-adapters",
                )
            },
        }
    )
    return info


def _selftest() -> dict:
    result: dict = {"sqlite": sqlite3.sqlite_version}
    t0 = time.perf_counter()

    def step(name: str, fn) -> None:
        t = time.perf_counter()
        try:
            result[name] = {"ok": True, "ms": round((time.perf_counter() - t) * 1000, 1), "detail": fn()}
        except Exception as exc:
            result[name] = {
                "ok": False,
                "ms": round((time.perf_counter() - t) * 1000, 1),
                "error": f"{type(exc).__name__}: {exc}",
            }

    def rust_bindings() -> dict:
        import chromadb_rust_bindings as rb

        return {"file": getattr(rb, "__file__", "?")}

    def chroma_roundtrip() -> dict:
        import chromadb

        path = Path(_info.get("data_dir", ".")) / "chroma-selftest"
        client = chromadb.PersistentClient(path=str(path))
        col = client.get_or_create_collection("mobile_selftest")
        col.upsert(
            ids=["a", "b", "c"],
            documents=["alpha", "beta", "gamma"],
            embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        )
        got = col.query(query_embeddings=[[1.0, 0.0, 0.0]], n_results=1)
        return {"count": col.count(), "top": got["ids"][0][0]}

    def langchain_chroma_import() -> dict:
        import langchain_chroma

        return {"file": getattr(langchain_chroma, "__file__", "?")}

    def tiktoken_import() -> dict:
        import tiktoken

        return {
            "version": getattr(tiktoken, "__version__", "?"),
            "encodings": len(list(tiktoken.list_encoding_names())),
        }

    def grpc_import() -> dict:
        import grpc

        return {"version": grpc.__version__}

    step("chromadb_rust_bindings", rust_bindings)
    step("chroma_roundtrip", chroma_roundtrip)
    step("langchain_chroma", langchain_chroma_import)
    step("tiktoken", tiktoken_import)
    step("grpc", grpc_import)
    result["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return result


def prepend_route(fastapi_app) -> None:
    """把刚注册的路由提到路由表最前。

    app.main 在 import 期就把前端 dist 挂到 "/"，Starlette 按注册顺序匹配，后加的
    诊断路由会被该静态挂载吞掉（M2 探针、M4 引导各踩一次）——每注册一条就调一次。
    """
    routes = fastapi_app.router.routes
    routes.insert(0, routes.pop())


def _attach_probe_route(fastapi_app) -> None:
    @fastapi_app.get("/api/mobile/info")
    def mobile_info() -> dict:
        return probe()

    prepend_route(fastapi_app)

    @fastapi_app.get("/api/mobile/selftest")
    def mobile_selftest() -> dict:
        return _selftest()

    prepend_route(fastapi_app)


def start(data_root: str, frontend_dir: str, token: str, port: int = PORT) -> dict:
    global _started, _info
    token = (token or "").strip()
    if not token:
        # 手机独立模式没有桌面那种「本机即同一用户」的信任前提：无令牌 = 本机任意
        # 应用都能读密钥/改存档。宁可启动失败留 boot-error，也不降级成无鉴权。
        raise ValueError("mobile_server.start 必须提供本机访问令牌")
    chosen = _choose_port(port)
    root = Path(data_root)
    t0 = time.perf_counter()
    _info = _prepare(root, Path(frontend_dir), chosen, token)
    _info["phase"] = "env-ready"
    try:
        import app.main as backend

        _info["import_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        _info["phase"] = "imported"
    except Exception:
        text = traceback.format_exc()
        (root / "boot-error.txt").write_text(text, encoding="utf-8")
        print("mobile_server: import app.main failed\n" + text, flush=True)
        raise

    _attach_probe_route(backend.app)
    if _started:
        return probe()
    _started = True

    import uvicorn

    config = uvicorn.Config(
        backend.app,
        host="127.0.0.1",
        port=chosen,
        log_level="info",
        access_log=False,
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    def _run() -> None:
        try:
            server.run()
        except Exception:
            text = traceback.format_exc()
            (root / "boot-error.txt").write_text(text, encoding="utf-8")
            print("mobile_server: uvicorn crashed\n" + text, flush=True)

    threading.Thread(target=_run, daemon=True, name="uvicorn").start()
    _info["phase"] = "serving"
    _info["handoff_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    print(
        "mobile_server: serving on 127.0.0.1:%d, import %s ms, handoff %s ms"
        % (chosen, _info.get("import_ms", -1), _info["handoff_ms"]),
        flush=True,
    )
    return probe()
