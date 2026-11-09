import { describe, expect, it, vi } from "vitest";
import type { MsgPart } from "../types/chat";
import {
  chooseDialogueAudio, dialogueAudioFilename, inspectDialogueAudio,
  prepareDialogueAudio, waitForDialogueAudioReady,
} from "./dialogueAudio";

const audio = (seq: number, url = `a${seq}.wav`): MsgPart => ({
  type: "audio", url, slotId: `audio-m1-${seq - 1}`, status: "ready", seq, total: 3,
});
const pendingAudio = (): MsgPart => ({
  type: "media-slot", slotId: "audio-m1-9", status: "pending", kind: "audio",
});
const merged = (url = "merged.flac"): MsgPart => ({
  type: "audio", url, slotId: "merged-m1", status: "ready",
});

describe("inspectDialogueAudio", () => {
  it("分条按 seq 升序、排除 merged，pending 只数没失败的音频槽", () => {
    const state = inspectDialogueAudio([
      audio(3, "c.wav"), merged(), audio(1, "a.wav"), pendingAudio(),
      { type: "media-slot", slotId: "audio-x", status: "failed", kind: "audio" },
      { type: "media-slot", slotId: "img", status: "pending", kind: "image" },
      { type: "text", text: "正文" },
    ]);
    expect(state.perLine).toEqual(["a.wav", "c.wav"]);
    expect(state.merged).toBe("merged.flac");
    expect(state.pending).toBe(1);
  });
});

describe("chooseDialogueAudio", () => {
  it("≥2 条分条 ⇒ 让后端拼接", () => {
    expect(chooseDialogueAudio({ perLine: ["a", "b"], merged: "", pending: 0 })).toEqual({ kind: "merge" });
  });
  it("1 条分条 ⇒ 它本身就是整段（无需 ffmpeg）", () => {
    expect(chooseDialogueAudio({ perLine: ["a"], merged: "", pending: 0 }))
      .toEqual({ kind: "per_line_single", url: "a" });
  });
  it("只有整条 ⇒ 直接复用", () => {
    expect(chooseDialogueAudio({ perLine: [], merged: "m", pending: 0 }))
      .toEqual({ kind: "merged", url: "m" });
  });
  it("什么都没有 ⇒ none", () => {
    expect(chooseDialogueAudio({ perLine: [], merged: "", pending: 0 })).toEqual({ kind: "none" });
  });
});

describe("waitForDialogueAudioReady", () => {
  it("一点音轨都没有 ⇒ 只等宽限窗口就返回（不拖死视频槽）", async () => {
    const started = Date.now();
    const state = await waitForDialogueAudioReady(() => [], {
      graceMs: 20, tickMs: 5, settleMs: 5, timeoutMs: 5_000,
    });
    expect(state.perLine).toEqual([]);
    expect(Date.now() - started).toBeLessThan(2_000);
  });
  it("先有 pending、后落定 ⇒ 等到数量稳定才返回", async () => {
    let reads = 0;
    const state = await waitForDialogueAudioReady(() => {
      reads += 1;
      if (reads < 4) return [pendingAudio()];
      return [audio(1), audio(2)];
    }, { graceMs: 1_000, tickMs: 5, settleMs: 20, timeoutMs: 5_000 });
    expect(state.perLine).toEqual(["a1.wav", "a2.wav"]);
    expect(state.pending).toBe(0);
    expect(reads).toBeGreaterThan(4);
  });
  it("分条一直 pending ⇒ 到超时就返回当前快照", async () => {
    const started = Date.now();
    const state = await waitForDialogueAudioReady(() => [audio(1), pendingAudio()], {
      graceMs: 5, tickMs: 5, settleMs: 5, timeoutMs: 60,
    });
    expect(state.perLine).toEqual(["a1.wav"]);
    expect(state.pending).toBe(1);
    expect(Date.now() - started).toBeGreaterThanOrEqual(50);
  });
});

describe("dialogueAudioFilename", () => {
  it("local-view 取 path 查询参数的文件名", () => {
    const url = "/api/comfyui/local-view?path=" + encodeURIComponent("C:\\out\\merged_ab.flac");
    expect(dialogueAudioFilename(url)).toBe("merged_ab.flac");
  });
  it("裸路径取尾段", () => {
    expect(dialogueAudioFilename("D:/out/merged_cd.flac")).toBe("merged_cd.flac");
  });
  it("取不到名字时回默认", () => {
    expect(dialogueAudioFilename("")).toBe("dialogue.flac");
  });
});

describe("prepareDialogueAudio", () => {
  const blob = new Blob([new Uint8Array([1, 2])], { type: "audio/flac" });
  const base = () => ({
    readParts: () => [audio(1, "a.wav"), audio(2, "b.wav")],
    mergeMerged: vi.fn(async (_force: boolean) => "/api/comfyui/local-view?path=" + encodeURIComponent("C:\\out\\merged_ab.flac")),
    fetchBlob: vi.fn(async () => blob),
    upload: vi.fn(async () => "up_merged_ab.flac"),
    warn: vi.fn(),
    wait: { graceMs: 1, tickMs: 2, settleMs: 1, timeoutMs: 200 },
  });

  it("≥2 条分条 ⇒ ffmpeg 拼接后上传整条，文件名取真实产物名", async () => {
    const args = base();
    const jobs = new Map<string, Promise<string>>();
    const name = await prepareDialogueAudio(args, jobs, "r1:m1:video");
    expect(args.mergeMerged).toHaveBeenCalledWith(true);
    expect(args.fetchBlob).toHaveBeenCalledWith(
      "/api/comfyui/local-view?path=" + encodeURIComponent("C:\\out\\merged_ab.flac"),
    );
    expect(args.upload).toHaveBeenCalledWith(blob, "merged_ab.flac");
    expect(name).toBe("up_merged_ab.flac");
    // 同键第二次调用复用同一份产物，不重复拼接/上传
    expect(await prepareDialogueAudio(args, jobs, "r1:m1:video")).toBe("up_merged_ab.flac");
    expect(args.mergeMerged).toHaveBeenCalledTimes(1);
    expect(args.upload).toHaveBeenCalledTimes(1);
  });

  it("拼接失败但有旧整条 ⇒ 用旧整条，不再二次拼接", async () => {
    const args = base();
    args.readParts = () => [audio(1, "a.wav"), audio(2, "b.wav"), merged("old.flac")];
    args.mergeMerged = vi.fn(async () => { throw new Error("ffmpeg 失败"); });
    const name = await prepareDialogueAudio(args, new Map(), "r1:m1:video");
    expect(name).toBe("up_merged_ab.flac");
    expect(args.fetchBlob).toHaveBeenCalledWith("old.flac");
    expect(args.warn).toHaveBeenCalled();
  });

  it("force 拼接失败且无旧整条 ⇒ 退回非 force 再取一次", async () => {
    const args = base();
    const merge = vi.fn(async (force: boolean) => {
      if (force) throw new Error("快照里分条已被清掉");
      return "snapshot.flac";
    });
    args.mergeMerged = merge;
    await prepareDialogueAudio(args, new Map(), "r1:m1:video");
    expect(merge.mock.calls.map((call) => call[0])).toEqual([true, false]);
    expect(args.fetchBlob).toHaveBeenCalledWith("snapshot.flac");
  });

  it("没有分条也没有整条 ⇒ 空串 + 告警，失败不缓存（下一槽可重试）", async () => {
    const args = base();
    args.readParts = () => [];
    args.wait = { graceMs: 5, tickMs: 2, settleMs: 1, timeoutMs: 100 };
    const jobs = new Map<string, Promise<string>>();
    expect(await prepareDialogueAudio(args, jobs, "r1:m1:video")).toBe("");
    expect(args.warn).toHaveBeenCalled();
    expect(args.upload).not.toHaveBeenCalled();
    expect(jobs.has("r1:m1:video")).toBe(false);
  });
});
