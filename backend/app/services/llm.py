"""对话模型调用的深模块：建模型（含 /v1 规则）、多模态内容展平、单轮调用。

此前散落各处的三件事收拢于此：
- normalize_base_url：OpenAI 兼容接口的 /v1 后缀规则（原 _build_chat_model / rag_store._norm_url / image_agent._build 各一份）。
- flatten_content：把 LLM 返回的 content（可能是 list 分段）展平成纯文本（原重复 6 处）。
- build_model / chat：构建 init_chat_model 并单轮调用取文本。

不含 HTTP 语义（不抛 HTTPException）——路由层按需把 ValueError 包成 4xx/5xx。
"""
import logging
import os
import re
import time
from collections.abc import Callable
from typing import Any, cast

_LOG = logging.getLogger("uvicorn.error")


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
                http_client: Any = None, stream_usage: bool = False):
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
    http_client：可中断取消（2026-10-21）。调用方持有该 client，取消时 `close()`
    强制断开底层 TCP：这是唯一一条不依赖「下一个 token」的中断路径（中转缓冲式
    下发时首字可能 40~80s 才到，on_delta 里的取消检查点在那段时间形同虚设）。
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
    # 流式读超时（「多久一个字节都没收到就判死」）。
    # 2026-08-31 定 30s，依据是「正常吐字 1 秒都不到，30s 无 token 必然上游卡死」。
    # ⚠ 2026-10-20 实测该假设已失效：中转改为**缓冲式下发**，同一轮内三次调用
    #   首字 43s、后续空档 62s / 68s / 80s —— 30s 会把**正在正常生成**的请求判死。
    #   代价实锤：3 笔 × $2.7735，每笔 output 都已产出 4,000 tokens，
    #   我们却在 30s 处断开（网关侧显示「客户端断开」），用户钱花了、字没拿到。
    # ⇒ 放宽到 120s（覆盖实测最大空档 80s）。语义不变：**只要还在出 token 就永不触发**。
    #   LAF_STREAM_READ_TIMEOUT 可覆盖（秒），便于换中转后重新标定。
    _read_timeout = timeout_override if timeout_override is not None else (
        _stream_read_timeout() if streaming else (120 if p else 200))
    if http_client is not None:
        # 可中断取消（2026-10-21）：调用方持有 client，停止时 close() 强制断开上游连接。
        kw["http_client"] = http_client
    elif p:
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
    return init_chat_model(model, **cast("dict[str, Any]", kw))  # kw 键集即 chat 构造参数；stub 重载解析不了 dict[str, object]


def _make_http_client(base_url: str, proxy: str, timeout: float | None = None):
    """创建**调用方可关闭**的 httpx.Client（代理/本机规则与 build_model 一致）。

    2026-10-21 定位：取消此前只挂在 on_delta 回调里，而中转缓冲式下发时首字可能
    40~80s 才到——用户点停止后后端仍阻塞在 `next()`，thread 准入不释放（前端 60s
    后提示「后端仍在收尾生成任务」），上游继续生成/计费。给每次流式尝试注入专属
    client 后，停止信号置位时可以 close() 强制断开 TCP，不再等下一个 token。
    """
    import httpx
    p = (proxy or "").strip()
    if p:
        return httpx.Client(proxy=p, timeout=timeout)
    if _is_local_url(base_url):
        return httpx.Client(trust_env=False, timeout=timeout)
    # 公网：保留 trust_env=True（系统代理通路），与 build_model 的默认语义一致。
    return httpx.Client(timeout=timeout)


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


class StreamInterrupted(RuntimeError):
    """流式调用中途失败：携带已收到的**原始增量**（已付费 token 不得作废）。

    2026-10-20 真机（turn f77e68b1）：中转缓冲式下发，思考出了 1304 字后 ReadTimeout，
    旧实现把局部 `parts` 一丢、只上抛一句 RuntimeError ⇒ 上层自愈循环拿不到任何素材，
    只能整段重掷（thinking 745→972→1184→1304 反复重写、正文 0 字）。
    `partial` 是拼接续写的唯一素材（roleplay_turn 的思考断点续写/正文断点续写都吃它）。

    继承 RuntimeError：既有 `except RuntimeError` 捕获点与 4xx 判死逻辑全部不受影响。
    """

    def __init__(self, message: str, partial: str = "") -> None:
        super().__init__(message)
        self.partial = partial or ""


def _is_transient(err: Exception) -> bool:
    """判断是否上游临时故障（值得重试）：502/503/504、timeout、connection、upstream 等。
    中转对大请求/长耗时请求常临时 502(upstream_error)，短对话不触发——退避重试多能自愈。"""
    s = str(err).lower()
    return any(t in s for t in (
        "502", "503", "504", "upstream", "timeout", "timed out",
        "temporarily", "overload", "rate limit", "429", "connection error"))


def _attempt_note(index: int | str, err: Exception, seconds: float, *, transient: bool) -> str:
    """单次尝试的诊断摘要（2026-10-20 观测补口，**只描述、不改任何控制流**）。

    为什么需要：非流式链（`chat_messages` / `chat`）的重试次数**此前完全不可观测**——
    `build_model` 未传 `sdk_retries` ⇒ LangChain/OpenAI SDK 默认 `max_retries=2`，
    会在**我们这一层之下**自己再发 2 次。于是网关日志看到 N 发请求时，
    根本分不清哪些是我们的、哪些是 SDK 的（真机：23:07 表格整理 3 发、
    插画判据一次调用 432.7s，都栽在这上面）。

    判据：**单次尝试的耗时 vs 该次配置的读超时**——若一次 `invoke` 耗时约为超时的
    2～3 倍，多出来的就是 SDK 的重试；配合「本层尝试 N 次」即可反推总发数。

    ⚠ 摘要文本刻意不新增 "timeout" / "upstream" / "5xx" 这类字面：整串会随异常
    再次被 `_is_transient` / `is_client_rejected` 读取，不能污染上游判定
    （原始错误文本里本来就有的那部分不算新增，与现状一致）。
    """
    head = str(err).strip().replace("\n", " ")[:60]
    return f"#{index} {type(err).__name__}/{seconds:.1f}s/{'瞬时' if transient else '非瞬时'}:{head}"


def _is_proxy_side_error(err: Exception) -> bool:
    """判断失败是否「可能是代理本身不可用」——只有这类才值得去掉代理重试一次。

    刻意收窄：只认连接层错误（`connection error` / `connect` / `proxy` / `timed out`）。
    4xx（参数/鉴权/模型名）与内容问题**不在此列**——那些去掉代理也一样错，
    重试只会白烧一次调用。
    """
    s = str(err).lower()
    if any(t in s for t in ("400", "401", "403", "404", "422", "invalid", "unauthorized")):
        return False
    return any(t in s for t in (
        "connection error", "connect", "proxy", "timed out", "timeout",
        "getaddrinfo", "name or service not known", "connection refused",
        "ssl", "eof occurred"))


# ── 模型单轮输出上限（OpenAI 兼容网关按模型 max output 校验 max_tokens）──
# 2026-09-04 trace 实锤：GrayWill 预设 openai_max_tokens=600000 → 正文 +4000=604000 直发，
# tokenrhythm 网关对 deepseek-v4-flash-0731 报 400 LITELLM_ERROR「max_tokens should be
# less or equal to 393216」→ 扮演失败且自愈整段重掷 3 次全白烧（opus@中转网关 网关无此校验
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


def _stream_read_timeout() -> float:
    """流式读超时（秒）：多久收不到**任何**字节才算上游卡死。

    2026-10-20 从 30s 放宽到 120s。证据：中转缓冲式下发，实测同一轮三次调用
    首字 43s、段间空档 62s/68s/80s；30s 会在模型**正常生成**时断开
    （网关侧记「客户端断开」，3 笔 × $2.7735，每笔已产出 4,000 output tokens）。
    语义不变：**只要还在出 token 就永远不会触发**，这个值只决定「静默多久算死」。

    `LAF_STREAM_READ_TIMEOUT` 覆盖（秒，≤0 / 非法则回默认），换中转后便于重新标定。
    """
    raw = os.environ.get("LAF_STREAM_READ_TIMEOUT", "").strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 120.0


def _prompt_cache_enabled() -> bool:
    """显式 prompt cache 开关（默认开；LAF_PROMPT_CACHE=0 关）。

    2026-10-19 用户实锤：8 笔请求缓存创建 291,657 tokens、读取仅 61,438（4.75 倍），
    按 4× 服务系数白付约 $6.7。根因是 Anthropic 默认 **5 分钟 TTL**扛不住剧情对话间隔
    （相邻两轮常 >5 分钟），每轮全额重建。这里显式带 `ttl:"1h"` 把缓存寿命拉到 1 小时。

    ⚠ 2026-10-20 A/B（本机 → 中转网关 网关 / claude-opus-4-8，三臂串行实测）：
      none 200 / ephemeral(5m) 200 / ephemeral+ttl=1h 200 —— **`ttl:"1h"` 不被拒**；
      但三臂 usage 里 `cache_creation_input_tokens` / `cache_read_input_tokens` **全为 null**
      ⇒ 该网关在 OpenAI 兼容路径上不回缓存统计，**命中率不可观测**。
      同批还有两臂在 SSL 握手阶段 ConnectTimeout（>90s），说明这条链路本身极不稳
      （与生产 trace 的「首包 43s、段间空档 60~80s」同源）。
      ⇒ 结论：**保留** 1h 断点（不报错，且 5 分钟 TTL 撑不住剧情轮间隔）；
      但**不得**再用 10-19 的「291,657 创建 / 61,438 读取」推断当前命中率——那两个数
      采自网关还回字段的时期，现在要核对得走网关账单或 Anthropic 原生 `/v1/messages`。
    """
    return os.environ.get("LAF_PROMPT_CACHE", "1").strip().lower() not in {"0", "false", "off"}


# 每轮重排的动态块**专属签名**（不能用宽泛的「本轮」「每轮」，也不能用裸的
# `<user last input>`）。
#
# ⚠ 2026-10-19 两次实测教训，都吃过误判：
#   ① 宽泛词 ("本轮","每轮") → 边界被判到 msg6：system 头的【状态更新规则】里写着
#      「本轮剧情导致的状态变化」、静态世界书块里也有「本轮」这种普通行文。
#   ② 裸标签 "user last input" → 边界被判到 msg26：预设的 COT 指南里写着
#      「在这里分析<user last input>中主人输入内容」，那是**静态指令**不是动态块。
# 结论：只认这三条**带上下文的专属短语**。
_PERTURN_MARKERS = (
    "本轮召回（每轮重排）",   # system 头之后的 worldbook 召回块标题
    "本轮用户安排",            # 尾部 system「用户安排·最高优先级」标题
    "以下为用户最新输入",      # 本轮输入的包装前缀（裸标签不够，必须带这句）
)


def _content_text(msg: dict) -> str:
    """把消息内容展平成纯文本（兼容 str 与块数组）。"""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(blk.get("text") or "") for blk in content if isinstance(blk, dict)
        )
    return ""


def _attach_breakpoint(msg: dict, cc: dict) -> dict:
    """给一条消息挂 cache_control（内容为 str 则包成块数组；已是块数组则补在末个 text 上）。"""
    content = msg.get("content")
    if isinstance(content, list):
        blocks = [dict(blk) if isinstance(blk, dict) else blk for blk in content]
        for blk in reversed(blocks):
            if isinstance(blk, dict) and blk.get("type") == "text":
                blk["cache_control"] = cc
                break
        return {**msg, "content": blocks}
    return {**msg, "content": [{"type": "text", "text": content or "", "cache_control": cc}]}


def _stable_prefix_tail(messages: list[dict]) -> int:
    """返回「稳定前缀」的末尾下标（＝最早的每轮块之前那条消息）；无可靠边界返回 -1。

    每轮边界有两类，取**最早**的一类：
      ① `_PERTURN_MARKERS` 里的每轮块（世界书召回 / 用户安排 / 本轮输入包装）；
      ② **`<history>` 开标签行**（上一轮剧情轮的起点）。

    ⚠ 2026-10-21 修正（用户实锤缓存命中率只有 8.3%，账单 10,752/129,455）：
    10-19 的结论「首异在 msg30、稳定前缀 75.5%」是用**同一回合的两次重试**量的
    ～重试历史完全一样，首异自然落在尾部动态块；**跨回合**时真正的首异在
    `<history>`（上一轮用户输入+回复），而预设把 Order/COT 等静态块排在它之后
    ⇒ 旧逻辑把断点落在 msg30（历史之后），该缓存块**每轮都变、永远读不中**，
    还每轮付 1.25 倍创建费，只剩 system 头那 2,883 字能命中。
    现按「最早边界」判定：今天的真实布局落在 msg16（静态头+预设静态块 32,728 字）。

    ⚠ 触发条件**不限 role**：本轮输入的包装是 `user` 消息（「以下为用户最新输入」），
    它本身每轮就变，必须能触发边界；触发后回退到**该条之前的最后一条 system**，
    那才是静态区间的末尾（roleplay 实测落在预设 `</Order>` 那条）。
    ② 只认「整行以 `<history>` 结尾」的块（预设把它渲染成独立的开标签行）——
    与 10-19「裸标签 `user last input` 被 COT 指南骗到」的教训同源：
    静态指令里**提到** `<history>` 不算边界。
    """
    for index, msg in enumerate(messages):
        if index == 0:
            continue
        text = _content_text(msg)
        is_history_open = text.rstrip().endswith("<history>")
        if not is_history_open and not any(mark in text for mark in _PERTURN_MARKERS):
            continue
        for back in range(index - 1, 0, -1):
            if messages[back].get("role") == "system":
                return back
        return -1
    return -1


def _inject_prompt_cache(messages: list[dict], *, is_claude: bool) -> list[dict]:
    """给**跨轮逐字节稳定**的前缀挂 Anthropic `cache_control`（ttl=1h），最多两个断点。

    断点位置（Anthropic 上限 4 个，这里用 2 个）：
      ① 第一条 system（system 头）——任何情况下都稳定；
      ② `_stable_prefix_tail()` 定位到的稳定区末尾（roleplay 实测 msg30）——
         把预设静态块与既有对话一并纳入缓存，命中面从 2,883 字扩到 ~36,942 字。

    ⚠ 刻意**不**往每轮重排的块上挂（【本轮召回】【本轮用户安排】）：挂了只会白付
    1.25 倍创建费且永远读不中。找不到可靠边界时只留断点①，绝不猜。

    内容已是块数组时只补 cache_control，不重复包 text；非 Claude 模型原样返回。
    """
    if not is_claude or not _prompt_cache_enabled():
        return messages
    cc = {"type": "ephemeral", "ttl": "1h"}
    tail = _stable_prefix_tail(messages)
    out: list[dict] = []
    head_done = False
    for index, msg in enumerate(messages):
        if not head_done and msg.get("role") == "system":
            out.append(_attach_breakpoint(msg, cc))
            head_done = True
            continue
        if index == tail and tail > 0:
            out.append(_attach_breakpoint(msg, cc))
            continue
        out.append(msg)
    return out


def _payload(model: str, messages: list[dict], *, provider_profile: str = "") -> list[tuple[str, str]]:
    prepared = prepare_messages(model, messages, provider_profile=provider_profile)
    # is_claude 判定必须与 prepare_messages **逐字同构**：显式 profile 优先，
    # 仅在没有 profile 时才按模型名推断。否则 openai_compatible + Claude 模型名
    # 会被误判成 Claude 档，给不该加缓存的请求也挂上 cache_control。
    profile = (provider_profile or "").strip().lower()
    is_claude = profile == "claude_compatible" if profile else (
        "claude" in (model or "").casefold()
    )
    prepared = _inject_prompt_cache(prepared, is_claude=is_claude)
    return [
        (_ROLE_MAP.get(message["role"], "human"), message["content"])
        for message in prepared
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
    max_tokens = cap_max_tokens(model, max_tokens)  # 模型输出上限收敛（min 语义，见 cap_max_tokens）
    payload = _payload(model, messages, provider_profile=provider_profile)
    llm = build_model(base_url, api_key, model, temperature=temperature, proxy=proxy,
                      top_p=top_p, max_tokens=max_tokens,
                      timeout_override=timeout_override)
    last: Exception | None = None
    attempts: list[str] = []
    for i in range(max(1, retries)):
        started = time.monotonic()
        try:
            resp = llm.invoke(payload)
            if callable(on_usage):
                try:
                    # LangChain: AIMessage 有 usage_metadata
                    usage_raw = getattr(resp, "usage_metadata", None) or {}
                    stats = _collect_usage(usage_raw)
                    if stats:
                        on_usage(stats)
                except Exception as exc:  # noqa: BLE001 [EXC-留痕] usage 采集失败不阻断回答，但本轮用量不计数
                    _LOG.warning("usage 采集失败（回答照常返回，但本轮 token 用量统计缺失）: %s", exc)
            return flatten_content(resp.content).strip()
        except Exception as e:  # noqa: BLE001
            last = e
            transient = _is_transient(e)
            attempts.append(
                _attempt_note(i + 1, e, time.monotonic() - started, transient=transient))
            if i < retries - 1 and transient:
                if callable(on_retry):
                    try:
                        on_retry(i + 1, e)
                    except Exception as exc:  # noqa: BLE001 [EXC-留痕] on_retry 回调失败不阻断重试，但该次重试未上报
                        _LOG.warning("on_retry 回调失败（退避重试继续，但该次重试未上报）: %s", exc)
                time.sleep(2 ** i)   # 1s、2s、4s 退避
                continue
            break
    # 2026-10-20 用户实锤（「表格调用的模型不应该按照设置的机制来吗，既然能对话，表格理应能生成」）：
    # 仓库对话链**从不把代理发给后端**（`useChatSession` 里 `chatProxyUrl` 只定义不使用）
    # ⇒ 走的是 `build_model` 的「proxy 为空 ⇒ 完全默认构造」那条**一直能连通**的路径；
    # 而手动填表/纪要索引显式带了 `proxy` ⇒ 注入代理 http_client ⇒ 代理一旦不可用
    # 就整段 `Connection error`，于是出现「能聊、不能填表」。
    # 兜底：**仅在**「本来带了代理」且「重试用尽仍失败」时，去掉代理再试一次——
    # 退回与仓库对话完全相同的构造。代理本身好用的人不会走到这里（第一轮就成功了）。
    attempted_plain = False
    plain_note = ""
    if proxy and isinstance(last, Exception) and _is_proxy_side_error(last):
        attempted_plain = True
        _LOG.warning("代理调用失败（%s），回退直连再试一次：%s", proxy, last)
        plain_started = time.monotonic()
        try:
            plain = build_model(base_url, api_key, model, temperature=temperature,
                                proxy="", top_p=top_p, max_tokens=max_tokens,
                                timeout_override=timeout_override)
            resp = plain.invoke(payload)
            return flatten_content(resp.content).strip()
        except Exception as e:  # noqa: BLE001
            last = e
            plain_note = "；" + _attempt_note(
                "直连", e, time.monotonic() - plain_started, transient=_is_transient(e))
    # 2026-10-20：把「有没有回退过」写进错误信息。否则用户看到
    # 「Connection error｜代理 127.0.0.1:7897」无法判断是「还在用代理」还是
    # 「已经直连也连不上」——这两种的处理方式完全相反。
    hint = "（已尝试去代理直连，仍失败 ⇒ 该接口必须走代理，问题在代理本身）" if attempted_plain else ""
    # 2026-10-20 观测补口：把「本层到底试了几次、每次多久」写进错误信息——
    # 非流式链的重试此前完全不可见（SDK 层还会偷偷再试 2 次），网关看到 N 发请求时
    # 无法归因。这里只加描述、不改控制流，重试与否仍由 `_is_transient` 决定。
    digest = "（本层尝试 {} 次：{}{}）".format(len(attempts), " ｜ ".join(attempts), plain_note)
    _LOG.warning("chat_messages 失败汇总：%s", digest)
    raise RuntimeError(f"调用对话模型失败：{last}{hint}{digest}")


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


class StreamAborted(RuntimeError):
    """调用方要求中止本次流（用户点停止）：中断≠失败。

    2026-10-21 定位：取消检查此前只挂在 on_delta 回调里，而中转缓冲式下发时
    首字可能 40~80s 才到——用户点停止后后端继续阻塞在 `next()`，thread 准入
    不释放（前端 60s 后提示「后端仍在收尾生成任务」），上游继续生成、按完整
    用量计费（网关侧记「客户端断开」）。
    本异常只表示「我方主动放弃本次流」，不携带已收增量；调用方按中断语义处理。
    """


def _close_quietly(client: Any) -> None:
    """关闭专属 http 客户端（取消/收尾共用）；失败不掩盖真实中断或异常。"""
    if client is None:
        return
    try:
        client.close()
    except Exception:  # noqa: BLE001 - 释放在途连接失败无需改变控制流
        pass


def _iter_stream_chunks(llm: Any, payload: Any, should_abort: Callable[[], bool] | None,
                        http_client: Any, *, poll_interval: float = 0.2):
    """迭代上游流式增量，且**在无增量期间**也能响应取消（2026-10-21）。

    后台线程执行 `llm.stream(payload)`（读 socket 阻塞），主线程用 queue 轮询：
    should_abort() 为真 ⇒ 抛 StreamAborted，finally 关闭专属 client（强制断开
    TCP，上游立刻停止生成）。正常结束/上游异常时同样关闭（本次尝试用完即弃）。
    """
    import queue
    import threading

    done = object()
    q: "queue.Queue" = queue.Queue(maxsize=64)

    def pump() -> None:
        try:
            for chunk in llm.stream(payload):
                try:
                    q.put(chunk, timeout=0.5)
                except queue.Full:  # 消费端已放弃（取消/回调异常）：后台线程安静退出
                    return
            try:
                q.put(done, timeout=1.0)  # 消费端已放弃时不留阻塞线程
            except queue.Full:
                return
        except BaseException as exc:  # noqa: BLE001 - 原样交给消费端判定重试/中断
            try:
                q.put(("__error__", exc), timeout=0.5)
            except queue.Full:
                return

    threading.Thread(target=pump, daemon=True, name="llm-stream-pump").start()
    try:
        while True:
            try:
                item = q.get(timeout=poll_interval)
            except queue.Empty:
                if should_abort is not None and should_abort():
                    raise StreamAborted("用户已停止生成")
                continue
            if item is done:
                return
            if isinstance(item, tuple) and len(item) == 2 and item[0] == "__error__":
                raise item[1]
            yield item
    finally:
        _close_quietly(http_client)


def chat_messages_stream(base_url: str, api_key: str, model: str, messages: list[dict],
                         on_delta: Callable[[str], None], temperature: float = 0.7,
                         proxy: str = "", retries: int = 2,
                         top_p: float | None = None, max_tokens: int | None = None,
                         provider_profile: str = "",
                         timeout_override: float | None = None,
                         on_usage: Callable[[dict], None] | None = None,
                         on_finish: Callable[[dict], None] | None = None,
                         should_abort: Callable[[], bool] | None = None) -> str:
    """流式调用多消息对话，并把每个正文增量交给调用方；同时返回完整原文供后处理。

    仅在本次尝试尚未产生任何增量时重试，避免连接中断后把已显示的半段正文重复输出。
    timeout_override: 显式覆盖流式默认读超时（30s）——2026-09-04 非流式可取消收口：
      关闭流式输出的场景也走本通道做本地聚合（外部语义仍是整段返回），首 token 慢的
      模型需要沿用原非流式的宽限（120s 代理 / 200s 公网），否则被 30s 误掐。
    on_usage: 可选回调，结束后收到解析后的 usage dict（含缓存命中分算，见 _collect_usage）。
    on_finish: 可选回调，成功结束后收到 {"finish_reason": …}；流被中途掐断时为空串。
    should_abort: 可选取消探针（用户点停止）。**每 0.2s 在无增量时也会查**——
      为真即关闭底层连接并抛 StreamAborted，不再依赖「等到下一个 token」（2026-10-21）。
    """
    max_tokens = cap_max_tokens(model, max_tokens)  # 模型输出上限收敛（min 语义，见 cap_max_tokens）
    payload = _payload(model, messages, provider_profile=provider_profile)
    _read_timeout = timeout_override if timeout_override is not None else _stream_read_timeout()

    def _build():
        # stream_usage：不显式要 usage，OpenAI 兼容网关在流式末块不回该字段（观测恒为 0）。
        client = _make_http_client(base_url, proxy, _read_timeout) if should_abort is not None else None
        try:
            llm = build_model(
                base_url, api_key, model, temperature=temperature, streaming=True, proxy=proxy,
                top_p=top_p, max_tokens=max_tokens, sdk_retries=0,
                timeout_override=timeout_override, stream_usage=_stream_usage_enabled(),
                http_client=client,
            )
        except Exception:  # noqa: BLE001 - 建模型失败（配置缺失等）也要释放刚建的 client
            _close_quietly(client)
            raise
        return llm, client

    last: Exception | None = None
    # 已收到的原始增量。断流时随异常带走（StreamInterrupted.partial），
    # 让上层能「从断点续写」而不是整段重掷（2026-10-20 定案，见 StreamInterrupted）。
    last_partial = ""
    attempt = 0
    while attempt < max(1, retries):
        attempt += 1
        sent_usage_opt = _stream_usage_enabled()
        parts: list[str] = []
        llm, client = _build()
        try:
            # 流式模式：LangChain stream 返回 Iterator[AIMessageChunk]，usage 在收尾块里。
            last_chunk_usage = None
            stop_reason = ""
            for chunk in _iter_stream_chunks(llm, payload, should_abort, client):
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
                except Exception as exc:  # noqa: BLE001 [EXC-留痕] usage 采集失败不阻断回答，但本轮用量不计数
                    _LOG.warning("流式 usage 采集失败（回答照常返回，但本轮 token 用量统计缺失）: %s", exc)
            if callable(on_finish):
                try:
                    on_finish({"finish_reason": stop_reason})
                except Exception as exc:  # noqa: BLE001 [EXC-留痕] on_finish 回调失败不阻断回答，但收尾事件未上报
                    _LOG.warning("on_finish 回调失败（回答照常返回，但收尾事件未上报）: %s", exc)
            return "".join(parts).strip()
        except _OnDeltaAbort as exc:
            # 回调异常（用户停止等调用方控制流）：原样上抛，不包 RuntimeError
            #（2026-09-04：包了会让普通对话节点把停止显示成「回答失败」）。
            cause = exc.__cause__
            assert cause is not None  # _OnDeltaAbort 恒带 cause；若缺失，AssertionError 与原 TypeError 同为立即失败
            raise cause from None
        except StreamAborted:
            # 用户停止：中断不是失败，不重试（重试=再花一次钱），不包装成错误。
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            # 断流现场留档：本次尝试已吐出的增量。重试会从零再来，这里只保**最后一次**——
            # 拼接续写要的是「最接近断点的那份残缺输出」。
            last_partial = "".join(parts)
            if sent_usage_opt and _looks_like_stream_options_rejected(e):
                # 上游明确不要 stream_options：关掉开关、不带该字段重建模型再试一次。
                # 这次降级**不消耗重试预算**（attempt 回退），否则 retries=1 时整条流直接废。
                _disable_stream_usage()
                attempt -= 1
                continue
            if parts or attempt >= max(1, retries) or not _is_transient(e):
                break
            time.sleep(2 ** (attempt - 1))
    raise StreamInterrupted(f"调用对话模型失败：{last}", last_partial)


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
