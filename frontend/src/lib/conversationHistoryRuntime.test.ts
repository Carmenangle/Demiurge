import { describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "../types/chat";
import { ConversationHistoryRuntime, resolveInitialHistory } from "./conversationHistoryRuntime";

function setup(initial: ChatMessage[]) {
  const values = new Map<string, string>();
  let current = initial;
  const publish = vi.fn((next: ChatMessage[]) => { current = next; });
  const persist = vi.fn();
  const runtime = new ConversationHistoryRuntime("repo-1", {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => { values.set(key, value); },
  });
  runtime.bind({ current: () => current, publish, persist });
  return { runtime, publish, persist, current: () => current };
}

const messages: ChatMessage[] = [
  { id: "u1", role: "user", text: "one" },
  { id: "a1", role: "assistant", text: "two" },
  { id: "u2", role: "user", text: "three" },
];

describe("conversation history transaction", () => {
  it("treats a successful empty backend snapshot as authoritative", () => {
    expect(resolveInitialHistory([], messages)).toEqual([]);
    expect(resolveInitialHistory(null, messages)).toEqual(messages);
  });

  it("publishes and persists deletion in one operation", () => {
    const { runtime, persist, current } = setup(messages);
    runtime.deleteMessage("a1");
    expect(current().map((message) => message.id)).toEqual(["u1", "u2"]);
    expect(persist).toHaveBeenCalledWith(current());
  });

  it("restores a checkpoint through the same persistence path", () => {
    const { runtime, persist, current } = setup(messages);
    const [checkpoint] = runtime.createCheckpoint("a1");
    runtime.deleteMessage("u1");
    persist.mockClear();
    expect(runtime.restoreCheckpoint(checkpoint.id)).toBe(true);
    expect(current().map((message) => message.id)).toEqual(["u1", "a1"]);
    expect(persist).toHaveBeenCalledOnce();
  });

  it("can replace imported state without writing it back a second time", () => {
    const { runtime, persist, current } = setup(messages);
    runtime.replace([messages[0]], false);
    expect(current()).toEqual([messages[0]]);
    expect(persist).not.toHaveBeenCalled();
  });
});
