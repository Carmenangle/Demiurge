import { describe, expect, it } from "vitest";

import type { ChatMessage } from "../types/chat";
import {
  dropMediaSlot, pruneUnsubmittedMediaSlots, reduceChatStreamEvent, resolveMediaSlot,
  restoreSubmittedMediaSlots,
} from "./chatSessionEvents";

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

  it("用最终清洗正文替换流式预览", () => {
    let messages = reduceChatStreamEvent(base(), "bot", { type: "delta", text: "原始流" });
    messages = reduceChatStreamEvent(messages, "bot", { type: "replace", text: "最终正文" });

    expect(messages[0].text).toBe("最终正文");
    expect(messages[0].parts).toBeUndefined();
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

  it("按事件顺序形成文本—插槽—文本 parts", () => {
    let messages = reduceChatStreamEvent(base(), "bot", { type: "delta", text: "高潮段落。" });
    messages = reduceChatStreamEvent(messages, "bot", {
      type: "illustrate_request", prompt: "1girl", motion: 0, actors: [], id: "slot-1",
    });
    messages = reduceChatStreamEvent(messages, "bot", { type: "delta", text: "\n后续段落。" });

    expect(messages[0].text).toBe("高潮段落。\n后续段落。");
    expect(messages[0].parts).toEqual([
      { type: "text", text: "高潮段落。" },
      { type: "media-slot", slotId: "slot-1", status: "pending" },
      { type: "text", text: "\n后续段落。" },
    ]);
  });

  it("按最终正文偏移插入流式插画槽", () => {
    let messages = reduceChatStreamEvent(base(), "bot", { type: "replace", text: "高潮段落。后续段落。" });
    messages = reduceChatStreamEvent(messages, "bot", {
      type: "illustrate_request", prompt: "p", motion: 0, actors: [], id: "slot-1", offset: 5,
    });

    expect(messages[0].parts).toEqual([
      { type: "text", text: "高潮段落。\n" },
      { type: "media-slot", slotId: "slot-1", status: "pending" },
      { type: "text", text: "\n后续段落。" },
    ]);
  });

  it("插画槽位总在高潮画面文段的下一行", () => {
    let messages = reduceChatStreamEvent(base(), "bot", {
      type: "replace", text: "高潮画面。\n\n后续段落。",
    });
    messages = reduceChatStreamEvent(messages, "bot", {
      type: "illustrate_request", prompt: "p", motion: 0, actors: [], id: "slot-1", offset: 5,
    });

    expect(messages[0].parts).toEqual([
      { type: "text", text: "高潮画面。\n" },
      { type: "media-slot", slotId: "slot-1", status: "pending" },
      { type: "text", text: "\n\n后续段落。" },
    ]);
  });

  it("重复插画事件遇到同slot已完成图片时不得追加第二个pending槽", () => {
    const current: ChatMessage[] = [{
      id: "bot", role: "assistant", text: "正文", parts: [
        { type: "text", text: "正文" },
        { type: "image", url: "local://done", slotId: "slot-1", status: "ready" },
      ],
    }];
    const next = reduceChatStreamEvent(current, "bot", {
      type: "illustrate_request", prompt: "p", motion: 0, actors: [], id: "slot-1",
    });
    expect(next).toEqual(current);
  });

  it("图片完成后原位替换，失败时删除slot并保留正文", () => {
    const current: ChatMessage[] = [{
      id: "bot", role: "assistant", text: "前文后文", parts: [
        { type: "text", text: "前文" },
        { type: "media-slot", slotId: "slot-1", status: "pending" },
        { type: "text", text: "后文" },
      ],
    }];
    const ready = resolveMediaSlot(current, "bot", "slot-1", "local://image", "image");
    expect(ready[0].parts?.[1]).toEqual({
      type: "image", url: "local://image", slotId: "slot-1", status: "ready",
    });
    const failed = dropMediaSlot(current, "bot", "slot-1");
    expect(failed[0].parts).toEqual([{ type: "text", text: "前文后文" }]);
  });

  it("重新生图按相同slot替换已有图片且不追加消息", () => {
    const current: ChatMessage[] = [{
      id: "bot", role: "assistant", text: "前文后文", parts: [
        { type: "text", text: "前文" },
        { type: "image", url: "local://old", slotId: "slot-1", status: "ready" },
        { type: "text", text: "后文" },
      ],
    }];

    const ready = resolveMediaSlot(current, "bot", "slot-1", "local://new", "image");

    expect(ready).toHaveLength(1);
    expect(ready[0].parts?.[1]).toEqual({
      type: "image", url: "local://new", slotId: "slot-1", status: "ready",
    });
  });

  it("仅有失败slot时移除parts", () => {
    const current: ChatMessage[] = [{
      id: "bot", role: "assistant", text: "正文", parts: [
        { type: "media-slot", slotId: "slot-1", status: "pending" },
      ],
    }];

    expect(dropMediaSlot(current, "bot", "slot-1")[0].parts).toBeUndefined();
  });

  it("刷新恢复时先用本地任务补回prompt_id，再删除真正的预提交孤儿槽", () => {
    const current: ChatMessage[] = [{
      id: "bot", role: "assistant", text: "前文后文", parts: [
        { type: "text", text: "前文" },
        { type: "media-slot", slotId: "orphan", status: "pending" },
        { type: "text", text: "后文" },
        { type: "media-slot", slotId: "submitted", status: "pending", promptId: "prompt-1" },
      ],
    }];

    const restored = restoreSubmittedMediaSlots(current, [{
      prompt_id: "prompt-local", createdAt: 1,
      target: { messageId: "bot", slotId: "orphan", background: true },
    }]);
    const result = pruneUnsubmittedMediaSlots(restored);

    expect(result.removed).toEqual([]);
    expect(result.messages[0].parts).toEqual([
      { type: "text", text: "前文" },
      { type: "media-slot", slotId: "orphan", status: "pending", promptId: "prompt-local" },
      { type: "text", text: "后文" },
      { type: "media-slot", slotId: "submitted", status: "pending", promptId: "prompt-1" },
    ]);
  });
});
