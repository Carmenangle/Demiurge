// @vitest-environment jsdom
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ChatMessage } from "../types/chat";
import { useIllustrationChain } from "./useIllustrationChain";

// T2.2-A：自动插画/视频提交链（submitIllustration）从 useChatSession 外移后的回归网。
// 三件证据互补：① 直断言行为网（失败槽 stage/payload、编译链、pollResult 接线）
// ② 逐字搬移锁（判定语句按次数钉在新属主、且在视图缺席）③ 源码形态锁（调用块逐字）。
// 硬要求：每个用例必须卸载；源码只能在 it 回调内读（jsdom 模块求值期 import.meta.url 非 file:）。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../api/comfyui", () => ({
  comfyStatus: vi.fn(async () => ({ running: true })),
  submitWorkflow: vi.fn(async () => ({ prompt_id: "pid-1", node_count: 1 })),
  interruptComfy: vi.fn(async () => undefined),
  uploadImage: vi.fn(async () => ({ name: "uploaded.png" })),
  moveComfyOutputToInput: vi.fn(async () => "moved.png"),
  uploadRemoteImageToInput: vi.fn(async () => "remote.png"),
  localViewUrl: (p: string) => `local:${p}`,
  pollWorkflowResult: vi.fn(async () => ({ kind: "complete", result: { images: ["frame.png"] } })),
}));

vi.mock("../api/ai", () => ({
  saveSnapshot: vi.fn(async () => undefined),
  claimIllustrationSubmission: vi.fn(async () => ({ claimed: true })),
  genProfilePrompt: vi.fn(async () => ({ prompt: "编译后P", negative_prompt: "" })),
  genFramePrompts: vi.fn(async () => ({ frames: {} })),
  reportIllustrationSubmission: vi.fn(async () => undefined),
  reportIllustrationFailure: vi.fn(async () => undefined),
}));

vi.mock("../api/loras", () => ({
  listLoras: vi.fn(async () => ({ items: [] })),
}));

import {
  comfyStatus, submitWorkflow,
} from "../api/comfyui";
import {
  claimIllustrationSubmission, genProfilePrompt, reportIllustrationSubmission,
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
}

function makeDeps(over: DepsOver = {}) {
  const state = { messages: [] as ChatMessage[] };
  const setMessages = vi.fn((fn: unknown) => {
    state.messages = typeof fn === "function" ? (fn as (m: ChatMessage[]) => ChatMessage[])(state.messages) : (fn as ChatMessage[]);
    return state.messages;
  });
  const messagesRef = { current: [] as ChatMessage[] };
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
    cardNames: ["沈糯"] as never,
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
      characterLoras: { "沈糯": { loraName: "loraA", loraWeight: 0.8 } },
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

  it("视频分支（smartVideo + videoTemplateId + motion>=2）→ 主槽 mediaType「video」、模板用视频模板", async () => {
    const preset = {
      templateId: "tpl1", videoTemplateId: "vid1", smartVideo: true,
      negativePrompt: "", appearanceSource: "character_card", loraMode: "single",
    };
    const md0 = makeDeps({ preset });
    await submitChain(md0.deps, { ...baseInput, source: "automatic", motion: 3 });
    const { pollResult } = md0;
    expect(pollResult).toHaveBeenCalledTimes(1);
    expect(pollResult.mock.calls[0][5]).toBe("video");
    expect(submitWorkflow).toHaveBeenCalledWith(
      "vid1", expect.anything(), "http://127.0.0.1:8188", expect.anything(), [], "none",
    );
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

  it("失败槽快照：retryArgs 携带原始入参（source 为原值）", async () => {
    vi.mocked(submitWorkflow).mockResolvedValue({} as never);
    const md0 = makeDeps();
    await submitChain(md0.deps, { ...baseInput, source: "manual", motion: 2 });
    const { discardFailedIllustration } = md0;
    const call = discardFailedIllustration.mock.calls[0];
    expect(call[5]).toEqual(expect.objectContaining({ prompt: "原始P", motion: 2, source: "manual", slotId: "s1" }));
  });
});

// ───────────── 逐字搬移锁 + 源码形态锁 ─────────────
describe("逐字搬移锁（T2.2-A 插画链）", () => {
  const MOVED: [string, number][] = [
    ["const preset = settings.mediaInsert?.[repo?.id || \"\"];", 1],
    ["if (!claim.claimed) {", 1],
    ["const useVideo = resolveVideoTemplateChoice(preset, videoMode, motion);", 1],
    ["const useFirstlastImages = videoMode === \"firstlast\" && !!preset.templateId;", 1],
    ["throw new Error(`实际加载的 LoRA 没有精确元数据记录：${lora.name}`);", 1],
    ["prompt = withLoraTriggers(prompt);", 1],
    ["const st = await comfyStatus(settings.comfyuiUrl);", 1],
    ["failSlot(\"submit\", \"ComfyUI 没有返回任务 ID\");", 1],
    ["pollResult(r.prompt_id, outputNodeIds, regeneration, target, prompt, useVideo ? \"video\" : \"image\", baseSlotRef);", 1],
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
      "const useVideo = resolveVideoTemplateChoice(preset, videoMode, motion);",
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
