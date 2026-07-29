import { describe, expect, it } from "vitest";

import { decodeChatStreamEvent } from "./chatStreamProtocol";

const event = (type: string, data: Record<string, unknown>) => ({
  protocol: "laf-chat-stream",
  version: 1,
  type,
  data,
});

describe("chat stream protocol", () => {
  it("decodes every payload through the discriminant", () => {
    expect(decodeChatStreamEvent(event("delta", { text: "回答" }))).toEqual({
      type: "delta", text: "回答",
    });
    expect(decodeChatStreamEvent(event("image", {
      url: "local://image", id: "i1", regeneration: { prompt: "p" },
    }))).toEqual({
      type: "image", url: "local://image", id: "i1", regeneration: { prompt: "p" },
    });
    expect(decodeChatStreamEvent(event("interrupted", {}))).toEqual({ type: "interrupted" });
  });

  it("rejects unknown versions and event types", () => {
    expect(() => decodeChatStreamEvent({ ...event("delta", { text: "x" }), version: 2 }))
      .toThrow("不支持的对话流协议");
    expect(() => decodeChatStreamEvent(event("new_event", {})))
      .toThrow("不支持的对话流事件");
  });

  it("rejects malformed required fields", () => {
    expect(() => decodeChatStreamEvent(event("image", { id: "i1" })))
      .toThrow("data.url");
    expect(() => decodeChatStreamEvent(event("error", {})))
      .toThrow("data.message");
  });
});
