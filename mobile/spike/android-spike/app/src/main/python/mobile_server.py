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

import json
import logging
import os
import random
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

PORT = 8010
FALLBACK_PORTS = range(18010, 18100)
_started = False
_info: dict = {}
_server = None            # 当前 uvicorn.Server（pause/resume/restart 的操作对象）
_server_thread = None     # 承载它的线程（restart 时 join 用）


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



def running_generation_count() -> int:
    """在途生成任务数（comfy_pending 是服务端真源：提交登记、终态删除、30 分钟 TTL）。

    「退后台 30s 暂停」与「前台服务保活」共用的闸；后端未 import 完成时返回 0，
    宁可多保活一拍也不误杀在途任务。
    """
    try:
        from app.services import comfy_pending

        return len(comfy_pending.snapshot())
    except Exception:  # noqa: BLE001 — app 未就绪/模块缺失：保守按 0，由调用方保活
        return 0


def _setup_logging(data_root: Path) -> str:
    """backend.log 轮转 2×2 MB（PLAN §7 崩溃恢复）。app 私有目录，无敏感导出面。

    令牌进 URL 的引导请求不落日志由 uvicorn access_log=False 保证（M4 安全已定），
    这里只加文件轮转，不新增任何含密内容。

    **只挂 root**：uvicorn.error 在 `_serve(log_config=None)` 下不再被 dictConfig
    接管（dictConfig 会清空已挂 handler——真机 backend.log 曾因此恒 0 字节），
    日志沿默认 propagate 汇到 root；同一 handler 若也挂 uvicorn.error 会双写。
    """
    logs_dir = data_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "backend.log"
    handler = RotatingFileHandler(
        path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return str(path)


def _serve(app_obj, port: int) -> None:
    """在 daemon 线程里跑一个 uvicorn.Server 并登记句柄（start/resume/restart 共用）。"""
    global _server, _server_thread
    import uvicorn

    config = uvicorn.Config(
        app_obj, host="127.0.0.1", port=port,
        log_level="info", access_log=False, loop="asyncio",
        # 默认 log_config 会 dictConfig，把 root 上的文件 handler 从 uvicorn.error
        # 清走；而 app 内 70 个模块都走 uvicorn.error ⇒ backend.log 永久空。
        log_config=None,
    )
    server = uvicorn.Server(config)
    _server = server
    _server_thread = threading.Thread(target=server.run, daemon=True, name="uvicorn")
    _server_thread.start()


def pause_if_idle() -> dict:
    """退后台 30s 后由壳层调用：有在途生成就保活，否则优雅停 uvicorn 线程。

    暂停＝uvicorn 优雅退出（在途请求做完）；解释器与 app 状态保活，
    resume() 同端口秒级重拉。不做「关机后继续生成」的承诺（PLAN §7）。
    """
    if _server is None:
        return {"paused": False, "reason": "not-serving"}
    running = running_generation_count()
    if running > 0:
        return {"paused": False, "running": running}
    _server.should_exit = True
    _info["phase"] = "paused"
    return {"paused": True, "running": 0}


def resume() -> dict:
    """回前台：uvicorn 不在跑就在同端口重拉（解释器不重启）。"""
    if _server_thread is not None and _server_thread.is_alive():
        _info["phase"] = "serving"
        return {"resumed": False, "reason": "already-serving"}
    import app.main as backend

    _serve(backend.app, get_port())
    _info["phase"] = "serving"
    return {"resumed": True}


def restart_uvicorn() -> dict:
    """健康检查 3 连败后的自愈：只重启 uvicorn 线程，不重启解释器（PLAN §7）。

    旧线程 join 上限 5s（端口释放前置）；join 超时也照拉，绑定失败会落
    boot-error.txt，比静默卡死可见。
    """
    global _server, _server_thread
    old_server, old_thread = _server, _server_thread
    if old_server is not None:
        old_server.should_exit = True
    if old_thread is not None:
        old_thread.join(timeout=5)
    import app.main as backend

    _serve(backend.app, get_port())
    _info["phase"] = "restarted"
    return {"restarted": True, "port": get_port()}


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
            "LAF_MOBILE_REMOTE": "1",
            "TIKTOKEN_CACHE_DIR": str(data_root / "tiktoken-cache"),
            "TMPDIR": str(tmp_dir),
            "HOME": str(data_root),
        }
    )
    seeded = _seed_mobile_defaults(data_root)
    frontend_ok = bool(frontend_dir) and frontend_dir.is_dir()
    if frontend_ok:
        os.environ["LAF_FRONTEND_DIST"] = str(frontend_dir)
    os.chdir(data_dir)
    (data_root / "tiktoken-cache").mkdir(exist_ok=True)
    return {
        "data_dir": str(data_dir),
        "frontend_dir": str(frontend_dir),
        "frontend_ok": frontend_ok,
        "seeded": seeded,
        "port": port,
    }


def _seed_mobile_defaults(data_root: Path) -> dict:
    """首启/升级播种（PLAN §6 D1）：只填空路径键，绝不覆盖用户已配置的值。

    默认目录全在 app 私有面（外置可见性不做承诺，导出走 transfer）。
    **覆盖安装也必须补**：0.4.3 → 0.30.0 的 `user_state.json` 已存在但五个路径键
    全空，旧实现「文件缺失才播种」永远不补 ⇒ 角色卡/世界书/预设入口全被
    「请先到设置配置路径」挡住，而手机端没有目录选择器。故按**键为空**逐项补。
    JSON 读不出来时保持原文件不动（不拿空状态覆盖用户数据）。
    """
    state_path = data_root / "data" / "user_state.json"
    state: dict = {}
    if state_path.is_file():
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"seeded": False, "keys": [], "error": "state-unreadable"}
        if isinstance(raw, dict):
            state = raw

    settings = state.get("settings")
    if not isinstance(settings, dict):
        settings = {}
        state["settings"] = settings

    defaults = {
        "outputDir": data_root / "pictures",
        "characterDir": data_root / "characters",
        "worldbookDir": data_root / "worldbooks",
        "presetDir": data_root / "presets",
        "workflowDir": data_root / "workflows",
    }
    filled: list[str] = []
    for key, path in defaults.items():
        if not str(settings.get(key) or "").strip():
            path.mkdir(parents=True, exist_ok=True)
            settings[key] = str(path)
            filled.append(key)
    if filled or not state_path.is_file():
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"seeded": bool(filled), "keys": filled}


def _migration_probe() -> dict:
    """覆盖安装的迁移检查（PLAN §10）：只观测不迁移。

    sqlite user_version 与 chroma 目录形态进 probe（诊断导出面）；后续真有 schema
    迁移需求时，先在这里发现版本漂移，再落迁移逻辑——不做无据迁移。
    """
    data_dir = Path(os.environ.get("LAF_DATA_DIR", "."))
    info: dict = {"chroma_dir_present": (data_dir / "chroma").is_dir()}
    try:
        db = sqlite3.connect(f"file:{data_dir / 'app.db'}?mode=ro", uri=True)
        try:
            info["sqlite_user_version"] = int(
                db.execute("PRAGMA user_version").fetchone()[0])
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — 首启无库/读失败：观测字段置空，不挡启动
        info["sqlite_user_version"] = None
    return info


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
    info["migration"] = _migration_probe()
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


def _comfy_hosts_file() -> Path:
    return Path(os.environ.get("LAF_DATA_DIR", ".")) / "comfy_hosts.json"


def _load_comfy_hosts() -> list[str]:
    """上次登记的 PC 主机（app 私有目录；形态坏/文件缺都当空处理）。"""
    try:
        data = json.loads(_comfy_hosts_file().read_text(encoding="utf-8"))
        return [str(h) for h in data.get("hosts", []) if isinstance(h, str)]
    except Exception:  # noqa: BLE001 — 首启无文件/手改坏 JSON：播种为空，不挡启动
        return []


def _save_comfy_hosts(hosts: list[str]) -> None:
    path = _comfy_hosts_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"hosts": hosts}, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed_comfy_hosts() -> list[str]:
    """把已登记的 PC 主机播种进 url_guard 白名单（app.main import 之后调用）。"""
    try:
        from app.services import url_guard
    except Exception:  # noqa: BLE001 — app 未 import 时静默空播（仅测试环境会出现）
        return []
    seeded: list[str] = []
    for host in _load_comfy_hosts():
        try:
            url_guard.register_comfyui_host(host)
            seeded.append(host)
        except ValueError:
            continue
    return seeded


def _attach_comfy_host_routes(fastapi_app) -> None:
    """M7 联机：设置页保存 ComfyUI 地址时登记 PC 主机（运行时白名单 + 落盘复播）。

    http 路由，走 local_auth 令牌中间件；单一真源＝前端设置页的 comfyuiUrl，
    壳层无 PC 地址配置项、Java 零改动。
    """
    from fastapi import Body

    from app.services import url_guard

    @fastapi_app.get("/api/mobile/comfy-host")
    def comfy_host_list() -> dict:
        return {"hosts": list(url_guard.comfy_extra_hosts()),
                "seeded": _load_comfy_hosts()}

    prepend_route(fastapi_app)

    @fastapi_app.post("/api/mobile/comfy-host")
    def comfy_host_register(req: dict = Body(default={})) -> dict:
        raw = str(req.get("url") or "").strip()
        if "://" not in raw:
            raw = "http://" + raw  # 顺手兼容裸 host:port 填法
        host = (urlparse(raw).hostname or "").lower()
        if not host:
            return {"ok": False, "detail": "地址里没有主机名"}
        try:
            effect = url_guard.register_comfyui_host(host)
        except ValueError as exc:
            return {"ok": False, "detail": str(exc)}
        hosts = _load_comfy_hosts()
        if effect != "loopback" and host not in hosts:
            hosts.append(host)
            _save_comfy_hosts(hosts[-url_guard.MAX_EXTRA_HOSTS:])
        return {"ok": True, "host": effect}

    prepend_route(fastapi_app)


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
    if _started:
        # Activity 重建（按返回键退出后重开）会在同一解释器里再次调用 start()。
        # 此时环境与数据目录已是首次启动的定态，**不能再 _prepare/重选端口**：
        # 8010 被自己占着 ⇒ 选中回落端口并覆盖 _info.port ⇒ 壳层去轮询一个没人
        # 监听的端口（真机实测黑屏卡死）。复用原端口；服务已停（后台暂停）则重拉。
        if (_server is None or _server.should_exit
                or _server_thread is None or not _server_thread.is_alive()):
            if _server is not None:
                _server.should_exit = True
            if _server_thread is not None and _server_thread.is_alive():
                _server_thread.join(timeout=5)
            import app.main as backend

            _serve(backend.app, get_port())
            _info["phase"] = "serving"
        return probe()
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
    _info["comfy_hosts_seeded"] = _seed_comfy_hosts()
    _info["log_file"] = _setup_logging(root)
    _attach_comfy_host_routes(backend.app)
    if _started:
        return probe()
    _started = True

    _serve(backend.app, chosen)
    _info["phase"] = "serving"
    _info["handoff_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    print(
        "mobile_server: serving on 127.0.0.1:%d, import %s ms, handoff %s ms"
        % (chosen, _info.get("import_ms", -1), _info["handoff_ms"]),
        flush=True,
    )
    return probe()
