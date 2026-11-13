// @vitest-environment jsdom
// 改名是乐观更新：后端**明确拒绝**（文件夹归属冲突/目标已存在）必须回滚显示名并提示；
// 后端离线（网络层失败）保留乐观改名，等下次落盘用新名。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { useRepos } from "./repos";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

class MemoryStorage implements Storage {
  private map = new Map<string, string>();
  get length() { return this.map.size; }
  clear() { this.map.clear(); }
  getItem(k: string) { return this.map.get(k) ?? null; }
  key(i: number) { return [...this.map.keys()][i] ?? null; }
  removeItem(k: string) { this.map.delete(k); }
  setItem(k: string, v: string) { this.map.set(k, String(v)); }
}

const REPOS = [{ id: "r1", name: "旧名", createdAt: 1 }];

function json(body: unknown) {
  return { ok: true, status: 200, json: async () => body };
}

let renameReply: () => Promise<unknown> = async () => json({ folder: "renamed", error: "" });
let root: Root | null = null;
let container: HTMLDivElement | null = null;
let api: ReturnType<typeof useRepos> | null = null;

function Probe() {
  api = useRepos();
  return null;
}

beforeEach(() => {
  vi.stubGlobal("localStorage", new MemoryStorage());
  localStorage.setItem("laf_settings", JSON.stringify({ outputDir: "D:/works" }));
  api = null;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    if (u.includes("/user-state/rename-folder")) return renameReply();
    if (u.endsWith("/user-state")) return json({ repos: REPOS, settings: null });
    return json({});
  }));
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  container?.remove();
  container = null;
  vi.unstubAllGlobals();
});

async function mountHydrated() {
  await act(async () => { root!.render(<Probe />); });
  await act(async () => {});
  expect(api?.hydrated).toBe(true);
  expect(api?.repos[0]?.name).toBe("旧名");
}

describe("renameRepo 与后端文件夹改名的对账", () => {
  it("后端明确拒绝时回滚显示名并回调原因", async () => {
    let release: (v: unknown) => void = () => {};
    renameReply = () => new Promise((resolve) => { release = resolve; });
    await mountHydrated();
    const onError = vi.fn();

    await act(async () => { api!.renameRepo("r1", "新名", onError); });
    expect(api!.repos[0].name).toBe("新名"); // 乐观上屏
    await act(async () => {
      release(json({ folder: "target_exists", error: "目标文件夹已存在，未覆盖：D:/works/新名" }));
    });

    expect(api!.repos[0].name).toBe("旧名");
    expect(onError).toHaveBeenCalledWith("目标文件夹已存在，未覆盖：D:/works/新名");
  });

  it("后端离线时保留乐观改名且不弹错", async () => {
    renameReply = async () => { throw new TypeError("Failed to fetch"); };
    await mountHydrated();
    const onError = vi.fn();

    await act(async () => { api!.renameRepo("r1", "新名", onError); });
    await act(async () => {});

    expect(api!.repos[0].name).toBe("新名");
    expect(onError).not.toHaveBeenCalled();
  });

  it("请求期间又改了名：旧响应不回滚新名字也不弹旧错", async () => {
    const releases: Array<(v: unknown) => void> = [];
    renameReply = () => new Promise((resolve) => { releases.push(resolve); });
    await mountHydrated();
    const onError = vi.fn();

    await act(async () => { api!.renameRepo("r1", "新名", onError); });
    await act(async () => { api!.renameRepo("r1", "更新名", onError); });
    await act(async () => {
      releases[0](json({ folder: "target_exists", error: "第一次改名被拒" }));
    });

    expect(api!.repos[0].name).toBe("更新名");
    expect(onError).not.toHaveBeenCalled();
  });
});
