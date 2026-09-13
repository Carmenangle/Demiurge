"""灵感卡提炼逻辑单测：标题/内容拆分纯函数 + search_and_refine 代理透传。"""
from app.services import inspiration


def test_拆分标题与内容_两段式():
    title, content = inspiration.split_title_content("女仆装\n\n经典传统类有……\n文化融合类包括……", "女仆装款式")
    assert title == "女仆装"
    assert content == "经典传统类有……\n文化融合类包括……"


def test_拆分单行_标题回落query():
    title, content = inspiration.split_title_content("女仆装款式分为传统、法式、中式几类", "女仆装")
    assert title == "女仆装"
    assert content == "女仆装款式分为传统、法式、中式几类"


def test_拆分空输出_标题回落query内容为空():
    title, content = inspiration.split_title_content("", "女仆装")
    assert title == "女仆装"
    assert content == ""


def test_拆分首行过长_标题回落query内容为全文():
    text = "这是一个非常非常非常非常长的标题显然不符合短标题要求的第一行\n后面才是真正的内容"
    title, content = inspiration.split_title_content(text, "女仆装")
    assert title == "女仆装"
    assert content == text


def test_拆分忽略空行与首尾空白():
    title, content = inspiration.split_title_content("  女仆装  \n\n\n  内容段落A  \n\n  内容段落B  ", "女仆装")
    assert title == "女仆装"
    assert content == "内容段落A\n内容段落B"


def test_拆分空query兜底():
    title, content = inspiration.split_title_content("只有一行内容", "")
    assert title == "只有一行内容"
    assert content == "只有一行内容"


# ── 模型输出格式不稳定：清洗与回落 ─────────────────────────────

def test_标题带_标题冒号_前缀():
    title, content = inspiration.split_title_content("标题：女仆装\n经典传统类有……", "女仆装")
    assert title == "女仆装"
    assert content == "经典传统类有……"


def test_标题带_markdown井号():
    title, content = inspiration.split_title_content("# 女仆装\n经典传统类有……", "女仆装")
    assert title == "女仆装"
    assert content == "经典传统类有……"


def test_标题带_markdown加粗():
    title, content = inspiration.split_title_content("**女仆装**\n经典传统类有……", "女仆装")
    assert title == "女仆装"
    assert content == "经典传统类有……"


def test_标题带书名号与行尾冒号():
    title, content = inspiration.split_title_content("《女仆装》：\n经典传统类有……", "女仆装")
    assert title == "女仆装"
    assert content == "经典传统类有……"


def test_首行是前缀但内容为空_标题回落query():
    title, content = inspiration.split_title_content("总结：\n经典传统类有……", "女仆装")
    assert title == "女仆装"
    assert content == "总结：\n经典传统类有……"


def test_首行清洗后过长_标题回落query():
    text = "标题：女仆装和男仆装常见款式分类与风格特征详解大全分析\n经典传统类有……"
    title, content = inspiration.split_title_content(text, "女仆装")
    assert title == "女仆装"
    assert content == text


def test_search_and_refine返回标题内容来源且代理透传(monkeypatch):
    captured = {}

    def fake_search(query, *, max_results, proxy, provider=None):
        captured["search_proxy"] = proxy
        captured["provider"] = provider
        return [{"title": "t", "snippet": "s", "url": "https://e.test"}]

    def fake_image_search(query, *, max_results, proxy, provider=None):
        captured["image_search_called"] = True
        captured["image_proxy"] = proxy
        return [{"thumb_url": "https://t.test/1.jpg", "full_url": "https://f.test/1.jpg",
                 "source_url": "https://s.test", "title": "img"}]

    def fake_chat(*args, **kwargs):
        captured["chat_proxy"] = kwargs.get("proxy")
        return "女仆装\n\n经典传统类有……"

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration.ws, "image_search", fake_image_search)
    monkeypatch.setattr(inspiration._llm, "chat", fake_chat)

    data = inspiration.search_and_refine(
        "女仆装款式", "b", "k", "m",
        proxy="search-proxy", chat_proxy="chat-proxy", search_provider="ddg",
    )

    assert data["title"] == "女仆装"
    assert data["content"] == "经典传统类有……"
    assert data["sources"] == [{"title": "t", "url": "https://e.test"}]
    assert data["images"] == [{"thumb_url": "https://t.test/1.jpg", "full_url": "https://f.test/1.jpg",
                               "source_url": "https://s.test", "title": "img"}]
    assert captured["search_proxy"] == "search-proxy"
    assert captured["chat_proxy"] == "chat-proxy"
    assert captured["provider"] == "ddg"
    assert captured["image_search_called"] is True
    assert captured["image_proxy"] == "search-proxy"


def test_search_and_refine_图片搜索失败降级(monkeypatch):
    """图片搜索失败时 images=[] 不抛错，文字卡正常返回。"""
    def fake_search(query, *, max_results, proxy, provider=None):
        return [{"title": "t", "snippet": "s", "url": "https://e.test"}]

    def fake_image_search(query, *, max_results, proxy, provider=None):
        raise RuntimeError("图片搜索挂了")

    def fake_chat(*args, **kwargs):
        return "女仆装\n\n经典传统类有……"

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration.ws, "image_search", fake_image_search)
    monkeypatch.setattr(inspiration._llm, "chat", fake_chat)

    data = inspiration.search_and_refine("女仆装款式", "b", "k", "m")
    assert data["title"] == "女仆装"
    assert data["images"] == []  # 降级，不抛


# ── 查询净化与拆段（2026-09-11，修「找灵感拿不到真实网页」） ─────────
# 事故：用户原话「搜索超时空辉夜姬的月见八千代的外貌信息」被整句塞给 Bing ——
# 引擎把「搜索」当主题词返回五个搜索引擎首页；剥掉引导词后长句又被放松到首字
# 「超」返回词典条目。两条路都拿不到真实网页，模型只好声明失败或凭名字编造。

def test_净化_剥句首引导动词():
    assert inspiration.clean_query("搜索超时空辉夜姬的月见八千代的外貌信息") == "超时空辉夜姬的月见八千代的外貌信息"
    assert inspiration.clean_query("帮我搜一下可畏的外貌设定") == "可畏的外貌设定"
    assert inspiration.clean_query("查查圣路易斯") == "圣路易斯"
    assert inspiration.clean_query("请找一下洛丽塔裙子") == "洛丽塔裙子"


def test_净化_保留无引导词的查询与句尾标点():
    assert inspiration.clean_query("月见八千代") == "月见八千代"
    assert inspiration.clean_query("圣路易斯 碧蓝航线") == "圣路易斯 碧蓝航线"
    assert inspiration.clean_query("月见八千代的外貌？") == "月见八千代的外貌"


def test_净化_整句就是引导词时保原句():
    assert inspiration.clean_query("搜索一下") == "搜索一下"


def test_拆段_按的与空白切分并封顶():
    assert inspiration.query_segments("超时空辉夜姬的月见八千代的外貌信息") == ["超时空辉夜姬", "月见八千代", "外貌信息"]
    assert inspiration.query_segments("圣路易斯 碧蓝航线") == ["圣路易斯", "碧蓝航线"]
    assert inspiration.query_segments("月见八千代") == ["月见八千代"]  # 单段 → 不触发拆段重搜


def test_search_and_refine_引导词不进引擎(monkeypatch):
    seen = []

    def fake_search(query, *, max_results, proxy, provider=None):
        seen.append(query)
        return [{"title": "月见八千代 - 萌娘百科", "snippet": "", "url": "https://m.test"}]

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration.ws, "image_search", lambda *a, **k: [])
    monkeypatch.setattr(inspiration._llm, "chat", lambda *a, **k: "月见八千代\n\n外貌资料……")

    inspiration.search_and_refine("搜索月见八千代", "b", "k", "m")

    assert seen == ["月见八千代"]  # 引导词剥掉；单实体查询整句命中后不再拆段


def test_search_and_refine_整句无结果时拆段重搜(monkeypatch):
    """整句被引擎放松到首字（过滤后为空）→ 拆实体段逐段搜，能命中的段救回来。"""
    seen = []

    def fake_search(query, *, max_results, proxy, provider=None):
        seen.append(query)
        if query == "月见八千代":
            return [{"title": "月见八千代 - 萌娘百科 万物皆可萌的百科全书", "snippet": "外貌资料", "url": "https://m.test"}]
        return []

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration.ws, "image_search", lambda *a, **k: [])
    monkeypatch.setattr(inspiration._llm, "chat", lambda *a, **k: "月见八千代\n\n外貌资料……")

    data = inspiration.search_and_refine("超时空辉夜姬的月见八千代", "b", "k", "m")

    assert seen == ["超时空辉夜姬的月见八千代", "超时空辉夜姬", "月见八千代"]
    assert data["sources"] == [{"title": "月见八千代 - 萌娘百科 万物皆可萌的百科全书", "url": "https://m.test"}]


def test_search_and_refine_拆段合并按整句相关性重排(monkeypatch):
    """拆段按原文顺序合并会让限定词（作品）的条目占满前排、喧宾夺主。

    2026-09-11 实锤：「超时空辉夜姬的月见八千代」拆段后前 4 条全是作品页、
    角色词条排第 5，摘要跟着把重点放在作品上。修复 = 合并后按「与整句 query
    的 token 命中数」降序稳定排序——同时命中角色名+作品名的角色词条页
    排到只提作品名的条目前面。
    """
    def fake_search(query, *, max_results, proxy, provider=None):
        if query == "超时空辉夜姬":  # 段1：作品页，只命中作品 token
            return [
                {"title": "超时空辉夜姬！剧场版动画介绍", "snippet": "超时空辉夜姬于2024年上映，导演谈创作历程", "url": "https://w.test/1"},
                {"title": "超时空辉夜姬 官方网站", "snippet": "超时空辉夜姬的故事概要与制作阵容", "url": "https://w.test/2"},
            ]
        if query == "月见八千代":  # 段2：角色页，同时命中角色+作品 token
            return [{"title": "月见八千代 - 萌娘百科 万物皆可萌的百科全书",
                     "snippet": "月见八千代是超时空辉夜姬中的角色，由早见沙织配音", "url": "https://m.test"}]
        return []  # 整句被引擎放松，无结果

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration.ws, "image_search", lambda *a, **k: [])
    monkeypatch.setattr(inspiration._llm, "chat", lambda *a, **k: "月见八千代\n\n角色资料……")

    data = inspiration.search_and_refine("超时空辉夜姬的月见八千代", "b", "k", "m")

    titles = [s["title"] for s in data["sources"]]
    # token 命中数：角色页 9（月见/见八/八千/千代 + 超时/时空/空辉/辉夜/夜姬）
    # > 官网页 6（多命中「辉夜姬的」的「姬的」）> 剧场版页 5
    assert titles == [
        "月见八千代 - 萌娘百科 万物皆可萌的百科全书",  # 命中数最高 → 第一
        "超时空辉夜姬 官方网站",
        "超时空辉夜姬！剧场版动画介绍",
    ]


def test_系统提示词含实体主题导向():
    """_SYSTEM 必须把「最后的实体才是主题」写进提炼导向，防摘要喧宾夺主。"""
    assert "最后的实体才是主题" in inspiration._SYSTEM
    assert "不得喧宾夺主" in inspiration._SYSTEM


def test_search_and_refine_拆段仍无结果抛NoResults(monkeypatch):
    monkeypatch.setattr(inspiration.ws, "web_search", lambda *a, **k: [])
    monkeypatch.setattr(inspiration.ws, "image_search", lambda *a, **k: [])
    try:
        inspiration.search_and_refine("超时空辉夜姬的月见八千代", "b", "k", "m")
    except inspiration.NoResults:
        pass
    else:
        raise AssertionError("应抛 NoResults")


def test_search_and_refine_单段查询不触发拆段(monkeypatch):
    seen = []

    def fake_search(query, *, max_results, proxy, provider=None):
        seen.append(query)
        return []

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration.ws, "image_search", lambda *a, **k: [])
    try:
        inspiration.search_and_refine("月见八千代", "b", "k", "m")
    except inspiration.NoResults:
        pass
    else:
        raise AssertionError("应抛 NoResults")
    assert seen == ["月见八千代"]      # 只搜了一次，没有拆段重搜


# ── 深度语料与图片拆段（2026-09-11 晚，修「实际搜索的都是最浅层的/没有图片」） ──
# 实锤：SERP 摘要只有 1-2 行，外貌/服装细节在页面正文里（萌娘百科正文 9.8K 字含
# 发色/瞳色/声优全套，摘要一个字没有）；Bing Images 同样放松查询，整句图搜返回
# 钢筋调直机防护罩，实体段才有正确图片。

def test_search_and_refine_正文摘录进语料(monkeypatch):
    """前 2 条结果抓正文摘录追加进语料，标注来源标题；抓不到的页自动跳过。"""
    captured = {}

    def fake_search(query, *, max_results, proxy, provider=None):
        return [
            {"title": "月见八千代 - 萌娘百科", "snippet": "简介行", "url": "https://m.test/yachiyo"},
            {"title": "月见八千代_百度百科", "snippet": "反爬站", "url": "https://b.test/yachiyo"},
            {"title": "第三条不应抓取", "snippet": "s", "url": "https://x.test/3"},
        ]

    def fake_fetch(url, proxy="", timeout=8.0, max_chars=2000, max_images=6):
        captured.setdefault("fetched", []).append(url)
        if url.startswith("https://m.test"):
            return ("多种发色 渐变发（银色→粉色），多种瞳色 渐变瞳（上粉下青），声优 早见沙织。" * 5, [])
        return ("", [])  # 百度反爬页 → 空串跳过

    def fake_chat(*args, **kwargs):
        captured["user"] = args[4] if len(args) > 4 else kwargs.get("user") or args[-1]
        return "月见八千代\n\n外貌资料……"

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration.ws, "fetch_page_text_and_images", fake_fetch)
    monkeypatch.setattr(inspiration.ws, "image_search", lambda *a, **k: [])
    monkeypatch.setattr(inspiration._llm, "chat", fake_chat)

    data = inspiration.search_and_refine("月见八千代的外貌", "b", "k", "m")

    # 反爬页空串不计入「成功」→ 顺延抓到第 3 条；语料里只进抓到的正文
    assert captured["fetched"] == ["https://m.test/yachiyo", "https://b.test/yachiyo", "https://x.test/3"]
    assert "【页面正文摘录｜月见八千代 - 萌娘百科】" in captured["user"]
    assert "渐变瞳" in captured["user"]
    assert "【页面正文摘录｜月见八千代_百度百科】" not in captured["user"]  # 空串不进语料
    assert data["title"] == "月见八千代"


def test_search_and_refine_图片整句无关时按实体段重搜(monkeypatch):
    """整句图搜被相关性过滤判空（放松查询）→ 按实体段逐段重搜。"""
    seen_img_queries = []

    def fake_search(query, *, max_results, proxy, provider=None):
        return [{"title": "月见八千代 - 萌娘百科", "snippet": "s", "url": "https://m.test"}]

    def fake_image_search(query, *, max_results, proxy, provider=None):
        seen_img_queries.append(query)
        if query == "月见八千代":
            return [{"thumb_url": "https://t.test/1.jpg", "full_url": "https://f.test/1.jpg",
                     "source_url": "https://m.test", "title": "月见八千代 立绘"}]
        return []  # 整句与「外貌信息」段全被过滤判空

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration.ws, "image_search", fake_image_search)
    monkeypatch.setattr(inspiration.ws, "fetch_page_text_and_images", lambda *a, **k: ("", []))
    monkeypatch.setattr(inspiration._llm, "chat", lambda *a, **k: "月见八千代\n\n资料……")

    data = inspiration.search_and_refine("搜索月见八千代的外貌信息", "b", "k", "m")

    assert seen_img_queries[0] == "月见八千代的外貌信息"  # 整句先试
    assert "月见八千代" in seen_img_queries[1:]           # 失败后按实体段重搜
    assert data["images"][0]["full_url"] == "https://f.test/1.jpg"


def test_search_and_refine_整句只回限定词时拆段补主题页(monkeypatch):
    """整句「成功」≠覆盖主题实体（09-11 晚实锤：整句只回 4 条作品页，角色页不在结果里，
    拆段兜底永不触发）→ 多实体查询整句+拆段都跑，合并去重后重排让角色页排 sources[0]。"""
    def fake_search(query, *, max_results, proxy, provider=None):
        if query == "超时空辉夜姬的月见八千代":  # 整句：全作品页，能过相关性过滤
            return [
                {"title": "超时空辉夜姬！剧场版介绍", "snippet": "超时空辉夜姬于2026年上映", "url": "https://w.test/1"},
                {"title": "超时空辉夜姬 官网", "snippet": "超时空辉夜姬制作阵容", "url": "https://w.test/2"},
            ]
        if query == "月见八千代":  # 拆段：角色词条页
            return [{"title": "月见八千代 - 萌娘百科", "snippet": "月见八千代是超时空辉夜姬中的角色",
                     "url": "https://m.test"}]
        return []

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration.ws, "image_search", lambda *a, **k: [])
    monkeypatch.setattr(inspiration.ws, "fetch_page_text_and_images", lambda *a, **k: ("", []))
    monkeypatch.setattr(inspiration._llm, "chat", lambda *a, **k: "月见八千代\n\n角色资料……")

    data = inspiration.search_and_refine("超时空辉夜姬的月见八千代", "b", "k", "m")

    assert data["sources"][0]["title"] == "月见八千代 - 萌娘百科"  # 重排后角色页第一
    assert len(data["sources"]) == 3  # 整句 2 条 + 拆段补 1 条


def test_search_and_refine_信息面扩散_子查询进搜索(monkeypatch):
    """LLM 按意图扩散信息面（角色→外貌/服装/设定）→ 子查询参与多路搜索（09-11 晚用户新指令）。"""
    calls = {"n": 0}
    seen_queries = []

    def fake_chat(*args, **kwargs):
        calls["n"] += 1
        return '["月见八千代 外貌设定", "月见八千代 服装造型"]' if calls["n"] == 1 \
            else "月见八千代\n\n角色资料……"

    def fake_search(query, *, max_results, proxy, provider=None):
        seen_queries.append(query)
        return [{"title": "月见八千代 - 萌娘百科", "snippet": "s", "url": "https://m.test"}]

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration.ws, "image_search", lambda *a, **k: [])
    monkeypatch.setattr(inspiration.ws, "fetch_page_text_and_images", lambda *a, **k: ("", []))
    monkeypatch.setattr(inspiration._llm, "chat", fake_chat)

    inspiration.search_and_refine("搜索月见八千代", "b", "k", "m")

    assert calls["n"] >= 2                       # 第 1 次是扩散规划，之后是总结
    assert seen_queries[0] == "月见八千代"        # 原句在前
    assert "月见八千代 外貌设定" in seen_queries   # 扩散子查询都进了搜索
    assert "月见八千代 服装造型" in seen_queries


def test_search_and_refine_扩散不可解析时回落确定性管线(monkeypatch):
    """模型输出不可解析（fail-open）→ 只搜原句+拆段，不抛错。"""
    calls = {"n": 0}
    seen_queries = []

    def fake_chat(*args, **kwargs):
        calls["n"] += 1
        return "月见八千代\n\n角色资料……"  # 不是 JSON

    def fake_search(query, *, max_results, proxy, provider=None):
        seen_queries.append(query)
        if query == "月见八千代":
            return [{"title": "月见八千代 - 萌娘百科", "snippet": "s", "url": "https://m.test"}]
        return []

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration.ws, "image_search", lambda *a, **k: [])
    monkeypatch.setattr(inspiration.ws, "fetch_page_text_and_images", lambda *a, **k: ("", []))
    monkeypatch.setattr(inspiration._llm, "chat", fake_chat)

    data = inspiration.search_and_refine("搜索月见八千代", "b", "k", "m")

    assert seen_queries == ["月见八千代"]  # 扩散失败 → 只搜原句
    assert data["title"] == "月见八千代"


def test_search_and_refine_查漏轮_缺口触发补搜并并入(monkeypatch):
    """总结后 LLM 判断缺口 → 补搜 → 新来源进 sources、总结被第二轮输出替换。"""
    calls = {"n": 0}
    seen_queries = []

    def fake_chat(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return "[]"                                  # 扩散：无需子查询
        if calls["n"] == 2:
            return "月见八千代\n\n身份资料（缺外貌）。"      # 第一轮总结
        if calls["n"] == 3:
            return '["月见八千代 立绘 人设"]'              # 查漏：外貌有缺口
        return "月见八千代\n\n银发渐变粉瞳，声优早见沙织。"  # 并入后的最终总结

    def fake_search(query, *, max_results, proxy, provider=None):
        seen_queries.append(query)
        if query == "月见八千代 立绘 人设":
            return [{"title": "月见八千代 人设图", "snippet": "银发渐变 粉瞳", "url": "https://f.test"}]
        return [{"title": "月见八千代 - 萌娘百科", "snippet": "s", "url": "https://m.test"}]

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration.ws, "image_search", lambda *a, **k: [])
    monkeypatch.setattr(inspiration.ws, "fetch_page_text_and_images", lambda *a, **k: ("", []))
    monkeypatch.setattr(inspiration._llm, "chat", fake_chat)

    data = inspiration.search_and_refine("搜索月见八千代", "b", "k", "m")

    assert "月见八千代 立绘 人设" in seen_queries          # 查漏查询真的去搜了
    assert data["content"] == "银发渐变粉瞳，声优早见沙织。"  # 并入后的最终总结
    assert {s["url"] for s in data["sources"]} >= {"https://m.test", "https://f.test"}


def test_search_and_refine_查漏轮_资料足够不补搜(monkeypatch):
    calls = {"n": 0}

    def fake_chat(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return "[]"
        if calls["n"] == 2:
            return "月见八千代\n\n资料齐全。"
        return "[]"                                       # 查漏判断：已足够

    def fake_search(query, *, max_results, proxy, provider=None):
        return [{"title": "月见八千代 - 萌娘百科", "snippet": "s", "url": "https://m.test"}]

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration.ws, "image_search", lambda *a, **k: [])
    monkeypatch.setattr(inspiration.ws, "fetch_page_text_and_images", lambda *a, **k: ("", []))
    monkeypatch.setattr(inspiration._llm, "chat", fake_chat)

    data = inspiration.search_and_refine("搜索月见八千代", "b", "k", "m")

    assert calls["n"] == 3                                # 扩散 + 总结 + 查漏，无第四轮
    assert data["content"] == "资料齐全。"


def test_search_and_refine_搜索图不足时正文页图兜底(monkeypatch):
    """bing images 整体放松（0 图）→ 已抓正文页的 <img> 补进卡片（09-11 晚用例）。"""
    def fake_search(query, *, max_results, proxy, provider=None):
        return [{"title": "月见八千代 - 萌娘百科", "snippet": "s", "url": "https://m.test/yachiyo"}]

    def fake_fetch(url, proxy="", timeout=8.0, max_chars=2000, max_images=6):
        if url.startswith("https://m.test"):
            return ("正文" * 200, [
                {"thumb_url": "https://cdn.test/p1.jpg", "full_url": "https://cdn.test/p1.jpg",
                 "source_url": url, "title": "角色立绘"},
                {"thumb_url": "https://cdn.test/p2.jpg", "full_url": "https://cdn.test/p2.jpg",
                 "source_url": url, "title": "Q版"},
            ])
        return ("", [])

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration.ws, "image_search", lambda *a, **k: [])  # 搜索图全灭
    monkeypatch.setattr(inspiration.ws, "fetch_page_text_and_images", fake_fetch)
    monkeypatch.setattr(inspiration._llm, "chat", lambda *a, **k: "月见八千代\n\n资料……")

    data = inspiration.search_and_refine("搜索月见八千代", "b", "k", "m")

    assert [i["full_url"] for i in data["images"]] == ["https://cdn.test/p1.jpg", "https://cdn.test/p2.jpg"]


def test_search_and_refine_页图优先不被搜索图挤掉(monkeypatch):
    """拆段重搜拿回 8 条作品宣传图（全过过滤）→ 角色词条页图仍置顶，搜索图只补位。

    09-11 晚真机复现：旧口径「搜索图 <6 才页图兜底」，「超时空辉夜姬」段的 8 条
    作品图把角色图整体挤掉；且页图满 8 张时不再打图片搜索（省尾延迟）。
    """
    bing_calls = {"n": 0}

    def fake_search(query, *, max_results, proxy, provider=None):
        return [{"title": "月见八千代 - 萌娘百科", "snippet": "s", "url": "https://m.test/yachiyo"}]

    def fake_image_search(query, max_results=8, proxy="", provider=None):
        bing_calls["n"] += 1
        return [{"thumb_url": f"https://bing.test/{i}.jpg", "full_url": f"https://bing.test/{i}.jpg",
                 "source_url": "https://bing.test", "title": "超时空辉夜姬 宣传图"} for i in range(8)]

    def fake_fetch(url, proxy="", timeout=8.0, max_chars=2000, max_images=6):
        if url.startswith("https://m.test"):
            return ("正文" * 200, [
                {"thumb_url": f"https://cdn.test/p{i}.jpg", "full_url": f"https://cdn.test/p{i}.jpg",
                 "source_url": url, "title": "角色立绘"} for i in range(6)
            ])
        return ("", [])

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration.ws, "image_search", fake_image_search)
    monkeypatch.setattr(inspiration.ws, "fetch_page_text_and_images", fake_fetch)
    monkeypatch.setattr(inspiration._llm, "chat", lambda *a, **k: "月见八千代\n\n资料……")

    data = inspiration.search_and_refine("搜索月见八千代", "b", "k", "m")

    urls = [i["full_url"] for i in data["images"]]
    assert urls[:6] == [f"https://cdn.test/p{i}.jpg" for i in range(6)]   # 页图（角色图）置顶
    assert urls[6:] == ["https://bing.test/0.jpg", "https://bing.test/1.jpg"]  # 搜索图只补位到 8
    assert bing_calls["n"] >= 1


def test_search_and_refine_页图已满8张跳过图片搜索(monkeypatch):
    """正文页图 ≥8 张时不再请求 bing images（省最多 4 次外网请求的尾延迟）。"""
    bing_calls = {"n": 0}

    def fake_search(query, *, max_results, proxy, provider=None):
        return [
            {"title": "月见八千代 - 萌娘百科", "snippet": "s", "url": "https://m.test/yachiyo"},
            {"title": "月见八千代 设定集", "snippet": "s", "url": "https://n.test/yachiyo"},
        ]

    def fake_image_search(query, max_results=8, proxy="", provider=None):
        bing_calls["n"] += 1
        return []

    def fake_fetch(url, proxy="", timeout=8.0, max_chars=2000, max_images=6):
        if url.startswith("https://m.test"):
            # 两页各 6 张 → 页图 12 张，截到 8
            return ("正文" * 200, [
                {"thumb_url": f"https://cdn.test/p{i}.jpg", "full_url": f"https://cdn.test/p{i}.jpg",
                 "source_url": url, "title": "角色立绘"} for i in range(6)
            ])
        if url.startswith("https://n.test"):
            return ("正文" * 200, [
                {"thumb_url": f"https://cdn2.test/q{i}.jpg", "full_url": f"https://cdn2.test/q{i}.jpg",
                 "source_url": url, "title": "设定图"} for i in range(6)
            ])
        return ("", [])

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration.ws, "image_search", fake_image_search)
    monkeypatch.setattr(inspiration.ws, "fetch_page_text_and_images", fake_fetch)
    monkeypatch.setattr(inspiration._llm, "chat", lambda *a, **k: "月见八千代\n\n资料……")

    data = inspiration.search_and_refine("搜索月见八千代", "b", "k", "m")

    assert len(data["images"]) == 8
    assert bing_calls["n"] == 0                                    # 页图已满，图片搜索整体跳过
    assert all(i["full_url"].startswith("https://cdn") for i in data["images"])
