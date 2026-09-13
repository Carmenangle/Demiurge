"""对话模型调用的深模块：建模型（含 /v1 规则）、多模态内容展平、单轮调用。

此前散落各处的三件事收拢于此：
- normalize_base_url：OpenAI 兼容接口的 /v1 后缀规则（原 _build_chat_model / rag_store._norm_url / image_agent._build 各一份）。
- flatten_content：把 LLM 返回的 content（可能是 list 分段）展平成纯文本（原重复 6 处）。
- build_model / chat：构建 init_chat_model 并单轮调用取文本。

不含 HTTP 语义（不抛 HTTPException）——路由层按需把 ValueError 包成 4xx/5xx。
"""
import re
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
                sdk_retries: int | None = None, timeout_override: float | None = None,
                stream_usage: bool = False):
    """构建 OpenAI 兼容对话模型。缺配置抛 ValueError（由调用方决定如何呈现）。

    stream_usage: 流式请求是否显式带上 `stream_options: {"include_usage": true}`。
        **不显式要，OpenAI 兼容网关不会在流式末块回 usage**（DeepSeek 官方 harness 同做法），
        命中率观测就永远是 0。默认 False——由 `chat_messages_stream` 按上游实际支持情况开启
        （见该函数内的自动降级），避免本函数被直接调用时踩到拒绝该字段的中转。
    ⚠教训：曾强行给无代理分支加 trust_env=False，反而切断了原本靠系统环境代理连中转的通路
    (表现 timed out / Connection error)。默认不碰 http_client 才是安全的。

    proxy **显式非空**时才注入代理 http_client；为空则**完全默认构造**——与仓库对话
    (image_agent 的 init_chat_model)走同一路径，那条路径一直能连通。
    ⚠教训：曾强行给无代理分支加 trust_env=False，反而切断了原本靠系统环境代理连中转的通路
    (表现 timed out / Connection error)。默认不碰 http_client 才是安全的。
    top_p/max_tokens：非空才注入（此前自定义 Agent/预设存了这两项却从未生效，现打通到模型）。
    ⚠2026-09-12：此处曾有 `on_usage` 参数但**函数体从未引用**（usage 只能从 invoke/stream 的
    响应取，构造函数挂不上钩子）——死参数会让调用方误以为观测已接通，已删除；护栏见
    `tests/test_llm_usage.py::test_build_model_不得再有死参on_usage`。
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
    if stream_usage:
        # 走 model_kwargs（而不是 LangChain 的 stream_usage= 构造参数）：前者对所有版本的
        # ChatOpenAI 都等价于「原样并进请求体」，后者依赖版本，且我们要的只是这个请求字段。
        kw["model_kwargs"] = {"stream_options": {"include_usage": True}}
    p = (proxy or "").strip()
    # 2026-08-31 深夜用户定案：流式读超时=30s。正常吐字 1 秒都不到，30s 一个 token
    # 都不返回必然是上游卡死——立刻 ReadTimeout 走自愈，不误伤正常慢出字（只要还在
    # 出 token 就永远不会触发）。
    _read_timeout = timeout_override if timeout_override is not None else (
        30 if streaming else (120 if p else 200))
    if p:
        import httpx
        kw["http_client"] = httpx.Client(proxy=p, timeout=_read_timeout)  # 仅显式代理时注入
    elif _is_local_url(base_url):
        # 本地端点（Ollama / 127.0.0.1 / localhost）：显式禁用环境代理。
        # ⚠Windows 下 httpx trust_env=True 会读 WinINET 注册表里的系统代理(如 Clash 127.0.0.1:7897)，
        #   localhost 请求被转发到代理 → 502。本地直连必须 trust_env=False。
        import httpx
        kw["http_client"] = httpx.Client(trust_env=False, timeout=_read_timeout)
    else:
        kw["timeout"] = _read_timeout  # 公网中转：不带代理也设单次超时(不碰 http_client，保持系统代理通路)。
        #   非流式慢时单次搭建(复杂prompt+长JSON)可能60-120s，给足200s；
        #   流式 60s 无 token 即断（见上）。
    return init_chat_model(model, **kw)


def _is_local_url(base_url: str) -> bool:
    """判断接口地址是否指向本机（localhost / 127.0.0.1 / ::1 / 192.168.* 等内网）。

    本地端点必须绕过系统代理（WinINET 注册表代理会把 localhost 转发到 Clash 等导致 502）；
    公网端点保留系统代理通路（某些中转必须走代理才能连通）。
    """
    import re
    u = (base_url or "").strip().lower()
    # 去掉协议头
    rest = u.split("//", 1)[-1]
    # IPv6 字面量 [::1] 处理
    if rest.startswith("["):
        host = rest.split("]", 1)[0].lstrip("[")
    else:
        host = rest.split("/", 1)[0].split(":", 1)[0]
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    # 内网网段 10.* / 192.168.* / 172.16-31.*
    if re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)", host):
        return True
    return False


def _is_transient(err: Exception) -> bool:
    """判断是否上游临时故障（值得重试）：502/503/504、timeout、connection、upstream 等。
    中转对大请求/长耗时请求常临时 502(upstream_error)，短对话不触发——退避重试多能自愈。"""
    s = str(err).lower()
    return any(t in s for t in (
        "502", "503", "504", "upstream", "timeout", "timed out",
        "temporarily", "overload", "rate limit", "429", "connection error"))


# ── 模型单轮输出上限（OpenAI 兼容网关按模型 max output 校验 max_tokens）──
# 2026-09-04 trace 实锤：GrayWill 预设 openai_max_tokens=600000 → 正文 +4000=604000 直发，
# tokenrhythm 网关对 deepseek-v4-flash-0731 报 400 LITELLM_ERROR「max_tokens should be
# less or equal to 393216」→ 扮演失败且自愈整段重掷 3 次全白烧（opus@xtoken 网关无此校验
# 故此前未炸）。用户定案语义（2026-09-04）：请求上限 > 模型上限 → 按模型上限（min）；
# 模型上限更高/未登记 → 按请求原样正常走。登记键 = 模型名前缀（精确或带 -/_ 后缀变体均命中）。
MODEL_OUTPUT_TOKEN_CAPS: dict[str, int] = {
    "deepseek-v4-flash": 393216,  # tokenrhythm 网关实测输出上限（400 证据）
}


def cap_max_tokens(model: str, max_tokens: int | None) -> int | None:
    """按模型输出上限收敛 max_tokens（用户定案：min 语义）。未登记模型原样返回。

    供 transport 层（chat_messages/chat_messages_stream）与采样裁决点共用，保证
    trace 记录的值与实际发出请求一致；对已收敛值再次调用是幂等的。
    """
    if max_tokens is None or not isinstance(max_tokens, int) or max_tokens <= 0:
        return max_tokens
    name = (model or "").strip()
    for prefix, cap in MODEL_OUTPUT_TOKEN_CAPS.items():
        if name == prefix or name.startswith(f"{prefix}-") or name.startswith(f"{prefix}_"):
            return min(max_tokens, cap)
    return max_tokens


_CLIENT_CODE_RE = re.compile(
    r"error code[:：]?\s*4\d\d|http\s+4\d\d|status[ _-]?code[=: ]+4\d\d",
    re.IGNORECASE,
)


def is_client_rejected(err: Exception) -> bool:
    """请求/配置类确定性错误（4xx，429 除外）——重试必然同错，调用方应判死不重试。

    与 _is_transient 互补：429/5xx/timeout 走退避重试/截断自愈；400/401/403/404/422
    是确定性错误（超限参数、坏 key、坏模型名、不支持字段），整段重掷只会重复计费。
    2026-09-04 实锤：max_tokens=604000 触发网关 400，自愈 3 次整段重掷共 4 次调用全白烧。
    """
    if _is_transient(err):  # 429 属瞬时，先排除
        return False
    return bool(_CLIENT_CODE_RE.search(str(err)))


_ROLE_MAP = {"system": "system", "user": "human", "assistant": "ai", "human": "human", "ai": "ai"}


def prepare_messages(model: str, messages: list[dict], *, provider_profile: str = "") -> list[dict[str, str]]:
    """返回实际发送结构。

    ``provider_profile`` 非空时是运行态真源；仅为兼容旧调用，缺省时才按模型名推断。
    """
    def _has_content(content: Any) -> bool:
        if isinstance(content, str):
            return bool(content.strip())
        return bool(content)  # 多模态内容块列表（text/image_url parts）

    cleaned = [
        {"role": (m.get("role") or "user"), "content": m.get("content") or ""}
        for m in messages if _has_content(m.get("content"))
    ]
    profile = (provider_profile or "").strip().lower()
    is_claude = profile == "claude_compatible" if profile else (
        "claude" in (model or "").casefold()
    )
    if not is_claude:
        return cleaned

    # Claude-compatible 包装按纯文本字符串处理：多模态内容块展平为文本
    # （image_url 块无 text 字段会丢弃——该 profile 不承载视觉输入，fabric_loop
    # 带图任务走 openai 兼容通道不经此分支）。
    cleaned = [
        {**m, "content": m["content"] if isinstance(m["content"], str)
         else flatten_content(m["content"])}
        for m in cleaned
    ]

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


def _payload(model: str, messages: list[dict], *, provider_profile: str = "") -> list[tuple[str, str]]:
    return [
        (_ROLE_MAP.get(message["role"], "human"), message["content"])
        for message in prepare_messages(model, messages, provider_profile=provider_profile)
    ]


def _first_int(*values: Any) -> int:
    """取第一个可用整数（忽略 None/空串/非数值）；全空返回 0。"""
    for value in values:
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _collect_usage(usage: Any) -> dict:
    """从模型响应提取 usage 统计（兼容 OpenAI / DeepSeek / Claude / 中转差异）。

    2026-09-12（P0）三处口径修正——原则是「不假设上游回填，但回填了要算对」：
    1. **DeepSeek 的 `prompt_tokens` 已含命中**（`prompt_tokens = hit + miss`），
       不能用 `prompt_tokens - cached` 倒推未命中；有 `prompt_cache_miss_tokens` 时以它为准
       （官方 harness dsh 同口径）。
    2. 命中/写入字段三家各写各的，统一取值：OpenAI `prompt_tokens_details.cached_tokens`、
       DeepSeek `prompt_cache_hit_tokens`、Claude 原生 `cache_read_input_tokens` /
       `cache_creation_input_tokens`、LangChain 标准 `input_token_details.{cache_read,cache_creation}`。
    3. 新增 `uncached_prompt_tokens`（真正按全价计费的输入）与 `cache_write_tokens`。
       命中率分母与 dsh 一致：`uncached + cached + cache_write`。
    """
    if not isinstance(usage, dict):
        return {}
    details = usage.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    in_details = usage.get("input_token_details")
    in_details = in_details if isinstance(in_details, dict) else {}

    completion = _first_int(usage.get("completion_tokens"), usage.get("output_tokens"))
    cached = _first_int(
        usage.get("cached_tokens"), details.get("cached_tokens"),
        usage.get("prompt_cache_hit_tokens"), usage.get("cache_read_input_tokens"),
        in_details.get("cache_read"))
    cache_write = _first_int(
        usage.get("cache_creation_input_tokens"), usage.get("cache_write_tokens"),
        in_details.get("cache_creation"))

    if "cache_read_input_tokens" in usage:
        # Claude 原生口径：input_tokens **不含**缓存部分，总量要自己加回来
        uncached = _first_int(usage.get("input_tokens"), usage.get("prompt_tokens"))
        prompt = uncached + cached + cache_write
    else:
        prompt = _first_int(usage.get("prompt_tokens"), usage.get("input_tokens"))
        miss_raw = usage.get("prompt_cache_miss_tokens")
        uncached = (_first_int(miss_raw) if miss_raw is not None
                    else max(0, prompt - cached - cache_write))
        if not prompt:
            prompt = uncached + cached + cache_write

    denominator = uncached + cached + cache_write
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
        "uncached_prompt_tokens": uncached,
        "cache_write_tokens": cache_write,
        "total_tokens": _first_int(usage.get("total_tokens")) or (prompt + completion),
        "cache_hit_ratio": round(cached / denominator, 4) if denominator else 0.0,
    }


def chat_messages(base_url: str, api_key: str, model: str, messages: list[dict],
                  temperature: float = 0.7, proxy: str = "", retries: int = 2,
                  top_p: float | None = None, max_tokens: int | None = None,
                  provider_profile: str = "",
                  timeout_override: float | None = None,
                  on_usage: Callable[[dict], None] | None = None,
                  on_retry: Callable[[int, Exception], None] | None = None) -> str:
    """多消息单轮对话：messages=[{"role":"system|user|assistant","content":..}]，保留各条 role
    发给模型（不折叠成单 system 串），返回展平后的回复文本。空/无 content 的条目跳过。
    上游临时故障退避重试；调用失败抛 RuntimeError。`chat` 是它 system+user 两条的特例。
    on_usage: 可选回调，成功后收到解析后的 usage dict（prompt/completion/cached/total/cache_hit_ratio）。"""
    import time
    max_tokens = cap_max_tokens(model, max_tokens)  # 模型输出上限收敛（min 语义，见 cap_max_tokens）
    payload = _payload(model, messages, provider_profile=provider_profile)
    llm = build_model(base_url, api_key, model, temperature=temperature, proxy=proxy,
                      top_p=top_p, max_tokens=max_tokens,
                      timeout_override=timeout_override)
    last: Exception | None = None
    for i in range(max(1, retries)):
        try:
            resp = llm.invoke(payload)
            if callable(on_usage):
                try:
                    # LangChain: AIMessage 有 usage_metadata
                    usage_raw = getattr(resp, "usage_metadata", None) or {}
                    stats = _collect_usage(usage_raw)
                    if stats:
                        on_usage(stats)
                except Exception:
                    pass
            return flatten_content(resp.content).strip()
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries - 1 and _is_transient(e):
                if callable(on_retry):
                    try:
                        on_retry(i + 1, e)
                    except Exception:
                        pass
                time.sleep(2 ** i)   # 1s、2s、4s 退避
                continue
            break
    raise RuntimeError(f"调用对话模型失败：{last}")


def _stop_reason(chunk: Any) -> str:
    """从流 chunk 提取结束原因（OpenAI finish_reason / Anthropic stop_reason）。

    中转在正文中间掐断流时连接直接结束、不携带结束原因 → 返回空串。这是「模型自己
    结束但结构不完整（格式问题）」与「提供商中途掐断（连接/上限问题）」的关键区分证据。
    """
    meta = getattr(chunk, "response_metadata", None)
    if isinstance(meta, dict):
        for key in ("finish_reason", "stop_reason"):
            value = meta.get(key)
            if value:
                return str(value)
    return ""


class _OnDeltaAbort(RuntimeError):
    """透传 on_delta 回调异常（用户停止/自愈超时等调用方控制流）。

    2026-09-04：on_delta 是调用方代码——流式层在其中检查 cancel_event 抛
    RoleplayInterrupted（正文停止），若被本模块的 except Exception 兜底包成
    RuntimeError，普通对话节点会把用户停止显示成「回答失败」。回调异常必须
    原样穿透：不进 retry、不换包装，由调用方（execute_turn 等）按语义还原。
    """


# 2026-09-12（P0）：流式 usage 观测开关。
# 上游若**明确拒绝** `stream_options`，进程内自动降级为不发该字段——中转实现参差，
# 不能假设必然支持，更不能因为要观测就允许整条链路 400。
# 运维阀门：环境变量 `DEMIURGE_STREAM_USAGE=0` 可永久关闭（重启即生效，无需改代码）。
_stream_usage_disabled = False


def _stream_usage_enabled() -> bool:
    """是否给流式请求带 `stream_options.include_usage`（被上游拒绝过或运维关闭则为 False）。"""
    if _stream_usage_disabled:
        return False
    import os
    flag = (os.environ.get("DEMIURGE_STREAM_USAGE") or "").strip().lower()
    return flag not in ("0", "false", "off", "no")


def _looks_like_stream_options_rejected(err: Exception) -> bool:
    """上游是否**明确拒绝**了 stream_options/include_usage（而非别的 400）。

    只看「提到该字段 + 提到不支持/非法」两个条件同时成立，避免把内容审核 400、
    max_tokens 超限 400 也误判成「这家中转不支持观测」而白白关掉开关。
    """
    text = str(err).lower()
    if "stream_options" not in text and "include_usage" not in text:
        return False
    return any(tag in text for tag in (
        "unsupported", "not support", "unknown", "unexpected", "invalid",
        "extra_forbidden", "extra fields", "400"))


def chat_messages_stream(base_url: str, api_key: str, model: str, messages: list[dict],
                         on_delta: Callable[[str], None], temperature: float = 0.7,
                         proxy: str = "", retries: int = 2,
                         top_p: float | None = None, max_tokens: int | None = None,
                         provider_profile: str = "",
                         timeout_override: float | None = None,
                         on_usage: Callable[[dict], None] | None = None,
                         on_finish: Callable[[dict], None] | None = None) -> str:
    """流式调用多消息对话，并把每个正文增量交给调用方；同时返回完整原文供后处理。

    仅在本次尝试尚未产生任何增量时重试，避免连接中断后把已显示的半段正文重复输出。
    timeout_override: 显式覆盖流式默认读超时（30s）——2026-09-04 非流式可取消收口：
      关闭流式输出的场景也走本通道做本地聚合（外部语义仍是整段返回），首 token 慢的
      模型需要沿用原非流式的宽限（120s 代理 / 200s 公网），否则被 30s 误掐。
    on_usage: 可选回调，结束后收到解析后的 usage dict（含缓存命中分算，见 _collect_usage）。
    on_finish: 可选回调，成功结束后收到 {"finish_reason": …}；流被中途掐断时为空串。"""
    import time
    max_tokens = cap_max_tokens(model, max_tokens)  # 模型输出上限收敛（min 语义，见 cap_max_tokens）
    payload = _payload(model, messages, provider_profile=provider_profile)

    def _build():
        # stream_usage：不显式要 usage，OpenAI 兼容网关在流式末块不回该字段（观测恒为 0）。
        return build_model(
            base_url, api_key, model, temperature=temperature, streaming=True, proxy=proxy,
            top_p=top_p, max_tokens=max_tokens, sdk_retries=0,
            timeout_override=timeout_override, stream_usage=_stream_usage_enabled(),
        )

    llm = _build()
    last: Exception | None = None
    attempt = 0
    while attempt < max(1, retries):
        attempt += 1
        sent_usage_opt = _stream_usage_enabled()
        parts: list[str] = []
        try:
            # 流式模式：LangChain stream 返回 Iterator[AIMessageChunk]，usage 在收尾块里。
            last_chunk_usage = None
            stop_reason = ""
            for chunk in llm.stream(payload):
                # 结束原因与 usage 都必须**先于**空 delta 判定采集：OpenAI 兼容网关把
                # finish_reason 与 usage 都挂在 content 为空的收尾块上，先 continue 会漏采
                #（2026-09-12 P0 修：此前 usage 采集写在 continue 之后，末块 usage 必丢）。
                stop_reason = _stop_reason(chunk) or stop_reason
                try:
                    chunk_usage = getattr(chunk, "usage_metadata", None)
                except Exception:  # noqa: BLE001 - usage 只是观测，取不到不影响正文
                    chunk_usage = None
                if chunk_usage:
                    last_chunk_usage = chunk_usage
                delta = flatten_content(chunk.content)
                if not delta:
                    continue
                parts.append(delta)
                try:
                    on_delta(delta)
                except BaseException as exc:  # noqa: BLE001 - 回调异常原样穿透
                    raise _OnDeltaAbort from exc
            if callable(on_usage) and last_chunk_usage is not None:
                try:
                    stats = _collect_usage(last_chunk_usage)
                    if stats:
                        on_usage(stats)
                except Exception:
                    pass
            if callable(on_finish):
                try:
                    on_finish({"finish_reason": stop_reason})
                except Exception:
                    pass
            return "".join(parts).strip()
        except _OnDeltaAbort as exc:
            # 回调异常（用户停止等调用方控制流）：原样上抛，不包 RuntimeError
            #（2026-09-04：包了会让普通对话节点把停止显示成「回答失败」）。
            raise exc.__cause__ from None
        except Exception as e:  # noqa: BLE001
            last = e
            if sent_usage_opt and _looks_like_stream_options_rejected(e):
                # 上游明确不要 stream_options：关掉开关、不带该字段重建模型再试一次。
                # 这次降级**不消耗重试预算**（attempt 回退），否则 retries=1 时整条流直接废。
                _disable_stream_usage()
                llm = _build()
                attempt -= 1
                continue
            if parts or attempt >= max(1, retries) or not _is_transient(e):
                break
            time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"调用对话模型失败：{last}")


def _disable_stream_usage() -> None:
    """进程内关闭流式 usage 观测（上游拒绝过该字段）。"""
    global _stream_usage_disabled
    _stream_usage_disabled = True


def chat(base_url: str, api_key: str, model: str, system: str, user: str,
         temperature: float = 0.7, proxy: str = "", retries: int = 2,
         top_p: float | None = None, max_tokens: int | None = None,
         timeout: float | None = None,
         on_retry: Callable[[int, Exception], None] | None = None,
         on_usage: Callable[[dict], None] | None = None) -> str:
    """非流式单轮对话（system+user 两条），返回展平后的回复文本。多角色片段用 chat_messages。

    on_usage: 可选回调，成功后收到解析后的 usage dict（2026-09-12 P0 补——此前只有
        chat_messages / chat_messages_stream 有，fabric 自由循环的每条决策走的就是本函数，
    结果整个循环的成本与命中率都观测不到）。
    """
    return chat_messages(
        base_url, api_key, model,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature, proxy=proxy, retries=retries,
        top_p=top_p, max_tokens=max_tokens, timeout_override=timeout,
        on_retry=on_retry, on_usage=on_usage,
    )
