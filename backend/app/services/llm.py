"""对话模型调用的深模块：建模型（含 /v1 规则）、多模态内容展平、单轮调用。

此前散落各处的三件事收拢于此：
- normalize_base_url：OpenAI 兼容接口的 /v1 后缀规则（原 _build_chat_model / rag_store._norm_url / image_agent._build 各一份）。
- flatten_content：把 LLM 返回的 content（可能是 list 分段）展平成纯文本（原重复 6 处）。
- build_model / chat：构建 init_chat_model 并单轮调用取文本。

不含 HTTP 语义（不抛 HTTPException）——路由层按需把 ValueError 包成 4xx/5xx。
"""
from typing import Any


def normalize_base_url(base_url: str) -> str:
    """OpenAI 兼容接口地址补 /v1 后缀（已含 /v1 或 /chat/completions 则不动）。"""
    url = (base_url or "").rstrip("/")
    if not url.endswith("/v1") and "/chat/completions" not in url:
        url += "/v1"
    return url


def flatten_content(content: Any) -> str:
    """把 LLM 返回内容展平成纯文本。content 可能是 str，也可能是 [{"type":"text","text":..}] 分段。"""
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return content or ""


def build_model(base_url: str, api_key: str, model: str,
                temperature: float = 0.7, streaming: bool = False, proxy: str = ""):
    """构建 OpenAI 兼容对话模型。缺配置抛 ValueError（由调用方决定如何呈现）。

    proxy **显式非空**时才注入代理 http_client；为空则**完全默认构造**——与仓库对话
    (image_agent 的 init_chat_model)走同一路径，那条路径一直能连通。
    ⚠教训：曾强行给无代理分支加 trust_env=False，反而切断了原本靠系统环境代理连中转的通路
    (表现 timed out / Connection error)。默认不碰 http_client 才是安全的。
    """
    if not base_url or not model:
        raise ValueError("请先在「设置 → 对话模型」配置接口地址与模型")
    from langchain.chat_models import init_chat_model
    kw = dict(
        model_provider="openai",
        base_url=normalize_base_url(base_url),
        api_key=api_key or "not-needed",
        temperature=temperature,
        streaming=streaming,
    )
    p = (proxy or "").strip()
    if p:
        import httpx
        kw["http_client"] = httpx.Client(proxy=p, timeout=120)  # 仅显式代理时注入
    else:
        kw["timeout"] = 200  # 不带代理也设单次超时。中转慢时单次搭建(复杂prompt+长JSON)可能60-120s，给足200s(精简直连只调1次，前端240s内)
    return init_chat_model(model, **kw)


def _is_transient(err: Exception) -> bool:
    """判断是否上游临时故障（值得重试）：502/503/504、timeout、connection、upstream 等。
    中转对大请求/长耗时请求常临时 502(upstream_error)，短对话不触发——退避重试多能自愈。"""
    s = str(err).lower()
    return any(t in s for t in (
        "502", "503", "504", "upstream", "timeout", "timed out",
        "temporarily", "overload", "rate limit", "429", "connection error"))


def chat(base_url: str, api_key: str, model: str, system: str, user: str,
         temperature: float = 0.7, proxy: str = "", retries: int = 2) -> str:
    """非流式单轮对话，返回展平后的回复文本。proxy 透传；上游临时故障(502/超时等)退避重试。
    调用失败抛 RuntimeError。"""
    import time
    llm = build_model(base_url, api_key, model, temperature=temperature, proxy=proxy)
    last: Exception | None = None
    for i in range(max(1, retries)):
        try:
            resp = llm.invoke([("system", system), ("user", user)])
            return flatten_content(resp.content).strip()
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries - 1 and _is_transient(e):
                time.sleep(2 ** i)   # 1s、2s、4s 退避
                continue
            break
    raise RuntimeError(f"调用对话模型失败：{last}")
