// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { WorldBookPopup } from "./WorldBookPopup";
import { listWorldbookEntries, updateWorldbookEntry } from "../api/worldbook";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../api/worldbook", () => ({
  listWorldbookEntries: vi.fn(async () => ({
    entries: [{ index: 0, comment: "条目甲", content: "甲的内容", keys: ["k"], constant: false, enabled: true }],
  })),
  repoWorldbookEntries: vi.fn(),
  addWorldbookEntry: vi.fn(async () => ({ ok: true, index: 1 })),
  updateWorldbookEntry: vi.fn(async () => ({ ok: true })),
  deleteWorldbookEntry: vi.fn(async () => ({ ok: true })),
  repoWorldbookEntryAdd: vi.fn(),
  repoWorldbookEntryUpdate: vi.fn(),
  repoWorldbookEntryDelete: vi.fn(),
}));

let container: HTMLDivElement;
let root: Root;

async function mount(onClose: () => void) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<WorldBookPopup location={{ base: "W", name: "书一" }} onClose={onClose} />);
  });
}

function byTitle(title: string): HTMLButtonElement {
  return container.querySelector(`button[title="${title}"]`) as HTMLButtonElement;
}

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  vi.clearAllMocks();
});

describe("WorldBookPopup 条目编辑弹窗", () => {
  it("编辑不再内联：打开独立弹窗并预填", async () => {
    await mount(() => {});
    await act(async () => { byTitle("编辑").click(); });

    const modal = container.querySelector(".worldbook-entry-modal") as HTMLElement;
    expect(modal).toBeTruthy();
    expect((modal.querySelector("textarea.worldbook-entry-content") as HTMLTextAreaElement).value)
      .toBe("甲的内容");
  });

  it("Esc 先关编辑弹窗，再按一次才关整本世界书", async () => {
    const onClose = vi.fn();
    await mount(onClose);
    await act(async () => { byTitle("编辑").click(); });

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(container.querySelector(".worldbook-entry-modal")).toBeNull();
    expect(onClose).not.toHaveBeenCalled();

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("保存把编辑内容交回 updateWorldbookEntry", async () => {
    await mount(() => {});
    await act(async () => { byTitle("编辑").click(); });
    const textarea = container.querySelector("textarea.worldbook-entry-content") as HTMLTextAreaElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
      setter.call(textarea, "甲的新内容");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const save = [...container.querySelectorAll("button")].find(
      (b) => (b.textContent || "").includes("保存"),
    ) as HTMLButtonElement;
    await act(async () => { save.click(); });

    expect(updateWorldbookEntry).toHaveBeenCalledWith(
      { base: "W", name: "书一" }, 0,
      expect.objectContaining({ content: "甲的新内容", comment: "条目甲" }),
    );
    expect(container.querySelector(".worldbook-entry-modal")).toBeNull();
    expect(listWorldbookEntries).toHaveBeenCalled();
  });
});
