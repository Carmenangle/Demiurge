"""灵感搜索的深模块：联网搜 → 对话模型提炼成「标题 + 内容」知识卡 → 出 {title, content, sources}。

灵感卡的正确形态是「标题 + 内容」的成段中文总结，而非英文生图标签：
- 插入对话时作为文本发送（同剧情预设），AI 拿到丰富的成段中文内容，后续生成提示词/续写时理解更充分。
- sources 保留来源链接作溯源，供 M1.4 资产库化与 M2.1 derived_from 派生元数据追溯。

此前 /inspiration 路由与 image_agent 的 search_inspiration 工具各写一遍同样的
「DDG 搜索 + 提炼 system + re.split 切标签」，本模块收成一处，两个调用方各自适配
（路由 → JSON；工具 → 灵感卡 + 快照）。持久化不在这里（见 generation_store）。
"""
import json
import re

from app.services import llm as _llm
from app.services import web_search as ws

_SYSTEM = (
    "你是联网资料整理助手。用户想了解某个主题（如服装款式、发型、画风、角色设定、世界观、剧情桥段等，主题不限）。"
    "下面给你若干联网搜索到的网页标题与摘要。请据此整理成一条结构化总结：\n"
    "1. 第一行只输出一个凝练的短标题（不超过 12 个字，直接概括主题，不要书名号、不要引号、不要冒号、不要句号）。\n"
    "2. 换行后，用成段中文总结该主题：结构清晰、信息完整，覆盖关键类别、特征、差异、适用场景等，"
    "供后续生成图像提示词或续写故事时参考。\n"
    "3. 查询常含限定成分（如「作品名 的 角色名 的 外貌/设定」）：**最后的实体才是主题**"
    "（角色名、物品名），作品名、系列名只是防重名的背景。总结必须以实体为中心："
    "优先提炼与实体直接相关的事实（身份、配音/声优、所属空间或组织、设定、能力等），"
    "多个来源一致的事实视为可信；作品层面的信息（导演、上映、剧情梗概）最多一句带过，"
    "不得喧宾夺主。\n"
    "3.5 「【页面正文摘录】」是网页正文原文，信息密度远高于前面的摘要行，"
    "外貌、服装、性格等细节应优先从正文摘录中提炼；摘要行没有的细节不许编造。\n"
    "4. **材料与主题无关时严禁编造**：若上述标题/摘要全是搜索引擎导航页、词典条目或与主题无关的内容，"
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


def _reorder_by_query_relevance(query: str, merged: list[dict]) -> list[dict]:
    """按（标题含有的实体段最深位置, 整句 token 命中数）降序**稳定**排序。

    为什么要「段位置」而不是「最后一段 token 命中」（两次真机实锤迭代）：
    ① 只按整句命中数——作品名 bigram 多（5-6）> 角色名（4），作品页压住角色页；
    ② 按最后一段命中——查询常以修饰词结尾（「…的外貌信息」），最后一段根本
    不是实体，角色页照样排不进正文抓取窗口（moegirl 角色页排第 4，配额被
    豆瓣/AniBase 作品页吃掉，正文摘录永远轮不到它）。
    「标题里出现的实体段越靠后 = 越贴近查询主题」（「作品名 的 角色名」结构里
    角色名在后），角色词条页（标题含第 2 段）压过作品页（只含第 1 段）；
    同分再看整句命中数，再同分保持原序（sort 稳定）。
    """
    segs = query_segments(query)
    q_tokens = ws._query_tokens(query)

    def key(r: dict) -> tuple[int, int]:
        title = r.get("title", "")
        idx = max((i for i, s in enumerate(segs) if s in title), default=-1)
        toks = ws._query_tokens(f"{title} {r.get('snippet', '')}")
        return (idx, len(q_tokens & toks))

    return sorted(merged, key=key, reverse=True)


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


def _image_results(query: str, proxy: str, max_results: int = 8) -> list[dict]:
    """图片检索：整句搜 → 全部被相关性过滤丢掉时按实体段**倒序**逐段重搜。

    2026-09-11 实锤：Bing Images 与文字 SERP 一样会放松查询——整句
    「超时空辉夜姬的月见八千代的外貌信息」返回钢筋调直机防护罩，实体段
    「超时空辉夜姬」才返回 4/5 相关图。`ws.image_search` 内部的
    `filter_relevant_images` 把无关整批判空，正好驱动这里的拆段重试。
    倒序（修饰词段 → 后段实体 → 前段限定词）的理由：查询「X 的 Y 的外貌」
    里 Y（角色/物品）才是图片主题，修饰词段（外貌信息）先被过滤淘汰、
    Y 命中即停——若正序先命中 X 的作品图，角色图就永远轮不到。
    """
    imgs = ws.image_search(query, max_results=max_results, proxy=proxy)
    if imgs:
        return imgs
    for seg in list(reversed(query_segments(query)))[:3]:
        imgs = ws.image_search(seg, max_results=max_results, proxy=proxy)
        if imgs:
            return imgs
    return []


# ── LLM 搜索规划（2026-09-11 晚，用户新指令：理解意图并扩散信息面 + 一次返回后再思考查漏） ──
# 两条 LLM 判断都是 **fail-open**：模型不可用/输出不可解析 → 返回空列表，
# 确定性管线（原句+拆段+过滤+重排+正文抓取）照常工作。LLM 只决定「搜什么」，
# 不决定「什么是真的」——事实仍只来自搜索语料，红线（严禁编造）不因多轮而放松。

_FACET_SYSTEM = (
    "你是搜索规划助手。用户想了解某个主题，请先判断意图属于哪类"
    "（角色/作品、服装、发型、画风、场景、物品、世界观等），再扩散出该类主题"
    "天然关联的信息面：查角色→外貌特征、服装造型、身份设定、声优与登场作品；"
    "查服装→布料材质、纹理花纹、款式剪裁、配色与搭配；查场景→构图、光影、氛围、"
    "时代元素；其他类型按此思路类推。"
    "输出 2-4 个互补的搜索子查询（JSON 字符串数组，只输出 JSON，不要解释），"
    "每个子查询 = 原主题关键词 + 一个信息面，用于补齐单一搜索覆盖不到的细节。"
    "若主题本身已足够具体（如单个实体名），输出该实体的不同检索措辞即可。"
)

_GAP_SYSTEM = (
    "你是资料查漏助手。会给定用户主题、已整理的总结和检索语料。请判断总结是否"
    "已覆盖用户想了解的信息面（如角色查外貌→发色/瞳色/五官/发型是否有着落，"
    "查服装→布料/纹理/花纹/款式是否有着落）。若有明显缺口且值得补搜，"
    "输出 1-2 个针对性的补充搜索查询（JSON 字符串数组，只输出 JSON，不要解释）；"
    "若已足够，或缺口明显无法靠网页搜索补齐（如该信息网上没有），输出 []。"
)


def _parse_json_list(raw: str, cap: int = 4) -> list[str]:
    """从模型输出里解析 JSON 字符串数组；不可解析返回空列表（fail-open）。"""
    s = (raw or "").strip()
    if not s:
        return []
    try:
        data = json.loads(s)
    except ValueError:
        m = re.search(r"\[[^\[\]]*\]", s, re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except ValueError:
            return []
    if not isinstance(data, list):
        return []
    return [str(x).strip() for x in data if str(x).strip()][:cap]


def expand_query_facets(query: str, base_url: str, api_key: str, model: str,
                        proxy: str = "") -> list[str]:
    """意图 → 信息面扩散：LLM 产出 2-4 个互补子查询。失败返回 []（只搜原句）。"""
    if not (base_url and api_key and model):
        return []
    try:
        raw = _llm.chat(base_url, api_key, model, _FACET_SYSTEM,
                        f"用户想了解的主题：{query}", temperature=0.3, proxy=proxy)
    except Exception:  # noqa: BLE001 - 扩散失败不阻断搜索
        return []
    return [f for f in _parse_json_list(raw) if f != query]


def _gap_queries(query: str, summary: str, corpus: str, base_url: str,
                 api_key: str, model: str, proxy: str = "") -> list[str]:
    """一次返回后查漏：LLM 判断总结缺口 → 1-2 个补充查询。失败/足够返回 []。"""
    if not (base_url and api_key and model):
        return []
    user = (f"用户想了解的主题：{query}\n\n已整理的总结：\n{summary}\n\n"
            f"检索语料：\n{corpus}")
    try:
        raw = _llm.chat(base_url, api_key, model, _GAP_SYSTEM, user,
                        temperature=0.2, proxy=proxy)
    except Exception:  # noqa: BLE001
        return []
    return _parse_json_list(raw, cap=2)


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
    搜回来一堆搜索引擎导航页）。搜索查询列表 = 原句 + **LLM 信息面扩散子查询**
    （查角色带外貌/服装/设定，查服装带布料/纹理/搭配，2026-09-11 晚用户新指令）
    + 实体拆段（多实体查询整句与拆段都跑：整句「成功」不等于覆盖主题实体——
    同日晚实锤，整句只返回 4 条作品页，角色词条根本不在结果里）。
    全部结果 URL 去重合并后按主题实体相关性重排，取正文摘录进语料，再总结。
    总结后还有一轮 **LLM 查漏**：判断总结缺口 → 补搜 → 并入总结（封顶一轮控时延）。
    LLM 规划环节全部 fail-open（模型不可用/输出不可解析 → 只跑确定性管线）。
    """
    query = clean_query(query)
    search_queries = [query]
    # 拆段排在扩散子查询**前面**：拆段是确定性高价值查询（主题实体直搜），
    # facets 最多 4 条，若排前面会把 `[:7]` 槽位占满、拆段被截掉——09-11 晚
    # 真机实锤：facets 挤掉拆段后 moegirl 兜底整轮没机会触发。
    segs = query_segments(query)
    if len(segs) >= 2:
        for seg in segs:
            if seg not in search_queries:
                search_queries.append(seg)
    for f in expand_query_facets(query, base_url, api_key, model, chat_proxy):
        if f not in search_queries:
            search_queries.append(f)
    seen: set[str] = set()
    results: list[dict] = []
    for i, q in enumerate(search_queries[:7]):
        for r in ws.web_search(q, max_results=6 if i == 0 else 4,
                               proxy=proxy, provider=search_provider):
            url = r.get("url")
            if url and url not in seen:
                seen.add(url)
                results.append(r)
    if not results:
        raise NoResults("联网搜索无结果（网络或搜索源不可用）")
    results = _reorder_by_query_relevance(query, results)
    corpus = "\n".join(f"- {r['title']}：{r['snippet']}" for r in results if r.get("title"))
    # 深度语料：抓正文摘录。SERP 摘要只有 1-2 行，外貌/服装/设定等细节在页面正文里
    # ——2026-09-11 实锤「实际搜索的都是最浅层的」：萌娘百科词条正文含发色/瞳色/声优/
    # 萌点全套，摘要里一个字没有。重排后主题页在前，但前面的结果可能是反爬站
    # （百度百科验证页）——所以顺延到前 4 条、取满 2 个成功正文即停，失败自动跳过。
    # 同页顺带提取 <img>（2026-09-11 晚补）：bing images 对长尾新角色间歇性整体
    # 放松，相关词条页本身就带角色图——搜索图不足时用它兜底。
    fetched = 0
    page_images: list[dict] = []
    for r in results[:4]:
        if fetched >= 2:
            break
        body, imgs_from_page = ws.fetch_page_text_and_images(r.get("url", ""), proxy=proxy)
        if body:
            corpus += f"\n【页面正文摘录｜{r['title']}】\n{body}"
            fetched += 1
        page_images.extend(imgs_from_page)
    user = f"用户想了解的主题：{query}\n\n联网搜索到的参考：\n{corpus}"
    raw = _llm.chat(
        base_url, api_key, model, _SYSTEM, user,
        temperature=0.5, proxy=chat_proxy,
    ).strip()
    title, content = split_title_content(raw, query)
    sources = [{"title": r["title"], "url": r["url"]} for r in results[:5] if r.get("title")]
    # 第二轮 LLM 查漏（用户新指令：一次返回的内容再思考去添加要额外搜集的信息）：
    # 判断总结缺了哪些信息面 → 补搜 → 新事实并入总结。封顶一轮；查漏失败/无缺口/
    # 补搜无新结果时保持第一轮总结不变。新事实仍只来自检索语料，红线不放松。
    extra = _gap_queries(query, content, corpus, base_url, api_key, model, chat_proxy)
    extra = [q for q in extra if q not in search_queries]
    if extra:
        new_results: list[dict] = []
        for q in extra[:2]:
            for r in ws.web_search(q, max_results=4, proxy=proxy, provider=search_provider):
                url = r.get("url")
                if url and url not in seen:
                    seen.add(url)
                    new_results.append(r)
        if new_results:
            new_results = _reorder_by_query_relevance(query, new_results)
            extra_corpus = "\n".join(
                f"- {r['title']}：{r['snippet']}" for r in new_results if r.get("title"))
            for r in new_results[:2]:  # 补充轮只抓 1 个正文，控时延
                body = ws.fetch_page_text(r.get("url", ""), proxy=proxy)
                if body:
                    extra_corpus += f"\n【页面正文摘录｜{r['title']}】\n{body}"
                    break
            user2 = (f"用户想了解的主题：{query}\n\n这是此前整理的总结：\n{content}\n\n"
                     f"补充检索到的参考：\n{extra_corpus}\n\n"
                     "请把补充参考中的新事实并入总结后重新输出，格式同前（第一行短标题，"
                     "随后成段总结）；补充参考里没有的细节保持缺失、严禁编造。")
            raw2 = _llm.chat(base_url, api_key, model, _SYSTEM, user2,
                             temperature=0.5, proxy=chat_proxy).strip()
            t2, c2 = split_title_content(raw2, query)
            if c2:
                title, content = t2, c2
            for r in new_results[:5]:
                if r.get("title") and not any(s["url"] == r["url"] for s in sources):
                    sources.append({"title": r["title"], "url": r["url"]})
    sources = sources[:8]
    images: list[dict] = []
    if include_images:
        # 页图优先（09-11 晚二次实锤）：正文页是重排后的主题词条页，页内 <img>
        # 就是角色图；而 bing images 拆段重搜会把「作品宣传图」当结果——
        # 「超时空辉夜姬」段 8 条全过相关性过滤，旧口径 len<6 才兜底 →
        # 角色词条页图被作品宣传图整体挤掉（同查询实测复现）。
        # 改为：页图置顶，搜索图只补位；页图已满 8 张时跳过图片搜索
        # （省掉最多 4 次外网请求的尾延迟）。
        images = list(page_images)[:8]
        if len(images) < 8:
            try:
                imgs_search = _image_results(query, proxy=proxy)
            except Exception:  # noqa: BLE001  图片搜索失败不阻断文字卡
                imgs_search = []
            have = {i.get("full_url") for i in images if i.get("full_url")}
            for si in imgs_search:
                u = (si.get("full_url") or "").strip()
                if u and u not in have:
                    images.append(si)
                    have.add(u)
                if len(images) >= 8:
                    break
    # M1.3 受控下载：搜索到的 full_url 登记为可下载候选（save 时校验命中）
    from app.services import web_material_candidates
    web_material_candidates.register_candidates(images, query=query, provider=search_provider or "")
    return {"title": title, "content": content, "sources": sources, "images": images}
