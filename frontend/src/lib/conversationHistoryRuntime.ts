import type { ChatMessage } from "../types/chat";

export function resolveInitialHistory(
  remoteSnapshot: ChatMessage[] | null,
  cachedMessages: ChatMessage[],
): ChatMessage[] {
  return remoteSnapshot === null ? cachedMessages : remoteSnapshot;
}

export interface ConversationCheckpoint {
  id: string;
  label: string;
  createdAt: number;
  messages: ChatMessage[];
}

type HistoryAdapter = {
  current: () => ChatMessage[];
  publish: (messages: ChatMessage[]) => void;
  persist: (messages: ChatMessage[]) => void;
};

export class ConversationHistoryRuntime {
  private adapter: HistoryAdapter | null = null;

  constructor(
    private readonly threadId: string,
    private readonly storage: Pick<Storage, "getItem" | "setItem">,
  ) {}

  bind(adapter: HistoryAdapter): void {
    this.adapter = adapter;
  }

  replace(messages: ChatMessage[], persist = true): void {
    if (!this.adapter) throw new Error("conversation history runtime is not bound");
    this.adapter.publish(messages);
    if (persist) this.adapter.persist(messages);
  }

  deleteMessage(messageId: string): ChatMessage[] {
    const next = this.requireAdapter().current().filter((message) => message.id !== messageId);
    this.replace(next);
    return next;
  }

  messagesThrough(messageId: string): ChatMessage[] {
    const messages = this.requireAdapter().current();
    const index = messages.findIndex((message) => message.id === messageId);
    return index < 0 ? messages : messages.slice(0, index + 1);
  }

  loadCheckpoints(): ConversationCheckpoint[] {
    try {
      const parsed = JSON.parse(this.storage.getItem(this.checkpointKey()) || "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  createCheckpoint(messageId: string): ConversationCheckpoint[] {
    const messages = this.messagesThrough(messageId);
    if (!messages.some((message) => message.id === messageId)) return this.loadCheckpoints();
    return this.writeCheckpoints([{
      id: crypto.randomUUID(),
      label: new Date().toLocaleString(),
      createdAt: Date.now(),
      messages,
    }, ...this.loadCheckpoints()]);
  }

  restoreCheckpoint(checkpointId: string): boolean {
    const checkpoint = this.loadCheckpoints().find((item) => item.id === checkpointId);
    if (!checkpoint) return false;
    this.replace(checkpoint.messages);
    return true;
  }

  deleteCheckpoint(checkpointId: string): ConversationCheckpoint[] {
    return this.writeCheckpoints(
      this.loadCheckpoints().filter((checkpoint) => checkpoint.id !== checkpointId),
    );
  }

  private requireAdapter(): HistoryAdapter {
    if (!this.adapter) throw new Error("conversation history runtime is not bound");
    return this.adapter;
  }

  private checkpointKey(): string {
    return `laf_ckpt_${this.threadId}`;
  }

  private writeCheckpoints(checkpoints: ConversationCheckpoint[]): ConversationCheckpoint[] {
    try { this.storage.setItem(this.checkpointKey(), JSON.stringify(checkpoints)); } catch { /* ignore quota */ }
    return checkpoints;
  }
}
