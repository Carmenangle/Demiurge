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
    "你是联网资料整理助手。用户想了解某个主题（如服装款式、发型、画风等）。"
    "下面给你若干联网搜索到的网页标题与摘要。请据此整理成一条结构化总结：\n"
    "1. 第一行只输出一个凝练的短标题（不超过 12 个字，直接概括主题，不要书名号、不要引号、不要冒号、不要句号）。\n"
    "2. 换行后，用成段中文总结该主题：结构清晰、信息完整，覆盖关键类别、特征、差异、适用场景等，"
    "供后续生成图像提示词或续写故事时参考。\n"
    "格式要求：第一行只放短标题，第二行开始放总结内容；不要输出「标题：」「总结：」这类前缀。"
)


class NoResults(Exception):
    """联网搜索无结果（网络/搜索源不可用）。"""


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
    search_provider 为搜索源名称（如 'ddg'），None 使用注册表默认源。
    """
    results = ws.web_search(query, max_results=6, proxy=proxy, provider=search_provider)
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
