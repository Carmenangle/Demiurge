// @vitest-environment jsdom
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ChatMessage } from "../types/chat";
import { useIllustrationChain, waitForSlotMedia } from "./useIllustrationChain";
import type { SlotMediaState } from "./chatGeneration";

// T2.2-A：自动插画/视频提交链（submitIllustration）从 useChatSession 外移后的回归网。
// 三件证据互补：① 直断言行为网（失败槽 stage/payload、编译链、pollResult 接线）
// ② 逐字搬移锁（判定语句按次数钉在新属主、且在视图缺席）③ 源码形态锁（调用块逐字）。
// 硬要求：每个用例必须卸载；源码只能在 it 回调内读（jsdom 模块求值期 import.meta.url 非 file:）。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../api/comfyui", () => ({
  comfyStatus: vi.fn(async () => ({ running: true })),
  submitWorkflow: vi.fn(async () => ({ prompt_id: "pid-1", node_count: 1 })),
  interruptComfy: vi.fn(async () => undefined),
  uploadImage: vi.fn(async (_file: File, _url: string) => ({ name: "uploaded.png" })),
  moveComfyOutputToInput: vi.fn(async () => "moved.png"),
  uploadRemoteImageToInput: vi.fn(async () => "remote.png"),
  localViewUrl: (p: string) => `local:${p}`,
  // 真的 viewUrl 回**后端代理可 fetch 地址**（`/api/comfyui/view?…`）——底图归一按
  // 「已是可 fetch 地址就不包 localViewUrl」判定，桩必须同形，否则测不出二次包装。
  viewUrl: (img: { filename: string }, url: string) => `/api/comfyui/view?filename=${img.filename}&url=${url}`,
  pollWorkflowResult: vi.fn(async () => ({ kind: "complete", result: { images: ["frame.png"] } })),
  // pollWorkflowResult 真源在 lib/workflowGenerationRuntime，它内部调 getResult；
  // 帧路径跑到轮询才会用到（此前用例都在更早处就 failSlot 了）。
  getResult: vi.fn(async () => ({
    status: "completed", error: undefined,
    images: [{ filename: "frame.png", subfolder: "", type: "output" }],
    videos: [], audios: [], texts: [],
  })),
}));

vi.mock("../api/ai", () => ({
  saveSnapshot: vi.fn(async () => undefined),
  claimIllustrationSubmission: vi.fn(async () => ({ claimed: true })),
  genProfilePrompt: vi.fn(async () => ({ prompt: "编译后P", negative_prompt: "" })),
  genFramePrompts: vi.fn(async () => ({ frames: {} })),
  reportIllustrationSubmission: vi.fn(async () => undefined),
  reportIllustrationFailure: vi.fn(async () => undefined),
  // 2026-10-19：插画链新增 claim/compile/upload/submit 四段阶段标记（纯观测），
  // mock 必须提供，否则整条提交链在测试环境直接抛「No export」。
  reportIllustrationStage: vi.fn(async () => undefined),
}));

vi.mock("../api/loras", () => ({
  listLoras: vi.fn(async () => ({ items: [] })),
}));

import {
  comfyStatus, moveComfyOutputToInput, submitWorkflow, uploadImage, uploadRemoteImageToInput,
  getResult,
} from "../api/comfyui";
import {
  claimIllustrationSubmission, genFramePrompts, genProfilePrompt, reportIllustrationSubmission,
} from "../api/ai";

const SEAM = "./useIllustrationChain.ts";
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
const TPL = {
  id: "tpl1", name: "图片模板", node_order: [], exposed: [], primary_output_node_id: "9",
} as never;
const VIDEO_TPL = { id: "vid1", name: "视频模板", node_order: [], exposed: [], primary_output_node_id: "9" } as never;
const PRESET = {
  templateId: "tpl1", negativePrompt: "负面N", appearanceSource: "character_card", loraMode: "single",
} as never;

interface DepsOver {
  settings?: Record<string, unknown>;
  templates?: unknown[];
  preset?: Record<string, unknown> | null;
  source?: "automatic" | "manual";
  /** messagesRef 内容（底图反查用；与 state.messages 各自独立）。 */
  messages?: ChatMessage[];
}

function makeDeps(over: DepsOver = {}) {
  const state = { messages: [] as ChatMessage[] };
  const setMessages = vi.fn((fn: unknown) => {
    state.messages = typeof fn === "function" ? (fn as (m: ChatMessage[]) => ChatMessage[])(state.messages) : (fn as ChatMessage[]);
    return state.messages;
  });
  const messagesRef = { current: (over.messages ?? []) as ChatMessage[] };
  const discardFailedIllustration = vi.fn();
  const pollResult = vi.fn();
  const mediaInsert = over.preset === null ? {} : { r1: over.preset ?? PRESET };
  const deps = {
    settings: {
      comfyuiUrl: "http://127.0.0.1:8188", outputDir: "D:/out",
      mediaInsert, ...(over.settings || {}),
    } as never,
    templates: over.templates ?? [TPL, VIDEO_TPL] as never,
    repo: { id: "r1" } as never,
    threadId: "r1",
    cardNames: ["庄听澜"] as never,
    promptProfile: "krea2" as never,
    characterBaseImages: {} as never,
    chat: { baseUrl: "u", apiKey: "k", modelName: "m" } as never,
    modelProxies: { chatProxyUrl: "", genProxyUrl: "", videoProxyUrl: "", embedProxyUrl: "" },
    effectivePersona: { name: "用户", content: "" } as never,
    messagesRef: messagesRef as never,
    setMessages: setMessages as never,
    pollResult: pollResult as never,
    discardFailedIllustration: discardFailedIllustration as never,
  };
  return { deps, state, discardFailedIllustration, pollResult };
}

let container: HTMLDivElement;
let root: Root | null = null;

async function renderChain(deps: unknown): Promise<{ submitIllustration: (input: never) => Promise<void> }> {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  let api: { submitIllustration: (input: never) => Promise<void> } | null = null;
  function C() {
    const chain = useIllustrationChain(deps as never);
    api = chain as never;
    return null;
  }
  await act(async () => {
    root!.render(createElement(C) as ReactElement);
  });
  return api!;
}

async function submitChain(deps: unknown, input: Record<string, unknown>): Promise<void> {
  const api = await renderChain(deps);
  await act(async () => {
    await api.submitIllustration(input as never);
  });
}

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  root = null;
  container?.remove();
  vi.unstubAllGlobals();
});

const baseInput = { prompt: "原始P", messageId: "m1", slotId: "s1", source: "manual" };

// 用例隔离：vi.mock 的调用计数与 mockResolvedValue 覆盖会跨用例泄漏 ⇒ 每例重置默认实现。
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(comfyStatus).mockResolvedValue({ running: true } as never);
  vi.mocked(submitWorkflow).mockResolvedValue({ prompt_id: "pid-1", node_count: 1 } as never);
  vi.mocked(claimIllustrationSubmission).mockResolvedValue({ claimed: true } as never);
  vi.mocked(genProfilePrompt).mockResolvedValue({ prompt: "编译后P", negative_prompt: "" } as never);
});

// ───────────── 行为网 ─────────────
describe("useIllustrationChain 行为网", () => {
  it("未配模板（mediaInsert 空）→ failSlot(configuration,「当前作品没有配置自动插画模板」)，不提交", async () => {
    const md0 = makeDeps({ preset: null });
    await submitChain(md0.deps, { ...baseInput });
    const { discardFailedIllustration } = md0;
    expect(discardFailedIllustration).toHaveBeenCalledTimes(1);
    const call = discardFailedIllustration.mock.calls[0];
    expect(call[2]).toBe("configuration");
    expect(call[3]).toBe("当前作品没有配置自动插画模板");
    expect(submitWorkflow).not.toHaveBeenCalled();
  });

  it("空提示词且无 sceneSpec → failSlot(prompt,「剧情出图提示词为空」)", async () => {
    const md0 = makeDeps();
    await submitChain(md0.deps, { ...baseInput, prompt: "  " });
    const { discardFailedIllustration } = md0;
    const call = discardFailedIllustration.mock.calls[0];
    expect(call[2]).toBe("prompt");
    expect(call[3]).toBe("剧情出图提示词为空");
  });

  it("automatic 认领失败（重试一轮仍 false）→ failSlot(submission_claim)；saveSnapshot 刷新两轮", async () => {
    vi.mocked(claimIllustrationSubmission).mockClear();
    vi.mocked(claimIllustrationSubmission).mockResolvedValue({ claimed: false } as never);
    const md0 = makeDeps();
    await submitChain(md0.deps, { ...baseInput, source: "automatic" });
    const { discardFailedIllustration } = md0;
    const call = discardFailedIllustration.mock.calls[0];
    expect(call[2]).toBe("submission_claim");
    expect(call[3]).toBe("自动插画提交认领失败（槽位未就绪或已被认领）");
    expect(claimIllustrationSubmission).toHaveBeenCalledTimes(2);
    vi.mocked(claimIllustrationSubmission).mockResolvedValue({ claimed: true } as never);
  });

  it("automatic 认领抛错 → failSlot(submission_claim, error.message)，不再重试", async () => {
    vi.mocked(claimIllustrationSubmission).mockClear();
    vi.mocked(claimIllustrationSubmission).mockRejectedValue(new Error("后端挂了"));
    const md0 = makeDeps();
    await submitChain(md0.deps, { ...baseInput, source: "automatic" });
    const { discardFailedIllustration } = md0;
    const call = discardFailedIllustration.mock.calls[0];
    expect(call[2]).toBe("submission_claim");
    expect(call[3]).toBe("后端挂了");
    expect(claimIllustrationSubmission).toHaveBeenCalledTimes(1);
    vi.mocked(claimIllustrationSubmission).mockResolvedValue({ claimed: true } as never);
  });

  it("模板不存在 → failSlot(configuration,「已保存的工作流模板不存在，请重新选择模板」)", async () => {
    const md0 = makeDeps({ templates: [] });
    await submitChain(md0.deps, { ...baseInput });
    const { discardFailedIllustration } = md0;
    const call = discardFailedIllustration.mock.calls[0];
    expect(call[2]).toBe("configuration");
    expect(call[3]).toBe("已保存的工作流模板不存在，请重新选择模板");
  });

  it("LoRA 元数据缺失 → failSlot(lora_metadata,「实际加载的 LoRA 没有精确元数据记录：xxx」)", async () => {
    const preset = {
      templateId: "tpl1", negativePrompt: "", appearanceSource: "worldbook", loraMode: "single",
      characterLoras: { "庄听澜": { loraName: "loraA", loraWeight: 0.8 } },
    };
    const md0 = makeDeps({ preset });
    await submitChain(md0.deps, { ...baseInput });
    const { discardFailedIllustration } = md0;
    const call = discardFailedIllustration.mock.calls[0];
    expect(call[2]).toBe("lora_metadata");
    expect(call[3]).toBe("实际加载的 LoRA 没有精确元数据记录：loraA");
  });

  it("automatic + storedProfile 可用（profile 匹配且非 manual）→ 复用成稿，不调 genProfilePrompt", async () => {
    vi.mocked(genProfilePrompt).mockClear();
    const sceneSpec = { narrative: "剧情N", profile: "krea2", profile_prompt: "成稿P" };
    const md0 = makeDeps();
    await submitChain(md0.deps, { ...baseInput, source: "automatic", sceneSpec });
    const { pollResult } = md0;
    expect(genProfilePrompt).not.toHaveBeenCalled();
    expect(submitWorkflow).toHaveBeenCalledWith(
      "tpl1", expect.anything(), "http://127.0.0.1:8188", "成稿P", [], "single",
    );
    expect(pollResult).toHaveBeenCalledTimes(1);
  });

  it("automatic + 无 storedProfile → genProfilePrompt 编译，prompt 用编译结果", async () => {
    vi.mocked(genProfilePrompt).mockClear();
    const sceneSpec = { narrative: "剧情N", profile: "krea2" };
    await submitChain(makeDeps().deps, { ...baseInput, source: "automatic", sceneSpec });
    expect(genProfilePrompt).toHaveBeenCalledTimes(1);
    expect(submitWorkflow).toHaveBeenCalledWith(
      "tpl1", expect.anything(), "http://127.0.0.1:8188", "编译后P", [], "single",
    );
  });

  it("genProfilePrompt 抛错 → failSlot(prompt_profile, error.message)", async () => {
    vi.mocked(genProfilePrompt).mockClear();
    vi.mocked(genProfilePrompt).mockRejectedValue(new Error("LLM 超时"));
    const sceneSpec = { narrative: "剧情N", profile: "krea2" };
    const md0 = makeDeps();
    await submitChain(md0.deps, { ...baseInput, source: "automatic", sceneSpec });
    const { discardFailedIllustration } = md0;
    const call = discardFailedIllustration.mock.calls[0];
    expect(call[2]).toBe("prompt_profile");
    expect(call[3]).toBe("LLM 超时");
  });

  it("快乐路径：pollResult 7 参（target background、mediaType image）+ reportIllustrationSubmission + 槽位 bindPrompt", async () => {
    const md0 = makeDeps();
    await submitChain(md0.deps, { ...baseInput, source: "automatic" });
    const { pollResult } = md0;
    expect(pollResult).toHaveBeenCalledTimes(1);
    const call = pollResult.mock.calls[0];
    expect(call[0]).toBe("pid-1");
    expect(call[1]).toEqual(["9"]);
    expect(call[3]).toEqual({ messageId: "m1", slotId: "s1", background: true });
    expect(call[4]).toBe("原始P");
    expect(call[5]).toBe("image");
    expect(reportIllustrationSubmission).toHaveBeenCalledTimes(1);
    expect(reportIllustrationSubmission).toHaveBeenCalledWith(expect.objectContaining({
      messageId: "m1", slotId: "s1", source: "automatic",
    }));
  });

  it("ComfyUI 未启动 → failSlot(comfyui_status,「ComfyUI 尚未启动」)", async () => {
    vi.mocked(comfyStatus).mockResolvedValue({ running: false } as never);
    const md0 = makeDeps();
    await submitChain(md0.deps, { ...baseInput });
    const { discardFailedIllustration } = md0;
    const call = discardFailedIllustration.mock.calls[0];
    expect(call[2]).toBe("comfyui_status");
    expect(call[3]).toBe("ComfyUI 尚未启动");
  });

  it("submitWorkflow 无 prompt_id → failSlot(submit,「ComfyUI 没有返回任务 ID」)", async () => {
    vi.mocked(submitWorkflow).mockResolvedValue({} as never);
    const md0 = makeDeps();
    await submitChain(md0.deps, { ...baseInput });
    const { discardFailedIllustration } = md0;
    const call = discardFailedIllustration.mock.calls[0];
    expect(call[2]).toBe("submit");
    expect(call[3]).toBe("ComfyUI 没有返回任务 ID");
  });

  it("高潮点视频（smartVideo + videoTemplateId + 后端视频成稿）→ 主槽出图、图成后视频槽用视频模板", async () => {
    const preset = {
      templateId: "tpl1", videoTemplateId: "vid1", smartVideo: true,
      negativePrompt: "", appearanceSource: "character_card", loraMode: "single",
    };
    const md0 = makeDeps({ preset });
    await submitChain(md0.deps, { ...baseInput, source: "automatic", motion: 3, videoPrompt: "视频P" });
    const { pollResult } = md0;
    // 两段链 ⇒ 两次回填：主槽（图）+ `s1:video`（视频），槽位各在自己的楼层位置上。
    expect(pollResult).toHaveBeenCalledTimes(2);
    expect(pollResult.mock.calls[0][5]).toBe("image");
    expect(pollResult.mock.calls[0][3]).toMatchObject({ slotId: "s1" });
    expect(pollResult.mock.calls[1][5]).toBe("video");
    expect(pollResult.mock.calls[1][3]).toMatchObject({ slotId: "s1:video" });
    // 第一次提交=图片模板（带图片 LoRA 口径），第二次=视频模板（视频 LoRA 不自动接线）
    expect(submitWorkflow).toHaveBeenNthCalledWith(
      1, "tpl1", expect.anything(), "http://127.0.0.1:8188", expect.anything(), [], "single",
    );
    expect(submitWorkflow).toHaveBeenNthCalledWith(
      2, "vid1", expect.anything(), "http://127.0.0.1:8188", expect.anything(), [], "none",
    );
  });

  it("motion=1 但后端编出了视频成稿 → 照常出视频槽（2026-10-31 实锤：词表闸把成稿丢了）", async () => {
    const preset = {
      templateId: "tpl1", videoTemplateId: "vid1", smartVideo: true,
      negativePrompt: "", appearanceSource: "character_card", loraMode: "single",
    };
    const md0 = makeDeps({ preset });
    md0.state.messages = [{
      id: "m1", role: "assistant", text: "正文",
      parts: [{ type: "media-slot", slotId: "s1", status: "pending", kind: "image" }],
    }] as never;
    await submitChain(md0.deps, {
      ...baseInput, source: "automatic", motion: 1,
      videoPrompt: "[元信息]：8 seconds，9:16\n\n[主体/场景]：Bedroom…",
    });
    expect(submitWorkflow).toHaveBeenCalledTimes(2);       // 图 + 视频，各一次
    const parts = (md0.state.messages[0] as unknown as { parts: Record<string, unknown>[] }).parts;
    expect(parts.map((p) => [p.slotId, p.kind])).toEqual([["s1", "image"], ["s1:video", "video"]]);
    expect(md0.discardFailedIllustration).not.toHaveBeenCalled();
  });

  it("后端没编出视频成稿（静态画面）→ 只有图片槽，绝不建视频槽", async () => {
    const preset = {
      templateId: "tpl1", videoTemplateId: "vid1", smartVideo: true,
      negativePrompt: "", appearanceSource: "character_card", loraMode: "single",
    };
    const md0 = makeDeps({ preset });
    md0.state.messages = [{
      id: "m1", role: "assistant", text: "正文",
      parts: [{ type: "media-slot", slotId: "s1", status: "pending", kind: "image" }],
    }] as never;
    await submitChain(md0.deps, { ...baseInput, source: "automatic", motion: 3, videoPrompt: "" });
    expect(submitWorkflow).toHaveBeenCalledTimes(1);       // 只出图
    const parts = (md0.state.messages[0] as unknown as { parts: Record<string, unknown>[] }).parts;
    expect(parts.map((p) => p.slotId)).toEqual(["s1"]);
  });

  it("manual + storedProfile 可用也必须重编译（编译链升级后旧成稿不自愈）", async () => {
    vi.mocked(genProfilePrompt).mockClear();
    const sceneSpec = { narrative: "剧情N", profile: "krea2", profile_prompt: "旧成稿P" };
    await submitChain(makeDeps().deps, { ...baseInput, source: "manual", sceneSpec });
    expect(genProfilePrompt).toHaveBeenCalledTimes(1);
    expect(submitWorkflow).toHaveBeenCalledWith(
      "tpl1", expect.anything(), "http://127.0.0.1:8188", "编译后P", [], "single",
    );
  });

  it("firstlast 独立图片模式 + 帧编译失败 → failSlot(frame_gen)（禁止把中文原文提交给 ComfyUI）", async () => {
    const preset = {
      templateId: "tpl1", firstlast: true, negativePrompt: "",
      appearanceSource: "character_card", loraMode: "single",
    };
    const { deps, discardFailedIllustration } = makeDeps({ preset });
    const sceneSpec = { narrative: "剧情N", profile: "krea2" };
    await submitChain(deps, {
      ...baseInput, source: "automatic", sceneSpec, firstFrameDesc: "首帧画面",
    });
    const call = discardFailedIllustration.mock.calls[0];
    expect(call[2]).toBe("frame_gen");
    expect(call[3]).toBe("首帧生图失败：帧提示词编译失败（首帧），已阻止把中文原文直接提交给 ComfyUI");
    expect(submitWorkflow).not.toHaveBeenCalled();
  });

  it("firstlast 双帧生成：每帧各带 regeneration 快照 + outputNodeIds（帧卡重生成不再走 legacy 兜底）", async () => {
    // 2026-09-22 补：帧路径此前 pollResult 第 2/3 参传 []/undefined ⇒ 帧槽没有 regeneration，
    // 帧卡上的「重新生图」会走 legacy 兜底（按资产库提示词提交一张**普通插画**：模板/逐帧
    // LoRA/尺寸全不是那一帧的），「重新生图（含提示词）」也无从预填。
    vi.mocked(genFramePrompts).mockResolvedValue({
      frames: { first: { prompt: "编译首帧P" }, last: { prompt: "编译尾帧P" } },
    } as never);
    const frameTpl = {
      id: "tpl1", name: "图片模板", node_order: [],
      exposed: [{ node_id: "6", field: "text", semantic: "prompt" }],
      primary_output_node_id: "9",
    } as never;
    const preset = {
      templateId: "tpl1", firstlast: true, negativePrompt: "", appearanceSource: "character_card", loraMode: "single",
    };
    const { deps, pollResult, discardFailedIllustration } = makeDeps({ preset, templates: [frameTpl] });
    await submitChain(deps, {
      ...baseInput, source: "automatic",
      sceneSpec: { narrative: "剧情N", profile: "krea2" },
      firstFrameDesc: "首帧画面", lastFrameDesc: "尾帧画面",
    });
    // 两帧各提交一次、各回填一个槽（不会连坐、也不会只回填一个）
    expect(pollResult).toHaveBeenCalledTimes(2);
    const [firstCall, lastCall] = pollResult.mock.calls;
    // ① outputNodeIds 与主路径同口径（此前是 []）
    expect(firstCall[1]).toEqual(["9"]);
    expect(lastCall[1]).toEqual(["9"]);
    // ② 槽位各是各的：主槽给首帧、":last" 副槽给尾帧
    expect(firstCall[3]).toEqual({ messageId: "m1", slotId: "s1", background: true });
    expect(lastCall[3]).toEqual({ messageId: "m1", slotId: "s1:last", background: true });
    // ③ 快照：逐帧 template 快照，提示词=**该帧自己的**编译产物，values 也按帧绑好
    //    （此前这里是 undefined ⇒ 帧卡「重新生图」走 legacy 兜底换到不同档的图）
    expect(firstCall[2]).toMatchObject({
      kind: "template", templateId: "tpl1", prompt: "编译首帧P", outputNodeIds: ["9"],
      comfyuiUrl: "http://127.0.0.1:8188", values: { "6.text": "编译首帧P" },
    });
    expect(lastCall[2]).toMatchObject({
      kind: "template", templateId: "tpl1", prompt: "编译尾帧P", outputNodeIds: ["9"],
      values: { "6.text": "编译尾帧P" },
    });
    // ④ 两帧不得共用同一份快照（否则点尾帧卡会按首帧提示词重出）
    expect(firstCall[2]).not.toBe(lastCall[2]);
  });

  it("跨机首帧：图片机出帧、视频机取片 ⇒ 帧图改上传到视频机（同机才走 move 原路）", async () => {
    vi.mocked(genFramePrompts).mockResolvedValue({
      frames: { first: { prompt: "编译首帧P" }, last: { prompt: "编译尾帧P" } },
    } as never);
    const preset = {
      templateId: "tpl1", firstlast: true, videoTemplateId: "vid1", smartVideo: true,
      negativePrompt: "", appearanceSource: "character_card", loraMode: "single",
    };
    const md0 = makeDeps({
      preset,
      settings: {
        comfyExtra: [{ id: "vid", name: "视频机", url: "http://video:8188" }],
        comfyRoute: { video: "vid" },
      },
    });
    await submitChain(md0.deps, {
      ...baseInput, source: "automatic", motion: 3,
      sceneSpec: { narrative: "剧情N", profile: "krea2" },
      firstFrameDesc: "首帧画面", lastFrameDesc: "尾帧画面",
    });
    // 帧图在图片机（127.0.0.1）产出的文件名在视频机上不存在 ⇒ 必须取回字节再上传到视频机，
    // 否则正片模板的 first_frame_image/last_frame_image 引用不到文件。
    expect(vi.mocked(uploadRemoteImageToInput)).toHaveBeenCalledTimes(2);
    expect(vi.mocked(uploadRemoteImageToInput).mock.calls[0][1]).toBe("http://video:8188");
    expect(vi.mocked(moveComfyOutputToInput)).not.toHaveBeenCalled();
    // 正片提交到视频机（而不是帧图所在的图片机）
    expect(submitWorkflow).toHaveBeenCalledWith(
      "vid1", expect.anything(), "http://video:8188", expect.anything(), [], "none",
    );
  });

  it("失败槽快照：retryArgs 携带原始入参（source 为原值）", async () => {
    vi.mocked(submitWorkflow).mockResolvedValue({} as never);
    const md0 = makeDeps();
    await submitChain(md0.deps, { ...baseInput, source: "manual", motion: 2 });
    const { discardFailedIllustration } = md0;
    const call = discardFailedIllustration.mock.calls[0];
    expect(call[5]).toEqual(expect.objectContaining({ prompt: "原始P", motion: 2, source: "manual", slotId: "s1" }));
  });
});

// ───────────── 视频首帧底图引用归一（2026-10-31 LoadImage 实锤）─────────────
// 真机事故：底图 url 已是 `/api/comfyui/local-view?path=…`，代码又套一层 localViewUrl
// ⇒ 取回 404 JSON（34 B）当图片上传 ⇒ ComfyUI
// `LoadImage (#61): cannot identify image file '/ComfyUI/input/local-view?path=…'`。
// 两段链落地后，视频那次提交由 `videoOnly` 递归承担（首帧=主槽刚出的成图），
// 归一守卫按「递归携带的 videoBaseUrl 是什么形态」来验——形态覆盖与旧一致。
describe("视频首帧底图上传（引用不二次包装）", () => {
  const LOCAL_VIEW = "/api/comfyui/local-view?path=" + encodeURIComponent(
    "C:\\demo\\userdata\\pictures\\某作品\\SAVE02\\pictures\\workflow_03780d6582af1705874d8c4f.png",
  );
  const VIDEO_WITH_IMAGE = {
    id: "vid1", name: "视频模板", node_order: [],
    image_node_id: "61",
    exposed: [
      { node_id: "61", field: "image", semantic: "image", control: "image", binding: "base_image" },
      { node_id: "333", field: "text", semantic: "prompt" },
    ],
    primary_output_node_id: "67",
  } as never;
  const videoPreset = {
    templateId: "tpl1", videoTemplateId: "vid1", smartVideo: true,
    negativePrompt: "", appearanceSource: "character_card", loraMode: "single",
  };
  // 直接投 `videoOnly` 那次提交（视频槽自己的重试形态）：主槽出图在上一段链里已验。
  const videoOnlyInput = (videoBaseUrl: string) => ({
    ...baseInput, source: "manual", motion: 3, slotId: "s1:video",
    prompt: "视频P", videoPrompt: "视频P", videoOnly: true, videoBaseUrl,
    eventVideoMode: "climax", videoBaseSlotId: "s1",
  });

  it("已完成插画的 local-view 地址原样 fetch，文件名取真实图片名（不把 local-view?path= 当文件名）", async () => {
    const fetchMock = vi.fn(async (_url: string) => ({
      ok: true, status: 200,
      blob: async () => new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const md0 = makeDeps({ preset: videoPreset, templates: [TPL, VIDEO_WITH_IMAGE] });
    await submitChain(md0.deps, videoOnlyInput(LOCAL_VIEW));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    // 二次包装会变成 `local:/api/comfyui/local-view?...`（404 JSON）⇒ 必须逐字相等
    expect(fetchMock.mock.calls[0][0]).toBe(LOCAL_VIEW);
    const file = vi.mocked(uploadImage).mock.calls[0][0];
    expect(file.name).toBe("workflow_03780d6582af1705874d8c4f.png");
    // 上传名回填到模板的 base_image 绑定，视频槽按视频提交
    expect(vi.mocked(submitWorkflow).mock.calls[0][1]).toMatchObject({ "61.image": "uploaded.png" });
    expect(md0.pollResult.mock.calls[0][5]).toBe("video");
    expect(md0.pollResult.mock.calls[0][3]).toMatchObject({ slotId: "s1:video" });
    expect(md0.discardFailedIllustration).not.toHaveBeenCalled();
  });

  it("底图取回 404 ⇒ 不当图片上传，视频分支落 failSlot(upload)", async () => {
    const fetchMock = vi.fn(async (_url: string) => ({
      ok: false, status: 404,
      blob: async () => new Blob([new Uint8Array([0x7b, 0x7d])], { type: "application/json" }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const md0 = makeDeps({ preset: videoPreset, templates: [TPL, VIDEO_WITH_IMAGE] });
    await submitChain(md0.deps, videoOnlyInput(LOCAL_VIEW));

    expect(uploadImage).not.toHaveBeenCalled();
    expect(submitWorkflow).not.toHaveBeenCalled();
    expect(md0.discardFailedIllustration).toHaveBeenCalledTimes(1);
    expect(md0.discardFailedIllustration.mock.calls[0][1]).toBe("s1:video");
    expect(md0.discardFailedIllustration.mock.calls[0][2]).toBe("upload");
    expect(md0.discardFailedIllustration.mock.calls[0][3]).toBe("首帧底图上传 ComfyUI 失败");
  });

  it("裸本地路径（预设风格底图）仍走 localViewUrl 包装（原路径零回归）", async () => {
    const fetchMock = vi.fn(async (_url: string) => ({
      ok: true, status: 200,
      blob: async () => new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const md0 = makeDeps({
      preset: {
        ...videoPreset, appearanceSource: "worldbook", loraMode: "none",
        styleBaseImage: "D:\\out\\风格底图.png",
      },
      templates: [TPL, VIDEO_WITH_IMAGE],
    });
    await submitChain(md0.deps, videoOnlyInput("D:\\out\\风格底图.png"));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("local:D:\\out\\风格底图.png");
    expect(vi.mocked(uploadImage).mock.calls[0][0].name).toBe("风格底图.png");
  });
});

// ───────────── 高潮点「图 + 视频」两段链（2026-10-31 用户定案）─────────────
// 用户原话：「图片提示词和视频提示词还是生成的吧，那也应该支持重新生成这两个才是，
// 可是我并未看到视频重新生成的槽位，只有图片重新生成的槽位，对应视频的槽位就在图片下方就行」
// ⇒ 主槽出图，成图当首帧再出视频，视频槽就在图片槽正下方，两个槽各带自己的重试快照。
describe("高潮点图+视频两段链", () => {
  const preset = {
    templateId: "tpl1", videoTemplateId: "vid1", smartVideo: true,
    negativePrompt: "", appearanceSource: "character_card", loraMode: "single",
  };
  // 真视频模板形态：图像口 + 文本口 + 后端自动绑定的视频节点 width/height（2026-10-31）。
  const VIDEO_WITH_SIZE = {
    id: "vid1", name: "视频模板", node_order: [],
    exposed: [
      { node_id: "61", field: "image", semantic: "image", control: "image", binding: "base_image" },
      { node_id: "333", field: "text", semantic: "prompt" },
      { node_id: "329", field: "width", semantic: "width", control: "number", binding: "video_width" },
      { node_id: "329", field: "height", semantic: "height", control: "number", binding: "video_height" },
    ],
    primary_output_node_id: "67",
  } as never;
  const videoOnlyInput = {
    ...baseInput, source: "manual", motion: 3, slotId: "s1:video",
    prompt: "视频P", videoPrompt: "视频P", videoOnly: true,
    videoBaseUrl: "/api/comfyui/view?filename=out.png", videoBaseSlotId: "s1",
  };
  const partsOf = (md: { state: { messages: unknown[] } }) => (
    md.state.messages[0] as unknown as { parts: Record<string, unknown>[] }
  ).parts;

  beforeEach(() => {
    vi.mocked(getResult).mockResolvedValue({
      status: "completed", error: undefined,
      images: [{ filename: "frame.png", subfolder: "", type: "output" }],
      videos: [], audios: [], texts: [],
    } as never);
  });

  it("视频提交把首帧真实尺寸写进 width/height（覆盖模板里的分辨率连线）", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true, status: 200,
      blob: async () => new Blob([new Uint8Array([0x89, 0x50])], { type: "image/png" }),
    })));
    // 864×1536 = 576×1024 latent ×1.5（Krea2 高清流的成图口径），已在 32 网格上
    vi.stubGlobal("createImageBitmap", async () => ({ width: 864, height: 1536 }));
    const md0 = makeDeps({ preset, templates: [TPL, VIDEO_WITH_SIZE] });
    await submitChain(md0.deps, videoOnlyInput);
    expect(vi.mocked(submitWorkflow).mock.calls[0][1]).toMatchObject({
      "61.image": "uploaded.png", "329.width": 864, "329.height": 1536,
    });
  });

  it("首帧尺寸不在 32 网格 ⇒ 就近取整（节点 min 32 / step 32），不提交非法尺寸", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true, status: 200,
      blob: async () => new Blob([new Uint8Array([0x89, 0x50])], { type: "image/png" }),
    })));
    vi.stubGlobal("createImageBitmap", async () => ({ width: 1000, height: 1500 }));
    const md0 = makeDeps({ preset, templates: [TPL, VIDEO_WITH_SIZE] });
    await submitChain(md0.deps, videoOnlyInput);
    expect(vi.mocked(submitWorkflow).mock.calls[0][1]).toMatchObject({
      "329.width": 992, "329.height": 1504,
    });
  });

  it("量不到首帧尺寸（环境无 createImageBitmap）⇒ 不写这两键，交回模板默认而不是失败", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true, status: 200,
      blob: async () => new Blob([new Uint8Array([0x89, 0x50])], { type: "image/png" }),
    })));
    const md0 = makeDeps({ preset, templates: [TPL, VIDEO_WITH_SIZE] });
    await submitChain(md0.deps, videoOnlyInput);
    const values = vi.mocked(submitWorkflow).mock.calls[0][1] as Record<string, unknown>;
    expect(values["329.width"]).toBeUndefined();
    expect(values["329.height"]).toBeUndefined();
    expect(values["61.image"]).toBe("uploaded.png");
  });

  it("成图失败 ⇒ 视频槽显式落败（stage video_chain）带整条链的重试快照，主槽不被视频侧牵连", async () => {
    vi.mocked(getResult).mockResolvedValueOnce({
      status: "failed", error: "生图节点执行失败",
      images: [], videos: [], audios: [], texts: [],
    } as never);
    const md0 = makeDeps({ preset });
    md0.state.messages = [{
      id: "m1", role: "assistant", text: "正文",
      parts: [{ type: "media-slot", slotId: "s1", status: "pending", kind: "image" }],
    }] as never;
    await submitChain(md0.deps, { ...baseInput, source: "automatic", motion: 3, videoPrompt: "视频P" });

    // 图已提交（主槽仍在途），视频槽已建在图片正下方并带失败原因
    expect(submitWorkflow).toHaveBeenCalledTimes(1);
    expect(partsOf(md0).map((p) => p.slotId)).toEqual(["s1", "s1:video"]);
    const videoSlot = partsOf(md0)[1];
    expect(videoSlot.status).toBe("failed");
    expect(videoSlot.kind).toBe("video");
    expect(String(videoSlot.error)).toContain("[video_chain]");
    expect(String(videoSlot.error)).toContain("生图节点执行失败");
    // 视频槽的「重新生成」= 整条链重跑（快照指向主槽：图回主槽、视频回本槽）
    expect(videoSlot.retryArgs).toEqual(expect.objectContaining({ slotId: "s1", videoPrompt: "视频P" }));
    // 主槽状态没被视频侧改写（仍是在途的 pending）
    expect(partsOf(md0)[0].status).toBe("pending");
  });

  it("两段都在途：视频槽就在图片槽正下方，各自绑自己的任务、都不落败", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true, status: 200,
      blob: async () => new Blob([new Uint8Array([0x89, 0x50])], { type: "image/png" }),
    })));
    const md0 = makeDeps({ preset });
    md0.state.messages = [{
      id: "m1", role: "assistant", text: "正文",
      parts: [{ type: "media-slot", slotId: "s1", status: "pending", kind: "image" }],
    }] as never;
    await submitChain(md0.deps, { ...baseInput, source: "automatic", motion: 3, videoPrompt: "视频P" });

    const [imageSlot, videoSlot] = partsOf(md0);
    // 视频段确已提交（否则「在途」是空跑的假象）
    expect(submitWorkflow).toHaveBeenCalledTimes(2);
    expect(imageSlot.slotId).toBe("s1");
    // 用户原话「对应视频的槽位就在图片下方就行」⇒ 紧邻，不追加到尾部、不并进同一 part
    expect(videoSlot.slotId).toBe("s1:video");
    expect(videoSlot.kind).toBe("video");
    expect(md0.discardFailedIllustration).not.toHaveBeenCalled();
    expect(videoSlot.status).toBe("pending");
  });

  it("视频段失败只落视频槽：重试快照＝只重跑视频（首帧沿用同一张成图），图片槽不受牵连", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true, status: 200,
      blob: async () => new Blob([new Uint8Array([0x89, 0x50])], { type: "image/png" }),
    })));
    // 第一次（图片）拿到 prompt_id，第二次（视频）拿不到 ⇒ 只有视频槽落败
    vi.mocked(submitWorkflow)
      .mockResolvedValueOnce({ prompt_id: "pid-1", node_count: 1 } as never)
      .mockResolvedValue({} as never);
    const md0 = makeDeps({ preset });
    md0.state.messages = [{
      id: "m1", role: "assistant", text: "正文",
      parts: [{ type: "media-slot", slotId: "s1", status: "pending", kind: "image" }],
    }] as never;
    await submitChain(md0.deps, { ...baseInput, source: "automatic", motion: 3, videoPrompt: "视频P" });

    // 视频段与图片段同一收口：discardFailedIllustration(消息, 槽, stage, 原因, "", 重试快照)
    const calls = md0.discardFailedIllustration.mock.calls;
    expect(calls).toHaveLength(1);
    const [messageId, slotId, stage, error, , retryArgs] = calls[0];
    expect(messageId).toBe("m1");
    expect(slotId).toBe("s1:video");
    expect(stage).toBe("submit");
    expect(error).toBe("ComfyUI 没有返回任务 ID");
    // 点它的「重新生成」不再重跑图片：快照只带视频槽自己 + 首帧那张成图 + 归属主槽
    expect(retryArgs).toEqual(expect.objectContaining({
      slotId: "s1:video", videoOnly: true, videoBaseSlotId: "s1", prompt: "视频P",
    }));
    expect(String(retryArgs.videoBaseUrl)).toContain("frame.png");
    expect(JSON.stringify(retryArgs)).not.toContain("undefined");
    // 图片槽已绑上自己的任务，没被视频侧牵连
    const [imageSlot] = partsOf(md0);
    expect(imageSlot.status).toBe("pending");
    expect(imageSlot.promptId).toBe("pid-1");
  });
});

// ───────────── 第 74 刀：首尾帧多批次「段桥」链（2026-10-31 用户定案）─────────────
// 用户原话：「首尾帧生成就是前面的图片放首帧后面的图片放尾帧生成视频」
// ⇒ 每张 extra：主槽只出**本锚点画面的图**，图正下方挂视频槽；
//   图片1（首帧）＝上一张画面的**成图**（后端按正文序标注 bridgePrevSlotId），
//   图片2（尾帧）＝本槽成图。取不到上一张 ⇒ 退「高潮片段延续」（两帧同本图），绝不空图提交。
describe("首尾帧多批次段桥链", () => {
  const preset = {
    templateId: "tpl1", videoTemplateId: "vid1",
    negativePrompt: "", appearanceSource: "character_card", loraMode: "single",
    // 段桥时长走**转场**口径（8），不是高潮正片口径（5）
    transitionDurationHint: 8, videoDurationHint: 5,
  };
  // 真视频模板形态：底图口 + **首/尾帧两个图像口**（binding 名 = 图片1 / 图片2）+ 时长口
  const FRAME_TPL = {
    id: "vid1", name: "视频模板", node_order: [],
    exposed: [
      { node_id: "60", field: "image", semantic: "image", control: "image", binding: "base_image" },
      { node_id: "61", field: "first", semantic: "image", control: "image", binding: "first_frame_image" },
      { node_id: "62", field: "last", semantic: "image", control: "image", binding: "last_frame_image" },
      { node_id: "58", field: "duration", control: "number", binding: "video_duration" },
      { node_id: "333", field: "text", semantic: "prompt" },
    ],
    primary_output_node_id: "67",
  } as never;
  const partsOf = (md: { state: { messages: unknown[] } }) => (
    md.state.messages[0] as unknown as { parts: Record<string, unknown>[] }
  ).parts;
  const valuesOf = (call: number) => vi.mocked(submitWorkflow).mock.calls[call][1] as Record<string, unknown>;
  const prevReady = { type: "image", slotId: "s0", status: "ready", url: "/api/comfyui/local-view?path=D:/out/prev.png" };
  const pendingMain = { type: "media-slot", slotId: "s1", status: "pending", kind: "image" };
  const bridgeInput = (over: Record<string, unknown> = {}) => ({
    ...baseInput, source: "automatic", motion: 3,
    eventVideoMode: "firstlast", videoPrompt: "",
    transitionVideoPrompt: "转场P", transitionVideoParams: { duration: 6 },
    bridgePrevSlotId: "s0", ...over,
  });

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true, status: 200,
      blob: async () => new Blob([new Uint8Array([0x89, 0x50])], { type: "image/png" }),
    })));
    // 上一张成图 = 跨机搬运（图片机产物 → 视频机 input），本张 = 底图口直传
    vi.mocked(uploadRemoteImageToInput).mockResolvedValue("remote.png" as never);
    vi.mocked(uploadImage).mockResolvedValue({ name: "uploaded.png" } as never);
  });

  it("段桥主槽出普通图，视频槽就在图片正下方（两支都提交）", async () => {
    const md0 = makeDeps({ preset, templates: [TPL, FRAME_TPL] });
    md0.state.messages = [{ id: "m1", role: "assistant", text: "正文", parts: [prevReady, pendingMain] }] as never;
    await submitChain(md0.deps, bridgeInput());

    expect(submitWorkflow).toHaveBeenCalledTimes(2);
    // 主槽＝图片模板（不是视频模板）：视频模板的键只出现在第二次提交里
    expect(valuesOf(0)["61.first"]).toBeUndefined();
    expect(valuesOf(1)["333.text"]).toBeTruthy();
    expect(partsOf(md0).map((p) => [p.slotId, p.kind || p.type])).toEqual([
      ["s0", "image"], ["s1", "image"], ["s1:video", "video"],
    ]);
    expect(md0.discardFailedIllustration).not.toHaveBeenCalled();
  });

  it("图片1＝上一张画面的成图、图片2＝本槽成图（用户口径的方向锁）", async () => {
    const md0 = makeDeps({ preset, templates: [TPL, FRAME_TPL] });
    md0.state.messages = [{ id: "m1", role: "assistant", text: "正文", parts: [prevReady, pendingMain] }] as never;
    await submitChain(md0.deps, bridgeInput());

    // 上一张走跨机搬运，取的就是 bridgePrevSlotId 那个槽的成图 url
    const uploaded = vi.mocked(uploadRemoteImageToInput).mock.calls.map((c) => String(c[0]));
    expect(uploaded).toContain("/api/comfyui/local-view?path=D:/out/prev.png");
    expect(valuesOf(1)["61.first"]).toBe("remote.png");     // 图片1 = 前面那张
    expect(valuesOf(1)["62.last"]).toBe("uploaded.png");    // 图片2 = 本槽这张
    expect(valuesOf(1)["61.first"]).not.toBe(valuesOf(1)["62.last"]);
  });

  it("桥视频用转场成稿与转场时长，不再重跑帧循环（不额外生成两张帧图）", async () => {
    const md0 = makeDeps({ preset, templates: [TPL, FRAME_TPL] });
    md0.state.messages = [{ id: "m1", role: "assistant", text: "正文", parts: [prevReady, pendingMain] }] as never;
    await submitChain(md0.deps, bridgeInput());

    expect(submitWorkflow).toHaveBeenCalledTimes(2);        // 图 + 视频，没有第三张帧图
    expect(valuesOf(1)["333.text"]).toBe("转场P");
    expect(valuesOf(1)["58.duration"]).toBe(8);            // 转场口径，不是正片的 5
    expect(md0.pollResult.mock.calls[1][3]).toMatchObject({ slotId: "s1:video" });
  });

  it("上一张已失败 ⇒ 取不到真图 ⇒ 两帧同本图（退高潮片段延续，不空图提交）", async () => {
    const md0 = makeDeps({ preset, templates: [TPL, FRAME_TPL] });
    md0.state.messages = [{
      id: "m1", role: "assistant", text: "正文",
      parts: [{ type: "media-slot", slotId: "s0", status: "failed", kind: "image" }, pendingMain],
    }] as never;
    await submitChain(md0.deps, bridgeInput());

    expect(vi.mocked(uploadRemoteImageToInput)).not.toHaveBeenCalled();
    expect(valuesOf(1)["61.first"]).toBe("uploaded.png");
    expect(valuesOf(1)["62.last"]).toBe("uploaded.png");
    expect(partsOf(md0)[2].status).toBe("pending");         // 视频照常提交，不是失败槽
  });

  it("本楼层没有前一张（bridgePrevSlotId 缺席）⇒ 退上一楼层尾帧成图", async () => {
    const tailUrl = "/api/comfyui/local-view?path=D:/out/tail.png";
    const md0 = makeDeps({
      preset, templates: [TPL, FRAME_TPL],
      messages: [{
        id: "m0", role: "assistant", text: "上一楼层",
        parts: [{ type: "video", slotId: "v0", status: "ready", url: "/api/x.mp4",
                  lastFrameDesc: "上一楼层收束画面", lastFrameUrl: tailUrl }],
      }] as never,
    });
    md0.state.messages = [{ id: "m1", role: "assistant", text: "正文", parts: [pendingMain] }] as never;
    await submitChain(md0.deps, bridgeInput({ bridgePrevSlotId: "" }));

    expect(vi.mocked(uploadRemoteImageToInput).mock.calls.map((c) => String(c[0]))).toContain(tailUrl);
    expect(valuesOf(1)["61.first"]).toBe("remote.png");
  });

  it("试跑：段桥也摆出视频槽并上报图与视频两份成稿", async () => {
    const md0 = makeDeps({
      preset, templates: [TPL, FRAME_TPL],
      settings: { promptOnlyGeneration: true },
    });
    md0.state.messages = [{ id: "m1", role: "assistant", text: "正文", parts: [prevReady, pendingMain] }] as never;
    await submitChain(md0.deps, bridgeInput());

    expect(submitWorkflow).not.toHaveBeenCalled();
    const reported = vi.mocked(reportIllustrationSubmission).mock.calls.map((c) => c[0] as Record<string, unknown>);
    expect(reported.map((r) => r.slotId)).toEqual(["s1", "s1:video"]);
    expect(reported.map((r) => r.prompt)).toEqual(["原始P", "转场P"]);
    expect(partsOf(md0).map((p) => [p.slotId, p.status])).toEqual([
      ["s0", "ready"], ["s1", "failed"], ["s1:video", "failed"],
    ]);
  });
});

// ───────────── 第 74 刀：有界等待上一张成图（waitForSlotMedia）─────────────
describe("waitForSlotMedia 有界等待", () => {
  const slot = (
    status: SlotMediaState["status"], mediaType: SlotMediaState["mediaType"], url?: string,
  ): SlotMediaState => ({ status, mediaType, ...(url ? { url } : {}) });
  const ready = slot("ready", "image", "/api/x.png");
  it("槽已是 ready 图片 ⇒ 立刻交真 url，不进入等待", async () => {
    const read = vi.fn(() => ready);
    await expect(waitForSlotMedia(read, "s0", { timeoutMs: 5, tickMs: 1 })).resolves.toBe("/api/x.png");
    expect(read).toHaveBeenCalledTimes(1);
  });

  it("槽不存在 / 已失败 ⇒ 立刻交空串（绝不空等一轮）", async () => {
    await expect(waitForSlotMedia(() => undefined, "s0", { timeoutMs: 5, tickMs: 1 })).resolves.toBe("");
    await expect(waitForSlotMedia(
      () => slot("failed", "image"), "s0", { timeoutMs: 5, tickMs: 1 },
    )).resolves.toBe("");
  });

  it("在途 ⇒ 每 tick 复查，转成 ready 图片后取到", async () => {
    let n = 0;
    const read = () => {
      n += 1;
      return n < 3 ? slot("pending", "image") : ready;
    };
    await expect(waitForSlotMedia(read, "s0", { timeoutMs: 200, tickMs: 1 })).resolves.toBe("/api/x.png");
    expect(n).toBe(3);
  });

  it("超时 / ready 但不是图片 ⇒ 交空串，调用方自行降级", async () => {
    await expect(waitForSlotMedia(
      () => slot("pending", "image"), "s0", { timeoutMs: 10, tickMs: 1 },
    )).resolves.toBe("");
    await expect(waitForSlotMedia(
      () => slot("ready", "video", "/api/x.mp4"), "s0", { timeoutMs: 5, tickMs: 1 },
    )).resolves.toBe("");
  });

  it("槽 id 为空 ⇒ 直接空串（不拿空槽去查）", async () => {
    const read = vi.fn(() => ready);
    await expect(waitForSlotMedia(read, "", { timeoutMs: 5, tickMs: 1 })).resolves.toBe("");
    expect(read).not.toHaveBeenCalled();
  });
});

// ───────────── 试跑（只生成提示词、不提交 ComfyUI）2026-09-28 ─────────────
// 用户需求：跑剧情时先只生成提示词、不参与 ComfyUI 出图（批量生图 ≈4.7 分钟/张，审查不该等），
// 且开关要**同时涵盖生图与生成视频**。语义＝编译链一字不改，只把「提交＋轮询」换成「上报 trace」。
describe("试跑开关（promptOnlyGeneration）", () => {
  /** 造一个带 pending 插画槽的楼层，用来断言试跑终态（槽位必须可见、可重新生成、绝不悬挂）。 */
  const seededMessage = () => ({
    id: "m1", role: "assistant", text: "正文",
    parts: [{ type: "media-slot", slotId: "s1", status: "pending", kind: "image" }],
  }) as never;

  it("生图：不查 ComfyUI、不提交、不轮询；成稿以 promptId 空上报 trace；槽位落试跑终态", async () => {
    const md0 = makeDeps({ settings: { promptOnlyGeneration: true } });
    md0.state.messages = [seededMessage()];
    await submitChain(md0.deps, { ...baseInput, source: "automatic" });

    // ① 全程不碰 ComfyUI（状态检查也不查：ComfyUI 没开也必须能试跑）
    expect(comfyStatus).not.toHaveBeenCalled();
    expect(submitWorkflow).not.toHaveBeenCalled();
    expect(md0.pollResult).not.toHaveBeenCalled();
    // ② 成稿照常上报 —— 与真出图**同一形状**（交接文档 §6 的六项检查表在试跑下原样可用）
    expect(reportIllustrationSubmission).toHaveBeenCalledTimes(1);
    expect(reportIllustrationSubmission).toHaveBeenCalledWith(expect.objectContaining({
      messageId: "m1", slotId: "s1", promptId: "", prompt: "原始P", source: "automatic",
    }));
    // ③ 槽位不悬挂：终态可见、带原因、带重试快照（可重新生成）
    const slot = (md0.state.messages[0] as unknown as { parts: Record<string, unknown>[] }).parts[0];
    expect(slot.status).toBe("failed");
    expect(String(slot.error)).toContain("试跑");
    expect(slot.retryArgs).toEqual(expect.objectContaining({ prompt: "原始P", slotId: "s1" }));
    // 快照同时带**编译成稿**（2026-10-18 成稿直送；本用例无 sceneSpec ⇒ 成稿=原始P）
    expect((slot.retryArgs as { finalPrompt?: string }).finalPrompt).toBe("原始P");
  });

  it("涵盖生成视频：两段链的图与视频都不提交，但两份成稿各上报一次、两个槽都落终态", async () => {
    const preset = {
      templateId: "tpl1", videoTemplateId: "vid1", smartVideo: true,
      negativePrompt: "", appearanceSource: "character_card", loraMode: "single",
    };
    const md0 = makeDeps({ preset, settings: { promptOnlyGeneration: true } });
    md0.state.messages = [seededMessage()];
    await submitChain(md0.deps, {
      ...baseInput, source: "automatic", motion: 3, videoPrompt: "视频P",
    });

    expect(submitWorkflow).not.toHaveBeenCalled();
    expect(md0.pollResult).not.toHaveBeenCalled();
    const reported = vi.mocked(reportIllustrationSubmission).mock.calls.map(
      (c) => c[0] as { slotId: string; templateId: string; prompt: string; promptId: string; loraMode: string },
    );
    // 主槽=图片模板成稿；视频槽=事件带来的视频成稿（两段各一条，试跑产出必须两条都在）
    expect(reported.map((r) => r.slotId)).toEqual(["s1", "s1:video"]);
    expect(reported[0]).toMatchObject({ templateId: "tpl1", prompt: "原始P", loraMode: "single" });
    expect(reported[1]).toMatchObject({ templateId: "vid1", prompt: "视频P", loraMode: "none" });
    expect(reported.every((r) => r.promptId === "")).toBe(true);
    const parts = (md0.state.messages[0] as unknown as { parts: Record<string, unknown>[] }).parts;
    expect(parts.map((p) => [p.slotId, p.status, p.kind])).toEqual([
      ["s1", "failed", "image"],
      ["s1:video", "failed", "video"],   // 视频槽就在图片正下方，且标着 video
    ]);
    // 视频槽的重试快照=整条链（图用成稿直送、图成后照常接视频）
    expect(parts[1].retryArgs).toEqual(expect.objectContaining({
      slotId: "s1", finalPrompt: "原始P", videoPrompt: "视频P",
    }));
  });

  it("涵盖首尾帧生图：两帧提示词各上报一次、一次都不提交", async () => {
    vi.mocked(genFramePrompts).mockResolvedValue({
      frames: { first: { prompt: "编译首帧P" }, last: { prompt: "编译尾帧P" } },
    } as never);
    const frameTpl = {
      id: "tpl1", name: "图片模板", node_order: [],
      exposed: [{ node_id: "6", field: "text", semantic: "prompt" }],
      primary_output_node_id: "9",
    } as never;
    const preset = {
      templateId: "tpl1", firstlast: true, negativePrompt: "",
      appearanceSource: "character_card", loraMode: "single",
    };
    const md0 = makeDeps({ preset, templates: [frameTpl], settings: { promptOnlyGeneration: true } });
    md0.state.messages = [seededMessage()];   // 独立图片模式会跳过主提交块 ⇒ 主槽必须仍不悬挂
    await submitChain(md0.deps, {
      ...baseInput, source: "automatic",
      sceneSpec: { narrative: "剧情N", profile: "krea2" },
      firstFrameDesc: "首帧画面", lastFrameDesc: "尾帧画面",
    });

    expect(submitWorkflow).not.toHaveBeenCalled();
    expect(md0.pollResult).not.toHaveBeenCalled();
    const reported = vi.mocked(reportIllustrationSubmission).mock.calls.map(
      (c) => c[0] as { prompt: string; slotId: string; promptId: string },
    );
    expect(reported.map((r) => r.prompt)).toEqual(["编译首帧P", "编译尾帧P"]);
    expect(reported.map((r) => r.slotId)).toEqual(["s1:first", "s1:last"]);
    expect(reported.every((r) => r.promptId === "")).toBe(true);
    // 主槽落试跑终态（该模式跳过主提交块，若不标记就会永久 pending）
    const mainSlot = (md0.state.messages[0] as unknown as { parts: Record<string, unknown>[] }).parts[0];
    expect(mainSlot.status).toBe("failed");
    expect(String(mainSlot.error)).toContain("试跑");
    // 主槽快照带双帧成稿 ⇒ 重试可逐字提交两帧（不再调 genFramePrompts 重编译）
    expect((mainSlot.retryArgs as { finalFramePrompts?: { first?: string; last?: string } }).finalFramePrompts)
      .toEqual({ first: "编译首帧P", last: "编译尾帧P" });
  });

  it("开关缺省（false）⇒ 试跑闸完全惰性：照常查 ComfyUI 并提交（零回归）", async () => {
    const md0 = makeDeps({ settings: { promptOnlyGeneration: false } });
    await submitChain(md0.deps, { ...baseInput, source: "automatic" });
    expect(comfyStatus).toHaveBeenCalledTimes(1);
    expect(submitWorkflow).toHaveBeenCalledTimes(1);
    expect(md0.pollResult).toHaveBeenCalledTimes(1);
  });

  it("试跑槽重试＝成稿直送：开关仍开也提交 ComfyUI，且不再重编译（2026-10-18 用户定案）", async () => {
    // 用户定案：试跑槽的「重新生成」意思是「直接用成稿跑图」——拿 trace 里那版编译
    // 成稿逐字提交，不走重编译（2026-08-30「manual 必须重编译」是有范围定案：
    // 普通失败槽仍重编译，见下一条；试跑成稿是用户审查过的对象， verbatim 才是本意）。
    const withSpec = { ...baseInput, source: "automatic",
                       sceneSpec: { narrative: "剧情N", profile: "krea2" } };

    // ① 试跑：不提交、trace 上报（promptId 空）、槽位终态 + 带成稿的快照
    const md0 = makeDeps({ settings: { promptOnlyGeneration: true } });
    md0.state.messages = [seededMessage()];
    await submitChain(md0.deps, withSpec);
    expect(submitWorkflow).not.toHaveBeenCalled();
    const slot0 = (md0.state.messages[0] as unknown as { parts: Record<string, unknown>[] }).parts[0];
    const snapshot = slot0.retryArgs as Record<string, unknown> & { finalPrompt?: string };
    expect(snapshot.prompt).toBe("原始P");            // 原始入参仍在快照里
    expect(snapshot.finalPrompt).toBe("编译后P");     // 成稿一并入快照

    // ② 点「重新生成」——试跑开关**不关**也照常出图（显式动作优先于批量开关）
    const md1 = makeDeps({ settings: { promptOnlyGeneration: true } });
    await submitChain(md1.deps, { ...snapshot, source: "manual" });
    expect(submitWorkflow).toHaveBeenCalledTimes(1);
    expect(vi.mocked(submitWorkflow).mock.calls[0][3]).toBe("编译后P");  // 成稿逐字提交
    expect(vi.mocked(genProfilePrompt)).toHaveBeenCalledTimes(1);       // 不再重编译（仅试跑那轮）
    const retryReport = vi.mocked(reportIllustrationSubmission).mock.calls[1][0] as {
      promptId: string; prompt: string;
    };
    expect(retryReport.promptId).toBe("pid-1");       // 真提交 ⇒ 带 promptId
    expect(retryReport.prompt).toBe("编译后P");
    expect(md1.pollResult).toHaveBeenCalledTimes(1);  // 走正常回填入库链
  });

  it("普通失败槽（快照无成稿）重试仍重编译（2026-08-30 定案不回退）", async () => {
    // 首轮：ComfyUI 没起 → failSlot(comfyui_status)；快照只有原始入参、无 finalPrompt
    // ⚠ 夹具的 discardFailedIllustration 是空 mock（不真改消息状态）⇒ 从调用参数取快照
    vi.mocked(comfyStatus).mockResolvedValue({ running: false } as never);
    const md0 = makeDeps({ settings: { promptOnlyGeneration: false } });
    md0.state.messages = [seededMessage()];
    await submitChain(md0.deps, { ...baseInput, source: "automatic",
                                  sceneSpec: { narrative: "剧情N", profile: "krea2" } });
    expect(submitWorkflow).not.toHaveBeenCalled();
    expect(md0.discardFailedIllustration).toHaveBeenCalledTimes(1);
    expect(md0.discardFailedIllustration.mock.calls[0][2]).toBe("comfyui_status");
    const snapshot = md0.discardFailedIllustration.mock.calls[0][5] as Record<string, unknown>;
    expect(snapshot.finalPrompt).toBeUndefined();     // 无成稿
    // 二轮：ComfyUI 起了，重试 ⇒ 照旧重编译后提交（编译链升级能生效）
    vi.mocked(comfyStatus).mockResolvedValue({ running: true } as never);
    const md1 = makeDeps({ settings: { promptOnlyGeneration: false } });
    await submitChain(md1.deps, { ...snapshot, source: "manual" });
    expect(submitWorkflow).toHaveBeenCalledTimes(1);
    expect(vi.mocked(genProfilePrompt)).toHaveBeenCalledTimes(2);  // 两轮都编译
  });
});

// ───────────── 逐字搬移锁 + 源码形态锁 ─────────────
describe("逐字搬移锁（T2.2-A 插画链）", () => {
  const MOVED: [string, number][] = [
    ["const preset = settings.mediaInsert?.[repo?.id || \"\"];", 1],
    ["if (!claim.claimed) {", 1],
    ["const useVideo = resolveVideoTemplateChoice(preset, videoMode, videoPrompt);", 1],
    ["const useFirstlastImages = videoMode === \"firstlast\" && !!preset.templateId && !videoOnly && !isSegmentBridge;", 1],
    ["throw new Error(`实际加载的 LoRA 没有精确元数据记录：${lora.name}`);", 1],
    ["prompt = withLoraTriggers(prompt);", 1],
    ["const st = await comfyStatus(comfyMainUrl);", 1],
    ["failSlot(\"submit\", \"ComfyUI 没有返回任务 ID\");", 1],
    ["pollResult(r.prompt_id, outputNodeIds, regeneration, target, prompt, mainIsVideo ? \"video\" : \"image\", baseSlotRef);", 1],
  ];

  it("判定/副作用语句在新属主按次数出现", () => {
    const seam = stripComments(read(SEAM));
    for (const [needle, n] of MOVED) {
      expect([needle, count(seam, needle)]).toEqual([needle, n]);
    }
  });

  it("同样的语句在视图缺席（确属搬移，而非复制）；三个调用点与重试链仍留在视图", () => {
    const view = stripComments(read(VIEW));
    // ⚠ `const preset = settings.mediaInsert?...` 不进缺席清单：submitAudio（留在视图）里有同款语句。
    for (const needle of [
      "const useVideo = resolveVideoTemplateChoice(preset, videoMode, videoPrompt);",
      "const failSlot = (stage: string, error: string) =>",
      "const withLoraTriggers = (text: string): string =>",
    ]) {
      expect([needle, count(view, needle)]).toEqual([needle, 0]);
    }
    // 调用点留在视图：handleAgentStreamEvent / regenerateResult / retryIllustration
    expect([count(view, "void submitIllustration(illustrationSubmit).catch"), 1]).toEqual([count(view, "void submitIllustration(illustrationSubmit).catch"), 1]);
    expect([count(view, "void submitIllustration({ ...snapshot, source: \"manual\" });"), 1]).toEqual([count(view, "void submitIllustration({ ...snapshot, source: \"manual\" });"), 1]);
  });
});

describe("源码形态锁（T2.2-A 插画链）", () => {
  it("试跑闸只认显式 `=== true`（旧数据/字符串 \"false\" 不得开启试跑）", () => {
    const seam = stripComments(read(SEAM));
    // 2026-10-18：尾部追加 `&& !useFinalDraft`（成稿直送旁路）——显式 === true 的前提不变
    expect(seam).toContain("const promptOnly = settings.promptOnlyGeneration === true && !useFinalDraft;");
  });

  it("视图恰一次使用 + 调用块逐字（14 deps 同名接线）", () => {
    const view = read(VIEW);
    const block = [
      "  const { submitIllustration } = useIllustrationChain({",
      "    settings, templates, repo, threadId, cardNames, promptProfile, characterBaseImages,",
      "    chat, modelProxies, effectivePersona, messagesRef, setMessages,",
      "    pollResult, discardFailedIllustration,",
      "  });",
    ].join("\n");
    expect(view.includes(block)).toBe(true);
    expect([count(view, "useIllustrationChain("), 1]).toEqual([count(view, "useIllustrationChain("), 1]);
  });
});
