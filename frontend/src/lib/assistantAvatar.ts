import type { ChatMessage } from "../types/chat";

export type AssistantAvatarState = "default" | "listening" | "thinking" | "complete";

export function assistantAvatarState(message: ChatMessage, streaming: boolean): AssistantAvatarState {
  if (!streaming) return "complete";
  const hasStartedReply = !!message.thinking
    || !!message.text
    || !!message.parts?.some((part) => part.type === "text" && part.text);
  return hasStartedReply ? "thinking" : "listening";
}
