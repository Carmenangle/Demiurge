"""Agent 跨簇微助手（方案 B 拆分底座）。

逐字节自 agent_graph.py 搬移（方案 B 第一步，见 docs/memory/agent-graph-seam-plan-2026-09-16.md）；
行为等价由既有测试网 + 搬移块 sha1 校验保证。
"""

from __future__ import annotations

def _proxy_kw(ctx: dict, key: str = "chat_proxy") -> dict:
    value = (ctx.get(key, "") or "").strip()
    return {"proxy": value} if value else {}
