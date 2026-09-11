import { describe, expect, it } from "vitest";
import {
  normalizeSearchProvider,
  searchProviderWireValue,
  SEARCH_PROVIDER_OPTIONS,
} from "./searchSource";

describe("联网搜索源选项", () => {
  it("默认是 auto（自动回落链），不是某个具体源", () => {
    expect(SEARCH_PROVIDER_OPTIONS[0].value).toBe("auto");
    expect(SEARCH_PROVIDER_OPTIONS.map((o) => o.value)).toEqual(["auto", "bing-cn", "ddg"]);
  });

  it("每个选项都有 label 与 hint（设置页直接渲染，不允许缺说明）", () => {
    for (const option of SEARCH_PROVIDER_OPTIONS) {
      expect(option.label.trim()).not.toBe("");
      expect(option.hint.trim()).not.toBe("");
    }
  });
});

describe("normalizeSearchProvider", () => {
  it("合法值原样保留", () => {
    expect(normalizeSearchProvider("bing-cn")).toBe("bing-cn");
    expect(normalizeSearchProvider("ddg")).toBe("ddg");
    expect(normalizeSearchProvider("auto")).toBe("auto");
  });

  it("非法/缺失/非字符串一律回落 auto（旧存档与脏数据不得让界面崩）", () => {
    expect(normalizeSearchProvider(undefined)).toBe("auto");
    expect(normalizeSearchProvider(null)).toBe("auto");
    expect(normalizeSearchProvider("")).toBe("auto");
    expect(normalizeSearchProvider("google")).toBe("auto");
    expect(normalizeSearchProvider(42)).toBe("auto");
    expect(normalizeSearchProvider({ value: "ddg" })).toBe("auto");
  });

  it("两侧空白被吃掉（存档里带空格不算另一个值）", () => {
    expect(normalizeSearchProvider("  ddg  ")).toBe("ddg");
  });
});

describe("searchProviderWireValue", () => {
  it("auto 送空串——后端约定「空=自动回落链 bing-cn→ddg」", () => {
    expect(searchProviderWireValue("auto")).toBe("");
    expect(searchProviderWireValue(undefined)).toBe("");
  });

  it("显式源名原样上线（后端按名精确取 Adapter，不回落）", () => {
    expect(searchProviderWireValue("bing-cn")).toBe("bing-cn");
    expect(searchProviderWireValue("ddg")).toBe("ddg");
  });
});
