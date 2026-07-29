import type { PromptApproval, RegenerationSnapshot, RouteChoice } from "../types/chat";

export const CHAT_STREAM_PROTOCOL = "laf-chat-stream" as const;
export const CHAT_STREAM_VERSION = 1 as const;

export interface StreamInspirationCard {
  id?: string;
  query: string;
  prompt: string;
  tags: string[];
  sources: { title: string; url: string }[];
}

export type ChatStreamEvent =
  | { type: "trace"; text: string }
  | { type: "delta"; text: string }
  | { type: "image"; url: string; id?: string; regeneration?: RegenerationSnapshot }
  | { type: "video"; url: string; id?: string }
  | { type: "inspiration"; card: StreamInspirationCard }
  | { type: "approval"; approval: PromptApproval }
  | { type: "route_choice"; choice: RouteChoice }
  | { type: "interrupted" }
  | { type: "error"; message: string };

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`对话流协议错误：${label} 不是对象`);
  }
  return value as Record<string, unknown>;
}

function requiredString(data: Record<string, unknown>, key: string): string {
  if (typeof data[key] !== "string") {
    throw new Error(`对话流协议错误：缺少字符串字段 data.${key}`);
  }
  return data[key];
}

export function decodeChatStreamEvent(value: unknown): ChatStreamEvent {
  const envelope = record(value, "事件");
  if (envelope.protocol !== CHAT_STREAM_PROTOCOL || envelope.version !== CHAT_STREAM_VERSION) {
    throw new Error(
      `不支持的对话流协议：${String(envelope.protocol || "unknown")} v${String(envelope.version ?? "?")}`,
    );
  }
  const data = record(envelope.data, "data");
  switch (envelope.type) {
    case "trace":
      return { type: "trace", text: requiredString(data, "text") };
    case "delta":
      return { type: "delta", text: requiredString(data, "text") };
    case "image":
      return {
        type: "image",
        url: requiredString(data, "url"),
        ...(typeof data.id === "string" ? { id: data.id } : {}),
        ...(data.regeneration ? { regeneration: data.regeneration as RegenerationSnapshot } : {}),
      };
    case "video":
      return {
        type: "video",
        url: requiredString(data, "url"),
        ...(typeof data.id === "string" ? { id: data.id } : {}),
      };
    case "inspiration":
      return { type: "inspiration", card: record(data.card, "data.card") as unknown as StreamInspirationCard };
    case "approval":
      return { type: "approval", approval: record(data.approval, "data.approval") as unknown as PromptApproval };
    case "route_choice":
      return { type: "route_choice", choice: record(data.choice, "data.choice") as unknown as RouteChoice };
    case "interrupted":
      return { type: "interrupted" };
    case "error":
      return { type: "error", message: requiredString(data, "message") };
    default:
      throw new Error(`不支持的对话流事件：${String(envelope.type)}`);
  }
}
