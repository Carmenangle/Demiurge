import { describe, it, expect } from "vitest";
import { runScripts, Placement, type RegexScript } from "./regexEngine";

const s = (o: Partial<RegexScript> & { findRegex: string }): RegexScript => o;

describe("regexEngine 显示层", () => {
  it("基本查找替换", () => {
    expect(runScripts("你好世界", Placement.AI_OUTPUT, [s({ findRegex: "世界", replaceString: "World" })]))
      .toBe("你好World");
  });

  it("捕获组 $1", () => {
    expect(runScripts("看[3]", Placement.AI_OUTPUT, [s({ findRegex: "\\[(\\d+)\\]", replaceString: "第$1条" })]))
      .toBe("看第3条");
  });

  it("{{match}} 整体宏", () => {
    expect(runScripts("喵喵", Placement.AI_OUTPUT, [s({ findRegex: "喵+", replaceString: "({{match}})" })]))
      .toBe("(喵喵)");
  });

  it("命名组 $<name>", () => {
    expect(runScripts("2026年", Placement.AI_OUTPUT, [s({ findRegex: "(?<y>\\d{4})年", replaceString: "$<y>" })]))
      .toBe("2026");
  });

  it("markdownOnly 隐藏 think 块（跨行）", () => {
    const script = s({ findRegex: "/<think>[\\s\\S]*?<\\/think>\\n?/", replaceString: "", markdownOnly: true });
    const out = runScripts("<think>\n内心\n</think>\n正文", Placement.AI_OUTPUT, [script], { isMarkdown: true });
    expect(out).not.toContain("内心");
    expect(out).toContain("正文");
  });

  it("markdownOnly 非显示层不跑", () => {
    const script = s({ findRegex: "秘", replaceString: "*", markdownOnly: true });
    expect(runScripts("秘密", Placement.AI_OUTPUT, [script], { isMarkdown: false })).toBe("秘密");
  });

  it("placement 过滤", () => {
    const script = s({ findRegex: "a", replaceString: "b", placement: [Placement.USER_INPUT] });
    expect(runScripts("aa", Placement.AI_OUTPUT, [script])).toBe("aa");
  });

  it("disabled 跳过", () => {
    expect(runScripts("xx", Placement.AI_OUTPUT, [s({ findRegex: "x", replaceString: "y", disabled: true })]))
      .toBe("xx");
  });

  it("depth 门控", () => {
    const script = s({ findRegex: "z", replaceString: "Z", minDepth: 2, maxDepth: 5 });
    expect(runScripts("z", Placement.AI_OUTPUT, [script], { depth: 0 })).toBe("z");
    expect(runScripts("z", Placement.AI_OUTPUT, [script], { depth: 3 })).toBe("Z");
  });

  it("非法正则不炸", () => {
    expect(runScripts("abc", Placement.AI_OUTPUT, [s({ findRegex: "/[unclosed/", replaceString: "x" })]))
      .toBe("abc");
  });
});
