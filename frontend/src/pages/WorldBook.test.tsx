// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { WorldBook } from "./WorldBook";
import { listWorldbookEntries } from "../api/worldbook";

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

  it("弹窗内可直接新增条目：表单出现在面板内且不收起弹窗", async () => {
    await mount();
    await act(async () => { byText("书一").click(); });

    await act(async () => { byText("新增条目").click(); });

    expect(content().classList.contains("is-sheet-open")).toBe(true);
    expect(content().querySelector('input[placeholder^="标题/备注"]')).toBeTruthy();
    expect(content().textContent).toContain("保存");
  });
});
