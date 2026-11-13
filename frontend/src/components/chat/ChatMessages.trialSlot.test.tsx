// @vitest-environment jsdom
//
// 第 88 刀（2026-11-05）：试跑失败槽的「重新生成」口径必须**真渲染出来**。
//
// 它替掉的旧锁＝ `useChatSession.contract.test.ts` 里那句
// `expect(chatMessages).toContain("用试跑成稿直接提交 ComfyUI 出图")`：纯源码 grep 只证明
// 「这串字在文件里」，把判定式改坏、文案仍躺在源码里，锁照样绿＝假绿。这里按真渲染钉三条：
//   ① 试跑槽（error 由 markMediaSlotFailed 拼成 `[prompt_only] 试跑…`）⇒ 成稿直送口径；
//   ② 普通失败槽（`[comfyui_status] …`）⇒ 原参数重生成口径，两种文案**不混词**；
//   ③ 已认领却未落定的 pending 槽 ⇒ 界面画「插画生成中…」＋停止键——
//      这正是用户报的「生产中」僵尸形状（前端把试跑改走失败通道后，服务端不再回位这种槽）。

import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("dompurify", () => ({ default: { sanitize: (html: string) => html } }));
vi.mock("../../lib/planTaskActivity", () => ({
  approvePlanTask: vi.fn(), cancelPlanTask: vi.fn(), getPlanTask: vi.fn(),
  keepRecipe: vi.fn(), deleteRecipe: vi.fn(), listRecipes: vi.fn(async () => ({})),
}));

import { AssistantMessage } from "./ChatMessages";

let container: HTMLDivElement;
let root: Root;

afterEach(() => {
  act(() => { root.unmount(); });
  container.remove();
});

const TRIAL_NOTE = "试跑：只生成提示词，未提交 ComfyUI（图片不出图）；"
  + "成稿见 trace 的 illustration.submitted；点「重新生成」用成稿直接出图";
const VIDEO_TRIAL_NOTE = "试跑：只生成提示词，未提交 ComfyUI（视频不出片）；"
  + "成稿见 trace 的 illustration.submitted；点「重新生成」用成稿直接出视频";

function slotMsg(part: Record<string, unknown>) {
  return { id: "m1", role: "assistant" as const, text: "正文", parts: [part] };
}

function mount(part: Record<string, unknown>, onRetryIllustration = () => {}) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(
      <AssistantMessage
        msg={slotMsg(part) as never}
        onSendImage={() => {}}
        onRetryIllustration={onRetryIllustration}
        onCancelGeneration={() => {}}
      />,
    );
  });
  return container;
}

function buttonByText(dom: HTMLElement, text: string): HTMLButtonElement | undefined {
  return [...dom.querySelectorAll("button")].find(
    (button) => button.textContent?.includes(text),
  ) as HTMLButtonElement | undefined;
}

describe("试跑槽与失败槽的重新生成口径（真渲染）", () => {
  it("试跑槽 title＝成稿直送（不受试跑开关影响），且不再画「生成中」", () => {
    const dom = mount({
      type: "media-slot", slotId: "s1", status: "failed", kind: "image",
      error: `[prompt_only] ${TRIAL_NOTE}`, retryArgs: { slotId: "s1", finalPrompt: "编译后P" },
    });
    const button = dom.querySelector(".media-slot-retry");
    expect(button).not.toBeNull();
    expect(button!.getAttribute("title")).toBe("用试跑成稿直接提交 ComfyUI 出图（不受试跑开关影响）");
    expect(dom.querySelector(".media-slot-generating")).toBeNull();
    expect(dom.textContent).toContain("试跑：只生成提示词");
  });

  it("普通失败槽 title＝原参数重生成（图/视频各一条），不与成稿直送混词", () => {
    const image = mount({
      type: "media-slot", slotId: "s2", status: "failed", kind: "image",
      error: "[comfyui_status] ComfyUI 尚未启动", retryArgs: { slotId: "s2" },
    });
    expect(image.querySelector(".media-slot-retry")!.getAttribute("title"))
      .toBe("用原参数重新生成这张图");
    expect(image.textContent).toContain("ComfyUI 尚未启动");

    const video = mount({
      type: "media-slot", slotId: "s2:video", status: "failed", kind: "video",
      error: "[submit] 视频提交失败", retryArgs: { slotId: "s2" },
    });
    expect(video.querySelector(".media-slot-retry")!.getAttribute("title"))
      .toBe("用原参数重新生成这段视频");
  });

  it("视频试跑槽的文案是视频口径：成稿直送＝出视频，不沿用「出图」", () => {
    const dom = mount({
      type: "media-slot", slotId: "s1:video", status: "failed", kind: "video",
      error: `[prompt_only] ${VIDEO_TRIAL_NOTE}`, retryArgs: { slotId: "s1" },
    });
    expect(dom.querySelector(".media-slot-retry")!.getAttribute("title"))
      .toBe("用试跑成稿直接提交 ComfyUI 出视频（不受试跑开关影响）");
    expect(dom.textContent).toContain("视频不出片");
    expect(dom.textContent).not.toContain("出图");
  });

  it("未落定的认领槽在界面上就是「插画生成中…」＋停止键（第 88 刀要消掉的僵尸形状）", () => {
    const dom = mount({
      type: "media-slot", slotId: "s3", status: "pending", kind: "image", submissionClaim: true,
    });
    expect(dom.querySelector(".media-slot-generating")).not.toBeNull();
    expect(dom.textContent).toContain("插画生成中…");
    expect(dom.querySelector(".media-slot-stop")).not.toBeNull();
    expect(dom.querySelector(".media-slot-retry")).toBeNull();
  });
});

describe("失败/试跑槽的「编辑提示词」（2026-11-10 用户需求）", () => {
  const TRIAL_IMAGE = {
    type: "media-slot", slotId: "s1", status: "failed", kind: "image",
    error: `[prompt_only] ${TRIAL_NOTE}`,
    retryArgs: { slotId: "s1", prompt: "原始P", finalPrompt: "编译后P" },
  };

  it("图片试跑槽显示入口，弹窗预填编译成稿，确认后把新提示词交回重试", () => {
    const onRetry = vi.fn();
    const dom = mount(TRIAL_IMAGE, onRetry);
    const edit = buttonByText(dom, "编辑提示词");
    expect(edit).toBeTruthy();
    expect(container.querySelector("textarea")).toBeNull();

    act(() => { edit!.click(); });
    expect(container.textContent).toContain("编辑提示词（图片）");
    const textarea = container.querySelector("textarea") as HTMLTextAreaElement;
    expect(textarea.value).toBe("编译后P");

    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
      setter.call(textarea, "新成稿P");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    act(() => { buttonByText(container, "保存并重新生成")!.click(); });
    expect(onRetry).toHaveBeenCalledWith("m1", "s1", "新成稿P");
    expect(container.querySelector("textarea")).toBeNull();
  });

  it("视频槽预填视频成稿，而不是同快照里的图片成稿", () => {
    const dom = mount({
      type: "media-slot", slotId: "s1:video", status: "failed", kind: "video",
      error: `[prompt_only] ${VIDEO_TRIAL_NOTE}`,
      videoPrompt: "视频成稿",
      retryArgs: { slotId: "s1", prompt: "原始P", finalPrompt: "图片成稿", videoPrompt: "视频成稿" },
    });
    act(() => { buttonByText(dom, "编辑提示词")!.click(); });
    expect(container.textContent).toContain("编辑提示词（视频）");
    expect((container.querySelector("textarea") as HTMLTextAreaElement).value).toBe("视频成稿");
  });

  it("弹窗「复制」把当前文本写入剪贴板", async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText }, configurable: true,
    });
    const dom = mount(TRIAL_IMAGE);
    act(() => { buttonByText(dom, "编辑提示词")!.click(); });
    await act(async () => { buttonByText(container, "复制")!.click(); });
    expect(writeText).toHaveBeenCalledWith("编译后P");
    expect(buttonByText(container, "已复制")).toBeTruthy();
  });

  it("没有提示词成稿的失败槽不显示入口（避免空弹窗）", () => {
    const dom = mount({
      type: "media-slot", slotId: "s1", status: "failed", kind: "image",
      error: "[comfyui_status] ComfyUI 尚未启动", retryArgs: { slotId: "s1" },
    });
    expect(buttonByText(dom, "编辑提示词")).toBeUndefined();
  });
});
