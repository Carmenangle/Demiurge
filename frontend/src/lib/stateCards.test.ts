import { describe, expect, it } from "vitest";
import { groupStateCards } from "./stateCards";
import type { CharacterStateDto } from "../api/state";

function sample(): CharacterStateDto {
  return {
    card_name: "白给谷",
    repo_id: "save-1",
    数值: {
      好感度: { value: 10, min: -100, max: 100, turn: 1, evidence: "身体开始动摇", source: "auto" },
    },
    叙事: {
      冷倾雪状态: { value: "卡栏动弹不得，仍在抗拒", turn: 1, evidence: "真气无法运转", source: "auto" },
    },
    快照: { text: "", turn: 1 },
    历史: [],
  };
}

describe("groupStateCards", () => {
  it("把通用好感度与唯一显式角色状态合并到同一角色卡", () => {
    const cards = groupStateCards(sample());
    expect(cards).toHaveLength(1);
    expect(cards[0].name).toBe("冷倾雪");
    expect(cards[0].fields.map((f) => f.label)).toEqual(["好感度", "角色状态"]);
    expect(cards[0].fields.map((f) => f.path)).toEqual(["数值/好感度", "叙事/冷倾雪状态"]);
  });

  it("识别带分隔符的多角色字段并分别成卡", () => {
    const state = sample();
    state.数值 = {
      "冷倾雪·好感度": { value: 10, min: -100, max: 100, turn: 1, evidence: "a", source: "auto" },
      "虞莹纱/好感度": { value: -5, min: -100, max: 100, turn: 1, evidence: "b", source: "auto" },
    };
    state.叙事 = {};
    expect(groupStateCards(state).map((c) => c.name)).toEqual(["冷倾雪", "虞莹纱"]);
  });

  it("把同一角色的身体和精神状态归入一张卡", () => {
    const state = sample();
    state.叙事 = {
      冷倾雪状态: { value: "仍在抗拒", turn: 1, evidence: "a", source: "auto" },
      冷倾雪身体状态: { value: "虚弱", turn: 1, evidence: "b", source: "auto" },
      冷倾雪精神状态: { value: "动摇", turn: 1, evidence: "c", source: "auto" },
    };
    const cards = groupStateCards(state);
    expect(cards).toHaveLength(1);
    expect(cards[0].name).toBe("冷倾雪");
    expect(cards[0].fields.map((field) => field.label)).toEqual([
      "好感度", "角色状态", "身体状态", "精神状态",
    ]);
  });

  it("多角色使用分隔符时不会串卡", () => {
    const state = sample();
    state.数值 = {
      "冷倾雪·好感度": { value: 10, min: -100, max: 100, turn: 1, evidence: "a", source: "auto" },
      "虞莹纱·好感度": { value: -5, min: -100, max: 100, turn: 1, evidence: "b", source: "auto" },
    };
    state.叙事 = {
      "冷倾雪·身体状态": { value: "虚弱", turn: 1, evidence: "c", source: "auto" },
      "虞莹纱·精神状态": { value: "警惕", turn: 1, evidence: "d", source: "auto" },
    };
    expect(groupStateCards(state).map((card) => [card.name, card.fields.map((field) => field.label)])).toEqual([
      ["冷倾雪", ["好感度", "身体状态"]],
      ["虞莹纱", ["好感度", "精神状态"]],
    ]);
  });
});
