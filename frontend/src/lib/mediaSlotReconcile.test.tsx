// @vitest-environment jsdom
// 2026-11-11 用户实锤：剧情插画图早已在快照里 ready，前端槽却一直显示「插画生成中…」。
// 回归网：① 纯函数按 messageId+slotId 收敛 pending 槽 ② 快照仍是 pending/别槽不动
// ③ hook 挂载即读快照并回填、home 线程不请求。
import { act, createElement, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "../types/chat";
import {
  hasPendingMediaSlot, reconcileMediaSlots, useMediaSlotReconcile,
} from "./mediaSlotReconcile";

vi.mock("../api/ai", () => ({ fetchSnapshot: vi.fn() }));

import { fetchSnapshot } from "../api/ai";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PENDING: ChatMessage = {
  id: "m1", role: "assistant", text: "正文", parts: [
    { type: "text", text: "正文" },
    { type: "media-slot", slotId: "slot-1", status: "pending", kind: "image" },
  ],
};

const SNAPSHOT_READY = {
  id: "m1", role: "assistant", text: "正文", parts: [
    { type: "text", text: "正文" },
    { type: "image", url: "http://img/a.png", slotId: "slot-1", status: "ready" },
  ],
} as ChatMessage;

describe("reconcileMediaSlots", () => {
  it("快照 ready 且 messageId+slotId 命中 ⇒ 本地 pending 槽原位换成图片", () => {
    const next = reconcileMediaSlots([PENDING], [SNAPSHOT_READY]);
    expect(next[0].parts?.[0]).toEqual({ type: "text", text: "正文" });
    expect(next[0].parts?.[1]).toMatchObject({
      type: "image", url: "http://img/a.png", slotId: "slot-1", status: "ready",
    });
    expect(hasPendingMediaSlot(next)).toBe(false);
  });

  it("快照仍是 pending / slotId 不同 / messageId 不同 ⇒ 原引用返回，不误判", () => {
    const stillPending = {
      id: "m1", role: "assistant", text: "正文", parts: [
        { type: "media-slot", slotId: "slot-1", status: "pending" },
      ],
    } as ChatMessage;
    const otherSlot = {
      id: "m1", role: "assistant", text: "正文", parts: [
        { type: "image", url: "http://img/b.png", slotId: "slot-2", status: "ready" },
      ],
    } as ChatMessage;
    const otherMessage = {
      id: "m2", role: "assistant", text: "正文", parts: [
        { type: "image", url: "http://img/c.png", slotId: "slot-1", status: "ready" },
      ],
    } as ChatMessage;
    const current = [PENDING];
    expect(reconcileMediaSlots(current, [stillPending])).toBe(current);
    expect(reconcileMediaSlots(current, [otherSlot])).toBe(current);
    expect(reconcileMediaSlots(current, [otherMessage])).toBe(current);
  });

  it("视频/音频按快照 part 类型回填（不只认图片）", () => {
    const video = reconcileMediaSlots([PENDING], [{
      id: "m1", role: "assistant", text: "正文", parts: [
        { type: "video", url: "http://img/v.mp4", slotId: "slot-1", status: "ready" },
      ],
    } as ChatMessage]);
    expect(video[0].parts?.[1]).toMatchObject({ type: "video", url: "http://img/v.mp4" });
    const audio = reconcileMediaSlots([PENDING], [{
      id: "m1", role: "assistant", text: "正文", parts: [
        { type: "audio", url: "http://img/a.wav", slotId: "slot-1", status: "ready" },
      ],
    } as ChatMessage]);
    expect(audio[0].parts?.[1]).toMatchObject({ type: "audio", url: "http://img/a.wav" });
  });

  it("hasPendingMediaSlot：只认 media-slot+pending", () => {
    expect(hasPendingMediaSlot([PENDING])).toBe(true);
    expect(hasPendingMediaSlot([SNAPSHOT_READY])).toBe(false);
    expect(hasPendingMediaSlot([])).toBe(false);
  });
});

function Harness({ initial, threadId }: { initial: ChatMessage[]; threadId: string }) {
  const [messages, setMessages] = useState(initial);
  useMediaSlotReconcile({ messages, threadId, setMessages });
  const part = messages[0]?.parts?.[1];
  return createElement("div", { id: "probe" },
    part?.type === "media-slot" ? "pending" : `${part?.type}:${part?.url}`);
}

let container: HTMLDivElement | null = null;
let root: Root | null = null;

async function render(threadId: string) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root!.render(createElement(Harness, { initial: [PENDING], threadId }));
  });
}

afterEach(async () => {
  await act(async () => { root?.unmount(); });
  root = null;
  container?.remove();
  container = null;
  vi.mocked(fetchSnapshot).mockReset();
});

describe("useMediaSlotReconcile", () => {
  it("有 pending 槽 ⇒ 挂载即读快照并回填，无需刷新页面", async () => {
    vi.mocked(fetchSnapshot).mockResolvedValue({ items: [SNAPSHOT_READY] });
    await render("t1");
    expect(fetchSnapshot).toHaveBeenCalledWith("t1");
    expect(container?.querySelector("#probe")?.textContent).toBe("image:http://img/a.png");
  });

  it("home 线程不请求快照", async () => {
    vi.mocked(fetchSnapshot).mockResolvedValue({ items: [SNAPSHOT_READY] });
    await render("home");
    expect(fetchSnapshot).not.toHaveBeenCalled();
    expect(container?.querySelector("#probe")?.textContent).toBe("pending");
  });
});
