"""生成产出的前端可取回 URL 构造：本地留存图 / ComfyUI 在线图。

集中拼接后端 /api/comfyui 两个取图端点的地址，避免 URL 格式散落在 generation_store。
- local_view：已留存到本地的图，前端经后端 local-view 端点按绝对路径回取。
- remote_view：尚未留存的 ComfyUI 产出，前端经后端 view 端点代理回取。

**wire 形态＝根相对（`/api/comfyui/...`），不带 origin。** origin 表达的是「客户端怎么到达本机」，
把它写进 URL 等于将一次性的到达方式烧进存档与对话正文：局域网设备、壳协商端口
（`LAF_RUNTIME_PORT`）、Tailscale Serve 域名三种场景下必然取不到图（2026-10-24 LAN 实测：
绝对回环地址让 4 张素材缩略图挂 3 张，且客户端会把带 origin 的地址回写进仓库封面）。
需要真正发起 HTTP 的地方（后端自取图）用 `fetchable()` 拼回本机地址；历史落盘的绝对回环
地址由 `normalize_wire()` 在出口收敛，不做存档迁移。
纯字符串构造，无副作用，可直接单测。
"""
import re
from urllib.parse import quote, urlencode

from app import config

LOCAL_VIEW_PATH = "/api/comfyui/local-view"
VIEW_PATH = "/api/comfyui/view"

# 历史形态：绝对回环 origin（端口不限，兼容隔离实例与老存档）＋ 自家两个取图端点。
# 只收敛回环，不碰任何外部主机：`http://evil.com/api/comfyui/local-view?...` 必须留在原样，
# 让 url_guard 的 SSRF 校验继续判假（两处锚定的是同一条端点形状，改动要一起看）。
_LOOPBACK_ORIGIN = r"https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?"
_VIEW_ENDPOINT = r"(/api/comfyui/(?:local-view|view)\?)"
_WIRE_TEXT = re.compile(_LOOPBACK_ORIGIN + _VIEW_ENDPOINT)
_WIRE_BYTES = re.compile(_LOOPBACK_ORIGIN.encode() + _VIEW_ENDPOINT.encode())


def local_view(path: str) -> str:
    """本地留存图的取回地址（按绝对路径）。"""
    return f"{LOCAL_VIEW_PATH}?path={quote(path)}"


def remote_view(filename: str, type: str = "output", subfolder: str = "",
                comfyui_url: str = "") -> str:
    """ComfyUI 在线产出的取回地址（经后端 view 端点代理）。"""
    params = {"filename": filename, "type": type,
              "subfolder": subfolder, "url": comfyui_url}
    return f"{VIEW_PATH}?{urlencode(params)}"


def fetchable(url: str) -> str:
    """把根相对地址拼成后端自己能发起 HTTP 的绝对地址；其余形态原样返回。

    `//` 开头的协议相对地址**不拼**：它的主机由调用方给出，拼上去等于让后端去敲一个
    不由我们决定的主机（SSRF 面），而自家根相对地址从来不该长这样。
    origin 在调用期取（不是 import 期）：端口来自 `LAF_RUNTIME_PORT`，隔离实例/测试要能
    在不改导入顺序的前提下换掉它。
    """
    if url.startswith("//"):
        return url
    if url.startswith("/"):
        return f"{config.BACKEND_BASE_URL}{url}"
    return url


def normalize_wire(text: str) -> str:
    """出口归一：历史落盘的绝对回环取图地址收敛成根相对（不改存档，读时归一）。"""
    return _WIRE_TEXT.sub(r"\1", text)


def normalize_wire_bytes(raw: bytes) -> bytes:
    """`normalize_wire` 的字节口径，供 JSON 响应出口直接改写（省掉解码再编码）。"""
    return _WIRE_BYTES.sub(rb"\1", raw)
