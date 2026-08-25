import { describe, it, expect } from "vitest";
import { inspirationInsertText, inspirationInsertImages } from "./inspirationInsert";

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
