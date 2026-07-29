import { describe, expect, it } from "vitest";

import type { ChatMessage } from "../types/chat";
import { reduceChatStreamEvent } from "./chatSessionEvents";

const base = (): ChatMessage[] => [
  { id: "bot", role: "assistant", text: "" },
  { id: "user", role: "user", text: "需求" },
];

describe("reduceChatStreamEvent", () => {
  it("merges trace and delta into the active assistant message", () => {
    let messages = reduceChatStreamEvent(base(), "bot", { type: "trace", text: "主管选择 image" });
    messages = reduceChatStreamEvent(messages, "bot", { type: "delta", text: "完成" });

    expect(messages[0].text).toBe("主管选择 image\n完成");
  });

  it("upserts media by the protocol event id", () => {
    let messages = reduceChatStreamEvent(base(), "bot", {
      type: "image", url: "local://first", id: "image-1",
    });
    messages = reduceChatStreamEvent(messages, "bot", {
      type: "image", url: "local://updated", id: "image-1",
    });

    expect(messages.filter((message) => message.id === "image-1")).toHaveLength(1);
    expect(messages.find((message) => message.id === "image-1")?.image).toBe("local://updated");
  });

  it("applies approval updates through one reducer", () => {
    const current: ChatMessage[] = [{
      id: "bot", role: "assistant", text: "", promptApproval: {
        id: "approval-1", messageId: "bot", kind: "image", status: "pending",
        prompt: "old", originalPrompt: "old",
      },
    }];

    const messages = reduceChatStreamEvent(current, "bot", {
      type: "approval",
      approval: {
        id: "approval-1", messageId: "bot", kind: "image", status: "cancelled",
        prompt: "old", originalPrompt: "old",
      },
    });

    expect(messages[0].promptApproval?.status).toBe("cancelled");
  });
});
