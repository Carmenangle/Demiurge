"""对话模型调用的深模块：建模型（含 /v1 规则）、多模态内容展平、单轮调用。

此前散落各处的三件事收拢于此：
- normalize_base_url：OpenAI 兼容接口的 /v1 后缀规则（原 _build_chat_model / rag_store._norm_url / image_agent._build 各一份）。
- flatten_content：把 LLM 返回的 content（可能是 list 分段）展平成纯文本（原重复 6 处）。
- build_model / chat：构建 init_chat_model 并单轮调用取文本。

不含 HTTP 语义（不抛 HTTPException）——路由层按需把 ValueError 包成 4xx/5xx。
"""
from collections.abc import Callable
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
                temperature: float = 0.7, streaming: bool = False, proxy: str = "",
                top_p: float | None = None, max_tokens: int | None = None,
                sdk_retries: int | None = None):
    """构建 OpenAI 兼容对话模型。缺配置抛 ValueError（由调用方决定如何呈现）。

    proxy **显式非空**时才注入代理 http_client；为空则**完全默认构造**——与仓库对话
    (image_agent 的 init_chat_model)走同一路径，那条路径一直能连通。
    ⚠教训：曾强行给无代理分支加 trust_env=False，反而切断了原本靠系统环境代理连中转的通路
    (表现 timed out / Connection error)。默认不碰 http_client 才是安全的。
    top_p/max_tokens：非空才注入（此前自定义 Agent/预设存了这两项却从未生效，现打通到模型）。
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
    if isinstance(top_p, (int, float)) and not isinstance(top_p, bool):
        kw["top_p"] = float(top_p)
    if isinstance(max_tokens, int) and not isinstance(max_tokens, bool) and max_tokens > 0:
        kw["max_tokens"] = max_tokens
    if isinstance(sdk_retries, int) and sdk_retries >= 0:
        kw["max_retries"] = sdk_retries
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


_ROLE_MAP = {"system": "system", "user": "human", "assistant": "ai", "human": "human", "ai": "ai"}


def prepare_messages(model: str, messages: list[dict]) -> list[dict[str, str]]:
    """返回实际发送结构；仅 Claude 合并 system、交替历史并去除末轮重复。"""
    cleaned = [
        {"role": (m.get("role") or "user"), "content": m.get("content") or ""}
        for m in messages if (m.get("content") or "").strip()
    ]
    if "claude" not in (model or "").casefold():
        return cleaned

    # GrayWill 常在倒数 user 包装 {{lastUserMessage}}，调用方又追加真实末轮 user。
    # 只处理末两条非 system 都是 user 的明确形态，避免删除更早历史里的相同短句。
    dialog_indexes = [i for i, m in enumerate(cleaned) if m["role"] != "system"]
    if len(dialog_indexes) >= 2:
        previous, current = dialog_indexes[-2:]
        current_text = cleaned[current]["content"]
        if (cleaned[previous]["role"] in ("user", "human")
                and cleaned[current]["role"] in ("user", "human")
                and current_text.strip() and current_text in cleaned[previous]["content"]):
            cleaned[previous]["content"] = cleaned[previous]["content"].replace(current_text, "")

    systems = [m["content"].strip() for m in cleaned if m["role"] == "system" and m["content"].strip()]
    turns: list[dict[str, str]] = []
    for message in cleaned:
        role = message["role"]
        if role == "system":
            continue
        canonical = "assistant" if role in ("assistant", "ai") else "user"
        content = message["content"].strip()
        if not content:
            continue
        if turns and turns[-1]["role"] == canonical:
            turns[-1]["content"] += "\n\n" + content
        else:
            turns.append({"role": canonical, "content": content})

    prepared: list[dict[str, str]] = []
    if systems:
        prepared.append({"role": "system", "content": "\n\n".join(systems)})
    prepared.extend(turns)
    return prepared or [{"role": "user", "content": ""}]


def _payload(model: str, messages: list[dict]) -> list[tuple[str, str]]:
    return [
        (_ROLE_MAP.get(message["role"], "human"), message["content"])
        for message in prepare_messages(model, messages)
    ]


def chat_messages(base_url: str, api_key: str, model: str, messages: list[dict],
                  temperature: float = 0.7, proxy: str = "", retries: int = 2,
                  top_p: float | None = None, max_tokens: int | None = None) -> str:
    """多消息单轮对话：messages=[{"role":"system|user|assistant","content":..}]，保留各条 role
    发给模型（不折叠成单 system 串），返回展平后的回复文本。空/无 content 的条目跳过。
    上游临时故障退避重试；调用失败抛 RuntimeError。`chat` 是它 system+user 两条的特例。"""
    import time
    payload = _payload(model, messages)
    llm = build_model(base_url, api_key, model, temperature=temperature, proxy=proxy,
                      top_p=top_p, max_tokens=max_tokens)
    last: Exception | None = None
    for i in range(max(1, retries)):
        try:
            resp = llm.invoke(payload)
            return flatten_content(resp.content).strip()
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries - 1 and _is_transient(e):
                time.sleep(2 ** i)   # 1s、2s、4s 退避
                continue
            break
    raise RuntimeError(f"调用对话模型失败：{last}")


def chat_messages_stream(base_url: str, api_key: str, model: str, messages: list[dict],
                         on_delta: Callable[[str], None], temperature: float = 0.7,
                         proxy: str = "", retries: int = 2,
                         top_p: float | None = None, max_tokens: int | None = None) -> str:
    """流式调用多消息对话，并把每个正文增量交给调用方；同时返回完整原文供后处理。

    仅在本次尝试尚未产生任何增量时重试，避免连接中断后把已显示的半段正文重复输出。
    """
    import time
    payload = _payload(model, messages)
    llm = build_model(
        base_url, api_key, model, temperature=temperature, streaming=True, proxy=proxy,
        top_p=top_p, max_tokens=max_tokens, sdk_retries=0,
    )
    last: Exception | None = None
    for i in range(max(1, retries)):
        parts: list[str] = []
        try:
            for chunk in llm.stream(payload):
                delta = flatten_content(chunk.content)
                if not delta:
                    continue
                parts.append(delta)
                on_delta(delta)
            return "".join(parts).strip()
        except Exception as e:  # noqa: BLE001
            last = e
            if parts or i >= retries - 1 or not _is_transient(e):
                break
            time.sleep(2 ** i)
    raise RuntimeError(f"调用对话模型失败：{last}")


def chat(base_url: str, api_key: str, model: str, system: str, user: str,
         temperature: float = 0.7, proxy: str = "", retries: int = 2,
         top_p: float | None = None, max_tokens: int | None = None) -> str:
    """非流式单轮对话（system+user 两条），返回展平后的回复文本。多角色片段用 chat_messages。"""
    return chat_messages(
        base_url, api_key, model,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature, proxy=proxy, retries=retries,
        top_p=top_p, max_tokens=max_tokens,
    )
