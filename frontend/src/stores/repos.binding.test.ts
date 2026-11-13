import { describe, expect, it } from "vitest";
import {
  applyBindingPatch, normalizeBindings, resolveRepoBinding, type Repo,
} from "./repos";

const parent: Repo = { id: "p", name: "Anima", createdAt: 1 };
const child: Repo = { id: "c", name: "服设", parentId: "p", createdAt: 2 };

describe("仓库角色卡解绑合同（2026-11-09）", () => {
  it("绑定子仓库不会上提到父仓库，也不会下沉给其他子仓库", () => {
    const fixed = normalizeBindings([
      parent,
      { ...child, cardName: "示例卡", cardNames: ["示例卡"], openingCardName: "示例卡" },
      { id: "c2", name: "另一个服设", parentId: "p", createdAt: 3 },
    ]);

    const next = fixed || [parent, child];
    expect(next.find((r) => r.id === "p")?.cardName).toBeUndefined();
    expect(next.find((r) => r.id === "c2")?.cardNames).toBeUndefined();
  });

  it("父仓库绑卡会下沉给未单独设置的子仓库，子仓库覆盖优先", () => {
    const boundParent: Repo = {
      ...parent, cardName: "示例卡", cardNames: ["示例卡"],
      openingCardName: "示例卡", worldbookName: "世界书A",
    };
    const override: Repo = {
      id: "c2", name: "覆盖作品", parentId: "p", createdAt: 3,
      cardName: "另一张卡", cardNames: ["另一张卡"], openingCardName: "另一张卡",
    };
    const unbound: Repo = {
      id: "c3", name: "显式解绑", parentId: "p", createdAt: 4, cardsUnbound: true,
    };

    const fixed = normalizeBindings([boundParent, child, override, unbound]);
    const next = fixed || [];

    expect(next.find((r) => r.id === "c")).toMatchObject({
      cardNames: ["示例卡"], openingCardName: "示例卡", worldbookName: "世界书A",
    });
    expect(next.find((r) => r.id === "c2")).toMatchObject({
      cardNames: ["另一张卡"], openingCardName: "另一张卡",
    });
    expect(next.find((r) => r.id === "c3")?.cardNames).toBeUndefined();
  });

  it("父仓库改卡/改世界书会同步到继承子仓库，子仓库覆盖不被覆盖", () => {
    const parentA: Repo = {
      ...parent, cardName: "卡A", cardNames: ["卡A"], openingCardName: "卡A",
      worldbookName: "书A",
    };
    const override: Repo = {
      id: "c2", name: "覆盖作品", parentId: "p", createdAt: 3,
      cardName: "卡B", cardNames: ["卡B"], openingCardName: "卡B", worldbookName: "书B",
    };
    const first = normalizeBindings([parentA, child, override]) || [];
    const inheritedChild = first.find((r) => r.id === "c");
    expect(inheritedChild).toMatchObject({
      cardNames: ["卡A"], openingCardName: "卡A", cardsInherited: true, worldbookName: "书A",
    });

    // 父仓库改卡/改书 → 继承子仓库跟着变；覆盖子仓库保持自己的卡/书。
    const parentC: Repo = {
      ...parentA, cardName: "卡C", cardNames: ["卡C"], openingCardName: "卡C",
      worldbookName: "书C",
    };
    const second = normalizeBindings([parentC, inheritedChild!, override]) || [];
    expect(second.find((r) => r.id === "c")).toMatchObject({
      cardNames: ["卡C"], openingCardName: "卡C", worldbookName: "书C",
    });
    expect(second.find((r) => r.id === "c2")).toMatchObject({
      cardNames: ["卡B"], worldbookName: "书B",
    });

    // 父仓库解绑 → 继承子仓库跟着清空；覆盖子仓库不动。
    const parentEmpty: Repo = {
      ...parentC, cardName: undefined, cardNames: undefined, openingCardName: undefined,
      worldbookName: undefined,
    };
    const third = normalizeBindings([parentEmpty, second.find((r) => r.id === "c")!, override]) || [];
    expect(third.find((r) => r.id === "c")?.cardNames).toBeUndefined();
    expect(third.find((r) => r.id === "c")?.worldbookName).toBeUndefined();
    expect(third.find((r) => r.id === "c2")).toMatchObject({
      cardNames: ["卡B"], worldbookName: "书B",
    });
  });

  it("继承子仓库原样保存不会冻结继承来源", () => {
    const inherited: Repo = {
      ...child, cardsInherited: true,
      cardName: "卡A", cardNames: ["卡A"], openingCardName: "卡A",
    };
    const saved = applyBindingPatch(inherited, {
      cardNames: ["卡A"], openingCardName: "卡A", cardName: "卡A",
    });

    expect(saved.cardsInherited).toBe(true);
    expect(saved.cardsUnbound).toBe(false);
  });

  it("显式解绑后不再从父仓库继承角色卡", () => {
    const parentBound: Repo = {
      ...parent, cardName: "示例卡", cardNames: ["示例卡"], openingCardName: "示例卡",
    };
    const bound = applyBindingPatch(child, {
      cardNames: ["示例卡"], openingCardName: "示例卡", cardName: "示例卡",
    });
    const unbound = applyBindingPatch(bound, {
      cardNames: [], openingCardName: "", cardName: "",
    });

    expect(unbound.cardsUnbound).toBe(true);
    expect(unbound.cardNames).toBeUndefined();
    expect(resolveRepoBinding(unbound, [parentBound, unbound]).cardNames).toEqual([]);
  });

  it("空表单原样保存不会误变成显式解绑", () => {
    const untouched = applyBindingPatch(child, {
      cardNames: [], openingCardName: "", cardName: "",
    });

    expect(untouched.cardsUnbound).toBe(false);
  });

  it("重新绑卡会清除显式解绑标记", () => {
    const rebound = applyBindingPatch({ ...child, cardsUnbound: true }, {
      cardNames: ["示例卡"], openingCardName: "示例卡", cardName: "示例卡",
    });

    expect(rebound.cardsUnbound).toBe(false);
    expect(resolveRepoBinding(rebound, [parent, rebound]).cardNames).toEqual(["示例卡"]);
  });

  it("显式解绑后 normalize 会清掉历史遗留的卡字段", () => {
    const stale: Repo = {
      ...child, cardsUnbound: true,
      cardName: "示例卡", cardNames: ["示例卡"], openingCardName: "示例卡",
    };
    const fixed = normalizeBindings([parent, stale]);

    expect(fixed?.find((r) => r.id === "c")).toMatchObject({
      cardName: undefined, cardNames: undefined, openingCardName: undefined,
    });
  });
});
