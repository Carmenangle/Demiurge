"""灵感搜索的深模块：联网搜 → 对话模型提炼英文提示词 → 出 {prompt, tags, sources}。

此前 /inspiration 路由与 image_agent 的 search_inspiration 工具各写一遍同样的
「DDG 搜索 + 提炼 system + re.split 切标签」，本模块收成一处，两个调用方各自适配
（路由 → JSON；工具 → 灵感卡 + 快照）。持久化不在这里（见 generation_store）。
"""
import re

from app.services import llm as _llm
from app.services import web_search as ws

_SYSTEM = (
    "你是 AI 绘画灵感助手。用户想找某类参考（如服装、发型、画风）。下面给你若干联网搜索到的"
    "网页标题与摘要。请据此提炼出可直接用于 Stable Diffusion / ComfyUI 的英文正向提示词：\n"
    "- 输出逗号分隔的英文标签/短语，覆盖该主题的关键视觉特征（款式、材质、颜色、细节、风格）。\n"
    "- 只提炼与用户主题相关的视觉描述，忽略广告、店铺名、无关内容。\n"
    "- 12~24 个标签为宜。只输出提示词本身，不要解释、不要编号、不要换行、不要引号。"
)


class NoResults(Exception):
    """联网搜索无结果（网络/搜索源不可用）。"""


def search_and_refine(query: str, base_url: str, api_key: str, model: str,
                      proxy: str = "") -> dict:
    """返回 {query, prompt, tags[], sources[]}。无搜索结果抛 NoResults；模型错误由 llm 抛。"""
    results = ws.web_search(query, max_results=6, proxy=proxy)
    if not results:
        raise NoResults("联网搜索无结果（网络或搜索源不可用）")
    corpus = "\n".join(f"- {r['title']}：{r['snippet']}" for r in results if r.get("title"))
    user = f"用户想找的灵感主题：{query}\n\n联网搜索到的参考：\n{corpus}"
    prompt = _llm.chat(base_url, api_key, model, _SYSTEM, user, temperature=0.5).strip()
    tags = [t.strip() for t in re.split(r"[,，;；\n]+", prompt) if t.strip()]
    sources = [{"title": r["title"], "url": r["url"]} for r in results[:5] if r.get("title")]
    return {"query": query, "prompt": prompt, "tags": tags, "sources": sources}
