// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { DEFAULT_LIQUID_GLASS } from "../stores/settings";
import { LiquidGlassDock } from "./LiquidGlassDock";

vi.mock("../api/userState", () => ({
  fetchWallpaperSceneProperties: vi.fn(),
  getWallpaperStore: vi.fn(),
  migrateWallpaperStore: vi.fn(),
  scanWorkshopWallpapers: vi.fn(),
  uploadWallpaper: vi.fn(),
  wallpaperThumbUrl: vi.fn(() => "thumb"),
}));
vi.mock("../api/comfyui", () => ({ localViewUrl: vi.fn((p: string) => p) }));
vi.mock("./LiquidGlassWallpaperLibrary", () => ({ LiquidGlassWallpaperLibrary: () => null }));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  const store = new Map<string, string>();
  // Node 24 自带的 localStorage 与 jsdom 不兼容（getItem 不是函数），这里换成内存实现
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => { store.set(key, String(value)); },
    removeItem: (key: string) => { store.delete(key); },
    clear: () => { store.clear(); },
  });
  container = document.createElement("div");
  // 复刻真实挂点：桌面 .app-dock 是 position:fixed，Blink 视为层叠上下文
  container.className = "app-dock";
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  document.querySelectorAll(".lg-panel, .lg-dock-scrim").forEach((n) => n.remove());
  vi.unstubAllGlobals();
});

describe("液态玻璃控制台浮层层级", () => {
  it("面板与遮罩挂到 body，不被 .app-dock 的层叠上下文困在输入区之下", () => {
    act(() => {
      root.render(
        <LiquidGlassDock
          lg={{ ...DEFAULT_LIQUID_GLASS, ropeShown: false }}
          update={() => {}}
        />,
      );
    });
    const mini = container.querySelector<HTMLButtonElement>(".lg-rope-mini");
    expect(mini).toBeTruthy();
    act(() => mini!.click());

    const panel = document.querySelector(".lg-panel");
    expect(panel).toBeTruthy();
    expect(panel!.parentElement).toBe(document.body);
    expect(container.querySelector(".lg-panel")).toBeNull();
    const scrim = document.querySelector(".lg-dock-scrim");
    expect(scrim).toBeTruthy();
    expect(scrim!.parentElement).toBe(document.body);
  });
});
