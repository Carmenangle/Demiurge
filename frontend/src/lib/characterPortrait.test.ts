import { describe, expect, it } from "vitest";
import { resolveCharacterPortrait, type CharacterPortrait } from "./characterPortrait";

const portraits: Record<string, CharacterPortrait> = {
  露娜: {
    name: "露娜", avatar: "luna.png",
    expressions: { 愤怒: "luna-angry.png", 平静: "luna-calm.png" },
  },
  米拉: {
    name: "米拉", avatar: "mira.png",
    expressions: {
      happy: "mira-happy.png",
      愤怒: "mira-angry.png",
      平静: "mira-calm.png",
      嫌弃脸: "mira-disgust.png",
      无奈苦笑: "mira-awkward.png",
      战斗受伤: "mira-hurt.png",
    },
  },
};

describe("resolveCharacterPortrait", () => {
  it("selects the mentioned bound card and its matching expression", () => {
    expect(resolveCharacterPortrait("米拉开心地笑了", ["露娜", "米拉"], "露娜", portraits))
      .toEqual({ name: "米拉", url: "mira-happy.png" });
  });

  it("falls back to the opening card avatar when no character is named", () => {
    expect(resolveCharacterPortrait("房间安静下来", ["露娜", "米拉"], "露娜", portraits))
      .toEqual({ name: "露娜", url: "luna.png" });
  });

  it("uses the last explicit speaker instead of the first mentioned character", () => {
    expect(resolveCharacterPortrait(
      "露娜把药递过去。米拉皱眉说道：‘成色不对。’",
      ["露娜", "米拉"], "露娜", portraits,
    )).toEqual({ name: "米拉", url: "mira-disgust.png" });
  });

  it("maps narrative emotion cues onto custom expression names", () => {
    expect(resolveCharacterPortrait(
      "米拉尴尬地别开脸，只能勉强笑了一下。",
      ["露娜", "米拉"], "露娜", portraits,
    )).toEqual({ name: "米拉", url: "mira-awkward.png" });
    expect(resolveCharacterPortrait(
      "米拉捂住流血的伤口，疼得脸色发白。",
      ["露娜", "米拉"], "露娜", portraits,
    )).toEqual({ name: "米拉", url: "mira-hurt.png" });
  });

  it("prefers an exact custom expression phrase when the story uses it", () => {
    expect(resolveCharacterPortrait(
      "米拉露出嫌弃脸，盯着那份药材。",
      ["露娜", "米拉"], "露娜", portraits,
    )).toEqual({ name: "米拉", url: "mira-disgust.png" });
  });

  it("uses the adjacent user turn when a first-person reply omits the character name", () => {
    expect(resolveCharacterPortrait(
      "她皱起眉，显然很不满意。",
      ["露娜", "米拉"], "露娜", portraits,
      "米拉，请你看看这批药材。",
    )).toEqual({ name: "米拉", url: "mira-disgust.png" });
  });

  it("lets an explicit current speaker override the character named by the user", () => {
    expect(resolveCharacterPortrait(
      "露娜平静地回答：‘她暂时不在。’",
      ["露娜", "米拉"], "露娜", portraits,
      "米拉在这里吗？",
    )).toEqual({ name: "露娜", url: "luna-calm.png" });
  });

  it("selects the final speaker when multiple characters talk in one message", () => {
    expect(resolveCharacterPortrait(
      "露娜说道：‘先检查封口。’米拉皱眉回答：‘药材已经受潮。’",
      ["露娜", "米拉"], "露娜", portraits,
    )).toEqual({ name: "米拉", url: "mira-disgust.png" });
  });

  it("keeps actor and expression selection stable when card binding order changes", () => {
    for (const order of [["露娜", "米拉"], ["米拉", "露娜"]]) {
      expect(resolveCharacterPortrait(
        "米拉捂着伤口，疼得直吸气。",
        order, "露娜", portraits,
      )).toEqual({ name: "米拉", url: "mira-hurt.png" });
    }
  });

  it("does not copy another character's emotion onto the current speaker", () => {
    expect(resolveCharacterPortrait(
      "露娜愤怒地拍响桌面，米拉平静地回答：‘我会重新检查。’",
      ["露娜", "米拉"], "露娜", portraits,
    )).toEqual({ name: "米拉", url: "mira-calm.png" });
  });

  it("prefers the longest non-overlapping character name", () => {
    const nested: Record<string, CharacterPortrait> = {
      莉亚: { name: "莉亚", avatar: "lia.png", expressions: {} },
      塞西莉亚: { name: "塞西莉亚", avatar: "cecilia.png", expressions: {} },
    };
    expect(resolveCharacterPortrait(
      "塞西莉亚回答：‘欢迎回来。’",
      ["莉亚", "塞西莉亚"], "塞西莉亚", nested,
    )).toEqual({ name: "塞西莉亚", url: "cecilia.png" });
  });

  it("keeps a non-opening actor across a pronoun-only continue turn", () => {
    expect(resolveCharacterPortrait(
      "她低头继续整理药材。",
      ["露娜", "米拉"], "露娜", portraits,
      "米拉正在诊室配药。\n继续。",
    )).toEqual({ name: "米拉", url: "mira.png" });
  });
});
