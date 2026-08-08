"""固定 Runtime 的单一启动入口。"""
from __future__ import annotations

import os
import importlib
import json
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


_DLL_DIRECTORY_HANDLES: list[object] = []


def runtime_root() -> Path:
    configured = os.environ.get("LAF_RUNTIME_ROOT", "").strip()
    if configured:
        return Path(configured).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def runtime_state(root: Path) -> dict:
    configured = os.environ.get("LAF_RUNTIME_STATE", "").strip()
    state_path = Path(configured) if configured else root / "current.json"
    if not state_path.is_file():
        return {}
    return json.loads(state_path.read_text(encoding="utf-8"))


def configure_environment(root: Path) -> dict:
    state = runtime_state(root)
    edition = str(state.get("edition") or os.environ.get("LAF_RUNTIME_EDITION", "full-rag"))
    application_id = str(state.get("application_id", ""))
    application_dir = root / "apps" / application_id if application_id else root
    backend_source = application_dir / "backend"
    backend_archive = application_dir / "backend.zip"
    if (backend_source / "app" / "main.py").is_file():
        sys.path.insert(0, str(backend_source))
    elif backend_archive.is_file():
        sys.path.insert(0, str(backend_archive))

    rag_id = str(state.get("rag_id", ""))
    if rag_id:
        rag_packages = root / "rag" / rag_id / "site-packages"
        if rag_packages.is_dir():
            sys.path.insert(0, str(rag_packages))
            if os.name == "nt":
                for dll_dir in (rag_packages, rag_packages / "torch" / "lib"):
                    if dll_dir.is_dir():
                        _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(dll_dir)))

    # 数据目录：优先用启动器传入的 LAF_DATA_DIR（指向 data/userdata），
    # 独立运行时回退到 exe 同级 data（开发/自检场景）。
    data_dir = Path(os.environ.get("LAF_DATA_DIR") or (root / "data"))
    os.environ.update({
        "LAF_RUNTIME_ROOT": str(root),
        "LAF_DATA_DIR": str(data_dir),
        "LAF_FRONTEND_DIST": str(application_dir / "frontend"),
        "LAF_COMFY_EXT_DIR": str(application_dir / "comfyui-ext"),
        "LAF_RUNTIME_EDITION": edition,
    })
    os.environ.pop("LAF_BUNDLED_RERANKER_DIR", None)
    data_dir.mkdir(parents=True, exist_ok=True)
    return state


def self_check() -> None:
    modules = [
        "app.main", "chromadb", "langchain_chroma", "langgraph",
        "langchain_mcp_adapters",
    ]
    if os.environ.get("LAF_RUNTIME_EDITION") == "full-rag":
        modules.extend(("torch", "transformers", "sentence_transformers"))
    loaded = {module: importlib.import_module(module) for module in modules}
    payload = {"status": "ok", "modules": modules}
    torch = loaded.get("torch")
    if torch is not None:
        payload.update({
            "torch_version": str(torch.__version__),
            "torch_cuda": getattr(torch.version, "cuda", None),
        })
    print(json.dumps(payload))


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def runtime_port() -> int:
    raw = os.environ.get("LAF_RUNTIME_PORT", "8010").strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid LAF_RUNTIME_PORT: {raw}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"LAF_RUNTIME_PORT out of range: {port}")
    return port


def _open_browser_when_ready(port: int, *, attempts: int = 240) -> None:
    for _ in range(attempts):
        if _port_open(port):
            webbrowser.open(f"http://127.0.0.1:{port}")
            return
        time.sleep(0.25)


def main() -> None:
    root = runtime_root()
    configure_environment(root)
    if os.environ.get("LAF_RUNTIME_SELF_TEST", "") == "1":
        self_check()
        sys.stdout.flush()
        os._exit(0)
    port = runtime_port()
    if os.environ.get("LAF_NO_BROWSER", "") != "1":
        threading.Thread(
            target=_open_browser_when_ready, args=(port,), daemon=True,
        ).start()
    import uvicorn
    uvicorn.run(
        "app.main:app", host="127.0.0.1", port=port,
        log_level="info", log_config=None,
    )


if __name__ == "__main__":
    main()
