// 剧情媒体槽兜底（2026-11-11）：图已在 ComfyUI/后端快照里 ready，但前端轮询中断、
// 切标签页或另一标签页先完成时，本地槽会一直停在 pending（界面显示「插画生成中…」）。
// 只要还有 pending 媒体槽，就定期读后端快照，用同 messageId+slotId 的 ready 结果
// 替换本地 pending 槽；不需要刷新页面。
import { useEffect, type Dispatch, type SetStateAction } from "react";
import { fetchSnapshot } from "../api/ai";
import { resolveMediaSlot } from "./chatSessionEvents";
import type { ChatMessage } from "../types/chat";

export interface MediaSlotReconcileDeps {
  messages: ChatMessage[];
  threadId: string;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
}

export function hasPendingMediaSlot(messages: readonly ChatMessage[]): boolean {
  return messages.some((message) =>
    (message.parts || []).some((part) => part.type === "media-slot" && part.status === "pending"));
}

export function reconcileMediaSlots(
  current: ChatMessage[], snapshot: readonly ChatMessage[],
): ChatMessage[] {
  const ready = new Map<string, { url: string; mediaType: "image" | "video" | "audio" }>();
  for (const item of snapshot) {
    for (const part of item.parts || []) {
      const raw = part as {
        type?: string; url?: string; slotId?: string; status?: string;
      };
      if (raw.type === "media-slot" || raw.status !== "ready" || !raw.url || !raw.slotId) {
        continue;
      }
      ready.set(`${item.id}\u0000${raw.slotId}`, {
        url: raw.url,
        mediaType: raw.type === "video" ? "video" : raw.type === "audio" ? "audio" : "image",
      });
    }
  }
  if (!ready.size) return current;
  let next = current;
  for (const message of current) {
    for (const part of message.parts || []) {
      if (part.type !== "media-slot" || part.status !== "pending" || !part.slotId) continue;
      const hit = ready.get(`${message.id}\u0000${part.slotId}`);
      if (!hit) continue;
      next = resolveMediaSlot(next, message.id, part.slotId, hit.url, hit.mediaType);
    }
  }
  return next;
}

export function useMediaSlotReconcile({
  messages, threadId, setMessages,
}: MediaSlotReconcileDeps): void {
  const pending = hasPendingMediaSlot(messages);
  useEffect(() => {
    if (!pending || threadId === "home") return;
    let alive = true;
    const sync = async () => {
      try {
        const snap = await fetchSnapshot(threadId);
        if (!alive) return;
        setMessages((current) =>
          reconcileMediaSlots(current, (snap.items || []) as ChatMessage[]));
      } catch { /* 后端未就绪时等下一轮 */ }
    };
    void sync();
    const timer = window.setInterval(() => { void sync(); }, 6000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [pending, threadId]);
}
