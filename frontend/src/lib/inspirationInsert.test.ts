import { describe, it, expect, vi } from "vitest";
import {
  inspirationInsertText, inspirationInsertImages,
  pushInspirationsToCanvas, consumePendingInspirations,
  inspirationToCanvasPayload, inspirationCanvasLabel,
} from "./inspirationInsert";

describe("inspiration insert text (M1.5 Agent 理解格式)", () => {
  it("带标题时生成「灵感参考 · 标题」头 + 身份标记 + 内容", () => {
    const text = inspirationInsertText({ title: "女仆装", content: "主流款式包括…" });
    expect(text).toContain("【灵感参考 · 女仆装】");
    expect(text).toContain("供创作时参考，非剧情指令");
    expect(text).toContain("主流款式包括…");
  });

  it("无标题时仍有「灵感参考」头与身份标记", () => {
    const text = inspirationInsertText({ content: "内容" });
    expect(text).toContain("【灵感参考】");
    expect(text).toContain("非剧情指令");
    expect(text).toContain("内容");
  });

  it("空内容回落为（空内容）", () => {
    const text = inspirationInsertText({ title: "T", content: "" });
    expect(text).toContain("(空内容)");
  });
});

describe("inspiration insert images", () => {
  it("优先选中图，其次卡内图，去重", () => {
    const urls = inspirationInsertImages({
      selected: ["a.png"],
      images: [{ url: "a.png" }, { url: "b.png" }, { full_url: "c.png" }],
    });
    expect(urls).toEqual(["a.png", "b.png", "c.png"]);
  });

  it("无图片返回空数组", () => {
    expect(inspirationInsertImages({})).toEqual([]);
  });
});

describe("inspiration → canvas bridge (M1.5)", () => {
  it("push 写入缓存并派发通知，consume 取出清空", () => {
    const listeners: Record<string, Array<() => void>> = {};
    vi.stubGlobal("window", {
      addEventListener: (t: string, fn: () => void) => { (listeners[t] ||= []).push(fn); },
      removeEventListener: (t: string, fn: () => void) => { listeners[t] = (listeners[t] || []).filter((f) => f !== fn); },
      dispatchEvent: (e: { type: string }) => { (listeners[e.type] || []).forEach((f) => f()); },
    });
    const spy = vi.fn();
    window.addEventListener("laf-inspiration-to-canvas", spy);
    pushInspirationsToCanvas([{ title: "女仆装", content: "x" }]);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(consumePendingInspirations()).toEqual([{ title: "女仆装", content: "x" }]);
    expect(consumePendingInspirations()).toEqual([]); // 消费即清空
    vi.unstubAllGlobals();
  });

  it("toCanvasPayload 优先选中图作封面，保留稳定 id", () => {
    const p = inspirationToCanvasPayload({
      id: "abc", messageId: "m1", title: "T", content: "C",
      images: [{ full_url: "https://x/1.png" }],
    });
    expect(p.id).toBe("abc");
    expect(p.imageUrl).toBe("https://x/1.png");
  });

  it("canvasLabel 反映图片数量", () => {
    expect(inspirationCanvasLabel({ images: [{ url: "a.png" }] })).toBe("发送画布（1 图）");
    expect(inspirationCanvasLabel({})).toBe("发送画布");
  });
});
