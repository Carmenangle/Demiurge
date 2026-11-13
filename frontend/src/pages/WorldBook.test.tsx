// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { WorldBook } from "./WorldBook";
import { listWorldbookEntries, updateWorldbookEntry } from "../api/worldbook";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../api/worldbook", () => ({
  listWorldbooks: vi.fn(async () => ({
    items: [{ name: "书一", file: "书一.json", entries: 2 }],
  })),
  listWorldbookEntries: vi.fn(async () => ({
    entries: [
      { index: 0, comment: "条目甲", content: "甲的内容", keys: [], constant: false, enabled: true },
      { index: 1, comment: "条目乙", content: "乙的内容", keys: ["k"], constant: true, enabled: true },
    ],
  })),
  importWorldbook: vi.fn(async () => ({ ok: true })),
  worldbookDetail: vi.fn(async () => ({ name: "书一", book: { entries: [] } })),
  deleteWorldbook: vi.fn(async () => ({ ok: true })),
  addWorldbookEntry: vi.fn(async () => ({ ok: true, index: 2 })),
  updateWorldbookEntry: vi.fn(async () => ({ ok: true })),
  deleteWorldbookEntry: vi.fn(async () => ({ ok: true })),
}));

vi.mock("../api/characters", () => ({
  listCharacters: vi.fn(async () => ({ items: [] })),
  characterDetail: vi.fn(async () => ({})),
}));

// jsdom 没有 Element.scrollTo（真浏览器有）；弹窗打开时的滚动复位在测试里打桩
Element.prototype.scrollTo = (() => {}) as typeof Element.prototype.scrollTo;

let container: HTMLDivElement;
let root: Root;

async function mount() {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(<WorldBook characterDir="" worldbookDir="W" />); });
}

function byText(text: string): HTMLButtonElement {
  const btn = Array.from(container.querySelectorAll("button"))
    .find((b) => (b.textContent || "").includes(text));
  expect(btn, `按钮「${text}」应存在`).toBeTruthy();
  return btn as HTMLButtonElement;
}

const content = () => container.querySelector(".worldbook-content") as HTMLElement;
const mask = () => container.querySelector(".worldbook-sheet-mask") as HTMLElement | null;
const entryModal = () => container.querySelector(".worldbook-entry-modal") as HTMLElement | null;
const entryMask = () => container.querySelector(".worldbook-entry-mask") as HTMLElement | null;
function entryCard(name: string): HTMLElement {
  return [...container.querySelectorAll(".chat-box")].find(
    (el) => el.textContent?.includes(name),
  ) as HTMLElement;
}
function setText(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")!.set!;
  setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  vi.clearAllMocks();
});

describe("WorldBook 窄屏条目弹窗", () => {
  it("选中世界书即打开条目弹窗，并加载条目", async () => {
    await mount();
    expect(content().classList.contains("is-sheet-open")).toBe(false);

    await act(async () => { byText("书一").click(); });

    expect(listWorldbookEntries).toHaveBeenCalledWith({ base: "W", name: "书一" });
    expect(content().classList.contains("is-sheet-open")).toBe(true);
    expect(mask()?.classList.contains("is-open")).toBe(true);
    expect(content().textContent).toContain("2 条");
    expect(content().textContent).toContain("条目甲");
  });

  it("关闭弹窗只收起面板，不清空已选书与条目（可再次点开）", async () => {
    await mount();
    await act(async () => { byText("书一").click(); });

    await act(async () => { mask()!.click(); });

    expect(content().classList.contains("is-sheet-open")).toBe(false);
    expect(mask()?.classList.contains("is-open")).toBe(false);
    // 选中态保留：条目仍在 DOM（窄屏由 CSS 隐藏面板），再次点书立即恢复
    expect(content().textContent).toContain("条目甲");
    await act(async () => { byText("书一").click(); });
    expect(content().classList.contains("is-sheet-open")).toBe(true);
  });

  it("Esc 关闭弹窗", async () => {
    await mount();
    await act(async () => { byText("书一").click(); });
    expect(content().classList.contains("is-sheet-open")).toBe(true);

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });

    expect(content().classList.contains("is-sheet-open")).toBe(false);
  });

  it("新增条目改为独立编辑弹窗，窄屏条目面板保持打开", async () => {
    await mount();
    await act(async () => { byText("书一").click(); });

    await act(async () => { byText("新增条目").click(); });

    expect(content().classList.contains("is-sheet-open")).toBe(true);
    expect(entryModal()).toBeTruthy();
    expect(entryModal()!.querySelector('input[placeholder^="例如：角色卡"]')).toBeTruthy();
    expect(entryModal()!.textContent).toContain("新增条目");
    expect(entryModal()!.textContent).toContain("保存");
  });
});

describe("WorldBook 条目编辑弹窗", () => {
  async function openEdit(name = "条目乙") {
    await mount();
    await act(async () => { byText("书一").click(); });
    await act(async () => {
      (entryCard(name).querySelector('button[title="编辑"]') as HTMLButtonElement).click();
    });
  }

  it("编辑条目弹出独立弹窗并预填字段", async () => {
    await openEdit();
    const modal = entryModal()!;
    expect(modal.textContent).toContain("编辑条目 #2");
    expect((modal.querySelector('input[placeholder^="例如：角色卡"]') as HTMLInputElement).value)
      .toBe("条目乙");
    expect((modal.querySelector("textarea.worldbook-entry-content") as HTMLTextAreaElement).value)
      .toBe("乙的内容");
    expect((modal.querySelector('input[placeholder^="逗号分隔"]') as HTMLInputElement).value)
      .toBe("k");
    expect((modal.querySelector('input[type="checkbox"]') as HTMLInputElement).checked)
      .toBe(true);   // 条目乙 constant=true
  });

  it("保存把编辑后的内容交回 updateWorldbookEntry，并关闭弹窗", async () => {
    await openEdit();
    const textarea = entryModal()!.querySelector("textarea.worldbook-entry-content") as HTMLTextAreaElement;
    await act(async () => { setText(textarea, "乙的新内容"); });
    await act(async () => { byText("保存").click(); });

    expect(updateWorldbookEntry).toHaveBeenCalledWith(
      { base: "W", name: "书一" },
      1,
      expect.objectContaining({ content: "乙的新内容", comment: "条目乙", keys: ["k"], constant: true }),
    );
    expect(entryModal()).toBeNull();
  });

  it("取消关闭弹窗且不写回", async () => {
    await openEdit();
    await act(async () => { byText("取消").click(); });
    expect(entryModal()).toBeNull();
    expect(updateWorldbookEntry).not.toHaveBeenCalled();
  });

  it("Esc 只关编辑弹窗，不收起窄屏条目面板", async () => {
    await openEdit();
    expect(content().classList.contains("is-sheet-open")).toBe(true);
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(entryModal()).toBeNull();
    expect(content().classList.contains("is-sheet-open")).toBe(true);
  });

  it("点遮罩关闭弹窗", async () => {
    await openEdit();
    await act(async () => { entryMask()!.click(); });
    expect(entryModal()).toBeNull();
  });

  it("空内容保存时错误显示在弹窗内，弹窗不关闭", async () => {
    await mount();
    await act(async () => { byText("书一").click(); });
    await act(async () => { byText("新增条目").click(); });
    await act(async () => { byText("保存").click(); });
    expect(entryModal()).toBeTruthy();
    expect(entryModal()!.querySelector(".worldbook-entry-error")?.textContent)
      .toContain("条目内容不能为空");
  });
});
