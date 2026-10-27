// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { normalizeBootHash } from "./viewRouting";

// 真机 2026-10-27：开屏那次 hash 落地用了 `location.hash = ...`，于是文档根之外多出一格
// 「画面一模一样的空白历史」——首页第一下返回键退到它，用户看到的就是「按了没反应」。
// 首帧改用 replaceState 就地改写，这条链上就不再有空格。
describe("normalizeBootHash", () => {
  afterEach(() => {
    window.location.hash = "";
    vi.restoreAllMocks();
  });

  it("无 hash（首帧）⇒ 走 replaceState，不把空白根留在返回链上", () => {
    const replace = vi.fn();
    vi.spyOn(window.history, "replaceState").mockImplementation(replace);
    window.location.hash = "";
    normalizeBootHash("#/story");
    expect(replace).toHaveBeenCalledWith(null, "", "#/story");
    // 没走赋值路径：jsdom 里 location.hash 仍是空（replaceState 已被 mock 掉）
    expect(window.location.hash).toBe("");
  });

  it("已有 hash（深链进来 / 后退之后）⇒ 照常赋值，保持每跳加一格", () => {
    const replace = vi.spyOn(window.history, "replaceState");
    window.location.hash = "#/assets/works";
    normalizeBootHash("#/story");
    expect(replace).not.toHaveBeenCalled();
    expect(window.location.hash).toBe("#/story");
  });
});
