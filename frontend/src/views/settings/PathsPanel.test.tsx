// @vitest-environment jsdom
// 地址簿（D 段）的界面行为网：额外机台的增删、按模态路由下拉、非回环画布预警、
// 联机中只读。白名单登记不在视图里做（保存时由 SettingsView 用 diffComfyHosts 对账，
// 那份纯逻辑在 comfyMachines.test 里钉住），这里只钉界面语义与草稿写回。
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { Settings } from "../../stores/settings";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
// jsdom 不实现滚动，面板挂载时「仓库文件夹为空」的引导会调它
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

let container: HTMLDivElement | null = null;
let root: Root | null = null;
let current: Settings;

function base(over: Record<string, unknown> = {}): Settings {
  return {
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
  await act(async () => {
    if (vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(0);
    else await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function mount(
  over: Record<string, unknown> = {},
  opts: { remoteActive?: boolean; remote?: "pending" | "fail" } = {},
) {
  vi.resetModules();
  vi.stubEnv("VITE_MOBILE_PROFILE", "1");  // 手机形态：联机只读与手机口径提示都在这条分支
  vi.stubEnv("VITE_API_BASE", "/api");
  const fetchSpy = vi.fn(async (input: unknown) => {
    const url = String(input);
    if (url.includes("/mobile/remote")) {
      // pending＝后端迟迟不答（真机冷启动窗口）；fail＝连续失败（走重试后回落）
      if (opts.remote === "pending") return new Promise<never>(() => {});
      if (opts.remote === "fail") throw new Error("remote probe failed");
      const active = opts.remoteActive ?? false;
      return {
        ok: true,
        json: async () => ({
          supported: true, enabled: active, active, token_set: true,
          base_url: active ? "http://192.0.2.10:8010" : "",
        }),
      };
    }
    if (url.includes("/comfyui/status")) {
      return { ok: true, json: async () => ({ running: true, managed: false }) };
    }
    return { ok: true, json: async () => ({ listening: false, source: "none", address: "" }) };
  });
  vi.stubGlobal("fetch", fetchSpy);
  const { PathsPanel } = await import("./PathsPanel");
  const initial = base(over);
  current = initial;
  function Harness() {
    const [d, setD] = useState<Settings>(initial);
    current = d;
    return <PathsPanel draft={d} setDraft={setD} />;
  }
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root?.render(<Harness />); });
  await flush();
  return fetchSpy;
}

function text() {
  return container?.textContent ?? "";
}

function button(label: string) {
  return Array.from(container?.querySelectorAll("button") ?? [])
    .find((b) => (b.textContent ?? "").includes(label)) as HTMLButtonElement | undefined;
}

// 面板里还有一个「联网搜索源」下拉：地址簿的三个按 aria-label 收窄
function selects() {
  return Array.from(
    container?.querySelectorAll("select[aria-label$='机台']") ?? [],
  ) as HTMLSelectElement[];
}

function deleteButtons() {
  return Array.from(container?.querySelectorAll("button[title^='删除这台机台']") ?? []) as HTMLButtonElement[];
}

function setInput(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function setSelect(el: HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!;
  act(() => {
    setter.call(el, value);
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

afterEach(() => {
  act(() => { root?.unmount(); });
  root = null;
  container?.remove();
  container = null;
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("PathsPanel · ComfyUI 地址簿（D 段）", () => {
  it("额外机台进下拉；选择按模态写回 comfyRoute，选「默认」删键", async () => {
    await mount({
      comfyExtra: [{ id: "m2", name: "云 A100", url: "https://cloud.example.com:8188" }],
      comfyRoute: { video: "m2" },
    });
    const sel = selects();
    expect(sel).toHaveLength(3);  // 图片 / 视频 / 配音
    expect(sel.map((s) => s.value)).toEqual(["default", "m2", "default"]);
    expect(Array.from(sel[0].options).map((o) => o.textContent)).toEqual([
      "默认（http://127.0.0.1:8188）",
      "云 A100（https://cloud.example.com:8188）",
    ]);

    setSelect(sel[0], "m2");
    expect(current.comfyRoute).toEqual({ video: "m2", image: "m2" });

    setSelect(sel[0], "default");
    expect(current.comfyRoute?.image).toBeUndefined();
    expect("image" in (current.comfyRoute || {})).toBe(false);
  });

  it("路由指向已删机台 ⇒ 下拉显示「默认」（与实际解析一致，不留死引用）", async () => {
    await mount({
      comfyExtra: [],
      comfyRoute: { video: "gone" },
    });
    expect(selects().map((s) => s.value)).toEqual(["default", "default", "default"]);
  });

  it("新增机台落成空条目；改名/改地址写回草稿", async () => {
    await mount();
    act(() => { button("新增机台")!.click(); });
    expect(current.comfyExtra).toHaveLength(1);
    const added = current.comfyExtra![0];
    expect(added.id).toBeTruthy();
    expect(added.name).toBe("");
    expect(added.url).toBe("");

    const nameInput = Array.from(container!.querySelectorAll("input"))
      .find((i) => i.placeholder.includes("机台名")) as HTMLInputElement;
    const urlInput = Array.from(container!.querySelectorAll("input"))
      .find((i) => i.placeholder.includes("192.168")) as HTMLInputElement;
    setInput(nameInput, "TTS 小机");
    setInput(urlInput, "http://tts:8188");
    expect(current.comfyExtra![0]).toMatchObject({ name: "TTS 小机", url: "http://tts:8188" });
  });

  it("删除机台：条目移除，指向它的路由一并清掉", async () => {
    await mount({
      comfyExtra: [
        { id: "m2", name: "云", url: "https://cloud.example.com:8188" },
        { id: "m3", name: "备用", url: "http://192.168.1.9:8188" },
      ],
      comfyRoute: { video: "m2", image: "m3" },
    });
    expect(deleteButtons()).toHaveLength(2);
    act(() => { deleteButtons()[0].click(); });
    expect(current.comfyExtra!.map((m) => m.id)).toEqual(["m3"]);
    expect(current.comfyRoute).toEqual({ image: "m3" });
  });

  it("探测额外机台：走 /comfyui/status 问那台机，连上后给出结论", async () => {
    const fetchSpy = await mount({
      comfyExtra: [{ id: "m2", name: "云", url: "https://cloud.example.com:8188" }],
    });
    act(() => { button("探测")!.click(); });
    await flush();
    const hit = fetchSpy.mock.calls.some((call) => {
      const url = String(call[0]);
      return url.includes("/comfyui/status")
        && url.includes(encodeURIComponent("https://cloud.example.com:8188"));
    });
    expect(hit).toBe(true);
    expect(text()).toContain("已连通");
  });

  it("默认地址非回环 ⇒ 预警原生画布不可用；回环地址不预警", async () => {
    await mount({ comfyuiUrl: "https://cloud.example.com:8188" });
    expect(text()).toContain("原生画布不可用");
    expect(text()).toContain("http://127.0.0.1:8188");
  });

  it("默认地址是回环 ⇒ 不出现画布预警", async () => {
    await mount();
    expect(text()).not.toContain("原生画布不可用");
  });

  it("联机中（remote active）⇒ 地址簿只读并点名真源在电脑", async () => {
    await mount({
      comfyExtra: [{ id: "m2", name: "云", url: "https://cloud.example.com:8188" }],
    }, { remoteActive: true });
    expect(text()).toContain("联机中");
    expect(text()).toContain("真源在电脑");
    expect(button("新增机台")!.disabled).toBe(true);
    expect(deleteButtons()[0].disabled).toBe(true);
    expect(selects().every((s) => s.disabled)).toBe(true);
  });

  it("未联机 ⇒ 地址簿可编辑", async () => {
    await mount({
      comfyExtra: [{ id: "m2", name: "云", url: "https://cloud.example.com:8188" }],
    });
    expect(button("新增机台")!.disabled).toBe(false);
    expect(deleteButtons()[0].disabled).toBe(false);
    expect(selects().some((s) => s.disabled)).toBe(false);
  });

  // 真机 0.30.14-beta 实测：开机头几秒（电脑掉线时窗口更长）地址簿先按可编辑渲染，
  // 读到 active 才切只读；此时点保存必然 403。未知态一律先只读。
  it("联机状态还没读到时先按只读渲染（且不说「真源在电脑」）", async () => {
    await mount({}, { remote: "pending" });
    expect(button("新增机台")!.disabled).toBe(true);
    expect(selects().every((s) => s.disabled)).toBe(true);
    expect(text()).not.toContain("真源在电脑");
  });

  it("联机状态连续读不到（重试两次后）⇒ 回落到可编辑，不留永久锁死", async () => {
    vi.useFakeTimers();
    try {
      await mount({}, { remote: "fail" });
      expect(button("新增机台")!.disabled).toBe(true);  // 重试期间仍是只读
      await act(async () => { await vi.advanceTimersByTimeAsync(1300); });
      await act(async () => { await vi.advanceTimersByTimeAsync(1300); });
      expect(button("新增机台")!.disabled).toBe(false);
      expect(selects().some((s) => s.disabled)).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });
});
