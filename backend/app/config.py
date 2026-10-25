import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("LAF_DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve()
WORKFLOWS_DIR = DATA_DIR / "workflows"
TEMPLATES_DIR = DATA_DIR / "templates"
PROMPT_TEMPLATES_DIR = DATA_DIR / "prompt_templates"  # 提示词模版（2026-10-25）：自定义模版 JSON，内置不落盘
ASSETS_DIR = DATA_DIR / "assets"
THUMBS_DIR = DATA_DIR / "thumbs"
DB_PATH = DATA_DIR / "app.db"
CHECKPOINT_DB = DATA_DIR / "checkpoints.db"   # LangGraph 对话多轮记忆（SqliteSaver）
CHROMA_DIR = DATA_DIR / "chroma"              # 仓库 RAG 知识库（Chroma 本地持久化）

# ComfyUI 锁定扩展目录（custom_nodes 形式，靠 extra_model_paths 外挂，不改 ComfyUI 本体）
COMFY_EXT_DIR = Path(
    os.environ.get("LAF_COMFY_EXT_DIR", str(BASE_DIR.parent / "comfyui-ext"))
).expanduser().resolve()

# ComfyUI 服务端口真源：整合包/默认安装的固定端口。BASE_URL 与各处 `or 端口` 回退
# 一律从这里取（check_hardcode 把裸端口字面量也列为门禁，防回退值逃逸出本文件）。
COMFYUI_DEFAULT_PORT = 8188
COMFYUI_BASE_URL = f"http://127.0.0.1:{COMFYUI_DEFAULT_PORT}"
COMFYUI_INPUT_DIR = Path(
    os.environ.get("LAF_COMFYUI_INPUT_DIR", str(DATA_DIR / "comfyui-input"))
).expanduser().resolve()
COMFYUI_OUTPUT_DIR = Path(
    os.environ.get("LAF_COMFYUI_OUTPUT_DIR", str(DATA_DIR / "comfyui-output"))
).expanduser().resolve()

# 后端自身地址（回环 + **实际监听端口**）。只给后端自己取回 local-view/view 图用
# （见 services/view_urls.fetchable）；对前端与存档一律是根相对地址，不带这里的 origin。
# 端口真源＝`LAF_RUNTIME_PORT`（ARCHITECTURE.md「端口与复用」、scripts/runtime_entry.runtime_port
# 用它决定 uvicorn 绑定端口），默认 8010。钉死 8010 会让「壳协商到别的端口」时后端去敲
# 一个没人听的端口，自取图直接失败。
def _backend_port() -> int:
    raw = os.environ.get("LAF_RUNTIME_PORT", "").strip()
    try:
        port = int(raw)
    except ValueError:
        return 8010
    return port if 1 <= port <= 65535 else 8010


BACKEND_BASE_URL = f"http://127.0.0.1:{_backend_port()}"

# AI 搭工作流编排重试预算（秒）；前端方案等待 240s、实际搭建等待 420s。
# 预算只阻止开始新的纠错轮次，单次在途模型请求由上游超时负责。
BUILD_TIME_BUDGET_SEC = 200
