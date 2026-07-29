"""MCP 客户端：把用户配置的 MCP 服务器工具加载为 langchain 工具，供智能体调用。

用 langchain-mcp-adapters 的 MultiServerMCPClient。get_tools 是 async，这里提供同步封装
（内部跑一次性 event loop），供同步的 agent 构建链路使用。

工具列表按「已启用服务器配置」缓存，避免每轮对话都重新连服务器拉取（慢且有副作用）。
配置变更（保存 MCP 设置）时调 invalidate_cache 让下次重新加载。
"""
from __future__ import annotations

import asyncio
import logging

from app.services import mcp_store

_log = logging.getLogger("uvicorn.error")

# 缓存：(配置指纹) -> 工具列表。配置没变就复用。
_cache_key: str | None = None
_cache_tools: list = []


def _connections_from(servers: list[dict]) -> dict:
    """把 mcp_config.json 的条目转成 MultiServerMCPClient 的 connections dict。"""
    conns: dict = {}
    for s in servers:
        name = s.get("name") or s.get("id") or "server"
        stype = (s.get("type") or "stdio").lower()
        if stype == "stdio":
            cmd = s.get("command")
            if not cmd:
                continue
            conns[name] = {
                "transport": "stdio",
                "command": cmd,
                "args": s.get("args") or [],
            }
        elif stype in ("http", "streamable_http"):  # Smithery 托管的远程服务器走 streamable http
            url = s.get("url")
            if not url:
                continue
            conns[name] = {"transport": "streamable_http", "url": url}
        else:  # sse
            url = s.get("url")
            if not url:
                continue
            conns[name] = {"transport": "sse", "url": url}
    return conns


async def _aload(conns: dict) -> list:
    from langchain_mcp_adapters.client import MultiServerMCPClient
    client = MultiServerMCPClient(conns)
    return await client.get_tools()


def _fingerprint(servers: list[dict]) -> str:
    import json
    return json.dumps(servers, sort_keys=True, ensure_ascii=False)


def _load_from_servers(servers: list[dict]) -> list:
    """从给定服务器配置列表加载工具（带指纹缓存）。失败返回空，不阻断。"""
    global _cache_key, _cache_tools
    if not servers:
        return []
    fp = _fingerprint(servers)
    if fp == _cache_key:
        return _cache_tools
    conns = _connections_from(servers)
    if not conns:
        _cache_key, _cache_tools = fp, []
        return []
    try:
        tools = _run_async(_aload(conns))
        _log.info("MCP 加载 %d 个工具（来自 %d 个服务器）", len(tools), len(conns))
        _cache_key, _cache_tools = fp, tools
        return tools
    except Exception as e:  # noqa: BLE001
        _log.warning("MCP 工具加载失败（跳过，不影响其他工具）：%s", e)
        return []


def load_mcp_tools() -> list:
    """加载所有已启用 MCP 服务器的工具（无 Agent 选择时用，如内置默认对话）。"""
    return _load_from_servers(mcp_store.enabled_servers())


def load_tools_for_servers(server_ids: list[str]) -> list:
    """按 MCP 服务器 id 列表加载工具（多 Agent 选择性启用；空列表=不加载）。仍要求该服务器 enabled。"""
    if not server_ids:
        return []
    idset = set(server_ids)
    servers = [s for s in mcp_store.enabled_servers() if s.get("id") in idset]
    return _load_from_servers(servers)


def invalidate_cache() -> None:
    """配置变更后调用，强制下次重新加载。"""
    global _cache_key
    _cache_key = None


def probe_server(server: dict) -> dict:
    """测试单个服务器连通性，返回 {ok, tools:[名字], error}。供设置页「测试」按钮用。"""
    conns = _connections_from([server])
    if not conns:
        return {"ok": False, "tools": [], "error": "配置不完整（缺 command 或 url）"}
    try:
        tools = _run_async(_aload(conns))
        return {"ok": True, "tools": [t.name for t in tools], "error": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "tools": [], "error": str(e)}


def _run_async(coro):
    """在同步上下文里跑一个协程。无运行中的 loop 时用 asyncio.run；有则新开线程跑。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # 已在事件循环内（少见于本项目同步链路）：另起线程执行
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()
