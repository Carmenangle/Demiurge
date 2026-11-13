import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../types/chat";
import { canMergeShotSequence, collectShotSequence, orderedShotUrls } from "./shotSequence";

function message(parts: ChatMessage["parts"]): ChatMessage {
  return { id: "m1", role: "assistant", text: "正文", parts };
}

describe("shotSequence 成片 barrier", () => {
  it("全部 shotIndex ready ⇒ 可按 shotIndex 拼出有序 URL", () => {
    const state = collectShotSequence([message([
      { type: "media-slot", slotId: "a:v", kind: "video", status: "ready", url: "u0",
        shotSequenceId: "seq", shotIndex: 0, shotTotal: 3 },
      { type: "media-slot", slotId: "b:m", kind: "video", status: "ready", url: "u1",
        shotSequenceId: "seq", shotIndex: 1, shotTotal: 3 },
      { type: "media-slot", slotId: "b:v", kind: "video", status: "ready", url: "u2",
        shotSequenceId: "seq", shotIndex: 2, shotTotal: 3 },
    ])], "m1");
    expect(state).not.toBeNull();
    expect(canMergeShotSequence(state)).toBe(true);
    expect(orderedShotUrls(state!)).toEqual(["u0", "u1", "u2"]);
  });

  it("任一镜未 ready/failed ⇒ 不合并", () => {
    const state = collectShotSequence([message([
      { type: "media-slot", slotId: "a:v", kind: "video", status: "ready", url: "u0",
        shotSequenceId: "seq", shotIndex: 0, shotTotal: 2 },
      { type: "media-slot", slotId: "b:m", kind: "video", status: "failed",
        shotSequenceId: "seq", shotIndex: 1, shotTotal: 2 },
    ])], "m1");
    expect(canMergeShotSequence(state)).toBe(false);
  });

  it("缺镜（只有 0，总数为 2）⇒ 不合并", () => {
    const state = collectShotSequence([message([
      { type: "media-slot", slotId: "a:v", kind: "video", status: "ready", url: "u0",
        shotSequenceId: "seq", shotIndex: 0, shotTotal: 2 },
    ])], "m1");
    expect(canMergeShotSequence(state)).toBe(false);
  });

  it("已有 merged 槽 ⇒ 不再重复拼接", () => {
    const state = collectShotSequence([message([
      { type: "media-slot", slotId: "a:v", kind: "video", status: "ready", url: "u0",
        shotSequenceId: "seq", shotIndex: 0, shotTotal: 1 },
      { type: "media-slot", slotId: "m1:merged", kind: "video", status: "ready",
        url: "merged", shotSequenceId: "seq", merged: true },
    ])], "m1");
    expect(state?.hasMerged).toBe(true);
    expect(canMergeShotSequence(state)).toBe(false);
  });
});
