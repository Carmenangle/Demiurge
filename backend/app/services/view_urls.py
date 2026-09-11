"""生成产出的前端可取回 URL 构造：本地留存图 / ComfyUI 在线图。

集中拼接后端 /api/comfyui 两个取图端点的地址，避免 URL 格式散落在 generation_store。
- local_view：已留存到本地的图，前端经后端 local-view 端点按绝对路径回取。
- remote_view：尚未留存的 ComfyUI 产出，前端经后端 view 端点代理回取。
纯字符串构造，无副作用，可直接单测。
"""
from urllib.parse import quote, urlencode

from app.config import BACKEND_BASE_URL

_LOCAL_VIEW = f"{BACKEND_BASE_URL}/api/comfyui/local-view"
_VIEW = f"{BACKEND_BASE_URL}/api/comfyui/view"


def local_view(path: str) -> str:
    """本地留存图的取回地址（按绝对路径）。"""
    return f"{_LOCAL_VIEW}?path={quote(path)}"


def remote_view(filename: str, type: str = "output", subfolder: str = "",
                comfyui_url: str = "") -> str:
    """ComfyUI 在线产出的取回地址（经后端 view 端点代理）。"""
    params = {"filename": filename, "type": type,
              "subfolder": subfolder, "url": comfyui_url}
    return f"{_VIEW}?{urlencode(params)}"
