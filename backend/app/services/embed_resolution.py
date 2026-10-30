"""嵌入/对话模型解析：请求里带的配置优先，不可用才退执行器本机设置。

联机同库时前端把手机上的模型设置随请求发给执行请求的那台后端（手机操作、手机
设置）；手机默认的「云端嵌入」在没填完时只是空占位，不能拿它去打请求——此时退到
执行器自己的 `user_state.settings`（「没有可调用的就用电脑那一套」）。回退只看
**配置是否完整**，不做健康探测，也绝不把执行器的配置回写进请求或手机存档。

「可用」口径：
- remote：base_url + embed_model 必填；公网地址（非回环/私网/本机名）还要 apiKey
  ——「填了云端地址却忘填 key」按不完整处理，避免每轮 401；
- local：model_dir 非空且真实存在（手机没有本地嵌入运行时，自然不可用 ⇒ 回退）。
"""
from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import urlparse

from app.services import repo_meta
from app.services.rag_backend import EmbedConfig

_CHAT_OLLAMA_BASE = "http://localhost:11434/v1"


def _host_of(base_url: str) -> str:
    parsed = urlparse(base_url if "://" in base_url else f"//{base_url}")
    return (parsed.hostname or "").strip().lower()


def _host_is_local(host: str) -> bool:
    if not host:
        return False
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def endpoint_usable(base_url: str, model: str, api_key: str = "") -> bool:
    """OpenAI 兼容端点能否直接使用（缺地址/模型一律不可用；公网缺 key 视为没配完）。"""
    base = (base_url or "").strip()
    if not base or not (model or "").strip():
        return False
    if (api_key or "").strip():
        return True
    return _host_is_local(_host_of(base))


def is_usable(cfg: EmbedConfig | None) -> bool:
    if cfg is None:
        return False
    if cfg.mode == "local":
        model_dir = (cfg.model_dir or "").strip()
        return bool(model_dir) and Path(model_dir).expanduser().is_dir()
    return endpoint_usable(cfg.base_url, cfg.embed_model, cfg.api_key)


def _settings() -> dict:
    state = repo_meta.load_user_state()
    settings = state.get("settings") if isinstance(state, dict) else None
    return settings if isinstance(settings, dict) else {}


def settings_embed(settings: dict | None = None) -> EmbedConfig:
    """执行器 settings.embedModel → EmbedConfig（camelCase 映射的单一属主）。"""
    settings = settings if isinstance(settings, dict) else _settings()
    embed = settings.get("embedModel")
    if not isinstance(embed, dict):
        embed = {}
    return EmbedConfig(
        base_url=str(embed.get("baseUrl") or ""),
        api_key=str(embed.get("apiKey") or ""),
        embed_model=str(embed.get("modelName") or ""),
        model_dir=str(embed.get("modelDir") or ""),
        reranker_dir=str(embed.get("rerankerDir") or ""),
        mode="local" if embed.get("mode") == "local" else "remote",
        proxy=str(embed.get("proxyUrl") or ""),
    )


def embed_from_settings(settings: dict | None = None) -> EmbedConfig | None:
    """执行器自己的嵌入配置；不完整返回 None（调用方跳过而不是打空转）。"""
    cfg = settings_embed(settings)
    return cfg if is_usable(cfg) else None


def resolve(requested: EmbedConfig | None) -> EmbedConfig | None:
    """请求自带配置可用就用它；否则用执行器自己的；两者都不可用返回 None。"""
    if is_usable(requested):
        return requested
    return embed_from_settings()


def resolve_or(requested: EmbedConfig) -> EmbedConfig:
    """同 resolve，但都没有时原样返回请求配置（保留既有失败语义，类型不变）。"""
    return resolve(requested) or requested


def chat_from_settings(settings: dict | None = None) -> tuple[str, str, str] | None:
    """执行器当前选中的对话模型 → (base, key, model)；不可用返回 None。

    与前端 `activeChatModel` 同口径：本地/Ollama 卡可只存模型名，地址缺省为本机
    Ollama（在**执行器**上解析，回环即执行器自己）。
    """
    settings = settings if isinstance(settings, dict) else _settings()
    models = settings.get("chatModels")
    if not isinstance(models, list):
        return None
    active_id = settings.get("activeChatModelId")
    chosen = next(
        (m for m in models if isinstance(m, dict) and m.get("id") == active_id), None)
    if chosen is None:
        chosen = next((m for m in models if isinstance(m, dict)), None)
    if chosen is None:
        return None
    base = str(chosen.get("baseUrl") or "").strip()
    key = str(chosen.get("apiKey") or "").strip()
    model = str(chosen.get("modelName") or "").strip()
    if str(chosen.get("source") or "") in ("ollama", "local"):
        base = base or _CHAT_OLLAMA_BASE
        key = key or "ollama"
        model = model or str(chosen.get("ollamaName") or "").strip()
    if not endpoint_usable(base, model, key):
        return None
    return base, key, model


def resolve_chat(base_url: str, api_key: str, model: str) -> tuple[str, str, str]:
    """请求的对话配置可用就用它；否则退执行器当前模型；都没有原样返回。"""
    if endpoint_usable(base_url, model, api_key):
        return base_url, api_key, model
    fallback = chat_from_settings()
    return fallback if fallback else (base_url, api_key, model)
