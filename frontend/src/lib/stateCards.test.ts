import { describe, expect, it } from "vitest";
import { groupStateCards, groupStateCardsDetailed } from "./stateCards";
import type { CharacterStateDto } from "../api/state";

function sample(): CharacterStateDto {
  return {
    card_name: "白露谷",
    repo_id: "save-1",
    数值: {
      好感度: { value: 10, min: -100, max: 100, turn: 1, evidence: "身体开始动摇", source: "auto" },
    },
    叙事: {
      顾凝霜状态: { value: "卡栏动弹不得，仍在抗拒", turn: 1, evidence: "真气无法运转", source: "auto" },
    },
    快照: { text: "", turn: 1 },
    历史: [],
  };
}

describe("groupStateCards", () => {
  it("把通用好感度与唯一显式角色状态合并到同一角色卡", () => {
    const cards = groupStateCards(sample());
    expect(cards).toHaveLength(1);
    expect(cards[0].name).toBe("顾凝霜");
    expect(cards[0].fields.map((f) => f.label)).toEqual(["好感度", "角色状态"]);
    expect(cards[0].fields.map((f) => f.path)).toEqual(["数值/好感度", "叙事/顾凝霜状态"]);
  });

  it("识别带分隔符的多角色字段并分别成卡", () => {
    const state = sample();
    state.数值 = {
      "顾凝霜·好感度": { value: 10, min: -100, max: 100, turn: 1, evidence: "a", source: "auto" },
      "陆莹纱/好感度": { value: -5, min: -100, max: 100, turn: 1, evidence: "b", source: "auto" },
    };
    state.叙事 = {};
    expect(groupStateCards(state).map((c) => c.name)).toEqual(["顾凝霜", "陆莹纱"]);
  });

  it("把同一角色的身体和精神状态归入一张卡", () => {
    const state = sample();
    state.叙事 = {
      顾凝霜状态: { value: "仍在抗拒", turn: 1, evidence: "a", source: "auto" },
      顾凝霜身体状态: { value: "虚弱", turn: 1, evidence: "b", source: "auto" },
      顾凝霜精神状态: { value: "动摇", turn: 1, evidence: "c", source: "auto" },
    };
    const cards = groupStateCards(state);
    expect(cards).toHaveLength(1);
    expect(cards[0].name).toBe("顾凝霜");
    expect(cards[0].fields.map((field) => field.label)).toEqual([
      "好感度", "角色状态", "身体状态", "精神状态",
    ]);
  });

  it("多角色使用分隔符时不会串卡", () => {
    const state = sample();
    state.数值 = {
      "顾凝霜·好感度": { value: 10, min: -100, max: 100, turn: 1, evidence: "a", source: "auto" },
      "陆莹纱·好感度": { value: -5, min: -100, max: 100, turn: 1, evidence: "b", source: "auto" },
    };
    state.叙事 = {
      "顾凝霜·身体状态": { value: "虚弱", turn: 1, evidence: "c", source: "auto" },
      "陆莹纱·精神状态": { value: "警惕", turn: 1, evidence: "d", source: "auto" },
    };
    expect(groupStateCards(state).map((card) => [card.name, card.fields.map((field) => field.label)])).toEqual([
      ["顾凝霜", ["好感度", "身体状态"]],
      ["陆莹纱", ["好感度", "精神状态"]],
    ]);
  });

  it("第一人称主角字段不入卡（我/主角/你，2026-09-01 用户定案）", () => {
    const state = sample();
    state.数值 = {
      好感度: { value: 10, min: -100, max: 100, turn: 1, evidence: "a", source: "auto" },
      "主角·好感度": { value: 99, min: -100, max: 100, turn: 1, evidence: "b", source: "auto" },
    };
    state.叙事 = {
      顾凝霜状态: { value: "仍在抗拒", turn: 1, evidence: "c", source: "auto" },
      "我·所在": { value: "寝殿", turn: 1, evidence: "d", source: "auto" },
      "你·心情": { value: "焦急", turn: 1, evidence: "e", source: "auto" },
    };

    const cards = groupStateCards(state);

    expect(cards).toHaveLength(1);
    expect(cards[0].name).toBe("顾凝霜");
    expect(cards[0].fields.map((f) => f.path)).toEqual(["数值/好感度", "叙事/顾凝霜状态"]);
  });

  it("角色状态表只放角色：卡名不成卡，无归属裸键不入表（2026-09-23 用户实锤）", () => {
    const state = sample();
    state.card_name = "苍梧仙闻录";
    state.数值 = {
      "沈映澜·好感度": { value: 10, min: -100, max: 100, turn: 1, evidence: "", source: "user" },
      "星织恋·好感度": { value: 14, min: -100, max: 100, turn: 1, evidence: "", source: "user" },
    };
    state.叙事 = {
      "沈映澜·身体状态": { value: "颈红至耳根", turn: 1, evidence: "", source: "user" },
      "星织恋·身体状态": { value: "塌腰撅臀", turn: 1, evidence: "", source: "user" },
      // 关系类裸键：既无分隔符也不带状态后缀 ⇒ 没有可归属的角色
      师徒名分: { value: "因阳具当面弹击师尊面部当场濒临破裂", turn: 1, evidence: "", source: "auto" },
    };

    const { cards, unowned } = groupStateCardsDetailed(state);

    expect(cards.map((c) => c.name)).toEqual(["沈映澜", "星织恋"]);
    expect(cards.some((c) => c.name === "苍梧仙闻录" || c.name === "全局状态")).toBe(false);
    expect(unowned).toEqual(["叙事/师徒名分"]);
  });

  it("全是裸键时一张卡都不出，只登记未归属字段", () => {
    const state = sample();
    state.card_name = "苍梧仙闻录";
    state.数值 = {};
    state.叙事 = {
      师徒名分: { value: "当场濒临破裂", turn: 1, evidence: "", source: "auto" },
    };
    const { cards, unowned } = groupStateCardsDetailed(state);
    expect(cards).toEqual([]);
    expect(unowned).toEqual(["叙事/师徒名分"]);
  });
});
