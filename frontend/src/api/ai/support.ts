// 右下角 AI 客服（原 api/ai.ts 客服段，2026-10-24 按资源域拆分）：
// 检索（系统库 + 指定仓库库）流式回答。返回中止函数。
import {
  decodeChatStreamEvent,
} from "../chatStreamProtocol";
import { openSSE } from "../sse";
import { chatBody, type Embed } from "./wire";

export function supportStream(
  message: string,
  repoId: string,
  chat: Embed,
  embed: Embed,
  onDelta: (text: string) => void,
  onDone: (err?: string) => void,
): () => void {
  return openSSE("/ai/support", {
    message, repo_id: repoId,
    ...chatBody(chat),
    embed_base_url: embed.baseUrl, embed_api_key: embed.apiKey, embed_model: embed.modelName,
  }, (obj) => {
    const event = decodeChatStreamEvent(obj);
    if (event.type === "delta") onDelta(event.text);
    else if (event.type === "error") throw new Error(event.message);
  }, onDone);
}
