// @vitest-environment jsdom
//
// 输入框草稿存档的**组件接线**回归测试（2026-09-21 用户需求）。
// 需求：用户写了一半（含插入的图片/文档）就切仓库或刷新，内容不该丢。
//
// 覆盖四件事：
//   ① 挂载即回填（惰性初值，无「先空后填」闪跳）
//   ② 输入后 300ms 落盘（防抖）
//   ③ pagehide 立即 flush（硬中断兜底——刷新/关标签不等防抖）
//   ④ 发送成功 ⇒ 草稿即刻作废（否则紧接着刷新会把已发送内容又回填回来）
// 外加：首页 home 是临时草稿区不落盘；配额不足降级落盘并提示一次。

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Node 25 的内置 localStorage 桩遮蔽 jsdom（clear 等方法缺失，settings.test 同源坑）：
// 统一换成内存实现，组件与测试读写的都是同一实例。
class MemoryStorage implements Storage {
  private map = new Map<string, string>();
  get length() { return this.map.size; }
  clear() { this.map.clear(); }
  getItem(k: string) { return this.map.get(k) ?? null; }
  key(i: number) { return [...this.map.keys()][i] ?? null; }
  removeItem(k: string) { return this.map.delete(k); }
  setItem(k: string, v: string) { this.map.set(k, String(v)); }
}
vi.stubGlobal("localStorage", new MemoryStorage());

import { act, createRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { RichInput, type RichInputHandle } from "./RichInput";
import { INPUT_DRAFT_PREFIX, type InputDraft } from "../lib/inputDraft";

// React 19 + jsdom：必须显式声明 act 环境（否则每次 act() 打环境警告）。
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const key = (threadId: string) => `${INPUT_DRAFT_PREFIX}${threadId}`;

const DRAFT_FIXTURE: InputDraft = {
  text: "写了一半",
  images: [],
  inspirationAttachments: [],
  maskedImage: null,
  media: [],
  attachments: [],
  updatedAt: 1,
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  localStorage.clear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => { root.unmount(); });
  container.remove();
  localStorage.clear();
});

function mount(threadId: string) {
  const ref = createRef<RichInputHandle>();
  const onSubmit = vi.fn();
  const onNotify = vi.fn();
  act(() => {
    root.render(
      <RichInput ref={ref} threadId={threadId} templateNames={[]} onSubmit={onSubmit} onNotify={onNotify} />,
    );
  });
  const textarea = () => container.querySelector("textarea") as HTMLTextAreaElement;
  return { ref, onSubmit, onNotify, textarea };
}

/** 模拟真实输入：绕过 React 的 value 追踪后派发 input 事件（onChange 才会触发）。 */
function typeText(el: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
  act(() => {
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

const waitDebounce = () => act(async () => { await new Promise((r) => setTimeout(r, 350)); });

describe("草稿回填（切走仓库再回来 / 刷新后）", () => {
  it("挂载即回填文字与图片", () => {
    localStorage.setItem(key("r1"), JSON.stringify({
      ...DRAFT_FIXTURE, text: "写了一半", images: ["data:image/png;base64,AAA"], updatedAt: 2,
    }));

    const h = mount("r1");

    expect(h.textarea().value).toBe("写了一半");
    expect(container.querySelectorAll(".rich-imgbar-item img")).toHaveLength(1);
  });

  it("不同仓库互不串味：r2 的草稿不进 r1 的输入框", () => {
    localStorage.setItem(key("r2"), JSON.stringify({ ...DRAFT_FIXTURE, text: "r2 的草稿", updatedAt: 2 }));

    const h = mount("r1");

    expect(h.textarea().value).toBe("");
  });
});

describe("草稿落盘", () => {
  it("输入后 300ms 才写；未到点前不写（防抖）", async () => {
    const h = mount("r1");

    typeText(h.textarea(), "新的草稿");
    expect(localStorage.getItem(key("r1"))).toBeNull();  // 未到点

    await waitDebounce();
    expect(JSON.parse(localStorage.getItem(key("r1"))!).text).toBe("新的草稿");
  });

  it("pagehide 立即 flush：硬中断（刷新/关标签）不等 300ms 防抖", () => {
    const h = mount("r1");

    typeText(h.textarea(), "来不及保存就刷新");
    // 不等待防抖，直接模拟页面隐藏/卸载
    act(() => { window.dispatchEvent(new Event("pagehide")); });

    expect(JSON.parse(localStorage.getItem(key("r1"))!).text).toBe("来不及保存就刷新");
  });

  it("插入图片也进草稿（不只是文字）", async () => {
    const h = mount("r1");

    act(() => { h.ref.current!.insertImage("data:image/png;base64,IMG"); });
    await waitDebounce();

    expect(JSON.parse(localStorage.getItem(key("r1"))!).images).toEqual(["data:image/png;base64,IMG"]);
  });
});

describe("发送后作废", () => {
  it("submit ⇒ 上游收到内容，且草稿即刻删除、输入框清空", () => {
    localStorage.setItem(key("r1"), JSON.stringify({ ...DRAFT_FIXTURE, text: "准备发送", updatedAt: 2 }));
    const h = mount("r1");
    expect(h.textarea().value).toBe("准备发送");

    act(() => { h.ref.current!.submit(); });

    expect(h.onSubmit).toHaveBeenCalledTimes(1);
    expect((h.onSubmit.mock.calls[0][0] as { text: string }).text).toBe("准备发送");
    expect(localStorage.getItem(key("r1"))).toBeNull();
    expect(h.textarea().value).toBe("");
  });

  it("空输入点提交 ⇒ 不调 onSubmit、也不留空草稿", () => {
    const h = mount("r1");

    act(() => { h.ref.current!.submit(); });

    expect(h.onSubmit).not.toHaveBeenCalled();
    expect(localStorage.getItem(key("r1"))).toBeNull();
  });
});

describe("首页（home）= 临时草稿区，不落 localStorage", () => {
  it("home 既不回填也不落盘（刷新即空是既有约定）", async () => {
    const h = mount("home");

    typeText(h.textarea(), "首页草稿");
    await waitDebounce();

    expect(localStorage.getItem(key("home"))).toBeNull();
  });
});

describe("配额不足 ⇒ 降级落盘并提示一次（不静默丢草稿）", () => {
  it("图片撑爆配额时保住文字，并只提示一次", async () => {
    // 打在**实例**上：上方 stubGlobal 的内存实现不继承 jsdom 的 Storage.prototype，
    // 原型补丁够不着它（语义不变：只放行轻载荷，整包抛配额错误）
    const ls = localStorage;
    const original = ls.setItem.bind(ls);
    ls.setItem = (k: string, v: string) => {
      if (String(v).length > 300) throw new DOMException("quota", "QuotaExceededError");
      return original(k, v);
    };
    try {
      const h = mount("r1");
      act(() => { h.ref.current!.insertImage("data:image/png;base64," + "A".repeat(400)); });
      typeText(h.textarea(), "短文字");
      await waitDebounce();

      expect(h.onNotify).toHaveBeenCalledTimes(1);
      expect(h.onNotify.mock.calls[0][1]).toBe("info");
      const stored = JSON.parse(localStorage.getItem(key("r1"))!);
      expect(stored.text).toBe("短文字");
      expect(stored.images).toEqual([]);       // 重字段被摘掉
      expect(stored.heavyDropped).toBe(true);
    } finally {
      ls.setItem = original;
    }
  });
});
