import { describe, expect, it } from "vitest";

import type { ChatMessage } from "../types/chat";
import {
  appendAudioSlot, appendImageSlot, appendTransitionSlot, bindMediaSlotPrompt, dropMediaSlot, markMediaSlotFailed, pruneUnsubmittedMediaSlots, reduceChatStreamEvent, resetMediaSlotForRetry, resolveMediaSlot,
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
  it("thinking 增量进思考面板字段，正文不受影响", () => {
    let messages = reduceChatStreamEvent(base(), "bot", { type: "delta", text: "正文开头" });
    messages = reduceChatStreamEvent(messages, "bot", { type: "thinking", text: "先推演" });
    messages = reduceChatStreamEvent(messages, "bot", { type: "thinking", text: "再落笔" });

    expect(messages[0].thinking).toBe("先推演再落笔");
    expect(messages[0].text).toBe("正文开头");
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

  it("illustrate_request 携带 lastFrameDesc → 槽位存储并随视频完成保留（V1.5/B2 尾帧反查数据源）", () => {
    let messages = reduceChatStreamEvent(base(), "bot", {
      type: "illustrate_request", prompt: "p", motion: 3, actors: [], id: "slot-1",
      videoMode: "firstlast", lastFrameDesc: "三人举杯同框，温情对视",
    });
    const slot = messages[0].parts?.[0];
    expect(slot).toMatchObject({
      type: "media-slot", slotId: "slot-1", status: "pending",
      lastFrameDesc: "三人举杯同框，温情对视",
    });

    // 视频完成后槽位升级为 video，lastFrameDesc 保留（供下一楼层 resolvePrevTailDesc 反查）
    const ready = resolveMediaSlot(messages, "bot", "slot-1", "local://v.mp4", "video");
    expect(ready[0].parts?.[0]).toEqual({
      type: "video", url: "local://v.mp4", slotId: "slot-1", status: "ready",
      lastFrameDesc: "三人举杯同框，温情对视",
    });
  });

  it("illustrate_request 携带 lastFrameUrl → 槽位存储并随完成保留（V1.5/F3 尾帧图地址反查数据源）", () => {
    let messages = reduceChatStreamEvent(base(), "bot", {
      type: "illustrate_request", prompt: "p", motion: 3, actors: [], id: "slot-1",
      videoMode: "firstlast", lastFrameDesc: "三人举杯同框", lastFrameUrl: "local://last-frame.png",
    });
    const slot = messages[0].parts?.[0];
    expect(slot).toMatchObject({
      type: "media-slot", slotId: "slot-1", status: "pending",
      lastFrameDesc: "三人举杯同框",
      lastFrameUrl: "local://last-frame.png",
    });

    // 视频完成后 lastFrameUrl 保留（供下一楼层转场视频 image 输入反查）
    const ready = resolveMediaSlot(messages, "bot", "slot-1", "local://v.mp4", "video");
    expect(ready[0].parts?.[0]).toEqual({
      type: "video", url: "local://v.mp4", slotId: "slot-1", status: "ready",
      lastFrameDesc: "三人举杯同框",
      lastFrameUrl: "local://last-frame.png",
    });
  });

  it("illustrate_request 携带 videoPrompt → 槽位存储并随完成保留（V1.5 默认开放测试点）", () => {
    let messages = reduceChatStreamEvent(base(), "bot", {
      type: "illustrate_request", prompt: "p", motion: 3, actors: [], id: "slot-1",
      videoPrompt: "使用视频模型生成，15 seconds。\n\n[动作]：挥拳；丝滑运镜。",
    });
    expect(messages[0].parts?.[0]).toMatchObject({
      type: "media-slot", slotId: "slot-1", status: "pending",
      videoPrompt: "使用视频模型生成，15 seconds。\n\n[动作]：挥拳；丝滑运镜。",
    });
    // 完成后保留（无视频模板/模型也能在槽上看到提示词，供测试核对）
    const ready = resolveMediaSlot(messages, "bot", "slot-1", "local://v.mp4", "video");
    expect(ready[0].parts?.[0]).toEqual({
      type: "video", url: "local://v.mp4", slotId: "slot-1", status: "ready",
      videoPrompt: "使用视频模型生成，15 seconds。\n\n[动作]：挥拳；丝滑运镜。",
    });
  });

  it("illustrate_request 携带 videoParams → 槽位存储并随完成保留（V1.5 参数上传核对）", () => {
    let messages = reduceChatStreamEvent(base(), "bot", {
      type: "illustrate_request", prompt: "p", motion: 3, actors: [], id: "slot-1",
      videoParams: {
        mode: "climax", model: "", size: "1280x720", endpoint: "",
        images: [], reference_binding: {}, warnings: ["缺高潮参考图"],
      },
    });
    expect(messages[0].parts?.[0]).toMatchObject({
      type: "media-slot", slotId: "slot-1", status: "pending",
      videoParams: { mode: "climax", size: "1280x720", warnings: ["缺高潮参考图"] },
    });
    const ready = resolveMediaSlot(messages, "bot", "slot-1", "local://v.mp4", "video");
    expect(ready[0].parts?.[0]).toEqual({
      type: "video", url: "local://v.mp4", slotId: "slot-1", status: "ready",
      videoParams: {
        mode: "climax", model: "", size: "1280x720", endpoint: "",
        images: [], reference_binding: {}, warnings: ["缺高潮参考图"],
      },
    });
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

  it("已有 parts 时插画槽仍按 offset 定位插入而非 append 末尾（多插画）", () => {
    // 2026-09-01：旧逻辑 `!message.parts` 时才对 text 切分，parts 已存在（第一个槽
    // 已插入）时第二个槽恒 append 末尾 → 多插画时后续图全落到消息尾。改为按 text
    // 对应 offset 位置插入，保留已有槽与顺序。
    const current: ChatMessage[] = [{
      id: "bot", role: "assistant", text: "开篇。高潮画面句。收束。", parts: [
        { type: "text", text: "开篇。" },
        { type: "media-slot", slotId: "slot-1", status: "pending" },
        { type: "text", text: "高潮画面句。收束。" },
      ],
    }];
    const next = reduceChatStreamEvent(current, "bot", {
      type: "illustrate_request", prompt: "p", motion: 0, actors: [], id: "slot-2", offset: 9,
    });

    // offset 9 = 「开篇。」(3) + 「高潮画面句。」(6) → 槽2 插在高潮句后、收束句前
    expect(next[0].parts).toEqual([
      { type: "text", text: "开篇。" },
      { type: "media-slot", slotId: "slot-1", status: "pending" },
      { type: "text", text: "高潮画面句。\n" },
      { type: "media-slot", slotId: "slot-2", status: "pending" },
      { type: "text", text: "\n收束。" },
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

  it("音频槽完成后保留角色名/序号/总数（分条气泡标签不丢）", () => {
    const current: ChatMessage[] = [{
      id: "bot", role: "assistant", text: "正文", parts: [
        { type: "media-slot", slotId: "audio-1", status: "pending", kind: "audio", speaker: "阿尼玛", seq: 2, total: 3 },
      ],
    }];
    const ready = resolveMediaSlot(current, "bot", "audio-1", "local://a.wav", "audio");
    expect(ready[0].parts?.[0]).toEqual({
      type: "audio", url: "local://a.wav", slotId: "audio-1", status: "ready",
      kind: "audio", speaker: "阿尼玛", seq: 2, total: 3,
    });
    // 普通图片/视频槽不受影响：不注入音频元数据
    const img = resolveMediaSlot([{
      id: "bot", role: "assistant", text: "正文", parts: [
        { type: "media-slot", slotId: "slot-1", status: "pending" },
      ],
    }], "bot", "slot-1", "local://a.png", "image");
    expect(img[0].parts?.[0]).toEqual({
      type: "image", url: "local://a.png", slotId: "slot-1", status: "ready",
    });
  });

  it("音频槽追加时保留正文（纯文本消息先转 text part）", () => {
    const current: ChatMessage[] = [{
      id: "bot", role: "assistant", text: "她低声道：「我认输。」",
    }];
    const next = appendAudioSlot(current, "bot", "audio-0", "虞妙玥", 1, 2);
    expect(next[0].parts).toEqual([
      { type: "text", text: "她低声道：「我认输。」" },
      { type: "media-slot", slotId: "audio-0", status: "pending",
        kind: "audio", speaker: "虞妙玥", seq: 1, total: 2 },
    ]);
    // 幂等：同 slot 不重复追加
    const again = appendAudioSlot(next, "bot", "audio-0", "虞妙玥", 1, 2);
    expect(again[0].parts).toHaveLength(2);
    // 已有 parts 的消息（如同轮已插画）在末尾追加，不动已有内容
    const withParts = appendAudioSlot([{
      id: "bot", role: "assistant", text: "正文",
      parts: [{ type: "image", url: "local://img", slotId: "img-1", status: "ready" }],
    }], "bot", "audio-0", "虞妙玥", 1, 2);
    expect(withParts[0].parts).toHaveLength(2);
    expect(withParts[0].parts?.[0]).toEqual({
      type: "image", url: "local://img", slotId: "img-1", status: "ready",
    });
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

describe("appendTransitionSlot（W3 转场视频槽）", () => {
  it("在同消息末尾追加 kind=video 的 pending 槽，保留正片槽独立 slotId", () => {
    const current: ChatMessage[] = [{
      id: "bot", role: "assistant", text: "", parts: [
        { type: "media-slot", slotId: "main", status: "pending" },
      ],
    }];

    const transitionParams = {
      mode: "transition" as const, model: "", size: "1280x720", endpoint: "",
      images: [], reference_binding: {}, warnings: [],
    };
    const withTransition = appendTransitionSlot(
      current, "bot", "main:transition", "转场分镜提示词", transitionParams,
    );

    expect(withTransition[0].parts).toHaveLength(2);
    expect(withTransition[0].parts?.[1]).toEqual({
      type: "media-slot", slotId: "main:transition", status: "pending",
      kind: "video", videoPrompt: "转场分镜提示词", videoParams: transitionParams,
    });
  });

  it("同 slotId 重复追加幂等（不去重外不重复插入）", () => {
    const current: ChatMessage[] = [{ id: "bot", role: "assistant", text: "" }];
    const once = appendTransitionSlot(current, "bot", "main:transition", "转场");
    const twice = appendTransitionSlot(once, "bot", "main:transition", "转场");
    expect(twice[0].parts).toHaveLength(1);
  });
});

describe("appendImageSlot（首尾帧副槽）", () => {
  it("在同消息末尾追加 kind=image 的 pending 槽，提示词随槽保留", () => {
    const current: ChatMessage[] = [{
      id: "bot", role: "assistant", text: "", parts: [
        { type: "media-slot", slotId: "main", status: "pending" },
      ],
    }];
    const withLast = appendImageSlot(current, "bot", "main:last", "尾帧画面描述");
    expect(withLast[0].parts).toHaveLength(2);
    expect(withLast[0].parts?.[1]).toEqual({
      type: "media-slot", slotId: "main:last", status: "pending",
      kind: "image", videoPrompt: "尾帧画面描述",
    });
  });

  it("同 slotId 重复追加幂等", () => {
    const current: ChatMessage[] = [{ id: "bot", role: "assistant", text: "" }];
    const once = appendImageSlot(current, "bot", "main:last", "尾帧");
    const twice = appendImageSlot(once, "bot", "main:last", "尾帧");
    expect(twice[0].parts).toHaveLength(1);
  });

  it("纯文本消息先转 text part 保留正文再追加槽", () => {
    const current: ChatMessage[] = [{ id: "bot", role: "assistant", text: "正文" }];
    const withSlot = appendImageSlot(current, "bot", "main:last");
    expect(withSlot[0].parts).toEqual([
      { type: "text", text: "正文" },
      { type: "media-slot", slotId: "main:last", status: "pending", kind: "image" },
    ]);
  });
});

describe("markMediaSlotFailed（2026-08-29 失败槽保留+重新生成）", () => {
  it("标记失败并保留重试快照与正文", () => {
    const messages: ChatMessage[] = [{ id: "bot", role: "assistant", text: "", parts: [
      { type: "text", text: "正文" },
      { type: "media-slot", slotId: "s1", status: "pending", kind: "image" },
    ] }];
    const next = markMediaSlotFailed(messages, "bot", "s1", "frame_gen", "首帧生图失败：队列未运转", ["p", 0]);
    const parts = next[0].parts!;
    expect(parts[0].type).toBe("text");           // 正文保留
    const slot = parts[1];
    expect(slot.status).toBe("failed");
    expect(slot.error).toBe("[frame_gen] 首帧生图失败：队列未运转");
    expect((slot as { retryArgs?: unknown[] }).retryArgs).toEqual(["p", 0]);
  });

  it("槽不存在时原样返回", () => {
    const messages: ChatMessage[] = [{ id: "bot", role: "assistant", text: "正文" }];
    const next = markMediaSlotFailed(messages, "bot", "ghost", "stage", "err");
    expect(next).toEqual(messages);
  });
});

describe("resetMediaSlotForRetry（2026-08-30 重试即时反馈）", () => {
  it("失败槽重试时回 pending 并清错误、保留快照", () => {
    const failed = markMediaSlotFailed(
      [{ id: "bot", role: "assistant", text: "", parts: [
        { type: "text", text: "正文" },
        { type: "media-slot", slotId: "s1", status: "pending", kind: "image" },
      ] }] as ChatMessage[],
      "bot", "s1", "frame_gen", "首帧生图失败：ComfyUI 未运行，请先启动", ["p", 0],
    );
    const next = resetMediaSlotForRetry(failed, "bot", "s1");
    const slot = next[0].parts![1];
    expect(slot.status).toBe("pending");
    expect(slot.error).toBeUndefined();
    expect((slot as { retryArgs?: unknown[] }).retryArgs).toEqual(["p", 0]);  // 快照保留供再次失败
  });

  it("非 failed 槽与不存在槽不受影响", () => {
    const messages: ChatMessage[] = [{ id: "bot", role: "assistant", text: "", parts: [
      { type: "media-slot", slotId: "s1", status: "ready", kind: "image" },
      { type: "media-slot", slotId: "s2", status: "pending", kind: "image" },
    ] }];
    expect(resetMediaSlotForRetry(messages, "bot", "s1")).toEqual(messages);
    expect(resetMediaSlotForRetry(messages, "bot", "ghost")).toEqual(messages);
  });
});


describe("媒体槽自愈兜底（2026-08-31 深夜实锤：槽丢失导致生成图不插入）", () => {
  it("bindMediaSlotPrompt 在槽丢失时补建 pending 占位槽", () => {
    const messages = reduceChatStreamEvent(base(), "bot", { type: "replace", text: "正文" });
    const bound = bindMediaSlotPrompt(messages, "bot", "slot-1", "prompt-1");
    const parts = bound[0].parts || [];
    expect(parts.some((p) => p.type === "media-slot" && p.slotId === "slot-1" && p.promptId === "prompt-1")).toBe(true);
  });
  it("resolveMediaSlot 在槽丢失时直接补插生成结果", () => {
    const messages = reduceChatStreamEvent(base(), "bot", { type: "replace", text: "正文" });
    const resolved = resolveMediaSlot(messages, "bot", "slot-1", "file:///x.png", "image");
    const parts = resolved[0].parts || [];
    expect(parts.some((p) => p.type === "image" && p.slotId === "slot-1" && p.url === "file:///x.png")).toBe(true);
  });
});
