"""Smithery 注册表客户端：搜索/详情 MCP 服务器。

Smithery 是最大的 MCP 服务器市场。用户在设置里浏览、一键添加服务器，无需手填命令/URL。
- 搜索/详情：registry.smithery.ai（公开可读，带 api_key 可提高配额/访问私有）
- 连接：远程服务器 URL = https://server.smithery.ai/{qualifiedName}/mcp?api_key=...（streamable http）
外网访问走用户配置的代理（与灵感搜索同路径）。
"""
from __future__ import annotations

import httpx

_REGISTRY = "https://registry.smithery.ai"
_SERVER_BASE = "https://server.smithery.ai"


def _client(proxy_url: str = "") -> httpx.Client:
    # registry.smithery.ai 国内 CDN 直连可达，默认直连最稳；仅显式传 proxy_url 时才走代理。
    # trust_env=False 避免误用本机代理环境变量（同项目其他外网调用的约定）。
    if proxy_url:
        return httpx.Client(proxy=proxy_url, timeout=20, trust_env=False)
    return httpx.Client(timeout=20, trust_env=False)


def _headers(api_key: str) -> dict:
    h = {"Accept": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def search_servers(q: str = "", page: int = 1, page_size: int = 20,
                   api_key: str = "", proxy_url: str = "") -> dict:
    """搜索 MCP 服务器。返回 {ok, servers:[...], pagination, error}。"""
    params = {"page": page, "pageSize": page_size}
    if q:
        params["q"] = q
    try:
        with _client(proxy_url) as c:
            r = c.get(f"{_REGISTRY}/servers", params=params, headers=_headers(api_key))
            if r.status_code != 200:
                return {"ok": False, "servers": [], "pagination": {}, "error": f"HTTP {r.status_code}"}
            d = r.json()
            return {"ok": True, "servers": d.get("servers", []), "pagination": d.get("pagination", {}), "error": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "servers": [], "pagination": {}, "error": str(e)}


def get_server(qualified_name: str, api_key: str = "", proxy_url: str = "") -> dict:
    """取服务器详情（含 connections/configSchema/tools）。返回 {ok, server, error}。"""
    try:
        with _client(proxy_url) as c:
            r = c.get(f"{_REGISTRY}/servers/{qualified_name}", headers=_headers(api_key))
            if r.status_code != 200:
                return {"ok": False, "server": None, "error": f"HTTP {r.status_code}"}
            return {"ok": True, "server": r.json(), "error": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "server": None, "error": str(e)}


def connection_url(qualified_name: str, api_key: str) -> str:
    """构造 Smithery 托管的远程连接 URL（streamable http）。"""
    base = f"{_SERVER_BASE}/{qualified_name}/mcp"
    return f"{base}?api_key={api_key}" if api_key else base


# ===== Skills（技能 = 提示词 + 可选依赖的 MCP 服务器）=====

def search_skills(q: str = "", page: int = 1, page_size: int = 20,
                  api_key: str = "", proxy_url: str = "") -> dict:
    """搜索 Smithery skills。返回 {ok, skills:[...], pagination, error}。"""
    params = {"page": page, "pageSize": page_size}
    if q:
        params["q"] = q
    try:
        with _client(proxy_url) as c:
            r = c.get(f"{_REGISTRY}/skills", params=params, headers=_headers(api_key))
            if r.status_code != 200:
                return {"ok": False, "skills": [], "pagination": {}, "error": f"HTTP {r.status_code}"}
            d = r.json()
            return {"ok": True, "skills": d.get("skills", []), "pagination": d.get("pagination", {}), "error": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "skills": [], "pagination": {}, "error": str(e)}


def get_skill(namespace: str, slug: str, api_key: str = "", proxy_url: str = "") -> dict:
    """取技能详情（含完整 prompt + 依赖的 servers）。返回 {ok, skill, error}。"""
    try:
        with _client(proxy_url) as c:
            r = c.get(f"{_REGISTRY}/skills/{namespace}/{slug}", headers=_headers(api_key))
            if r.status_code != 200:
                return {"ok": False, "skill": None, "error": f"HTTP {r.status_code}"}
            return {"ok": True, "skill": r.json(), "error": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "skill": None, "error": str(e)}
