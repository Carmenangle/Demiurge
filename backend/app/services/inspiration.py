"""灵感搜索的深模块：联网搜 → 对话模型提炼成「标题 + 内容」知识卡 → 出 {title, content, sources}。

灵感卡的正确形态是「标题 + 内容」的成段中文总结，而非英文生图标签：
- 插入对话时作为文本发送（同剧情预设），AI 拿到丰富的成段中文内容，后续生成提示词/续写时理解更充分。
- sources 保留来源链接作溯源，供 M1.4 资产库化与 M2.1 derived_from 派生元数据追溯。

此前 /inspiration 路由与 image_agent 的 search_inspiration 工具各写一遍同样的
「DDG 搜索 + 提炼 system + re.split 切标签」，本模块收成一处，两个调用方各自适配
（路由 → JSON；工具 → 灵感卡 + 快照）。持久化不在这里（见 generation_store）。
"""
import re

from app.services import llm as _llm
from app.services import web_search as ws

_SYSTEM = (
    "你是联网资料整理助手。用户想了解某个主题（如服装款式、发型、画风、角色设定、世界观、剧情桥段等，主题不限）。"
    "下面给你若干联网搜索到的网页标题与摘要。请据此整理成一条结构化总结：\n"
    "1. 第一行只输出一个凝练的短标题（不超过 12 个字，直接概括主题，不要书名号、不要引号、不要冒号、不要句号）。\n"
    "2. 换行后，用成段中文总结该主题：结构清晰、信息完整，覆盖关键类别、特征、差异、适用场景等，"
    "供后续生成图像提示词或续写故事时参考。\n"
    "3. **材料与主题无关时严禁编造**：若上述标题/摘要全是搜索引擎导航页、词典条目或与主题无关的内容，"
    "第一行输出「未找到相关资料」，正文用一两句说明检索失败并建议用户换更具体的关键词（如只给角色名/作品名）。"
    "不得依据主题名称臆造外貌、设定或背景。\n"
    "格式要求：第一行只放短标题，第二行开始放总结内容；不要输出「标题：」「总结：」这类前缀。"
)


class NoResults(Exception):
    """联网搜索无结果（网络/搜索源不可用）。"""


# ── 查询净化与拆段（2026-09-11，修「找灵感拿不到真实网页」） ────────────
# 事故：用户原话「搜索超时空辉夜姬的月见八千代的外貌信息」被整句塞给 Bing，
# 引擎把引导动词「搜索」当主题词 → 返回搜狗/360/一起搜/必应/Google 五个
# **搜索引擎首页**；剥掉引导词后长句又被引擎放松到首字「超」（词典条目）。
# 两条路都拿不到真实网页，模型只好声明检索失败或凭角色名编造设定。

# 句首引导动词（可叠前缀「请/帮我」、后缀「一下」）。循环剥到剥不动为止。
_LEAD_RE = re.compile(
    r"^(?:请|麻烦|帮我|帮忙|我想|想要|需要)?"
    r"(?:搜索|搜一下|搜下|搜搜|查一下|查下|查查|查找|查一查|找一下|找找|看看|了解|介绍)"
    r"(?:一下|下)?(?:关于|有关)?的?"
)
_TRAIL_RE = re.compile(r"[\s。．，,、；;！!？?]+$")


def clean_query(query: str) -> str:
    """剥掉口语引导动词与句尾标点，留下净查询词（空则回落原句）。"""
    raw = (query or "").strip()
    q = raw
    while True:
        nxt = _LEAD_RE.sub("", q).strip()
        if nxt == q or not nxt:      # 剥空了 → 整句就是引导词，保原句
            break
        q = nxt
    q = _TRAIL_RE.sub("", q).strip()
    return q or raw


def query_segments(query: str) -> list[str]:
    """把「X的Y的Z」拆成实体段（≥2 字、去重、保序、封顶 4 个），供整句搜不到时拆词重试。

    「超时空辉夜姬的月见八千代的外貌信息」→ [超时空辉夜姬, 月见八千代, 外貌信息]。
    中文引擎对长句/复合句（无 cookie SERP）会放松到首字，拆段后单段命中率高得多；
    段结果经 `web_search.filter_relevant` 过滤，垃圾段（如「外貌信息」的词典页）自动出局。
    """
    out: list[str] = []
    for p in re.split(r"[的，,、；;？?！!\s]+", (query or "").strip()):
        p = p.strip()
        if len(p) >= 2 and p not in out:
            out.append(p)
    return out[:4]


def _segmented_search(query: str, proxy: str, provider: str | None,
                      max_results: int) -> list[dict]:
    """整句搜不到时的兜底：逐段搜索并按 URL 去重合并。"""
    segs = query_segments(query)
    if len(segs) < 2:
        return []
    seen: set[str] = set()
    merged: list[dict] = []
    for seg in segs:
        for r in ws.web_search(seg, max_results=4, proxy=proxy, provider=provider):
            url = r.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            merged.append(r)
        if len(merged) >= max_results:
            break
    return merged[:max_results]


# 模型输出不稳定时，标题行常带这些「前缀」或「包装」，清洗掉才能得到真标题。
_TITLE_PREFIX_RE = re.compile(r"^\s*(?:标题|题目|主题|总结|摘要|结论)[:：]\s*")
_MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,6}\s*")
_MARKDOWN_BOLD_RE = re.compile(r"^\s*\*\*(.+?)\*\*\s*$")
_WRAP_PAIRS = (("《", "》"), ("「", "」"), ("『", "』"), ("【", "】"),
               ("“", "”"), ('"', '"'), ("'", "'"))


def clean_title(raw: str) -> str:
    """清洗模型输出的标题行：去 Markdown 记号、去「标题：」类前缀、去成对包裹、去行尾标点。"""
    t = (raw or "").strip()
    if not t:
        return ""
    t = _MARKDOWN_BOLD_RE.sub(r"\1", t)
    t = _MARKDOWN_HEADING_RE.sub("", t)
    t = _TITLE_PREFIX_RE.sub("", t)
    t = t.strip()
    # 先去行尾标点，否则「《女仆装》：」的包裹检查会因末尾冒号失败
    t = t.rstrip("：:。，,、；;").strip()
    if len(t) >= 2 and any(t.startswith(a) and t.endswith(b) for a, b in _WRAP_PAIRS):
        t = t[1:-1].strip()
    return t.rstrip("：:。，,、；;").strip()


def split_title_content(text: str, query: str) -> tuple[str, str]:
    """把模型输出拆成 (title, content)。

    约定模型第一行输出短标题、其余为内容；模型不遵守时回落：
    - 空输出 → (query, "")。
    - 单行 → 标题回落 query（或其本身），内容为该行。
    - 首行过长（>20 字，说明没按「第一行短标题」输出）→ 标题回落 query，内容为全文。
    - 首行带「标题：」「#」「**」「《》」等包装 → 先清洗再判断。
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return (query or "").strip() or "灵感", ""
    if len(lines) == 1:
        return (query or "").strip() or lines[0], lines[0]
    title = clean_title(lines[0])
    # 首行清洗后为空（如「总结：」后面直接换行）或仍过长 → 标题回落 query，内容为全文
    if not title or len(title) > 20:
        return (query or "").strip() or "灵感", "\n".join(lines)
    return title, "\n".join(lines[1:])


def search_and_refine(query: str, base_url: str, api_key: str, model: str,
                      proxy: str = "", chat_proxy: str = "",
                      search_provider: str | None = None,
                      include_images: bool = True) -> dict:
    """返回 {title, content, sources[], images[]}。无搜索结果抛 NoResults；模型错误由 llm 抛。

    images: [{thumb_url, full_url, source_url, title?}] 仅远程 URL、不落盘（M1.2）。
    图片搜索失败/无结果时 images=[] 降级纯文字卡，不抛错。
    search_provider 为搜索源名称（`bing-cn` / `ddg`）；**None = 走 web_search 的自动回落链**
    （bing-cn → ddg，国内直连源在前），因此不配代理也能搜到。

    查询先过 `clean_query`（剥「搜索/查一下」等引导动词——整句喂给引擎会把动词当主题词，
    搜回来一堆搜索引擎导航页）；整句搜不到再 `query_segments` 拆实体段逐段搜。
    """
    query = clean_query(query)
    results = ws.web_search(query, max_results=6, proxy=proxy, provider=search_provider)
    if not results:
        results = _segmented_search(query, proxy=proxy, provider=search_provider, max_results=6)
    if not results:
        raise NoResults("联网搜索无结果（网络或搜索源不可用）")
    corpus = "\n".join(f"- {r['title']}：{r['snippet']}" for r in results if r.get("title"))
    user = f"用户想了解的主题：{query}\n\n联网搜索到的参考：\n{corpus}"
    raw = _llm.chat(
        base_url, api_key, model, _SYSTEM, user,
        temperature=0.5, proxy=chat_proxy,
    ).strip()
    title, content = split_title_content(raw, query)
    sources = [{"title": r["title"], "url": r["url"]} for r in results[:5] if r.get("title")]
    images: list[dict] = []
    if include_images:
        try:
            images = ws.image_search(query, max_results=8, proxy=proxy)
        except Exception:  # noqa: BLE001  图片搜索失败不阻断文字卡
            images = []
    # M1.3 受控下载：搜索到的 full_url 登记为可下载候选（save 时校验命中）
    from app.services import web_material_candidates
    web_material_candidates.register_candidates(images, query=query, provider=search_provider or "")
    return {"title": title, "content": content, "sources": sources, "images": images}
