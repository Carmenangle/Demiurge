import { describe, expect, it } from "vitest";
import { classifyClipboardPaste } from "./richPaste";


describe("rich input clipboard classification", () => {
  it("keeps a sentence containing a png filename as text", () => {
    expect(classifyClipboardPaste({
      text: "请把 output.png 放进工作流说明里",
      html: "<span>请把 output.png 放进工作流说明里</span>",
      hasImageFile: false,
    })).toEqual({ kind: "text" });
  });

  // 2026-09-01 修复（用户报告「粘贴混合内容丢图片」）：翻转原「text 覆盖 image」决策——
  // 当剪贴板同时含「有意义文本 + 图片」时返回 text-with-image-file，调用方把图片进图片栏 + 文本进 textarea。
  it("text + image → mixed (image enters imagebar, text enters textarea)", () => {
    expect(classifyClipboardPaste({
      text: "日志里出现 result.png，但这是纯文本",
      html: "",
      hasImageFile: true,
    })).toEqual({ kind: "text-with-image-file" });
  });

  it("accepts a standalone media URL", () => {
    const url = "https://example.test/result.png?size=large";
    expect(classifyClipboardPaste({ text: url, html: "", hasImageFile: false }))
      .toEqual({ kind: "media-url", url });
  });

  it("uses a real image file when the clipboard has no text", () => {
    expect(classifyClipboardPaste({ text: "", html: "", hasImageFile: true }))
      .toEqual({ kind: "image-file" });
  });

  it("keeps copied image html support for generic alt text", () => {
    expect(classifyClipboardPaste({
      text: "图片",
      html: '<img src="https://example.test/generated.webp">',
      hasImageFile: false,
    })).toEqual({ kind: "html-image", url: "https://example.test/generated.webp" });
  });

  // 边界：通用图片词（「图片/图像/image/photo/photograph」）不视为有意义文本，
  // 即便同时有图片也走 image-file（保持既有「截图工具纯图片粘贴」行为）。
  it("generic image alt text + image → image-file (not mixed)", () => {
    expect(classifyClipboardPaste({
      text: "图片",
      html: "",
      hasImageFile: true,
    })).toEqual({ kind: "image-file" });
  });

  // 边界：多 token 文本 + 多图（用户截图场景：从某 AI 复制含 @image 引用 + 两张截图）
  it("long sentence + image → mixed (preserves both)", () => {
    expect(classifyClipboardPaste({
      text: "读取文档 @image#1:\"{...}.png\" @image#2:\"{...}.png\" 调用 krea2",
      html: "",
      hasImageFile: true,
    })).toEqual({ kind: "text-with-image-file" });
  });
});
