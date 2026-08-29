// stableDetailsOpen 的回归测试：innerHTML 重写后 <details> 展开状态保持。
// 真实故障（2026-08-29 用户验收）：预设正则把正文 <think> 折叠成 <details>，
// 插画回填/流式重写 innerHTML 后展开状态丢失。stub DOM 验证纯逻辑三态。
import { describe, expect, it } from "vitest";
import { detailsKey, restoreOpenDetails, trackDetailsToggle } from "./stableDetailsOpen";

function stubDetails(tagName: string, text: string, open: boolean) {
  return {
    tagName,
    open,
    querySelector: (sel: string) => (sel === "summary" ? { textContent: text } : null),
  };
}

function stubRoot(details: { tagName: string; open: boolean; key: string }[]) {
  const items = details.map((d) => stubDetails(d.tagName, d.key, d.open));
  return {
    querySelectorAll: (sel: string) => ({
      forEach: (cb: (d: never) => void) => {
        if (sel === "details") items.forEach((d) => cb(d as never));
      },
    }),
    items,
  };
}

describe("trackDetailsToggle", () => {
  it("展开时记录 summary 键，收起时移除", () => {
    const store = new Set<string>();
    const d = stubDetails("DETAILS", "思考过程", true);
    trackDetailsToggle(d, store);
    expect(store.has("思考过程")).toBe(true);
    (d as { open: boolean }).open = false;
    trackDetailsToggle(d, store);
    expect(store.has("思考过程")).toBe(false);
  });

  it("非 DETAILS 元素与无 summary 文本的折叠条不记录", () => {
    const store = new Set<string>();
    trackDetailsToggle(stubDetails("DIV", "思考过程", true), store);
    trackDetailsToggle(stubDetails("DETAILS", "  ", true), store);
    trackDetailsToggle(null, store);
    expect(store.size).toBe(0);
  });
});

describe("restoreOpenDetails", () => {
  it("按键恢复展开中的折叠条，其余不动", () => {
    const open = stubDetails("DETAILS", "思考过程", false);
    const other = stubDetails("DETAILS", "状态栏", false);
    const root = stubRoot([
      { tagName: "DETAILS", open: false, key: "x" },
      { tagName: "DETAILS", open: false, key: "y" },
    ]);
    // 用 items 替换校验目标：直接对两个 stub 断言
    const store = new Set(["思考过程"]);
    const fake = {
      querySelectorAll: (sel: string) => ({
        forEach: (cb: (d: never) => void) => {
          if (sel === "details") [open, other].forEach((d) => cb(d as never));
        },
      }),
    };
    const n = restoreOpenDetails(fake, store);
    expect(n).toBe(1);
    expect((open as { open: boolean }).open).toBe(true);
    expect((other as { open: boolean }).open).toBe(false);
    void root;
  });

  it("空 store 与空 root 返回 0", () => {
    expect(restoreOpenDetails(null, new Set())).toBe(0);
    expect(restoreOpenDetails(stubRoot([]), new Set())).toBe(0);
  });
});

describe("detailsKey", () => {
  it("取 summary 文本并去空白", () => {
    expect(detailsKey(stubDetails("DETAILS", " 思考过程 ", true) as unknown as Element)).toBe("思考过程");
  });
});
