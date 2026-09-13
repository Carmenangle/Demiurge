"""M1.1 搜索源 Adapter 注册表单元测试。

覆盖：
- 注册表：注册/查找/未注册回落
- DDG 行为迁移：HTML 解析/空查询/异常返回空列表
- Bing 中国站（bing-cn）：HTML 解析/相对 URL/摘要错位防护/实体还原
- 自动回落链：默认按 bing-cn→ddg 依次尝试；显式 provider 不回落
- 签名兼容：web_search 对旧调用方（不传 provider）行为不变
- available_adapters 探测
"""

from app.services import web_search as ws


# ── 注册表 ───────────────────────────────────────────────────

def test_register_and_get_adapter():
    """注册 Adapter 后可通过名称取回。"""
    class FakeAdapter:
        def search(self, query, max_results=6, proxy=""):
            return [{"title": "fake", "snippet": "s", "url": "https://x.test"}]

    fake = FakeAdapter()
    ws.register_adapter("test-source", fake)
    assert ws.get_adapter("test-source") is fake


def test_get_adapter_missing_returns_none():
    """未注册的源返回 None。"""
    assert ws.get_adapter("nonexistent") is None


def test_ddg_adapter_registered():
    """ddg 已注册（自动链第二跳，也是显式可选项）。"""
    adapter = ws.get_adapter("ddg")
    assert adapter is not None


def test_available_adapters_returns_sorted_names():
    """available_adapters 返回已注册源名称排序列表。"""
    names = ws.available_adapters()
    assert "ddg" in names
    assert names == sorted(names)


def test_注册表覆盖前端选项里的每个源():
    """前端「联网搜索源」下拉的取值必须都能在注册表里取到。

    源名写错后端会当成未注册源、静默返回空列表——所以这里把
    `lib/searchSource.ts` 的选项值钉在测试里：改名必须同时改这里。
    """
    assert {"bing-cn", "ddg"} <= set(ws.available_adapters())


def test_web_search_默认走自动回落链():
    """不传 provider = 自动回落链（bing-cn → moegirl → ddg）。

    2026-09-10 语义变更：原「默认源 ddg」在国内无代理时**必然失败**
    （DDG 直连不通），故改为按链依次尝试、国内直连源在前。旧用例
    `test_web_search_defaults_to_ddg_when_no_provider` 随之校正。
    2026-09-11 晚加入 moegirl 第二跳：bing 对长尾新角色名间歇性放松到首字，
    萌站 opensearch 精准命中（见 MoegirlSearchAdapter 注释）。
    """
    assert ws._AUTO_CHAIN == ("bing-cn", "moegirl", "ddg")


def test_web_search_unknown_provider_returns_empty():
    """不存在的 provider 返回空列表。"""
    result = ws.web_search("test query", provider="nonexistent", proxy="")
    assert result == []


# ── DDG HTML 解析 ────────────────────────────────────────────

_DDG_MOCK = """<!DOCTYPE html>
<html>
<body>
<div class="results">
    <a rel="nofollow" class="result__a" href="https://example.com/page1">Result One</a>
    <a class="result__snippet">Snippet for result one</a>
    <a rel="nofollow" class="result__a" href="https://example.com/page2">Result Two</a>
    <a class="result__snippet">Snippet for result two</a>
</div>
</body>
</html>"""


def test_ddg_parse_html(monkeypatch):
    """DDG adapter 正确解析 HTML 结果。"""
    import httpx

    class FakeResponse:
        text = _DDG_MOCK

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers, params):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)

    adapter = ws.get_adapter("ddg")
    results = adapter.search("test query", max_results=6, proxy="")

    assert len(results) == 2
    assert results[0]["title"] == "Result One"
    assert results[0]["snippet"] == "Snippet for result one"
    assert results[0]["url"] == "https://example.com/page1"
    assert results[1]["title"] == "Result Two"
    assert results[1]["snippet"] == "Snippet for result two"
    assert results[1]["url"] == "https://example.com/page2"


def test_ddg_empty_query_returns_empty():
    """DDG adapter 空查询返回空列表。"""
    adapter = ws.get_adapter("ddg")
    result = adapter.search("", proxy="")
    assert result == []


def test_ddg_http_error_returns_empty(monkeypatch):
    """DDG HTTP 异常返回空列表不抛。"""
    import httpx

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers, params):
            raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(httpx, "Client", FakeClient)

    adapter = ws.get_adapter("ddg")
    result = adapter.search("test query", proxy="")
    assert result == []


# ── 签名兼容 ─────────────────────────────────────────────────

def test_web_search_compat_no_provider(monkeypatch):
    """旧调用方不传 provider 时行为不变。"""
    import httpx

    class FakeClient:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, headers, params):
            raise httpx.ConnectError("no network")

    # 必须注入 FakeClient：否则 CI（可直连 DDG）会发真实请求返回真结果
    monkeypatch.setattr(httpx, "Client", FakeClient)

    # 验证 web_search 签名：旧调用方 web_search(q, n, proxy) 不报 TypeError
    result = ws.web_search("test", max_results=3, proxy="")
    # 无网络 → 返回空列表，不是异常
    assert result == []


def test_search_and_refine_accepts_provider(monkeypatch):
    """inspiration.search_and_refine 接受并透传 search_provider。"""
    from app.services import inspiration

    captured_provider = {}

    def fake_search(query, *, max_results, proxy, provider=None):
        captured_provider["provider"] = provider
        return [{"title": "t", "snippet": "s", "url": "https://x.test"}]

    def fake_chat(*args, **kwargs):
        return "tag1, tag2"

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration._llm, "chat", fake_chat)

    inspiration.search_and_refine(
        "query", "b", "k", "m", search_provider="ddg",
    )

    assert captured_provider["provider"] == "ddg"


# ── M1.2 图片搜索 adapter ──────────────────────────────────────

_BING_MOCK = """<!DOCTYPE html>
<html>
<body>
<a class="iusc" m="{&quot;murl&quot;:&quot;https://img.example.com/1.jpg&quot;,&quot;turl&quot;:&quot;https://t.example.com/1_thumb.jpg&quot;,&quot;purl&quot;:&quot;https://example.com/page1&quot;,&quot;t&quot;:&quot;Pic One&quot;}"></a>
<a class="other" m="{&quot;murl&quot;:&quot;https://img.example.com/garbage.jpg&quot;}"></a>
<a class="iusc" m="{&quot;murl&quot;:&quot;https://img.example.com/2.jpg&quot;,&quot;turl&quot;:&quot;data:image/jpeg;base64,xxx&quot;,&quot;purl&quot;:&quot;https://example.com/page2&quot;,&quot;t&quot;:&quot;Pic Two&quot;}"></a>
<a class="iusc" m="not-json"></a>
</body>
</html>"""


def test_bing_images_parse_html(monkeypatch):
    """Bing Images adapter 正确解析 m 属性 JSON。"""
    import httpx

    class FakeResponse:
        text = _BING_MOCK

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers, params):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    adapter = ws.get_image_adapter("bing-images")
    assert adapter is not None
    results = adapter.search_images("test query", max_results=8, proxy="")

    assert len(results) == 2
    assert results[0]["thumb_url"] == "https://t.example.com/1_thumb.jpg"
    assert results[0]["full_url"] == "https://img.example.com/1.jpg"
    assert results[0]["source_url"] == "https://example.com/page1"
    assert results[0]["title"] == "Pic One"
    # 第二张 turl 是 base64 → 回落 murl 做 thumb
    assert results[1]["thumb_url"] == "https://img.example.com/2.jpg"
    assert results[1]["full_url"] == "https://img.example.com/2.jpg"


def test_bing_images_empty_query_returns_empty():
    """图片搜索空查询返回空列表。"""
    adapter = ws.get_image_adapter("bing-images")
    assert adapter is not None
    assert adapter.search_images("", proxy="") == []


def test_bing_images_http_error_returns_empty(monkeypatch):
    """图片搜索 HTTP 异常返回空列表不抛。"""
    import httpx

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers, params):
            raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "Client", FakeClient)
    adapter = ws.get_image_adapter("bing-images")
    assert adapter is not None
    assert adapter.search_images("test", proxy="") == []


def test_image_search_compat_entry(monkeypatch):
    """image_search 入口：默认 bing-images，不存在返回空。"""
    import httpx

    class FakeClient:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, headers, params):
            raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "Client", FakeClient)
    assert ws.image_search("test", proxy="") == []
    assert ws.image_search("test", proxy="", provider="nonexistent") == []


def test_image_search_adapter_is_image_search_adapter():
    """bing-images adapter 满足 ImageSearchAdapter 协议。"""
    adapter = ws.get_image_adapter("bing-images")
    assert adapter is not None
    assert hasattr(adapter, "search_images")


def test_bing_images_proxy_passed_to_client(monkeypatch):
    """图片搜索 proxy 参数正确透传给 httpx.Client。"""
    import httpx

    class FakeResponse:
        text = _BING_MOCK
        def raise_for_status(self):
            pass

    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["kw"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers, params):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    adapter = ws.get_image_adapter("bing-images")
    adapter.search_images("test", max_results=8, proxy="http://127.0.0.1:7890")

    assert captured["kw"]["proxy"] == "http://127.0.0.1:7890"
    assert captured["kw"]["trust_env"] is False


# ── 协议检查 ─────────────────────────────────────────────────

def test_ddg_adapter_is_search_adapter():
    """DDG adapter 满足 SearchAdapter 协议。"""
    adapter = ws.get_adapter("ddg")
    assert isinstance(adapter, ws.SearchAdapter)


# ── 可用性探针（2026-09-10「继承全局」升级） ─────────────────────

def test_probe_有结果时报可用(monkeypatch):
    """probe 与灵感搜索同源：真搜到东西才算通。"""
    captured: dict = {}

    def fake_search(query, *, max_results, proxy, provider=None):
        captured.update({"query": query, "max_results": max_results, "proxy": proxy, "provider": provider})
        return [{"title": "t", "snippet": "s", "url": "https://x.test"}]

    monkeypatch.setattr(ws, "web_search", fake_search)
    ok, detail = ws.probe("http://127.0.0.1:7897")

    assert ok is True
    assert "1 条" in detail
    assert captured["proxy"] == "http://127.0.0.1:7897"  # 代理必须透传到搜索源


def test_probe_无结果时报不可用(monkeypatch):
    monkeypatch.setattr(ws, "web_search",
                        lambda query, *, max_results, proxy, provider=None: [])
    ok, detail = ws.probe("")
    assert ok is False
    assert "搜索无结果" in detail


def test_probe_异常不让调用方炸(monkeypatch):
    def boom(query, *, max_results, proxy, provider=None):
        raise RuntimeError("adapter exploded")

    monkeypatch.setattr(ws, "web_search", boom)
    ok, detail = ws.probe("")
    assert ok is False
    assert "RuntimeError" in detail


# ── Bing 中国站文字源（2026-09-10 新增，国内直连可用） ─────────────

_BING_CN_MOCK = """<!DOCTYPE html><html><body>
<li class="b_algo">
  <h2><a href="https://example.com/a">标题甲</a></h2>
  <p class="b_lineclamp4 b_algoSlug">摘要甲 &ensp; &amp; 补充</p>
</li>
<li class="b_algo">
  <h2><a href="/relative/b">标题乙</a></h2>
  <p class="b_lineclamp2">摘要乙</p>
</li>
<li class="b_algo">
  <h2><a href="javascript:void(0)">坏链接</a></h2>
  <p class="b_lineclamp2">摘要丙</p>
</li>
</body></html>"""


def _patch_httpx(monkeypatch, text: str = "", exc: Exception | None = None) -> dict:
    """把 httpx.Client 换成固定响应的替身；返回 kwargs 捕获字典（供代理断言）。"""
    import httpx

    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        @property
        def text(self):
            return text

    class FakeClient:
        def __init__(self, **kwargs):
            captured["kw"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers, params):
            if exc is not None:
                raise exc
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    return captured


def test_strip_tags_还原html实体():
    """实体走 html.unescape：`&ensp;` 这类手写替换表漏掉的实体也必须还原。"""
    assert ws._strip_tags("<b>甲</b> &amp; 乙") == "甲 & 乙"
    assert ws._strip_tags("甲 &ensp; 乙") == "甲 乙"
    assert ws._strip_tags("&lt;script&gt;") == "<script>"


def test_bing_cn_解析标题摘要与链接(monkeypatch):
    """bing-cn 解析 h2 链接 + b_lineclamp 摘要；相对 URL 补全为 cn.bing.com。"""
    _patch_httpx(monkeypatch, text=_BING_CN_MOCK)
    adapter = ws.get_adapter("bing-cn")
    assert adapter is not None

    results = adapter.search("测试", max_results=6, proxy="")

    # javascript: 那条被跳过 → 2 条
    assert len(results) == 2
    assert results[0]["title"] == "标题甲"
    assert results[0]["snippet"] == "摘要甲 & 补充"  # 实体已还原并压空白
    assert results[0]["url"] == "https://example.com/a"
    assert results[1]["title"] == "标题乙"
    assert results[1]["url"] == "https://cn.bing.com/relative/b"


def test_bing_cn_摘要数量与链接不符时丢弃全部摘要(monkeypatch):
    """结构漂移（链接 2 条 / 摘要 1 条）时宁缺摘要，也不按索引错配。

    错配会把 A 的摘要挂到 B 上，静默污染模型上下文；同类教训见画布节点卡
    「#20 框里是 #18」——判定必须校验身份，不能只数个数。
    """
    html = """<li class="b_algo"><h2><a href="https://e.test/1">甲</a></h2>
    <p class="b_lineclamp2">只此一条摘要</p></li>
    <li class="b_algo"><h2><a href="https://e.test/2">乙</a></h2></li>"""
    _patch_httpx(monkeypatch, text=html)
    results = ws.get_adapter("bing-cn").search("测试", proxy="")

    assert [r["title"] for r in results] == ["甲", "乙"]
    assert [r["snippet"] for r in results] == ["", ""]


def test_bing_cn_空查询不发起请求():
    assert ws.get_adapter("bing-cn").search("", proxy="") == []


def test_bing_cn_异常返回空列表(monkeypatch):
    import httpx

    _patch_httpx(monkeypatch, exc=httpx.ConnectError("no network"))
    assert ws.get_adapter("bing-cn").search("测试", proxy="") == []


def test_bing_cn_proxy透传且trust_env关闭(monkeypatch):
    """显式代理生效，且 trust_env=False（不读系统 env，行为可控）。"""
    captured = _patch_httpx(monkeypatch, text=_BING_CN_MOCK)
    ws.get_adapter("bing-cn").search("测试", proxy="http://127.0.0.1:7890")

    assert captured["kw"]["proxy"] == "http://127.0.0.1:7890"
    assert captured["kw"]["trust_env"] is False


def test_bing_cn_满足SearchAdapter协议():
    assert isinstance(ws.get_adapter("bing-cn"), ws.SearchAdapter)


# ── 自动回落链（2026-09-10） ─────────────────────────────────────

def _fake_adapters(monkeypatch, results_by_name: dict[str, list[dict]]) -> list[str]:
    """替换 get_adapter，记录尝试顺序。"""
    calls: list[str] = []

    class Fake:
        def __init__(self, name: str):
            self.name = name

        def search(self, query, max_results=6, proxy=""):
            calls.append(self.name)
            return results_by_name.get(self.name, [])

    monkeypatch.setattr(ws, "get_adapter", lambda name: Fake(name) if name in results_by_name else None)
    return calls


_HIT = [{"title": "命中", "snippet": "s", "url": "https://hit.test"}]


def test_自动链_首源为空时回落第二源(monkeypatch):
    calls = _fake_adapters(monkeypatch, {"bing-cn": [], "ddg": _HIT})

    out = ws.web_search("命中")

    assert calls == ["bing-cn", "ddg"]
    assert out == _HIT


def test_自动链_首源有结果就不试第二源(monkeypatch):
    calls = _fake_adapters(monkeypatch, {"bing-cn": _HIT, "ddg": _HIT})

    out = ws.web_search("命中")

    assert calls == ["bing-cn"]  # 不浪费第二次请求
    assert out == _HIT


def test_自动链_全空时返回空列表(monkeypatch):
    calls = _fake_adapters(monkeypatch, {"bing-cn": [], "ddg": []})

    assert ws.web_search("命中") == []
    assert calls == ["bing-cn", "ddg"]


def test_显式provider只用该源不回落(monkeypatch):
    """点名了源就不换源——悄悄回落只会把失败藏起来。"""
    calls = _fake_adapters(monkeypatch, {"bing-cn": [], "ddg": _HIT})

    out = ws.web_search("命中", provider="bing-cn")

    assert calls == ["bing-cn"]
    assert out == []


def test_auto别名等同自动链(monkeypatch):
    """provider="auto" 必须走链：前端设置项的值会原样透传，当成未注册源会静默返回空。"""
    calls = _fake_adapters(monkeypatch, {"bing-cn": _HIT, "ddg": _HIT})

    assert ws.web_search("命中", provider="auto") == _HIT
    assert calls == ["bing-cn"]


# ── 搜索源透传到 agent 路径（对话里说「找灵感」） ─────────────────

def test_inspire_node_透传搜索源到灵感搜索(monkeypatch):
    """`inspire_node` 必须把 `ctx["search_provider"]` 交给 `search_and_refine`。

    缺这一环的后果是**静默不一致**：用户在设置里点名了 DuckDuckGo，
    但对话路径（区别于 /ai/inspiration 端点）继续走自动链，界面显示一个源、
    实际用另一个源，日志里也看不出。
    """
    from app.services import agent_graph, generation_store, inspiration

    captured: dict = {}

    def fake_search_and_refine(*args, **kwargs):
        captured.update(kwargs)
        return {"title": "T", "content": "C", "sources": [], "images": []}

    monkeypatch.setattr(inspiration, "search_and_refine", fake_search_and_refine)
    monkeypatch.setattr(generation_store, "persist_inspiration",
                        lambda *a, **k: {"id": "card-1"})

    out = agent_graph.inspire_node({
        "_ctx": {
            "thread_id": "t1", "chat_base": "b", "chat_key": "k", "chat_model": "m",
            "proxy": "http://127.0.0.1:7897", "chat_proxy": "cp",
            "search_provider": "ddg",
        },
        "user_text": "找点灵感",
    })

    assert captured["search_provider"] == "ddg"
    assert captured["proxy"] == "http://127.0.0.1:7897"
    assert captured["chat_proxy"] == "cp"
    assert out.get("insp_cards") == [{"id": "card-1"}]


def test_inspire_node_搜索源为空时传None走自动链(monkeypatch):
    """`search_provider` 为空必须收敛成 None（=自动回落链），不能把空串当源名。"""
    from app.services import agent_graph, generation_store, inspiration

    captured: dict = {}

    def fake_search_and_refine(*args, **kwargs):
        captured.update(kwargs)
        return {"title": "T", "content": "C", "sources": [], "images": []}

    monkeypatch.setattr(inspiration, "search_and_refine", fake_search_and_refine)
    monkeypatch.setattr(generation_store, "persist_inspiration",
                        lambda *a, **k: {"id": "card-1"})

    agent_graph.inspire_node({
        "_ctx": {"thread_id": "t1", "chat_base": "b", "chat_key": "k",
                 "chat_model": "m", "search_provider": ""},
        "user_text": "找点灵感",
    })

    assert captured["search_provider"] is None


# ── 相关性过滤（2026-09-11，修「引擎改写查询返回垃圾」） ─────────
# 事故：搜「超时空辉夜姬的月见八千代的外貌信息」，Bing CN 无 cookie SERP 把查询
# 放松到首字「超」，返回超（汉语汉字）/超星/超自然行动组；搜「搜索X」则把引导动词
# 当主题词，返回搜狗/360/一起搜/必应/Google 五个搜索引擎首页。垃圾结果照单全收
# 喂给模型 → 模型声明检索失败或凭角色名编造设定。

def test_filter_relevant_丢弃改写产生的无关结果():
    """引擎放松到首字后的词典/导航类结果必须全丢。"""
    rs = [
        {"title": "超（汉语汉字）_百度百科", "snippet": "超的意思", "url": "https://a.test"},
        {"title": "超星", "snippet": "学习平台", "url": "https://b.test"},
    ]
    assert ws.filter_relevant("超时空辉夜姬的月见八千代的外貌信息", rs) == []


def test_filter_relevant_保留命中结果并容错改写():
    """标题被引擎改写但仍有 bigram 命中的要保留（别误杀真结果）。"""
    rs = [
        {"title": "月见八千代 - 萌娘百科 万物皆可萌的百科全书", "snippet": "", "url": "https://a.test"},
        {"title": "超辉夜姬！ - 萌娘百科", "snippet": "", "url": "https://b.test"},
        {"title": "搜狗搜索引擎 - 上网从搜狗开始", "snippet": "", "url": "https://c.test"},
    ]
    out = ws.filter_relevant("超时空辉夜姬 月见八千代", rs)
    assert [r["url"] for r in out] == ["https://a.test", "https://b.test"]


def test_filter_relevant_短查询不过滤():
    """单字查询没有可判信号，硬滤会误杀 → 原样放行。"""
    rs = [{"title": "任意结果", "snippet": "", "url": "https://a.test"}]
    assert ws.filter_relevant("超", rs) == rs


def test_web_search_整批无关视为该源失败回落下一源(monkeypatch):
    """bing-cn 返回全是垃圾 → 等同该源失败 → 继续试 ddg，而不是把垃圾交给模型。"""
    calls = []

    class FakeAdapter:
        def __init__(self, tag):
            self.tag = tag

        def search(self, query, max_results=6, proxy=""):
            calls.append(self.tag)
            if self.tag == "bing-cn":
                return [{"title": "搜狗搜索引擎 - 上网从搜狗开始", "snippet": "", "url": "https://sogou.test"}]
            return [{"title": "月见八千代 - 萌娘百科", "snippet": "", "url": "https://moegirl.test"}]

    table = {"bing-cn": FakeAdapter("bing-cn"), "ddg": FakeAdapter("ddg")}
    monkeypatch.setattr(ws, "get_adapter", lambda name: table.get(name))

    out = ws.web_search("月见八千代的外貌", provider=None)

    assert calls == ["bing-cn", "ddg"]
    assert [r["url"] for r in out] == ["https://moegirl.test"]


def test_filter_relevant_两字查询的放松结果也丢():
    """「可畏」只有 1 个 bigram —— 若按「有交集就留」会被放松结果钻空子（09-11 实锤）。"""
    rs = [
        {"title": "可（汉语文字）_百度百科", "snippet": "可的意思", "url": "https://a.test"},
        {"title": "可畏 - 碧蓝航线WIKI_BWIKI", "snippet": "舰船资料", "url": "https://b.test"},
    ]
    out = ws.filter_relevant("可畏", rs)
    assert [r["url"] for r in out] == ["https://b.test"]


def test_filter_relevant_英文按词判相关():
    """英文不能用字符 bigram：maid 的 ma/ai/id 会与任何英文都相交，形同虚设。"""
    rs = [
        {"title": "maid（英语单词）_百度百科", "snippet": "女仆的英文", "url": "https://a.test"},
        {"title": "Victorian Style Dress - Vintage Shop", "snippet": "victorian dress", "url": "https://b.test"},
    ]
    out = ws.filter_relevant("maid dress victorian style", rs)
    assert [r["url"] for r in out] == ["https://b.test"]


# ── 图片相关性过滤 + 正文抓取（2026-09-11 晚，修「图片全是无关垃圾/内容最浅层」） ──
# 实锤：Bing Images 与文字 SERP 一样放松查询——「月见八千代 外貌」返回钢筋调直机
# 防护罩、「月见八千代」返回电击棍；且 SERP 摘要只有 1-2 行，外貌细节在页面正文里
# （萌娘百科正文 9.8K 字含发色/瞳色/声优全套，摘要里一个字没有）。

def test_filter_relevant_images_整批无关判空():
    """与 query 零相交的垃圾图（放松查询产物）全丢 → 空列表=失败，驱动调用方拆段。"""
    junk = [
        {"title": "918电击棍-防抢夺电击棍多少钱", "full_url": "https://x/1.jpg"},
        {"title": "钢筋调直机防护罩进料口防护", "full_url": "https://x/2.jpg"},
    ]
    assert ws.filter_relevant_images("月见八千代", junk) == []


def test_filter_relevant_images_跨词bigram擦边图被丢():
    """09-11 晚真机反例：无关新闻标题偶然含 bigram「的外」擦边过关 → 收窄回 ≥2 命中。"""
    junk = [{"title": "凝固历史的外滩老建筑_海关大楼_中山东一路_风格",
             "full_url": "https://x/1.jpg"}]
    assert ws.filter_relevant_images("超时空辉夜姬的月见八千代的外貌信息", junk) == []


def test_filter_relevant_images_保留实体相关图():
    kept = ws.filter_relevant_images("月见八千代 外貌", [
        {"title": "月见八千代 立绘", "full_url": "https://x/1.jpg"},
        {"title": "月见八千代 官方人设图", "full_url": "https://x/2.jpg"},
    ])
    assert len(kept) == 2


def test_filter_relevant_images_空标题用来源文件名兜底():
    kept = ws.filter_relevant_images("月见八千代", [
        {"title": "", "source_url": "https://example.com/wiki/月见八千代", "full_url": "https://x/1.jpg"},
        {"title": "", "source_url": "", "full_url": "https://x/月见八千代.png"},
    ])
    assert len(kept) == 2


def test_filter_relevant_images_无token查询不过滤():
    results = [{"title": "随便什么", "full_url": "https://x/1.jpg"}]
    assert ws.filter_relevant_images("超", results) == results  # 单字中文无可判信号


class _FakePageResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _FakePageClient:
    """可编排响应的 httpx.Client 替身：get 按注册的 url 返回对应文本。"""

    pages: dict = {}

    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, url, headers=None, params=None):
        import httpx as _hx
        page = self.pages.get(url)
        if page is None:
            raise _hx.ConnectError("no network")
        return _FakePageResponse(page)


def test_fetch_page_text_剥脚本与标签():
    _FakePageClient.pages = {
        "https://a.test/page": (
            "<html><head><style>.x{}</style></head><body>"
            "<script>var a=1;</script><h1>月见八千代</h1>"
            "<p>多种发色 渐变发（银色→粉色），多种瞳色 渐变瞳（上粉下青）。</p>"
            + "正文填充" * 150 + "</body></html>"
        ),
    }
    import httpx as _hx
    orig = _hx.Client
    _hx.Client = _FakePageClient
    try:
        text = ws.fetch_page_text("https://a.test/page")
    finally:
        _hx.Client = orig
    assert "var a=1" not in text and ".x{}" not in text
    assert "月见八千代" in text and "渐变瞳" in text


def test_fetch_page_text_反爬验证页判失败():
    _FakePageClient.pages = {"https://waf.test/v": "百度安全验证"}  # <200 字
    import httpx as _hx
    orig = _hx.Client
    _hx.Client = _FakePageClient
    try:
        assert ws.fetch_page_text("https://waf.test/v") == ""
    finally:
        _hx.Client = orig


def test_fetch_page_text_非法url与网络错误返回空():
    assert ws.fetch_page_text("not-a-url") == ""
    _FakePageClient.pages = {}
    import httpx as _hx
    orig = _hx.Client
    _hx.Client = _FakePageClient
    try:
        assert ws.fetch_page_text("https://404.test/x") == ""
    finally:
        _hx.Client = orig


def test_fetch_page_text_and_images_提取图片并过滤():
    """同页提取 <img>：只留 http(s) 位图，跳过 svg/图标/小缩略图，alt 作标题、相对 src 绝对化（09-11 晚页图兜底）。"""
    _FakePageClient.pages = {
        "https://wiki.test/char": (
            "<html><body>"
            "<img src='https://cdn.test/a.svg' >"
            "<img src='/img/icon_logo.png'>"
            "<img src='https://cdn.test/p1.jpg' alt='角色立绘'>"
            "<img src='//cdn.test/p2.webp'>"
            "<img src='https://cdn.test/thumb.jpg!/fw/50?v=1'>"  # 萌站缩略图后缀 → 还原原图
            "<img src='https://cdn.test/p1.jpg'>"  # 重复 URL 去重
            + "正文填充" * 100 +
            "</body></html>"
        ),
    }
    import httpx as _hx
    orig = _hx.Client
    _hx.Client = _FakePageClient
    try:
        text, imgs = ws.fetch_page_text_and_images("https://wiki.test/char")
    finally:
        _hx.Client = orig
    assert "正文填充" in text
    assert [i["full_url"] for i in imgs] == [
        "https://cdn.test/p1.jpg", "https://cdn.test/p2.webp", "https://cdn.test/thumb.jpg",
    ]
    # 页面本身用 `!/fw/N` 语法 → thumb_url 加回 300px 轻缩略；thumb.jpg!/fw/50 去重后
    # 与 p1/p2 是同一原图集，原 `!/fw/50` 条目还原出 thumb.jpg 并生成轻缩略。
    assert [i["thumb_url"] for i in imgs] == [
        "https://cdn.test/p1.jpg", "https://cdn.test/p2.webp", "https://cdn.test/thumb.jpg!/fw/300",
    ]
    assert imgs[0]["title"] == "角色立绘"
    assert imgs[0]["source_url"] == "https://wiki.test/char"


# ── 萌娘百科站内搜索源（2026-09-11 晚，修「长尾角色名被 bing 放松」） ──

def _moegirl_json(titles, descs, urls):
    import json as _json
    import html as _html
    return _html.escape(_json.dumps(["q", titles, descs, urls], ensure_ascii=False), quote=False)


def test_moegirl_parse_opensearch(monkeypatch):
    """萌站 opensearch 响应解析为 [{title, snippet, url}]。"""
    import httpx

    class FakeResponse:
        text = _moegirl_json(["月见八千代", "超时空辉夜姬！"], ["", ""],
                             ["https://zh.moegirl.org.cn/A", "https://zh.moegirl.org.cn/B"])

        def raise_for_status(self):
            pass

        def json(self):
            import json as _json
            import html as _html
            return _json.loads(_html.unescape(self.text))

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers=None, params=None):
            assert params["action"] == "opensearch"
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    adapter = ws.get_adapter("moegirl")
    assert adapter is not None
    results = adapter.search("月见八千代", max_results=6, proxy="")
    assert [r["title"] for r in results] == ["月见八千代", "超时空辉夜姬！"]
    assert results[0]["url"] == "https://zh.moegirl.org.cn/A"


def test_moegirl_空结果与异常返回空(monkeypatch):
    import httpx

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers=None, params=None):
            raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "Client", FakeClient)
    adapter = ws.get_adapter("moegirl")
    assert adapter is not None
    assert adapter.search("月见八千代", proxy="") == []
    assert adapter.search("", proxy="") == []


def test_web_search_链上bing垃圾回落到moegirl(monkeypatch):
    """bing-cn 整批被过滤判空（放松查询）→ 自动链回落 moegirl 命中实体词条。"""
    import httpx

    class FakeResponse:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

        def json(self):
            import json as _json
            import html as _html
            return _json.loads(_html.unescape(self.text))

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers=None, params=None):
            if "cn.bing.com" in url:
                # 放松查询：返回「月（汉语文字）」词典页（与 query 零相交 → 过滤判空）
                return FakeResponse("<h2><a href='https://baike.baidu.com/item/x'>月（汉语文字）_百度百科</a></h2>"
                                    "<p class='b_lineclamp'>月的笔画与释义</p>")
            # moegirl opensearch
            return FakeResponse(_moegirl_json(
                ["月见八千代"], [""], ["https://zh.moegirl.org.cn/%E6%9C%88%E8%A7%81%E5%85%AB%E5%8D%83%E4%BB%A3"]))

    monkeypatch.setattr(httpx, "Client", FakeClient)
    results = ws.web_search("月见八千代", max_results=4, proxy="")  # 不点名 → 自动链
    assert len(results) == 1
    assert results[0]["title"] == "月见八千代"
    assert results[0]["url"].startswith("https://zh.moegirl.org.cn/")
