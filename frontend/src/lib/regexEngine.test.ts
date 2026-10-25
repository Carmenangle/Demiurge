import { describe, it, expect } from "vitest";
import {
  runScripts, Placement, hasNestedUnboundedQuant,
  MAX_INPUT_CHARS, MAX_PATTERN_CHARS, MAX_SCRIPTS_PER_RUN,
  type RegexScript,
} from "./regexEngine";

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

// ReDoS 防护（2026-10-24 二轮审计）：与后端 tests/test_regex_engine_redos.py 同一批
// 形态与常量——双端合同，改一边必须同步另一边。
describe("regexEngine ReDoS 防护", () => {
  it("嵌套无界量词经典形态拒编译", () => {
    const bad = ["(a+)+", "(a+)*", "(a*)*", "(a+){2,}", "(\\w+\\s?)+$", "((a+)+)b", "/(a+)+b/i"];
    for (const p of bad) {
      expect(hasNestedUnboundedQuant(p.startsWith("/") ? p.slice(1, p.lastIndexOf("/")) : p)).toBe(true);
    }
    // 端到端：坏形态脚本被跳过（若防线失效，此用例会因真跑指数回溯而超时红）
    expect(runScripts("aaaab", Placement.AI_OUTPUT, [s({ findRegex: "(a+)+", replaceString: "[X]" })]))
      .toBe("aaaab");
  });

  it("良性模式不受影响（含真实卡常见形态）", () => {
    const ok = ["(ab)+", "(a+)?", "(?:https?://)?(\\S+)", "(\\d+)-(\\d+)", "\\s*(\\w+)\\s*", "a{2,3}"];
    for (const p of ok) {
      expect(hasNestedUnboundedQuant(p)).toBe(false);
    }
    expect(runScripts("看[3]", Placement.AI_OUTPUT, [s({ findRegex: "\\[(\\d+)\\]", replaceString: "第$1条" })]))
      .toBe("看第3条");
  });

  it("pattern 长度上限", () => {
    expect(hasNestedUnboundedQuant("a".repeat(MAX_PATTERN_CHARS))).toBe(false);
    expect(runScripts("abc", Placement.AI_OUTPUT,
      [s({ findRegex: `(${"a".repeat(MAX_PATTERN_CHARS)})+`, replaceString: "X" })])).toBe("abc");
  });

  it("脚本数上限截前 N 保顺序（定宽 token 防子串交叉）", () => {
    const scripts: RegexScript[] = [];
    for (let i = 0; i < MAX_SCRIPTS_PER_RUN + 10; i += 1) {
      scripts.push(s({ findRegex: `z${String(i).padStart(3, "0")}`, replaceString: `<${String(i).padStart(3, "0")}>` }));
    }
    let text = "";
    for (let i = 0; i < MAX_SCRIPTS_PER_RUN + 10; i += 1) text += `z${String(i).padStart(3, "0")}`;
    const out = runScripts(text, Placement.AI_OUTPUT, scripts);
    expect(out).toContain(`<${String(MAX_SCRIPTS_PER_RUN - 1).padStart(3, "0")}>`);
    expect(out).toContain(`z${String(MAX_SCRIPTS_PER_RUN).padStart(3, "0")}`);
  });

  it("超长输入整轮跳过", () => {
    const big = "a".repeat(MAX_INPUT_CHARS + 1);
    expect(runScripts(big, Placement.AI_OUTPUT, [s({ findRegex: "a", replaceString: "b" })])).toBe(big);
  });

  it("形态拦截在 runScripts 生效：坏形态跳过、其余照常", () => {
    expect(runScripts("abc坏", Placement.AI_OUTPUT,
      [s({ findRegex: "(a+)+", replaceString: "[X]" }), s({ findRegex: "坏", replaceString: "[X]" })]))
      .toBe("abc[X]");
  });
});
