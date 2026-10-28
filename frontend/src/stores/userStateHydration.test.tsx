// @vitest-environment jsdom
// 联机同库的「谁是真值」行为网（2026-10-28 用户报障：对话/图像模型设置显示的是本机那份）。
//
// 钉住两条：① 联机开着但电脑不可达（/api/user-state 被代理成 502）时，绝不把本机快照
// 回写电脑；② 电脑回来后自动重新水合，界面换成电脑那份（无需重启 App）。
// 两个 store 必须一起挂：userStateSync.flush 要等 repos+settings 都就绪才会 POST。
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Node 25 的内置 localStorage 桩遮蔽 jsdom（clear 等方法缺失，settings.test 同源坑）。
class MemoryStorage implements Storage {
  private map = new Map<string, string>();
  get length() { return this.map.size; }
  clear() { this.map.clear(); }
  getItem(k: string) { return this.map.get(k) ?? null; }
  key(i: number) { return [...this.map.keys()][i] ?? null; }
  removeItem(k: string) { return this.map.delete(k); }
  setItem(k: string, v: string) { this.map.set(k, String(v)); }
}

const PC_KEY = "k".repeat(35);
const SERVER_SETTINGS = {
  theme: "night",
  chatModels: [{ id: "pc-1", modelName: "pc-model", baseUrl: "https://pc.example/v1", apiKey: PC_KEY }],
  imageModels: [],
  videoModels: [],
  activeChatModelId: "pc-1",
};
const SERVER_REPOS = [{ id: "pc-repo", name: "电脑上的作品" }];

let userStateDown = true;
let posts: Array<Record<string, unknown>> = [];
let container: HTMLDivElement | null = null;
let root: Root | null = null;
let notifyReconnect: (() => void) | null = null;
let api: { settings: { chatModels: Array<{ modelName: string; apiKey: string }> }; repos: Array<{ name: string }>; update: (p: Record<string, unknown>) => void; addRepo?: unknown } | null = null;

function json(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function stubFetch() {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: { method?: string; body?: string }) => {
    const u = String(url);
    const method = init?.method || "GET";
    if (u.startsWith("/api/mobile/remote")) {
      return json(200, {
        supported: true, enabled: true, active: true,
        base_url: "http://192.0.2.10:8012", token_set: true,
      });
    }
    if (u.startsWith("/api/user-state/proxy-status")) {
      return json(200, {
        listening: false, address: "", source: "none",
        system_address: "", manual_address: "", candidates: [],
      });
    }
    if (u.startsWith("/api/user-state") && method === "POST") {
      posts.push(JSON.parse(init?.body || "{}"));
      return json(200, { ok: true });
    }
    if (u.startsWith("/api/user-state")) {
      return userStateDown
        ? json(502, { detail: "联机模式：连不上电脑" })
        : json(200, { repos: SERVER_REPOS, settings: SERVER_SETTINGS });
    }
    return json(404, {});
  }));
}

async function mountStores() {
  vi.resetModules();
  vi.stubEnv("VITE_MOBILE_PROFILE", "1");
  vi.stubEnv("VITE_API_BASE", "/api");
  vi.stubGlobal("localStorage", new MemoryStorage());
  localStorage.clear();
  // jsdom 没有 matchMedia（settings 的 applyTheme 会用到）
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, dispatchEvent: () => false,
  })) as never;
  stubFetch();
  const { useSettings } = await import("./settings");
  const { useRepos } = await import("./repos");
  const { notifyUserStateReconnect } = await import("../lib/userStateSync");
  notifyReconnect = notifyUserStateReconnect;

  function Host() {
    const settingsStore = useSettings();
    const reposStore = useRepos();
    api = {
      settings: settingsStore.settings as never,
      repos: reposStore.repos as never,
      update: settingsStore.update,
    };
    return null;
  }
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root!.render(<Host />); });
  await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
}

async function settle(ms = 900) {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}

afterEach(async () => {
  await act(async () => { root?.unmount(); });
  container?.remove();
  root = null;
  container = null;
  api = null;
  posts = [];
  userStateDown = true;
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("联机同库的水合与回写", () => {
  it("电脑不可达时不把本机快照回写电脑", async () => {
    userStateDown = true;
    await mountStores();
    // 用户在断线期间改了设置：可以改，但绝不能当成电脑的真值上传
    await act(async () => { api!.update({ theme: "night" }); });
    await settle(900);
    expect(posts).toHaveLength(0);
  });

  it("电脑恢复后重新水合，界面换成电脑那份", async () => {
    userStateDown = true;
    await mountStores();
    expect(api!.settings.chatModels ?? []).toHaveLength(0); // 断线：跑在本机缓存上

    userStateDown = false;
    await act(async () => { notifyReconnect!(); });
    await settle(300);

    expect(api!.settings.chatModels).toHaveLength(1);
    expect(api!.settings.chatModels[0].modelName).toBe("pc-model");
    expect(api!.settings.chatModels[0].apiKey).toHaveLength(35);
    expect(api!.repos.map((r) => r.name)).toContain("电脑上的作品");
  });
});
