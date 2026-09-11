import { describe, it, expect } from "vitest";
import { hasPendingComfyGeneration, scanComfyActivities } from "./comfyBackgroundActivity";

const label = (id: string) => id === "repo-a" ? "仓库A" : id;
const NOW = 1_000_000;

function mockStorage(storage: Record<string, string>) {
  return {
    get length() { return Object.keys(storage).length; },
    key: (i: number) => Object.keys(storage)[i] ?? null,
    getItem: (k: string) => storage[k] ?? null,
    setItem: (k: string, value: string) => { storage[k] = value; },
    removeItem: (k: string) => { delete storage[k]; },
  };
}

describe("scanComfyActivities", () => {
  it("返回进行中的出图任务", () => {
    const ls = mockStorage({
      "laf_pending_gen_repo-a": JSON.stringify([
        { prompt_id: "p1", createdAt: NOW - 1000 },
      ]),
    });
    const result = scanComfyActivities(NOW, label, ls as Storage);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ promptId: "p1", threadId: "repo-a", label: "仓库A" });
  });

  it("过期任务（超30分钟）不展示", () => {
    const ls = mockStorage({
      "laf_pending_gen_repo-b": JSON.stringify([
        { prompt_id: "p2", createdAt: NOW - 31 * 60 * 1000 },
      ]),
    });
    expect(scanComfyActivities(NOW, label, ls as Storage)).toHaveLength(0);
  });

  it("home 线程不纳入", () => {
    const ls = mockStorage({
      "laf_pending_gen_home": JSON.stringify([
        { prompt_id: "p3", createdAt: NOW - 1000 },
      ]),
    });
    expect(scanComfyActivities(NOW, label, ls as Storage)).toHaveLength(0);
  });

  it("非 laf_pending_gen_ 前缀的 key 忽略，只返回匹配项", () => {
    const ls = mockStorage({
      "laf_repos": JSON.stringify([{ id: "repo-a", name: "仓库A" }]),
      "laf_pending_gen_repo-a": JSON.stringify([
        { prompt_id: "p4", createdAt: NOW - 1000 },
      ]),
    });
    expect(scanComfyActivities(NOW, label, ls as Storage)).toHaveLength(1);
  });

  it("后台活动只读展示，不得删除较慢生图任务的持久化真源", () => {
    const values = {
      "laf_pending_gen_repo-a": JSON.stringify([
        { prompt_id: "slow-prompt", createdAt: NOW - 31 * 60 * 1000 },
      ]),
    };
    const ls = mockStorage(values);

    expect(scanComfyActivities(NOW, label, ls as Storage)).toHaveLength(0);
    expect(JSON.parse(values["laf_pending_gen_repo-a"])).toEqual([
      { prompt_id: "slow-prompt", createdAt: NOW - 31 * 60 * 1000 },
    ]);
  });

  it("删除仓库前能检出子仓库的进行中生图", () => {
    const ls = mockStorage({
      "laf_pending_gen_child": JSON.stringify([{ prompt_id: "p1", createdAt: NOW }]),
    });

    expect(hasPendingComfyGeneration(["parent", "child"], ls as Storage)).toBe(true);
    expect(hasPendingComfyGeneration(["other"], ls as Storage)).toBe(false);
  });
});
