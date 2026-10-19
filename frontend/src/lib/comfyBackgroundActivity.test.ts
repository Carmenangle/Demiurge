import { describe, it, expect, vi } from "vitest";

const { listComfyPending } = vi.hoisted(() => ({ listComfyPending: vi.fn() }));
vi.mock("../api/comfyui", () => ({ listComfyPending }));

import {
  hasPendingComfyGeneration, hasServerPendingComfyGeneration, mergeComfyActivities,
  scanComfyActivities, type ComfyBackgroundActivity,
} from "./comfyBackgroundActivity";

const label = (id: string) => id === "repo-a" ? "仓库A" : id === "home" ? "首页" : id;
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

  it("home 线程（首页画布）也要纳入，否则首页出的图在面板里完全看不见", () => {
    const ls = mockStorage({
      "laf_pending_gen_home": JSON.stringify([
        { prompt_id: "p3", createdAt: NOW - 1000 },
      ]),
    });
    const result = scanComfyActivities(NOW, label, ls as Storage);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ promptId: "p3", threadId: "home", label: "首页" });
  });

  it("遗留空键不纳入（P17 迁移后不应再有内容）", () => {
    const ls = mockStorage({
      "laf_pending_gen_": JSON.stringify([{ prompt_id: "p9", createdAt: NOW - 1000 }]),
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

describe("mergeComfyActivities（服务端登记补齐跨端可见性）", () => {
  const local: ComfyBackgroundActivity[] = [
    { promptId: "p1", threadId: "repo-a", label: "仓库A" },
  ];

  it("本浏览器没见过的服务端任务会补进来（换浏览器/清缓存场景）", () => {
    const merged = mergeComfyActivities(
      local,
      [{ prompt_id: "p2", thread_id: "repo-a", created_at: (NOW - 1000) / 1000 }],
      label, NOW,
    );
    expect(merged.map((item) => item.promptId)).toEqual(["p1", "p2"]);
  });

  it("同一 promptId 以本地为准，不重复列", () => {
    const merged = mergeComfyActivities(
      local,
      [{ prompt_id: "p1", thread_id: "other", created_at: NOW / 1000 }],
      label, NOW,
    );
    expect(merged).toHaveLength(1);
    expect(merged[0].label).toBe("仓库A");
  });

  it("服务端超过 30 分钟的陈旧条目不再显示；home 保留", () => {
    const merged = mergeComfyActivities(local, [
      { prompt_id: "old", thread_id: "home", created_at: (NOW - 31 * 60 * 1000) / 1000 },
      { prompt_id: "fresh", thread_id: "home", created_at: (NOW - 1000) / 1000 },
    ], label, NOW);
    expect(merged.map((item) => item.promptId)).toEqual(["p1", "fresh"]);
    expect(merged[1].label).toBe("首页");
  });

  it("空 prompt_id 的脏数据丢弃", () => {
    const merged = mergeComfyActivities(local, [
      { prompt_id: "", thread_id: "repo-a", created_at: NOW / 1000 },
    ], label, NOW);
    expect(merged).toHaveLength(1);
  });
});

describe("hasServerPendingComfyGeneration（删除仓库守卫跨端一致）", () => {
  it("服务端登记里有该 thread 的在途任务 ⇒ true", async () => {
    listComfyPending.mockResolvedValueOnce({
      items: [{ prompt_id: "p1", thread_id: "child", created_at: NOW / 1000 }],
    });
    await expect(hasServerPendingComfyGeneration(["parent", "child"])).resolves.toBe(true);
  });

  it("只有别的 thread 在跑 ⇒ false", async () => {
    listComfyPending.mockResolvedValueOnce({
      items: [{ prompt_id: "p1", thread_id: "other", created_at: NOW / 1000 }],
    });
    await expect(hasServerPendingComfyGeneration(["child"])).resolves.toBe(false);
  });

  it("后端不可用时不阻断删除（宁可放过也不卡死）", async () => {
    listComfyPending.mockRejectedValueOnce(new Error("offline"));
    await expect(hasServerPendingComfyGeneration(["child"])).resolves.toBe(false);
  });
});
