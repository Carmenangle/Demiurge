"""MCP 服务器配置端点：列表/保存/测试。前端设置页「MCP」用。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import mcp_store, mcp_client

router = APIRouter()


class McpServer(BaseModel):
    id: str = ""
    name: str
    type: str = "stdio"          # stdio | sse
    command: str = ""            # stdio 用
    args: list[str] = []         # stdio 用
    url: str = ""                # sse 用
    enabled: bool = True


@router.get("")
def list_servers() -> list[McpServer]:
    return [McpServer(**s) for s in mcp_store.load_servers()]


@router.post("")
def save_servers(servers: list[McpServer]) -> list[McpServer]:
    saved = mcp_store.save_servers([s.model_dump() for s in servers])
    mcp_client.invalidate_cache()   # 配置变了，下次重新加载工具
    return [McpServer(**s) for s in saved]


class ProbeRequest(BaseModel):
    server: McpServer


@router.post("/test")
def test_server(req: ProbeRequest) -> dict:
    """测试连通性并列出该服务器暴露的工具名。"""
    return mcp_client.probe_server(req.server.model_dump())


# ===== Smithery 市场：浏览/搜索并一键添加 MCP 服务器 =====

@router.get("/smithery/search")
def smithery_search(q: str = "", page: int = 1, page_size: int = 20) -> dict:
    """搜索 Smithery 注册表的 MCP 服务器。registry 直连可达，不走代理。"""
    from app.services import smithery_client
    return smithery_client.search_servers(q, page, page_size, api_key=_smithery_key())


@router.get("/smithery/server/{qualified_name:path}")
def smithery_server(qualified_name: str) -> dict:
    """取某 Smithery 服务器详情（连接方式/配置项/工具列表）。"""
    from app.services import smithery_client
    return smithery_client.get_server(qualified_name, api_key=_smithery_key())


class AddSmitheryRequest(BaseModel):
    qualified_name: str
    display_name: str = ""


@router.post("/smithery/add")
def smithery_add(req: AddSmitheryRequest) -> list[McpServer]:
    """把一个 Smithery 服务器加入本地 MCP 配置（streamable http，用全局 Smithery key 连接）。"""
    from app.services import smithery_client
    key = _smithery_key()
    url = smithery_client.connection_url(req.qualified_name, key)
    servers = mcp_store.load_servers()
    servers.append({
        "name": req.display_name or req.qualified_name,
        "type": "http",          # streamable http
        "url": url,
        "enabled": True,
    })
    saved = mcp_store.save_servers(servers)
    mcp_client.invalidate_cache()
    return [McpServer(**s) for s in saved]


def _smithery_key() -> str:
    """从 user_state.json 的 settings 读全局 Smithery API Key。"""
    import json
    from app.config import DATA_DIR
    p = DATA_DIR / "user_state.json"
    if p.is_file():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return (d.get("settings") or {}).get("smitheryKey", "") or ""
        except Exception:
            pass
    return ""
