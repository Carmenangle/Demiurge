// 模型来源三分（2026-10-30）：云端 API / 本机 Ollama / 本地模型文件。
// 纯函数只负责「切来源时该改哪些字段」，界面与迁移共用同一份判定。
import {
  OLLAMA_DEFAULT_BASE_URL, isOllamaEndpoint,
  type ChatModel, type ChatModelSource, type EmbedModel, type RemoteModelSource, type VlmModel,
} from "../stores/settings";
import type { ProxyMode } from "./modelProxy";

export const LOCAL_CHAT_PLACEHOLDER = "本地模型（待导入）";

export function newChatModel(source: ChatModelSource): ChatModel {
  const base: ChatModel = {
    id: crypto.randomUUID(),
    source,
    apiKey: "",
    baseUrl: "",
    modelName: "",
    proxyMode: "on",
    providerProfile: "openai_compatible",
  };
  if (source === "api") return { ...base, modelName: "新模型" };
  const ollamaBacked: ChatModel = {
    ...base, baseUrl: OLLAMA_DEFAULT_BASE_URL, apiKey: "ollama", proxyMode: "off",
  };
  if (source === "ollama") return ollamaBacked;
  // 本地模型 = GGUF 文件 → 导入本机 Ollama；先占位，导入后再填模型名。
  return { ...ollamaBacked, displayName: LOCAL_CHAT_PLACEHOLDER, ggufPath: "", mmprojPath: "", ollamaName: "" };
}

export function isLocalChatPlaceholder(displayName: string | undefined): boolean {
  const value = (displayName || "").trim();
  return !value || value === LOCAL_CHAT_PLACEHOLDER;
}

/** 对话本地模型导入成功：卡片的运行时端点就是本机 Ollama，模型名取导入后的名字。 */
export function applyChatLocalImport(model: ChatModel, modelName: string): Partial<ChatModel> {
  return {
    source: "local",
    baseUrl: OLLAMA_DEFAULT_BASE_URL,
    apiKey: "ollama",
    modelName,
    proxyMode: "off",
    providerProfile: "openai_compatible",
    ...(isLocalChatPlaceholder(model.displayName) ? { displayName: modelName } : {}),
  };
}

/** 视觉本地模型导入成功：自动切到 Ollama 档，避免用户再手动切页。 */
export function applyVlmLocalImport(modelName: string): Partial<VlmModel> {
  return {
    mode: "remote",
    source: "ollama",
    baseUrl: OLLAMA_DEFAULT_BASE_URL,
    apiKey: "ollama",
    modelName,
    proxyMode: "off",
  };
}

export interface RemoteSourcePatch {
  source: RemoteModelSource;
  baseUrl?: string;
  apiKey?: string;
  modelName?: string;
  proxyMode?: ProxyMode;
}

/**
 * 切换远程来源（API ↔ Ollama）。只改「切过去不会用错」的字段：
 * - 切到 Ollama：当前地址不是 Ollama ⇒ 填本机默认地址与占位 Key，清掉云端模型名；
 * - 切回 API：当前地址是 Ollama ⇒ 清空连接字段，避免把本机地址当云端 API 保存；
 * - 同来源重复点击 / 自定义端口：不动，保住用户手填的地址。
 */
export function switchRemoteSource(
  source: RemoteModelSource,
  current: { source?: RemoteModelSource; baseUrl: string; apiKey: string; modelName: string },
): RemoteSourcePatch {
  if (current.source === source) return { source };
  if (source === "ollama" && !isOllamaEndpoint(current.baseUrl)) {
    return { source, baseUrl: OLLAMA_DEFAULT_BASE_URL, apiKey: "ollama", modelName: "", proxyMode: "off" };
  }
  if (source === "api" && isOllamaEndpoint(current.baseUrl)) {
    return { source, baseUrl: "", apiKey: "", modelName: "", proxyMode: "inherit" };
  }
  return { source };
}

export function switchEmbedSource(model: EmbedModel, source: RemoteModelSource): Partial<EmbedModel> {
  return { mode: "remote", ...switchRemoteSource(source, model) };
}

export function switchVlmSource(model: VlmModel, source: RemoteModelSource): Partial<VlmModel> {
  return { mode: "remote", ...switchRemoteSource(source, model) };
}
