import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("LAF_DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve()
WORKFLOWS_DIR = DATA_DIR / "workflows"
TEMPLATES_DIR = DATA_DIR / "templates"
ASSETS_DIR = DATA_DIR / "assets"
THUMBS_DIR = DATA_DIR / "thumbs"
DB_PATH = DATA_DIR / "app.db"
CHECKPOINT_DB = DATA_DIR / "checkpoints.db"   # LangGraph 对话多轮记忆（SqliteSaver）
CHROMA_DIR = DATA_DIR / "chroma"              # 仓库 RAG 知识库（Chroma 本地持久化）

# ComfyUI 锁定扩展目录（custom_nodes 形式，靠 extra_model_paths 外挂，不改 ComfyUI 本体）
COMFY_EXT_DIR = Path(
    os.environ.get("LAF_COMFY_EXT_DIR", str(BASE_DIR.parent / "comfyui-ext"))
).expanduser().resolve()

COMFYUI_BASE_URL = "http://127.0.0.1:8188"
COMFYUI_INPUT_DIR = Path(r"D:\tool\ComfyUI\input")
COMFYUI_OUTPUT_DIR = Path(r"D:\tool\ComfyUI\output")

# 后端自身对外地址（前端通过它回取本地留存图；生成产出 URL 据此拼接）
BACKEND_BASE_URL = "http://127.0.0.1:8010"

# AI 搭工作流编排重试预算（秒）；前端方案等待 240s、实际搭建等待 420s。
# 预算只阻止开始新的纠错轮次，单次在途模型请求由上游超时负责。
BUILD_TIME_BUDGET_SEC = 200
