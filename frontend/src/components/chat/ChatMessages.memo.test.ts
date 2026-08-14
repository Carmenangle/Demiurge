import { describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "../../types/chat";
import { assistantMessagePropsEqual } from "./ChatMessages";

function props(portrait: { name: string; url: string } | null) {
  return {
    msg: { id: "assistant-1", role: "assistant", text: "可复制的剧情正文" } as ChatMessage,
    portrait,
    onSendImage: vi.fn(),
  };
}

describe("assistantMessagePropsEqual", () => {
  it("keeps an unchanged rendered message stable when portrait resolution returns an equivalent object", () => {
    const previous = props({ name: "冷倾雪", url: "/portrait.png" });
    const next = { ...previous, portrait: { name: "冷倾雪", url: "/portrait.png" } };

    expect(assistantMessagePropsEqual(previous, next)).toBe(true);
  });

  it("rerenders when the visible portrait changes", () => {
    const previous = props({ name: "冷倾雪", url: "/neutral.png" });
    const next = { ...previous, portrait: { name: "冷倾雪", url: "/angry.png" } };

    expect(assistantMessagePropsEqual(previous, next)).toBe(false);
  });
});
