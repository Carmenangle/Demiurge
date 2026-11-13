import { describe, expect, it } from "vitest";
import {
  findBindingKey, isWorldbookRoster, parseRosterOverrides, rosterNamesOf,
  type CharacterRosterOverrides,
} from "./characterRoster";
import type { CharacterRosterResult } from "../api/ai/agent";

function roster(over: Partial<CharacterRosterResult> = {}): CharacterRosterResult {
  return {
    source: "worldbook",
    members: ["林昭", "苏晚"],
    entries: [{ name: "林昭" }, { name: "苏晚" }],
    candidates: [],
    fallback_card: "示例作品甲",
    ...over,
  };
}

describe("角色名册覆盖解析", () => {
  it("宽松解析非法结构并去重", () => {
    expect(parseRosterOverrides(null)).toEqual({
      mode: "auto", include: [], exclude: [], order: [], rename: {},
    });
    expect(parseRosterOverrides({
      mode: "worldbook",
      include: ["u1", "u1", " ", 3],
      exclude: "u2",
      rename: { u1: " 林昭 ", "": "空" },
    })).toEqual({
      mode: "worldbook",
      include: ["u1", "3"],
      exclude: [],
      order: [],
      rename: { u1: "林昭" },
    });
  });

  it("未知 mode 回落 auto", () => {
    const parsed: CharacterRosterOverrides = parseRosterOverrides({ mode: "bogus" });
    expect(parsed.mode).toBe("auto");
  });
});

describe("名册成员选择", () => {
  it("世界书名册优先于绑定卡回退", () => {
    expect(rosterNamesOf(roster(), ["示例作品甲"])).toEqual(["林昭", "苏晚"]);
  });

  it("世界书名册为空时回退绑定卡", () => {
    expect(rosterNamesOf(roster({ members: [], entries: [] }), ["甲", "乙"]))
      .toEqual(["甲", "乙"]);
    expect(rosterNamesOf(null, ["甲"])).toEqual(["甲"]);
  });

  it("cards 来源不劫持绑定卡", () => {
    const cards = roster({ source: "cards", members: ["甲"], entries: [] });
    expect(isWorldbookRoster(cards)).toBe(false);
    expect(rosterNamesOf(cards, ["甲", "乙"])).toEqual(["甲", "乙"]);
  });
});

describe("角色绑定名匹配（世界书全名 ↔ 多元数据短名）", () => {
  it("精确命中优先", () => {
    expect(findBindingKey("埃吉尔", ["埃吉尔", "腓特烈"])).toBe("埃吉尔");
  });

  it("短名匹配世界书全名", () => {
    expect(findBindingKey("腓特烈大帝", ["腓特烈", "奥古斯特"])).toBe("腓特烈");
    expect(findBindingKey("奥古斯特·冯·帕塞瓦尔", ["腓特烈", "奥古斯特"])).toBe("奥古斯特");
    expect(findBindingKey("埃姆登", ["埃姆登", "埃吉尔"])).toBe("埃姆登");
  });

  it("同分多义不猜", () => {
    expect(findBindingKey("阿尔法", ["阿尔", "阿尔法"]))
      .toBe("阿尔法"); // 精确命中仍优先
    expect(findBindingKey("阿尔法贝塔", ["阿尔法", "阿尔法贝"])).toBe("阿尔法贝");
    expect(findBindingKey("完全不同的名字", ["甲", "乙"])).toBe("");
  });
});
