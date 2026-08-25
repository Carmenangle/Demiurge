import { describe, it, expect, vi } from "vitest";
import {
  inspirationInsertText, inspirationInsertImages, inspirationToAttachment,
  pushInspirationsToCanvas, consumePendingInspirations,
  inspirationToCanvasPayload, inspirationCanvasLabel,
} from "./inspirationInsert";

describe("inspiration insert text (M1.5 Agent 理解格式)", () => {
  it("带标题时生成「灵感参考 · 标题」头 + 身份标记 + 内容", () => {
    const text = inspirationInsertText({ title: "女仆装", content: "主流款式包括…" });
    expect(text).toContain("【灵感参考 · 女仆装】");
    expect(text).toContain("不是剧情指令");
    expect(text).toContain("以用户要求为准");
    expect(text).toContain("主流款式包括…");
  });

  it("模板主题无关：不预设视觉/风格方向", () => {
    const text = inspirationInsertText({ title: "三幕式结构", content: "开端发展高潮…" });
    expect(text).toContain("【灵感参考 · 三幕式结构】");
    expect(text).toContain("该主题的检索参考资料");
    expect(text).not.toContain("风格/视觉/妆造/场景");
  });

  it("无图（纯文本卡）不声称「消息附带图片」", () => {
    const text = inspirationInsertText({ title: "T", content: "C" });
    expect(text).not.toContain("消息附带图片");
  });

  it("有封面图时附带「消息附带图片」说明", () => {
    const text = inspirationInsertText({ title: "T", content: "C", imageUrl: "https://x/1.png" });
    expect(text).toContain("消息附带图片");
    expect(text).toContain("可结合图片理解主题");
  });

  it("无标题时仍有「灵感参考」头与身份标记", () => {
    const text = inspirationInsertText({ content: "内容" });
    expect(text).toContain("【灵感参考】");
    expect(text).toContain("不是剧情指令");
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

describe("inspiration → attachment (输入框附件)", () => {
  it("封面图取选中图优先，内容保留", () => {
    const a = inspirationToAttachment({
      id: "c1", title: "T", content: "C",
      images: [{ full_url: "https://x/1.png" }],
    });
    expect(a.id).toBe("c1");
    expect(a.title).toBe("T");
    expect(a.content).toBe("C");
    expect(a.imageUrl).toBe("https://x/1.png");
  });

  it("无 id 时生成稳定 id，无图封面为空", () => {
    const a = inspirationToAttachment({ title: "T", content: "C" });
    expect(a.id).toMatch(/^insp-/);
    expect(a.imageUrl).toBe("");
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
