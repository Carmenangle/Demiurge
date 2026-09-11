import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../types/chat";
import { assistantAvatarState } from "./assistantAvatar";

const message = (patch: Partial<ChatMessage> = {}): ChatMessage => ({
  id: "assistant-message",
  role: "assistant",
  text: "",
  ...patch,
});

describe("assistantAvatarState", () => {
  it("listens before the first response content arrives", () => {
    expect(assistantAvatarState(message(), true)).toBe("listening");
  });

  it("shows thinking while response content is streaming", () => {
    expect(assistantAvatarState(message({ text: "正在处理" }), true)).toBe("thinking");
    expect(assistantAvatarState(message({ thinking: "分析中" }), true)).toBe("thinking");
  });

  it("keeps a completed message in the complete state", () => {
    expect(assistantAvatarState(message({ text: "任务完成" }), false)).toBe("complete");
  });
});
