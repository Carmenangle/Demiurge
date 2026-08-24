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
    expect(decodeChatStreamEvent(event("route", { route: "roleplay" }))).toEqual({ type: "route", route: "roleplay" });
    expect(decodeChatStreamEvent(event("rag_status", {
      state: "start", kind: "worldbook", count: 53,
    }))).toEqual({ type: "rag_status", state: "start", kind: "worldbook", count: 53 });
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

  it("decodes illustration scene source for prompt profiles", () => {
    const sceneSpec = {
      narrative: "高潮段", draft_prompt: "close-up", appearance: "银发、蓝眼",
      wardrobe: "红裙",
      locale: "寝殿", actors: ["爱丽丝"], rating: "nsfw", aspect_ratio: "2:3",
    };
    expect(decodeChatStreamEvent(event("illustrate_request", {
      prompt: "legacy", motion: 1, actors: ["爱丽丝"], id: "slot-1",
      scene_spec: sceneSpec, turn_id: "turn-1",
    }))).toEqual({
      type: "illustrate_request", prompt: "legacy", motion: 1,
      actors: ["爱丽丝"], id: "slot-1", sceneSpec, turnId: "turn-1",
    });
  });
});
