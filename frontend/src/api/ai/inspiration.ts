// 联网灵感（原 api/ai.ts 灵感段，2026-10-24 按资源域拆分）：/find 指令的搜索源抓取 + 图片代理。
import { apiPost, apiUrl } from "../client";
import type { StreamInspirationCard } from "../chatStreamProtocol";
import { chatBody, type Chat } from "./wire";

// 灵感卡：联网搜主题 → 整理成「标题+内容」中文总结
export type Inspiration = StreamInspirationCard;

// 联网找灵感：搜索源抓取 + 对话模型整理成「标题+内容」中文总结。/find 指令用。
// searchProvider 空 = 后端自动回落链（bing-cn → ddg）。见 lib/searchSource。
export function fetchInspiration(
  query: string,
  chat: Chat,
  proxyUrl = "",
  searchProvider = "",
) {
  return apiPost<Inspiration>("/ai/inspiration", {
    query,
    ...chatBody(chat),
    proxy_url: proxyUrl,
    search_provider: searchProvider,
  });
}

// 记录用户勾选的图片 URL 到灵感卡消息（M1.2 选中持久化）
export function selectInspiration(threadId: string, messageId: string, urls: string[]) {
  return apiPost<{ ok: boolean; selected: string[] }>("/ai/inspiration/select", {
    thread_id: threadId,
    message_id: messageId,
    urls,
  });
}

// 外网图片代理 URL（M1.2：缩略图走后端中转，防浏览器直连外网图床被墙/防盗链）
export function proxyImageUrl(url: string, proxy = "") {
  return apiUrl(`/ai/image-proxy?url=${encodeURIComponent(url)}&proxy=${encodeURIComponent(proxy)}`);
}
