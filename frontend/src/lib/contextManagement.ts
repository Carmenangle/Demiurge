import type { ChatMessage } from "../types/chat";

export const CONTEXT_REMINDER_TOKEN_STEP = 12_000;
export const CHAT_INPUT_HEIGHT_KEY = "laf_chat_input_height";
export const DEFAULT_CHAT_INPUT_HEIGHT = 100;
export const MIN_CHAT_INPUT_HEIGHT = 72;
export const MAX_CHAT_INPUT_HEIGHT = 360;

export function estimateTextTokens(text: string): number {
  const cjk = (text.match(/[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]/g) || []).length;
  const otherChars = text
    .replace(/[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]/g, "")
    .replace(/\s/g, "").length;
  return cjk + Math.ceil(otherChars / 4);
}

export function contextTokenEstimate(messages: Pick<ChatMessage, "role" | "text" | "parts" | "image">[]) {
  return messages.reduce((total, message) => {
    const text = (message.text || "").trim();
    const hasUserImage = message.role === "user"
      && !!message.parts?.some((part) => part.type === "image");
    if (!text && !hasUserImage) return total;
    return total + estimateTextTokens(text) + 4;
  }, 0);
}

export function nextContextReminderBucket(
  tokenCount: number,
  lastRemindedBucket: number,
  step = CONTEXT_REMINDER_TOKEN_STEP,
): number | null {
  const bucket = Math.floor(tokenCount / step);
  return bucket > 0 && bucket > lastRemindedBucket ? bucket : null;
}

export function clampChatInputHeight(height: number): number {
  if (!Number.isFinite(height)) return DEFAULT_CHAT_INPUT_HEIGHT;
  return Math.min(MAX_CHAT_INPUT_HEIGHT, Math.max(MIN_CHAT_INPUT_HEIGHT, Math.round(height)));
}

export function loadChatInputHeight(storage: Pick<Storage, "getItem">): number {
  const value = Number(storage.getItem(CHAT_INPUT_HEIGHT_KEY));
  return value > 0 ? clampChatInputHeight(value) : DEFAULT_CHAT_INPUT_HEIGHT;
}

export function saveChatInputHeight(storage: Pick<Storage, "setItem">, height: number) {
  storage.setItem(CHAT_INPUT_HEIGHT_KEY, String(clampChatInputHeight(height)));
}

export function clampSelectionScroll(
  previous: number,
  requested: number,
  elapsedMs: number,
): number {
  const elapsed = Math.min(32, Math.max(0, elapsedMs));
  const allowed = Math.max(6, elapsed * 0.45);
  const delta = requested - previous;
  if (Math.abs(delta) <= allowed) return requested;
  return previous + Math.sign(delta) * allowed;
}

export function compactedChatMessage(
  message: Pick<ChatMessage, "id" | "text"> & Partial<Pick<ChatMessage, "image">>,
): ChatMessage {
  return {
    id: message.id,
    role: "assistant",
    text: message.text,
    ...(message.image ? { image: message.image } : {}),
  };
}

export function recoverCompactedSummaryImage(
  messages: ChatMessage[],
  history: { role: string; content: string; images?: string[] }[],
): ChatMessage[] {
  if (
    messages.length !== 1
    || !messages[0].text.startsWith("【历史摘要】")
    || !!messages[0].image
    || !!messages[0].parts?.some((part) => part.type === "image")
  ) return messages;
  const source = [...history].reverse().find((turn) =>
    turn.role === "assistant"
    && turn.content.startsWith("【历史摘要】")
    && !!turn.images?.length,
  );
  const images = source?.images || [];
  const image = images[images.length - 1];
  return image ? [{ ...messages[0], image }] : messages;
}
