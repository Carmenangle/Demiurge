// @vitest-environment jsdom
//
// 「重新生图（含提示词）」的组件接线回归（2026-09-22 用户需求）。
//
// 需求原话：「在插入图片下方不是有查看原图、下载、重新生图等功能选项吗，再添加个
// 重新生图(含提示词)的选项」——用于**反复调提示词**，不必重跑正文生成。
//
// 这里只钉接线：按钮存在 → 点击弹出预填了当前提示词的编辑框 → 确认后把**编辑后的
// 提示词**交回上游（真源改写逻辑由 regeneration.promptOverride.test.ts 单独钉）。

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

const IMAGE_MSG = {
  id: "assistant-frame-1",
  role: "assistant" as const,
  text: "",
  parts: [{
    type: "image" as const,
    url: "http://127.0.0.1:8188/view?filename=a.png",
    slotId: "illo-req-1:last",
    regeneration: {
      kind: "template" as const,
      templateId: "tpl-1",
      values: { "6.text": "旧提示词" },
      comfyuiUrl: "http://127.0.0.1:8188",
      outputNodeIds: ["9"],
      prompt: "旧提示词：fingertip tracing three unseen characters",
    },
  }],
};

function mount(onRegenerateWithPrompt?: (id: string, slotId: string, prompt: string) => void) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(
      <AssistantMessage
        msg={IMAGE_MSG}
        onSendImage={() => {}}
        onRegenerate={() => {}}
        onRegenerateWithPrompt={onRegenerateWithPrompt}
      />,
    );
  });
  const btn = [...container.querySelectorAll("button")].find(
    (b) => b.textContent?.includes("重新生图（含提示词）"),
  );
  return { btn, textarea: () => container.querySelector("textarea") as HTMLTextAreaElement | null };
}

describe("重新生图（含提示词）", () => {
  it("图片卡上出现该选项，且与「重新生图」并存", () => {
    const { btn } = mount(() => {});
    expect(btn).toBeTruthy();
    const plain = [...container.querySelectorAll("button")].filter(
      (b) => b.textContent?.trim() === "重新生图",
    );
    expect(plain.length).toBe(1);
  });

  it("点击后弹出编辑框，且预填当前提示词", () => {
    const { btn, textarea } = mount(() => {});
    expect(textarea()).toBeNull();
    act(() => { btn!.click(); });
    const ta = textarea();
    expect(ta).toBeTruthy();
    expect(ta!.value).toBe("旧提示词：fingertip tracing three unseen characters");
  });

  it("确认后把编辑后的提示词交回上游（带 messageId + slotId）", () => {
    const spy = vi.fn();
    const { btn, textarea } = mount(spy);
    act(() => { btn!.click(); });
    const ta = textarea()!;
    // React 受控输入：改 value 后必须派发 input 事件
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
      setter.call(ta, "新提示词：hand pulling out, withdrawing just before completion");
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const confirm = [...container.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === "重新生成",
    )!;
    act(() => { confirm.click(); });
    expect(spy).toHaveBeenCalledWith(
      "assistant-frame-1",
      "illo-req-1:last",
      "新提示词：hand pulling out, withdrawing just before completion",
    );
    expect(textarea()).toBeNull();   // 提交后关闭
  });

  it("未接 onRegenerateWithPrompt 时不渲染该选项（老视图不炸）", () => {
    const { btn } = mount(undefined);
    expect(btn).toBeUndefined();
  });
});
