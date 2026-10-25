// @vitest-environment jsdom
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ChatMessage } from "../types/chat";
import type { AudioDialogueLine } from "../api/chatStreamProtocol";
import { useAudioTracks } from "./useAudioTracks";

// T2.2-C1：剧情对白音频化家族（submitAudio + mergeAudioTracks）从 useChatSession 外移后的回归网。
// 三件证据互补：① 直断言行为网（跳过闸/插槽 seq·total/上传失败 continue/提交失败 drop/
//    home 不补快照/force 重拼/失败透因）② 逐字搬移锁（判定语句按次数钉在新属主、且在视图缺席）
// ③ 源码形态锁（视图恰调用一次 hook + deps 字面序 + 对外 return 仍含 mergeAudioTracks）。
// 硬要求：每个用例必须卸载；源码只能在 it 回调内读（jsdom 模块求值期 import.meta.url 非 file:）。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../api/comfyui", () => ({
  submitWorkflow: vi.fn(async () => ({ prompt_id: "pid-1", node_count: 1 })),
  uploadImage: vi.fn(async () => ({ name: "uploaded.wav" })),
  localViewUrl: (p: string) => `local:${p}`,
  mergeAudio: vi.fn(async () => ({ ok: true, url: "http://127.0.0.1:8010/media/merged.wav" })),
}));

vi.mock("../api/ai", () => ({
  reportAudioSubmission: vi.fn(async () => undefined),
  ensureAudioSlot: vi.fn(async () => undefined),
}));

import { mergeAudio, submitWorkflow, uploadImage } from "../api/comfyui";
import { ensureAudioSlot, reportAudioSubmission } from "../api/ai";

const SEAM = "./useAudioTracks.ts";
const VIEW = "./useChatSession.ts";

function read(rel: string): string {
  return readFileSync(new URL(rel, import.meta.url), "utf8").replace(/\r\n/g, "\n");
}
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").split("\n").map((l) => l.replace(/(?<!:)\/\/.*$/, "")).join("\n");
}
function count(hay: string, needle: string): number {
  let n = 0;
  let i = 0;
  for (;;) {
    const at = hay.indexOf(needle, i);
    if (at === -1) return n;
    n += 1;
    i = at + needle.length;
  }
}

// ───────────── 夹具 ─────────────
// submitAudio 用例：纯文本消息（appendAudioSlot 会把 text 转成 parts 再插槽，终态可断言）。
const TEXT_MSG = { id: "m1", role: "assistant", text: "正文" } as ChatMessage;
// mergeAudioTracks 用例：已完成态（media-slot 被 resolve 成 type=audio、slotId 不变）。
const DONE_AUDIO_MSG = {
  id: "m1", role: "assistant", text: "正文", parts: [
    { type: "text", text: "正文" },
    { type: "audio", url: "u1", slotId: "audio-m1-0", status: "ready", kind: "audio", speaker: "沈糯" },
    { type: "audio", url: "u2", slotId: "audio-m1-1", status: "ready", kind: "audio", speaker: "顾小" },
  ],
} as ChatMessage;
// 基座保持可 spread 的对象字面量类型（`as never` 的常量无法展开）；用点再逐次转 never。
const AUDIO_TPL_RAW = {
  id: "tpl-audio", name: "IndexTTS", node_order: [], input_node_ids: [], output_node_ids: [],
  primary_output_node_id: "9",
  exposed: [
    { node_id: "10", field: "text", binding: "voice_text" },
    { node_id: "11", field: "audio", binding: "voice_reference" },
    { node_id: "12", field: "happy", binding: "voice_emotion_happy" },
  ],
};
const AUDIO_TPL = AUDIO_TPL_RAW as never;

interface DepsOver {
  preset?: Record<string, unknown> | null;
  templates?: unknown[];
  threadId?: string;
  messages?: ChatMessage[];
}

function makeDeps(over: DepsOver = {}) {
  const state = {
    messages: over.messages ?? [DONE_AUDIO_MSG],
  };
  const messagesRef = { current: state.messages };
  const setMessages = vi.fn((fn: unknown) => {
    state.messages = typeof fn === "function"
      ? (fn as (m: ChatMessage[]) => ChatMessage[])(state.messages)
      : (fn as ChatMessage[]);
    messagesRef.current = state.messages; // 复刻视图渲染期同步（mergeAudioTracks 读 ref）
    return state.messages;
  });
  const pollResult = vi.fn();
  const onNotify = vi.fn();
  const mediaInsert = over.preset === null
    ? {}
    : { r1: over.preset ?? { audioTemplateId: "tpl-audio", characterVoices: { 沈糯: { voiceRef: "voices/wan.wav" } } } };
  const deps = {
    settings: { comfyuiUrl: "http://127.0.0.1:8188", outputDir: "D:/out", mediaInsert } as never,
    repo: { id: "r1" } as never,
    templates: over.templates ?? [AUDIO_TPL] as never,
    onNotify,
    threadId: over.threadId ?? "r1",
    setMessages: setMessages as never,
    messagesRef: messagesRef as never,
    pollResult,
  };
  return { deps, state, setMessages, pollResult, onNotify, messagesRef };
}

let container: HTMLDivElement;
let root: Root | null = null;

async function renderAudio(deps: unknown): Promise<{
  submitAudio: (lines: AudioDialogueLine[], messageId: string) => Promise<void>;
  mergeAudioTracks: (messageId: string) => Promise<void>;
}> {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  let api: { submitAudio: (lines: AudioDialogueLine[], messageId: string) => Promise<void>; mergeAudioTracks: (messageId: string) => Promise<void> } | null = null;
  function C() {
    const audio = useAudioTracks(deps as never);
    api = audio as never;
    return null;
  }
  await act(async () => {
    root!.render(createElement(C) as ReactElement);
  });
  return api!;
}

async function callSubmit(rig: ReturnType<typeof makeDeps>, lines: AudioDialogueLine[], messageId = "m1") {
  const api = await renderAudio(rig.deps);
  await act(async () => {
    await api.submitAudio(lines, messageId);
  });
  return api;
}

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  root = null;
  container?.remove();
  vi.unstubAllGlobals();
});

// 用例隔离：vi.mock 的调用计数与实现覆盖会跨用例泄漏 ⇒ 每例重置默认实现。
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(submitWorkflow).mockResolvedValue({ prompt_id: "pid-1", node_count: 1 } as never);
  vi.mocked(uploadImage).mockResolvedValue({ name: "uploaded.wav" } as never);
  vi.mocked(mergeAudio).mockResolvedValue({ ok: true, url: "http://127.0.0.1:8010/media/merged.wav" } as never);
  vi.stubGlobal("fetch", vi.fn(async () => ({
    blob: async () => new Blob([new Uint8Array([1, 2, 3])], { type: "audio/wav" }),
  })));
});

// ───────────── submitAudio 行为网 ─────────────
describe("submitAudio 行为网", () => {
  it("未配置音频模板 ⇒ 静默返回（不上传、不提交、不提示）", async () => {
    const rig = makeDeps({ preset: { characterVoices: { 沈糯: { voiceRef: "voices/wan.wav" } } } });
    await callSubmit(rig, [{ speaker: "沈糯", text: "第一句" }]);
    expect(uploadImage).not.toHaveBeenCalled();
    expect(submitWorkflow).not.toHaveBeenCalled();
    expect(rig.onNotify).not.toHaveBeenCalled();
    expect(rig.setMessages).not.toHaveBeenCalled();
  });

  it("模板不存在（audioTemplateId 指向陌生 id）⇒ 静默返回", async () => {
    const rig = makeDeps({ preset: { audioTemplateId: "gone", characterVoices: { 沈糯: { voiceRef: "v.wav" } } } });
    await callSubmit(rig, [{ speaker: "沈糯", text: "第一句" }]);
    expect(submitWorkflow).not.toHaveBeenCalled();
    expect(rig.setMessages).not.toHaveBeenCalled();
  });

  it("部分角色未配音轨 ⇒ 提示一次（列跳过角色与已配置），已配角色照常提交", async () => {
    const rig = makeDeps({ messages: [TEXT_MSG] });
    await callSubmit(rig, [
      { speaker: "沈糯", text: "第一句" },
      { speaker: "路人甲", text: "第二句" },
    ]);
    expect(rig.onNotify).toHaveBeenCalledTimes(1);
    expect(rig.onNotify.mock.calls[0][0]).toContain("路人甲");
    expect(rig.onNotify.mock.calls[0][0]).toContain("沈糯"); // 「已配置」清单
    // 可配行只有沈糯一条 ⇒ 只提交一次
    expect(submitWorkflow).toHaveBeenCalledTimes(1);
  });

  it("全部行不可配（无任何音轨绑定）⇒ 提示跳过且不提交、不插槽", async () => {
    const rig = makeDeps({ preset: { audioTemplateId: "tpl-audio", characterVoices: {} } });
    await callSubmit(rig, [{ speaker: "沈糯", text: "第一句" }]);
    expect(rig.onNotify).toHaveBeenCalledTimes(1);
    expect(submitWorkflow).not.toHaveBeenCalled();
    expect(rig.setMessages).not.toHaveBeenCalled();
  });

  it("正常两行 ⇒ 逐行插槽(seq/total) + 补快照 + 提交 + 上屏日志 + pollResult 显式 audio/background", async () => {
    const rig = makeDeps({ messages: [TEXT_MSG] });
    await callSubmit(rig, [
      { speaker: "沈糯", text: "第一句", emotion: { happy: 0.8 } },
      { speaker: "沈糯", text: "第二句" },
    ]);
    // 音轨上传：localViewUrl → fetch → File → uploadImage
    expect(uploadImage).toHaveBeenCalledTimes(2);
    expect((vi.mocked(uploadImage).mock.calls[0][0] as File).name).toBe("wan.wav");
    // values 由 audioTemplateValues 按 exposed 语义绑定组装（text/reference/emotion）
    expect(submitWorkflow).toHaveBeenCalledTimes(2);
    const values = vi.mocked(submitWorkflow).mock.calls[0][1] as Record<string, unknown>;
    expect(values).toEqual({ "10.text": "第一句", "11.audio": "uploaded.wav", "12.happy": 0.8 });
    expect(vi.mocked(submitWorkflow).mock.calls[0][2]).toBe("http://127.0.0.1:8188");
    expect(vi.mocked(submitWorkflow).mock.calls[0][3]).toBe("第一句");
    expect(vi.mocked(submitWorkflow).mock.calls[0][4]).toEqual([]);
    expect(vi.mocked(submitWorkflow).mock.calls[0][5]).toBe("none");
    // 槽位：终态两个 media-slot，seq 1/2 与 2/2，slotId 按 index 编号
    const slots = (rig.state.messages[0].parts || []).filter((p) => p.type === "media-slot");
    expect(slots).toHaveLength(2);
    expect(slots[0]).toMatchObject({ slotId: "audio-m1-0", speaker: "沈糯", seq: 1, total: 2, status: "pending", kind: "audio" });
    expect(slots[1]).toMatchObject({ slotId: "audio-m1-1", seq: 2, total: 2 });
    // 非 home 线程：每条补写一次后端快照槽
    expect(ensureAudioSlot).toHaveBeenCalledTimes(2);
    expect(vi.mocked(ensureAudioSlot).mock.calls[0][0]).toMatchObject({ threadId: "r1", messageId: "m1", slotId: "audio-m1-0", speaker: "沈糯", seq: 1, total: 2 });
    // 上屏日志 + 轮询接线：第 4 参后台 target、第 6 参显式 audio
    expect(reportAudioSubmission).toHaveBeenCalledTimes(2);
    expect(vi.mocked(reportAudioSubmission).mock.calls[0][0]).toMatchObject({ threadId: "r1", repoId: "r1", messageId: "m1", slotId: "audio-m1-0", speaker: "沈糯", promptId: "pid-1" });
    expect(rig.pollResult).toHaveBeenCalledTimes(2);
    expect(rig.pollResult.mock.calls[0]).toEqual([
      "pid-1", ["9"], undefined, { messageId: "m1", slotId: "audio-m1-0", background: true }, "第一句", "audio",
    ]);
  });

  it("模板无 primary_output_node_id ⇒ pollResult 第 2 参为空数组", async () => {
    const rig = makeDeps({
      templates: [{ ...AUDIO_TPL_RAW, primary_output_node_id: undefined } as never],
      messages: [TEXT_MSG],
    });
    await callSubmit(rig, [{ speaker: "沈糯", text: "第一句" }]);
    expect(rig.pollResult.mock.calls[0][1]).toEqual([]);
  });

  it("音轨上传失败 ⇒ 该行 continue（无槽、无提交），后续行照常", async () => {
    const rig = makeDeps({ messages: [TEXT_MSG] });
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("net down"); }));
    await callSubmit(rig, [
      { speaker: "沈糯", text: "第一句" },
      { speaker: "沈糯", text: "第二句" },
    ]);
    // 两行都上传失败（同一 fetch mock）⇒ 零提交、零插槽
    expect(submitWorkflow).not.toHaveBeenCalled();
    expect(rig.setMessages).not.toHaveBeenCalled();
  });

  it("提交返回无 prompt_id ⇒ dropMediaSlot 摘槽、不轮询、不上屏日志", async () => {
    const rig = makeDeps({ messages: [TEXT_MSG] });
    vi.mocked(submitWorkflow).mockResolvedValue({ node_count: 1 } as never);
    await callSubmit(rig, [{ speaker: "沈糯", text: "第一句" }]);
    // 先 append 后 drop ⇒ 终态不残留 media-slot（text part 不受影响）
    const slots = (rig.state.messages[0].parts || []).filter((p) => p.type === "media-slot");
    expect(slots).toHaveLength(0);
    expect(rig.pollResult).not.toHaveBeenCalled();
    expect(reportAudioSubmission).not.toHaveBeenCalled();
  });

  it("提交抛错 ⇒ dropMediaSlot 摘槽（异常不冒泡）", async () => {
    const rig = makeDeps({ messages: [TEXT_MSG] });
    vi.mocked(submitWorkflow).mockRejectedValue(new Error("submit boom"));
    await callSubmit(rig, [{ speaker: "沈糯", text: "第一句" }]);
    const slots = (rig.state.messages[0].parts || []).filter((p) => p.type === "media-slot");
    expect(slots).toHaveLength(0);
    expect(rig.pollResult).not.toHaveBeenCalled();
  });

  it("home 线程 ⇒ 不补写后端快照槽，其余链路照常", async () => {
    const rig = makeDeps({ threadId: "home", messages: [TEXT_MSG] });
    await callSubmit(rig, [{ speaker: "沈糯", text: "第一句" }]);
    expect(ensureAudioSlot).not.toHaveBeenCalled();
    expect(submitWorkflow).toHaveBeenCalledTimes(1);
    expect(rig.pollResult).toHaveBeenCalledTimes(1);
  });
});

// ───────────── mergeAudioTracks 行为网 ─────────────
describe("mergeAudioTracks 行为网", () => {
  async function callMerge(rig: ReturnType<typeof makeDeps>, messageId = "m1") {
    const api = await renderAudio(rig.deps);
    await act(async () => {
      await api.mergeAudioTracks(messageId);
    });
  }

  it("分条音频不足 2 条 ⇒ 不请求拼接、静默返回", async () => {
    const rig = makeDeps({ messages: [{
      id: "m1", role: "assistant", text: "正文", parts: [
        { type: "audio", url: "u1", slotId: "audio-m1-0", status: "ready", kind: "audio", speaker: "沈糯" },
      ],
    } as ChatMessage] });
    await callMerge(rig);
    expect(mergeAudio).not.toHaveBeenCalled();
    expect(rig.onNotify).not.toHaveBeenCalled();
  });

  it("两条分条 ⇒ force=false 请求；成功 ⇒ 只留完整版（merged-）并提示", async () => {
    const rig = makeDeps();
    await callMerge(rig);
    expect(mergeAudio).toHaveBeenCalledTimes(1);
    expect(vi.mocked(mergeAudio).mock.calls[0][0]).toEqual({ threadId: "r1", messageId: "m1", force: false });
    const parts = rig.state.messages[0].parts || [];
    expect(parts).toHaveLength(2); // 原 text + 完整版
    expect(parts[0]).toEqual({ type: "text", text: "正文" });
    expect(parts[1]).toMatchObject({
      type: "audio", url: "http://127.0.0.1:8010/media/merged.wav",
      slotId: "merged-m1", status: "ready", kind: "audio", speaker: "完整版",
    });
    expect(rig.onNotify).toHaveBeenCalledWith("完整版音频已生成", "success");
  });

  it("已有旧完整版 ⇒ force=true 强制重拼", async () => {
    const rig = makeDeps({ messages: [{
      id: "m1", role: "assistant", text: "正文", parts: [
        { type: "audio", url: "u1", slotId: "audio-m1-0", status: "ready", kind: "audio" },
        { type: "audio", url: "u2", slotId: "audio-m1-1", status: "ready", kind: "audio" },
        { type: "audio", url: "old", slotId: "merged-m1", status: "ready", kind: "audio", speaker: "完整版" },
      ],
    } as ChatMessage] });
    await callMerge(rig);
    expect(vi.mocked(mergeAudio).mock.calls[0][0]).toMatchObject({ force: true });
    // ⚠ 锁**当前真实行为**（本刀逐字搬移、禁顺手改）：过滤条件只摘「非 merged- 的 audio」，
    // 旧完整版被保留、新完整版追加在后 ⇒ 两个 merged-m1 同 slotId 并存。
    // 这是搬移前就有的既有缺陷（重拼后旧完整版不消失），只报告不修；修它属独立刀。
    const merged = (rig.state.messages[0].parts || []).filter((p) => (p.slotId || "").startsWith("merged-"));
    expect(merged).toHaveLength(2);
    expect(merged[1]).toMatchObject({ url: "http://127.0.0.1:8010/media/merged.wav", slotId: "merged-m1" });
  });

  it("后端返回异常（!ok）⇒ 提示失败且分条不动（可重试）", async () => {
    const rig = makeDeps();
    vi.mocked(mergeAudio).mockResolvedValue({ ok: false } as never);
    await callMerge(rig);
    expect(rig.onNotify).toHaveBeenCalledWith("音频拼接失败", "error");
    expect((rig.state.messages[0].parts || []).filter((p) => p.type === "audio")).toHaveLength(2);
  });

  it("拼接抛错带 message ⇒ 透出后端原因（分条未动）", async () => {
    const rig = makeDeps();
    vi.mocked(mergeAudio).mockRejectedValue(new Error("拼接产物时长异常…已丢弃"));
    await callMerge(rig);
    expect(rig.onNotify).toHaveBeenCalledWith("拼接产物时长异常…已丢弃", "error");
    expect((rig.state.messages[0].parts || []).filter((p) => p.type === "audio")).toHaveLength(2);
  });

  it("拼接抛错无 message ⇒ 兜底文案", async () => {
    const rig = makeDeps();
    vi.mocked(mergeAudio).mockRejectedValue("boom");
    await callMerge(rig);
    expect(rig.onNotify).toHaveBeenCalledWith("音频拼接失败", "error");
  });
});

// ───────────── 逐字搬移锁（新属主恰含、视图缺席） ─────────────
describe("T2.2-C1 逐字搬移锁", () => {
  it("两个函数签名只在音域属主，视图不再内联定义", () => {
    const seam = stripComments(read(SEAM));
    const view = stripComments(read(VIEW));
    expect(count(seam, "const submitAudio = async (lines: AudioDialogueLine[], messageId: string) => {")).toBe(1);
    expect(count(seam, "const mergeAudioTracks = async (messageId: string) => {")).toBe(1);
    expect(count(view, "const submitAudio = async (lines: AudioDialogueLine[], messageId: string) => {")).toBe(0);
    expect(count(view, "const mergeAudioTracks = async (messageId: string) => {")).toBe(0);
  });

  it("关键判定与副作用语句逐字留在音域属主（各恰 1 处、视图 0）", () => {
    const seam = stripComments(read(SEAM));
    const view = stripComments(read(VIEW));
    const needles = [
      "const slotId = `audio-${messageId}-${index}`;",
      "const voiceRef = voiceReferenceFor(preset, line.speaker)!;",
      "uploadedRef = (await uploadImage(file, settings.comfyuiUrl)).name;",
      "const values = audioTemplateValues(tpl.exposed, {",
      "const r = await submitWorkflow(audioTemplateId, values, settings.comfyuiUrl, line.text, [], \"none\");",
      "pollResult(r.prompt_id, outputNodeIds, undefined, { messageId, slotId, background: true }, line.text, \"audio\");",
      "const r = await mergeAudio({ threadId, messageId, force: hasMerged });",
      "slotId: `merged-${messageId}`,",
    ];
    for (const needle of needles) {
      expect(count(seam, needle), `seam 应恰含 1 处：${needle}`).toBe(1);
      expect(count(view, needle), `view 应缺席：${needle}`).toBe(0);
    }
  });

  it("日志/快照/插槽接线只在音域属主（视图 0）", () => {
    const seam = stripComments(read(SEAM));
    const view = stripComments(read(VIEW));
    expect(count(seam, "void ensureAudioSlot({")).toBe(1);
    expect(count(seam, "void reportAudioSubmission({")).toBe(1);
    expect(count(seam, "appendAudioSlot(current, messageId, slotId, line.speaker, seq, resolvable.length)")).toBe(1);
    expect(count(view, "void ensureAudioSlot({")).toBe(0);
    expect(count(view, "void reportAudioSubmission({")).toBe(0);
    expect(count(view, "appendAudioSlot(current, messageId, slotId, line.speaker, seq, resolvable.length)")).toBe(0);
    // 音频日志前缀整体迁走（视图不再有 [auto-audio]/[merge-audio]）
    expect(count(view, "[auto-audio]")).toBe(0);
    expect(count(view, "[merge-audio]")).toBe(0);
    expect(count(seam, "[auto-audio]")).toBeGreaterThanOrEqual(6);
    expect(count(seam, "[merge-audio]")).toBeGreaterThanOrEqual(3);
  });

  it("home 闸与上传失败 continue 逐字保留（行为面两处最易丢）", () => {
    const seam = stripComments(read(SEAM));
    expect(count(seam, "if (threadId && threadId !== \"home\") {")).toBe(1);
    // ⚠ needle 不能带行尾注释（stripComments 会连注释一起吃）⇒ 钉 continue 本身：
    // 全文件只有上传失败那一个循环、一个 continue ⇒ 恰 1 处即锁住「单角色失败不阻断其它角色」。
    expect(count(seam, "continue;")).toBe(1);
    // 注释原文随体迁入（读未剥注释的源码）
    expect(read(SEAM)).toContain("continue; // 单角色音轨上传失败不阻断其它角色");
  });
});

// ───────────── 源码形态锁（视图接线） ─────────────
describe("T2.2-C1 视图接线形态锁", () => {
  it("视图恰调用一次 useAudioTracks，deps 八字面序", () => {
    const view = stripComments(read(VIEW));
    expect(count(view, "useAudioTracks({")).toBe(1);
    expect(view).toMatch(
      /useAudioTracks\(\{\s*settings, repo, templates, onNotify, threadId, setMessages, messagesRef, pollResult,?\s*\}\)/,
    );
  });

  it("对外合同不变：return 仍含 mergeAudioTracks（submitAudio 是视图内部消费）", () => {
    const view = stripComments(read(VIEW));
    expect(count(view, "mergeAudioTracks,")).toBe(1);
    expect(count(view, "void submitAudio(event.lines, botId);")).toBe(1);
  });

  it("行数上界：视图不再回涨超过搬移前，音域属主保持小而内聚", () => {
    const viewLines = read(VIEW).split("\n").length;
    const seamLines = read(SEAM).split("\n").length;
    expect(viewLines).toBeLessThanOrEqual(1700);
    expect(seamLines).toBeLessThanOrEqual(240);
  });

  it("视图死 import 已清（音频家族符号不再从原模块导入）", () => {
    const view = read(VIEW);
    for (const sym of ["mergeAudio,", "reportAudioSubmission", "ensureAudioSlot", "appendAudioSlot", "dropMediaSlot", "audioTemplateValues", "resolvableAudioLines", "skippedAudioSpeakers", "voiceReferenceFor", "AudioDialogueLine"]) {
      expect(view.includes(sym), `视图不应再导入/出现：${sym}`).toBe(false);
    }
  });
});
