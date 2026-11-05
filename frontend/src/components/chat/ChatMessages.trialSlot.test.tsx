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

const TRIAL_NOTE = "试跑：只生成提示词，未提交 ComfyUI（图片/视频均不出图）；"
  + "成稿见 trace 的 illustration.submitted；点「重新生成」用成稿直接出图";

function slotMsg(part: Record<string, unknown>) {
  return { id: "m1", role: "assistant" as const, text: "正文", parts: [part] };
}

function mount(part: Record<string, unknown>) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(
      <AssistantMessage
        msg={slotMsg(part) as never}
        onSendImage={() => {}}
        onRetryIllustration={() => {}}
        onCancelGeneration={() => {}}
      />,
    );
  });
  return container;
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
