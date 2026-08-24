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
