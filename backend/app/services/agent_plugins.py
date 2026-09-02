"""Agent 插件注册表（P6 插件化第一步）：调度主管从注册表读可用路由，而非硬编码。

设计对标 DeepSeek Harness「everything is a plugin」的轻量落地：
- 每个 Agent 是一个插件，声明 route（路由 key）、label（显示名）、description
  （给主管做语义路由看的描述）、tool_key（前端工具开关）、可用条件
  （是否有卡/是否有图/是否配置 MCP）。
- 内置插件在模块尾部注册（迁移原 agent_graph 硬编码的 _ROUTE_* 表）。
- 用户插件后续从 DATA_DIR/agent_plugins.json 动态加载（可覆盖内置 label/description，
  新增 route 需提供 node 实现——代码插件化在下一阶段接入）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.config import DATA_DIR


@dataclass(frozen=True)
class AgentPlugin:
    route: str
    label: str
    description: str
    tool_key: str = ""            # 对应前端工具开关；空=无开关
    requires_card: bool = False   # 需要角色卡才可用（roleplay）
    image_policy: str = "any"     # any=不关心 / no_images=有图不可用 / only_images=无图不可用
    requires_mcp: bool = False    # 需要配置 MCP 才可用（tool_agent）
    builtin: bool = True          # 内置插件（False=用户插件）


_PLUGINS: dict[str, AgentPlugin] = {}


def register(plugin: AgentPlugin) -> None:
    if not plugin.route.strip() or not plugin.label.strip() or not plugin.description.strip():
        raise ValueError("Agent 插件必须声明 route/label/description")
    if plugin.image_policy not in ("any", "no_images", "only_images"):
        raise ValueError(f"{plugin.route}: image_policy 非法")
    _PLUGINS[plugin.route] = plugin


def get(route: str) -> AgentPlugin | None:
    return _PLUGINS.get(route)


def all_plugins() -> list[AgentPlugin]:
    return [_PLUGINS[k] for k in sorted(_PLUGINS)]


def route_label(route: str) -> str:
    plugin = _PLUGINS.get(route)
    return plugin.label if plugin else route


def route_description(route: str) -> str:
    plugin = _PLUGINS.get(route)
    return plugin.description if plugin else ""


def route_available(route: str, *, has_images: bool, has_card: bool,
                    has_mcp: bool, agent_cfg: Any, tool_on: Any) -> bool:
    plugin = _PLUGINS.get(route)
    if plugin is None:
        return False
    if plugin.requires_card and not has_card:
        return False
    if plugin.image_policy == "no_images" and has_images:
        return False
    if plugin.image_policy == "only_images" and not has_images:
        return False
    if plugin.requires_mcp and not has_mcp:
        return False
    if plugin.tool_key and not tool_on(agent_cfg, plugin.tool_key):
        return False
    return True


def load_user_plugins() -> int:
    """从 DATA_DIR/agent_plugins.json 加载用户插件（覆盖内置 label/description）。

    文件形如 {"plugins": [{"route": "plan", "label": "…", "description": "…"}]}。
    当前只支持覆盖元数据；新增 route 需配套 node 实现（下一阶段）。
    """
    path = DATA_DIR / "agent_plugins.json"
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("plugins") if isinstance(data, dict) else data
    except (OSError, json.JSONDecodeError):
        return 0
    loaded = 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        route = str(item.get("route") or "").strip()
        existing = _PLUGINS.get(route)
        if existing is None:
            continue  # 新 route 需要 node 实现，本阶段忽略
        updated = AgentPlugin(
            route=route,
            label=str(item.get("label") or existing.label),
            description=str(item.get("description") or existing.description),
            tool_key=str(item.get("tool_key") or existing.tool_key),
            requires_card=bool(item.get("requires_card", existing.requires_card)),
            image_policy=str(item.get("image_policy") or existing.image_policy),
            requires_mcp=bool(item.get("requires_mcp", existing.requires_mcp)),
            builtin=False,
        )
        _PLUGINS[route] = updated
        loaded += 1
    return loaded


def _reset_for_tests() -> None:
    _PLUGINS.clear()


# ── 内置 Agent 插件（原 agent_graph._ROUTE_LABELS/_ROUTE_DESCRIPTIONS）─────────

register(AgentPlugin("answer", "继续对话",
                     "普通对话、问答，以及审查、解释、评价或优化已有内容"))
register(AgentPlugin("roleplay", "剧情扮演",
                     "沉浸式角色扮演：推进剧情、以角色身份出演对白与叙事",
                     requires_card=True))
register(AgentPlugin("generate", "生成图片",
                     "根据文本生成新图片，或执行无参考图的完整成稿提示词",
                     tool_key="generate_image", image_policy="no_images"))
register(AgentPlugin("img2img", "参考图生图",
                     "基于本轮图片附件生成、修改或续接新图片",
                     tool_key="image_to_image", image_policy="only_images"))
register(AgentPlugin("analyze", "反推提示词",
                     "从本轮图片附件反推并交付新的可复用提示词文本",
                     tool_key="analyze_image", image_policy="only_images"))
register(AgentPlugin("video", "生成视频",
                     "生成视频、动画或动图", tool_key="generate_video"))
register(AgentPlugin("inspire", "查找灵感",
                     "联网查找参考、灵感、流行款式或趋势", tool_key="search_inspiration"))
register(AgentPlugin("tool_agent", "调用工具",
                     "调用已接入的外部工具、接口、文件或数据库能力", requires_mcp=True))
register(AgentPlugin("edit", "编辑作品文件",
                     "创建角色卡、编写作品脚本、读取和修改当前作品文件并排错"))
register(AgentPlugin("plan", "智能编造计划",
                     "智能编造多步任务：批量出图、批量导入整理、跨能力编排等（自由循环/计划经审批后台执行）"))
