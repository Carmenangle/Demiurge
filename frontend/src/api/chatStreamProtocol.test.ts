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

  it("decodes optional video protocol fields (V1.5/B1)", () => {
    expect(decodeChatStreamEvent(event("illustrate_request", {
      prompt: "p", motion: 3, actors: ["甲"],
      video_mode: "firstlast",
      first_frame_desc: "雨夜门口的暖黄灯笼",
      last_frame_desc: "三人举杯同框",
      prev_tail_desc: "上一楼层：收伞",
      last_frame_url: "data:image/png;base64,xx",
    }))).toEqual({
      type: "illustrate_request", prompt: "p", motion: 3, actors: ["甲"],
      videoMode: "firstlast",
      firstFrameDesc: "雨夜门口的暖黄灯笼",
      lastFrameDesc: "三人举杯同框",
      prevTailDesc: "上一楼层：收伞",
      lastFrameUrl: "data:image/png;base64,xx",
    });
  });

  it("keeps old backend compatibility: video fields absent → no new keys (宽松解码)", () => {
    expect(decodeChatStreamEvent(event("illustrate_request", {
      prompt: "legacy", motion: 1, actors: [],
    }))).toEqual({
      type: "illustrate_request", prompt: "legacy", motion: 1, actors: [],
    });
  });

  it("ignores invalid video_mode value (宽松解码)", () => {
    expect(decodeChatStreamEvent(event("illustrate_request", {
      prompt: "p", motion: 0, actors: [], video_mode: "bogus",
    }))).toEqual({
      type: "illustrate_request", prompt: "p", motion: 0, actors: [],
    });
  });
});
