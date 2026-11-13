// @vitest-environment jsdom
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
  act(() => { root?.unmount(); });
  container?.remove();
});

function mount(msg: Record<string, unknown>, onSetCover: (url: string) => void) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(
      <AssistantMessage
        msg={msg as never}
        onSendImage={() => {}}
        onSetCover={onSetCover}
      />,
    );
  });
  return container;
}

function coverButton(dom: HTMLElement): HTMLButtonElement | undefined {
  return [...dom.querySelectorAll("button")].find(
    (button) => button.textContent?.includes("设为封面"),
  ) as HTMLButtonElement | undefined;
}

describe("视频消息设为动态封面", () => {
  it("顶层视频消息可设为封面", () => {
    const onSetCover = vi.fn();
    const dom = mount(
      { id: "m1", role: "assistant", text: "", video: "http://x/v.mp4" },
      onSetCover,
    );
    act(() => { coverButton(dom)!.click(); });
    expect(onSetCover).toHaveBeenCalledWith("http://x/v.mp4");
  });

  it("视频 media-slot 也可设为封面", () => {
    const onSetCover = vi.fn();
    const dom = mount(
      {
        id: "m1", role: "assistant", text: "正文",
        parts: [{ type: "video", url: "http://x/part.mp4", slotId: "s1" }],
      },
      onSetCover,
    );
    act(() => { coverButton(dom)!.click(); });
    expect(onSetCover).toHaveBeenCalledWith("http://x/part.mp4");
  });
});
