// @vitest-environment jsdom
// 分镜成片编排的行为网（2026-11-09）：从 useChatSession 外移后的 hook 仍守住三条红线——
// ① 全部 shotIndex 0..total-1 ready 才拼接；② 任一 pending/failed 绝不产出残缺成片；
// ③ 拼接顺序只认 shotIndex（正文序），不按槽位/完成序；④ 同序列只提交一次。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ChatMessage, MsgPart } from "../types/chat";
import { mergeShotVideos } from "../api/comfyui";
import { useShotMerge } from "./useShotMerge";

vi.mock("../api/comfyui", () => ({
  mergeShotVideos: vi.fn(async () => ({
    ok: true, url: "http://127.0.0.1:8010/media/shot_sequence_s1.mp4",
    path: "", duration: 8, width: 1280, height: 720, warnings: [],
  })),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | null = null;
let root: Root | null = null;
let latest: ChatMessage[] = [];
let retry: ((messageId: string) => boolean) | null = null;

function shotPart(index: number, status: "pending" | "ready" | "failed"): MsgPart {
  return {
    type: "media-slot", kind: "video", slotId: `s1:video:${index}`,
    status, shotSequenceId: "s1", shotIndex: index, shotTotal: 2,
    ...(status === "ready" ? { url: `http://comfy/video-${index}.mp4` } : {}),
  };
}

function thread(parts: MsgPart[]): ChatMessage[] {
  return [{ id: "bot", role: "assistant", text: "正文", parts: [{ type: "text", text: "正文" }, ...parts] }];
}

function Harness({ initial }: { initial: ChatMessage[] }) {
  const [messages, setMessages] = useState(initial);
  latest = messages;
  retry = useShotMerge({
    messages, threadId: "r1", repoId: "r1", outputDir: "D:/out", setMessages,
  }).retryShotMerge;
  return null;
}

async function mount(initial: ChatMessage[]) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<Harness initial={initial} />);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function mergedPart(): MsgPart | undefined {
  return latest[0].parts?.find((part) => part.merged);
}

beforeEach(() => {
  vi.mocked(mergeShotVideos).mockClear();
});
afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  root = null;
  container = null;
});

describe("useShotMerge", () => {
  it("全部镜 ready ⇒ 先挂 :merged pending，拼接成功后原位回填 url", async () => {
    await mount(thread([shotPart(0, "ready"), shotPart(1, "ready")]));
    expect(mergeShotVideos).toHaveBeenCalledTimes(1);
    const merged = mergedPart();
    expect(merged?.status).toBe("ready");
    expect(merged?.url).toBe("http://127.0.0.1:8010/media/shot_sequence_s1.mp4");
  });

  it("任一镜 pending/failed ⇒ 不发起拼接，也不出现成片槽", async () => {
    await mount(thread([shotPart(0, "ready"), shotPart(1, "pending")]));
    expect(mergeShotVideos).not.toHaveBeenCalled();
    expect(mergedPart()).toBeUndefined();

    await act(async () => { root?.unmount(); });
    container?.remove();
    container = null;
    root = null;
    latest = [];
    await mount(thread([shotPart(0, "ready"), shotPart(1, "failed")]));
    expect(mergeShotVideos).not.toHaveBeenCalled();
    expect(mergedPart()).toBeUndefined();
  });

  it("拼接顺序只认 shotIndex：槽位乱序时提交 urls 仍按正文序", async () => {
    await mount(thread([shotPart(1, "ready"), shotPart(0, "ready")]));
    expect(vi.mocked(mergeShotVideos).mock.calls[0][0].urls).toEqual([
      "http://comfy/video-0.mp4", "http://comfy/video-1.mp4",
    ]);
  });

  it("拼接失败 ⇒ :merged 标 failed 并带真实原因（可重试）", async () => {
    vi.mocked(mergeShotVideos).mockRejectedValueOnce(new Error("ffmpeg 不可用"));
    await mount(thread([shotPart(0, "ready"), shotPart(1, "ready")]));
    const merged = mergedPart();
    expect(merged?.status).toBe("failed");
    expect(merged?.error).toBe("ffmpeg 不可用");
  });

  it("同一序列只提交一次：后续 messages 更新不重复拼接", async () => {
    await mount(thread([shotPart(0, "ready"), shotPart(1, "ready")]));
    expect(mergeShotVideos).toHaveBeenCalledTimes(1);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(mergeShotVideos).toHaveBeenCalledTimes(1);
  });

  it("成片失败可重试：分镜仍全 ready ⇒ 重拼一次并回填 url", async () => {
    vi.mocked(mergeShotVideos).mockRejectedValueOnce(new Error("ffmpeg 不可用"));
    await mount(thread([shotPart(0, "ready"), shotPart(1, "ready")]));
    expect(mergedPart()?.status).toBe("failed");
    await act(async () => {
      expect(retry?.("bot")).toBe(true);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(mergeShotVideos).toHaveBeenCalledTimes(2);
    expect(mergedPart()?.status).toBe("ready");
  });

  it("分镜不全 ready 时拒绝重拼（不给残缺成片）", async () => {
    await mount(thread([shotPart(0, "ready"), shotPart(1, "pending")]));
    expect(retry?.("bot")).toBe(false);
    expect(mergeShotVideos).not.toHaveBeenCalled();
  });
});
