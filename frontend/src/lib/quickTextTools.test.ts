import { describe, expect, it } from "vitest";
import { DEFAULT_QUICK_TEXT_OPTIONS, runQuickTextTool } from "./quickTextTools";

describe("quick text tools", () => {
  it("uses the same cleaning and separator rules as the full tools", () => {
    expect(runQuickTextTool("clean", "# 标题\n\n- **正文**", DEFAULT_QUICK_TEXT_OPTIONS))
      .toBe("标题\n正文");
    expect(runQuickTextTool("join", "甲\n\n乙", {
      ...DEFAULT_QUICK_TEXT_OPTIONS, separator: " | ",
    })).toBe("甲 | 乙");
  });

  it("formats statistics for compact display", () => {
    expect(runQuickTextTool("stats", "你好, world!", DEFAULT_QUICK_TEXT_OPTIONS))
      .toContain("汉字 / 日文：2\n英文单词：1\n标点符号：2");
  });

  it("reports invalid byte input to the caller", () => {
    expect(() => runQuickTextTool("escape", "不是字节", {
      ...DEFAULT_QUICK_TEXT_OPTIONS, escapeDirection: "decode",
    })).toThrow("没有识别到");
  });
});
