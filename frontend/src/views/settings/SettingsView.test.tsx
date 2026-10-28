// @vitest-environment jsdom
// 保存时的地址簿白名单对账（D 段）：新增机台 ⇒ POST /comfyui/hosts；删机台 ⇒ DELETE；
// 登记失败不阻断保存但必须弹出来（否则用户只会在提交生成时撞见「不在允许范围内」）。
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { Settings } from "../../stores/settings";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

type Call = { method: string; url: string; body?: string };

let container: HTMLDivElement | null = null;
let root: Root | null = null;

function base(over: Record<string, unknown> = {}): Settings {
  return {
    theme: "bright",
    workflowDir: "",
    outputDir: "",
    characterDir: "",
    worldbookDir: "",
    presetDir: "",
    comfyuiPath: "",
    comfyuiUrl: "http://127.0.0.1:8188",
    comfyuiPython: "",
    comfyExtra: [],
    comfyRoute: {},
    proxyUrl: "",
    searchProvider: "auto",
    modelsDir: "",
    userPersonas: [],
    activeUserPersonaId: undefined,
    ...over,
  } as unknown as Settings;
}

async function flush() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}

async function mount(over: Record<string, unknown> = {}, opts: { hostFails?: boolean } = {}) {
  vi.resetModules();
  vi.stubEnv("VITE_MOBILE_PROFILE", "0");
  vi.stubEnv("VITE_API_BASE", "/api");
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: unknown, init?: { method?: string; body?: string }) => {
    const url = String(input);
    const method = (init?.method || "GET").toUpperCase();
    calls.push({ method, url, body: init?.body });
    if (url.includes("/comfyui/hosts") && method === "POST") {
      if (opts.hostFails) {
        return {
          ok: false, status: 400,
          json: async () => ({ detail: "白名单已达上限（8 台）" }),
          text: async () => "白名单已达上限（8 台）",
        };
      }
      return { ok: true, json: async () => ({ registered: "192.168.1.9" }) };
    }
    if (url.includes("/comfyui/hosts") && method === "DELETE") {
      return { ok: true, json: async () => ({ removed: true }) };
    }
    if (url.includes("/comfyui/config")) {
      return { ok: true, json: async () => ({ path: "", url: "", python_path: "", registered: "", register_error: "" }) };
    }
    return { ok: true, json: async () => ({ listening: false, source: "none", address: "" }) };
  }));
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false, media: query, onchange: null,
    addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
  const { SettingsView } = await import("./SettingsView");
  const initial = base(over);
  function Harness() {
    const [s, setS] = useState<Settings>(initial);
    return (
      <SettingsView
        settings={s}
        update={(next) => setS((prev) => ({ ...prev, ...next }))}
        initialSection="paths"
      />
    );
  }
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root?.render(<Harness />); });
  await flush();
  return calls;
}

function text() {
  return container?.textContent ?? "";
}

function saveButton() {
  return Array.from(container?.querySelectorAll("button") ?? [])
    .find((b) => (b.textContent ?? "").trim() === "保存") as HTMLButtonElement | undefined;
}

function button(label: string) {
  return Array.from(container?.querySelectorAll("button") ?? [])
    .find((b) => (b.textContent ?? "").includes(label)) as HTMLButtonElement | undefined;
}

function setInput(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function addMachine(url: string) {
  act(() => { button("新增机台")!.click(); });
  const urlInput = Array.from(container!.querySelectorAll("input"))
    .find((i) => i.placeholder.includes("192.168")) as HTMLInputElement;
  setInput(urlInput, url);
}

afterEach(() => {
  act(() => { root?.unmount(); });
  root = null;
  container?.remove();
  container = null;
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("SettingsView · 保存时同步地址簿白名单（D 段）", () => {
  it("新增机台 ⇒ 保存后 POST /comfyui/hosts 登记该地址", async () => {
    const calls = await mount();
    await addMachine("http://192.168.1.9:8188");
    act(() => { saveButton()!.click(); });
    await flush();
    const reg = calls.find((c) => c.method === "POST" && c.url.endsWith("/api/comfyui/hosts"));
    expect(reg).toBeTruthy();
    expect(JSON.parse(reg!.body!)).toEqual({ url: "http://192.168.1.9:8188" });
    // 默认地址那份配置同步照旧
    expect(calls.some((c) => c.url.endsWith("/api/comfyui/config"))).toBe(true);
  });

  it("删机台 ⇒ 保存后 DELETE 注销该地址", async () => {
    const calls = await mount({
      comfyExtra: [{ id: "m2", name: "云", url: "http://192.168.1.9:8188" }],
    });
    const del = container!.querySelector("button[title^='删除这台机台']") as HTMLButtonElement;
    act(() => { del.click(); });
    act(() => { saveButton()!.click(); });
    await flush();
    const unreg = calls.find((c) => c.method === "DELETE" && c.url.includes("/api/comfyui/hosts"));
    expect(unreg).toBeTruthy();
    expect(decodeURIComponent(unreg!.url)).toContain("url=http://192.168.1.9:8188");
  });

  it("登记失败 ⇒ 保存照常成功，但弹窗点名失败原因", async () => {
    const calls = await mount({}, { hostFails: true });
    await addMachine("http://192.168.1.9:8188");
    act(() => { saveButton()!.click(); });
    await flush();
    expect(calls.some((c) => c.url.endsWith("/api/comfyui/config"))).toBe(true);
    expect(text()).toContain("设置操作未完成");
    expect(text()).toContain("没能全部登记进白名单");
    expect(text()).toContain("白名单已达上限");
  });
});
