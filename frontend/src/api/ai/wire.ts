// AI API 共享 wire 层：三元组类型 + 请求体序列化器（原 api/ai.ts 顶部收口段，2026-10-24 按资源域拆分）。
// wire 格式序列化器（收口三元组，各调用方不再逐字段手拆）：
// - chatBody：对话端点用 base_url/api_key/model
// - ragEmbed：RAG POST 端点用 base_url/api_key/embed_model
// - sseEmbed：SSE 端点用 embed_base_url/embed_api_key/embed_model（默认模型 embedding-3）
// 两者都显式透传 remote/local 模式；本地目录不随代码发布包上传。
// ⚠ 拆分前它们是 ai.ts 私有函数；拆分后因跨域模块共用而导出——红线不变：
//   任何传对话/嵌入模型的调用必须走这三个序列化器，禁止逐字段手写（F/11 红线 4）。
import type { PresetSampling } from "../../stores/settings";

// 轮询类接口必须带超时：后端挂起时否则 await 永不返回，调用方的轮询链会静默断掉。
export const POLL_TIMEOUT_MS = 8000;

// 嵌入模型配置（设置 → 嵌入模型），单一属主。
export type Embed = {
  mode?: "remote" | "local";
  baseUrl: string;
  apiKey: string;
  modelName: string;
  modelDir?: string;
  rerankerDir?: string;
  proxyUrl?: string;
};
// 对话模型三元组配置。
export type Chat = { baseUrl: string; apiKey: string; modelName: string; proxyUrl?: string; providerProfile?: "openai_compatible" | "claude_compatible" };

// AI 搭工作流用的对话三元组（不含 proxy 字段；build 系列自行展开）。
export type ChatCfg = { baseUrl: string; apiKey: string; modelName: string };

/** 视觉模型三元组（由设置透传；后端不解析 local/remote 两态，避免口径漂移） */
export type VlmWire = { base_url: string; api_key: string; model: string; proxy: string };

export function chatBody(chat: Chat) {
  return {
    base_url: chat.baseUrl,
    api_key: chat.apiKey,
    model: chat.modelName,
    proxy: chat.proxyUrl || "",
  };
}
export function ragEmbed(embed?: Embed) {
  return {
    base_url: embed?.baseUrl || "",
    api_key: embed?.apiKey || "",
    embed_model: embed?.modelName || "",
    embed_mode: embed?.mode || "remote",
    embed_model_dir: embed?.modelDir || "",
    reranker_model_dir: embed?.rerankerDir || "",
    proxy_url: embed?.proxyUrl || "",
  };
}
export function sseEmbed(embed?: Embed) {
  return {
    embed_base_url: embed?.baseUrl || "",
    embed_api_key: embed?.apiKey || "",
    embed_model: embed?.modelName || "embedding-3",
    embed_mode: embed?.mode || "remote",
    embed_model_dir: embed?.modelDir || "",
    reranker_model_dir: embed?.rerankerDir || "",
    embed_proxy_url: embed?.proxyUrl || "",
  };
}
