import { describe, expect, it } from "vitest";
import type { Repo } from "../stores/repos";
import { recentWorks, repoActivityLabel, repoLastUsedAt } from "./repoPresentation";

const repo = (id: string, patch: Partial<Repo> = {}): Repo => ({
  id, name: id, createdAt: 1, ...patch,
});

describe("repo presentation", () => {
  it("首页只展示最近使用的小仓库并限制为五个", () => {
    const items = [repo("parent", { lastUsedAt: 99 })];
    for (let index = 1; index <= 7; index += 1) {
      items.push(repo(`work-${index}`, { parentId: "parent", lastUsedAt: index }));
    }
    expect(recentWorks(items).map((item) => item.id)).toEqual([
      "work-7", "work-6", "work-5", "work-4", "work-3",
    ]);
  });

  it("大仓库最近使用时间取自身和子作品最大值", () => {
    expect(repoLastUsedAt(repo("parent", { lastUsedAt: 5 }), [
      repo("work", { parentId: "parent", lastUsedAt: 12 }),
    ])).toBe(12);
  });

  it("后台活动用父仓库与作品名区分同名 SAVE01", () => {
    const items = [
      repo("parent-a", { name: "白给谷" }),
      repo("work-a", { name: "SAVE01", parentId: "parent-a" }),
      repo("parent-b", { name: "Anima" }),
      repo("work-b", { name: "SAVE01", parentId: "parent-b" }),
    ];
    expect(repoActivityLabel(items, "work-a")).toBe("白给谷 · SAVE01");
    expect(repoActivityLabel(items, "work-b")).toBe("Anima · SAVE01");
  });
});
