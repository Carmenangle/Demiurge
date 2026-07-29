import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../types/chat";
import {
  appendUniqueMessageIds,
  changedAssistantMessageIds,
  messageActivityVersion,
} from "./chatUnread";

const message = (id: string, role: ChatMessage["role"], text = ""): ChatMessage => ({
  id,
  role,
  text,
});

describe("chat unread agent messages", () => {
  it("tracks new assistant messages in conversation order and ignores user messages", () => {
    const previous = new Map([["assistant-old", messageActivityVersion(message("assistant-old", "assistant", "old"))]]);
    const result = changedAssistantMessageIds(previous, [
      message("user-new", "user", "prompt"),
      message("assistant-old", "assistant", "old"),
      message("assistant-first", "assistant", "first"),
      message("assistant-second", "assistant", "second"),
    ]);

    expect(result.ids).toEqual(["assistant-first", "assistant-second"]);
  });

  it("treats generated images and streamed text as assistant activity", () => {
    const original = message("assistant", "assistant", "draft");
    const previous = new Map([[original.id, messageActivityVersion(original)]]);
    const withImage = { ...original, text: "draft complete", image: "/generated/result.png" };

    expect(changedAssistantMessageIds(previous, [withImage]).ids).toEqual(["assistant"]);
  });

  it("keeps the first unread message stable while later updates arrive", () => {
    expect(appendUniqueMessageIds(["first"], ["second", "first", "third"]))
      .toEqual(["first", "second", "third"]);
  });
});
