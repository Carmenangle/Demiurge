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

  it("does not let an advertised image item override meaningful text", () => {
    expect(classifyClipboardPaste({
      text: "日志里出现 result.png，但这是纯文本",
      html: "",
      hasImageFile: true,
    })).toEqual({ kind: "text" });
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
});
