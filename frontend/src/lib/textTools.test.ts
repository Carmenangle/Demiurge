import { describe, expect, it } from "vitest";
import {
  cleanText, convertChinese, countText, escapeText, insertBetweenCharacters, joinText, resolveSeparator, unescapeText,
} from "./textTools";

describe("text tools", () => {
  it("cleans markdown while preserving visible text", () => {
    expect(cleanText("# 标题\n\n- **内容** [链接](https://x.test)", true, true))
      .toBe("标题\n内容 链接");
  });

  it("joins non-empty lines with decoded separators", () => {
    expect(joinText("甲\n\n乙", "\t")).toBe("甲\t乙");
  });
  it("resolveSeparator decodes separator escapes and keeps real newlines", () => {
    expect(resolveSeparator("\\n")).toBe("\n");
    expect(resolveSeparator("\\t")).toBe("\t");
    expect(resolveSeparator("、")).toBe("、");
    expect(resolveSeparator("回车\n粘贴")).toBe("回车\n粘贴");
  });

  it("joins keep real newline separators (回归：换行分隔符不再粘行)", () => {
    expect(joinText("甲\n\n乙", resolveSeparator("\\n"))).toBe("甲\n乙");
    expect(joinText("甲\n乙", "\n")).toBe("甲\n乙");
    expect(joinText("甲\n乙", "")).toBe("甲乙");
  });

  it("inserts the exact addition between Unicode characters", () => {
    expect(insertBetweenCharacters("甲😀乙", "·")).toBe("甲·😀·乙");
  });

  it("counts CJK, English words, punctuation, characters and lines", () => {
    expect(countText("你好, hello world!\nかな")).toEqual({
      cjk: 4, englishWords: 2, punctuation: 2, characters: 19, lines: 2,
    });
  });

  it("round trips UTF-8 byte escapes", () => {
    const escaped = escapeText("神权 A", "python");
    expect(unescapeText(escaped)).toBe("神权 A");
    expect(escapeText("A", "hex", true)).toBe("41");
  });

  it("converts simplified and traditional Chinese", () => {
    expect(convertChinese("後臺軟體", "to-simplified")).toBe("后台软件");
    expect(convertChinese("后台软件", "to-traditional")).toBe("後臺軟體");
    expect(convertChinese("「測試」", "to-simplified", true)).toBe("【测试】");
  });
});
