import { describe, expect, it } from "vitest";
import { shouldLoadMore } from "./infiniteScroll";

// 2026-10-19 用户实锤：滚到最下方不加载下一页。
// 原实现用 IntersectionObserver（root 默认视口），而本应用滚动在 .app-body 内层容器上，
// 跨嵌套滚动 + 祖先裁剪时回调不触发。改成直接量哨兵到视口底的距离 ⇒ 与「谁在滚」无关。
describe("shouldLoadMore", () => {
  it("哨兵在视口内 ⇒ 触发", () => {
    expect(shouldLoadMore(300, 800)).toBe(true);
    expect(shouldLoadMore(-500, 800)).toBe(true);
  });

  it("哨兵在视口下方 600px 内 ⇒ 提前触发（滚动到一半就开始加载）", () => {
    expect(shouldLoadMore(800 + 599, 800)).toBe(true);   // 距底 599
    expect(shouldLoadMore(800 + 1, 800)).toBe(true);
  });

  it("哨兵在视口下方超过 600px ⇒ 不触发", () => {
    expect(shouldLoadMore(800 + 600, 800)).toBe(false);  // 边界＝不触发
    expect(shouldLoadMore(800 + 2000, 800)).toBe(false);
  });

  it("阈值可调（小窗口/大屏）", () => {
    // 距底 200：阈值 300 ⇒ 触发；阈值 150 ⇒ 不触发
    expect(shouldLoadMore(1000, 800, 300)).toBe(true);
    expect(shouldLoadMore(1000, 800, 150)).toBe(false);
    expect(shouldLoadMore(1000, 800, 1200)).toBe(true);
  });

  it("非法测量值一律不触发（避免 NaN 比较恒 false 静默死掉）", () => {
    expect(shouldLoadMore(Number.NaN, 800)).toBe(false);
    expect(shouldLoadMore(300, Number.NaN)).toBe(false);
    expect(shouldLoadMore(300, 0)).toBe(false);
  });
});
