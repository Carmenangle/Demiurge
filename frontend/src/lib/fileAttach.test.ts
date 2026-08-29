import { describe, expect, it } from "vitest";
import {
  buildFileAttachmentText, FILE_ATTACH_MAX_CHARS, isTextFile,
} from "./fileAttach";

describe("file attach", () => {
  it("文本文件按扩展名与 MIME 判定", () => {
    expect(isTextFile({ name: "形象提示词-唐柚.md", type: "" })).toBe(true);
    expect(isTextFile({ name: "a.txt", type: "" })).toBe(true);
    expect(isTextFile({ name: "data", type: "application/json" })).toBe(true);
    expect(isTextFile({ name: "notes", type: "text/plain" })).toBe(true);
    expect(isTextFile({ name: "b.png", type: "image/png" })).toBe(false);
  });

  it("附件块带身份标记与字数", () => {
    const text = buildFileAttachmentText("唐柚.md", "套装一提示词");
    expect(text).toContain("【文件参考：唐柚.md】");
    expect(text).toContain("（共 6 字）");
    expect(text).toContain("套装一提示词");
    expect(text.endsWith("【文件参考结束：唐柚.md】")).toBe(true);
  });

  it("超长内容截断并注明", () => {
    const raw = "a".repeat(FILE_ATTACH_MAX_CHARS + 10);
    const text = buildFileAttachmentText("big.md", raw);
    expect(text).toContain("已截断至前");
    expect(text.length).toBeLessThan(raw.length + 200);
  });
});
