// @vitest-environment jsdom
// 联机手机模型页（2026-10-30 用户定案）：提示手机模型独立于电脑；回环地址由执行端
// （电脑）解释，模型跑在手机上时一键换成手机局域网地址。
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { Settings } from "../../stores/settings";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | null = null;
let root: Root | null = null;
let current: { chatModels: Array<{ baseUrl: string }> } | null = null;

function json(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

async function mount(opts: { active?: boolean; lanIp?: string } = {}) {
  vi.resetModules();
  vi.stubEnv("VITE_MOBILE_PROFILE", "1");
  vi.stubEnv("VITE_API_BASE", "/api");
  const active = opts.active ?? true;
  const lanIp = opts.lanIp ?? "192.168.1.7";
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    if (u.includes("/mobile/remote")) {
      return json(200, {
        supported: true, enabled: active, active,
        base_url: "http://192.0.2.10:8012", token_set: true,
      });
    }
    if (u.includes("/mobile/local-models")) return json(200, { models: {}, lan_ip: lanIp });
    return json(404, {});
  }));

  const { importSettings } = await import("../../stores/settings");
  const { ModelsPanel } = await import("./ModelsPanel");
  const initial = importSettings(JSON.stringify({
    chatModels: [{
      id: "c1", displayName: "手机上的服务", apiKey: "", modelName: "m",
      baseUrl: "http://127.0.0.1:11434/v1", source: "api", proxyMode: "off",
    }],
  }));
  function Harness() {
    const [draft, setDraft] = useState<Settings>(initial);
    current = draft as never;
    return <ModelsPanel draft={draft} setDraft={setDraft} />;
  }
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root!.render(<Harness />); });
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
}

function text() {
  return container?.textContent ?? "";
}

function button(label: string) {
  return Array.from(container?.querySelectorAll("button") ?? [])
    .find((b) => (b.textContent ?? "").trim() === label) as HTMLButtonElement | undefined;
}

afterEach(() => {
  act(() => { root?.unmount(); });
  root = null;
  container?.remove();
  container = null;
  current = null;
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("ModelsPanel · 联机手机", () => {
  it("显示「只存手机」提示，并给回环地址一键换 LAN 地址", async () => {
    await mount();

    expect(text()).toContain("联机中：这里的模型设置只存手机");
    const fix = button("用这个地址");
    expect(fix).toBeTruthy();
    expect(text()).toContain("http://192.168.1.7:11434/v1");

    act(() => { fix!.click(); });
    expect(current!.chatModels[0].baseUrl).toBe("http://192.168.1.7:11434/v1");
  });

  it("没联机时不显示提示与替换按钮", async () => {
    await mount({ active: false });
    expect(text()).not.toContain("联机中：这里的模型设置只存手机");
    expect(button("用这个地址")).toBeUndefined();
  });

  it("取不到手机局域网地址时只警示、不提供替换", async () => {
    await mount({ lanIp: "" });
    expect(text()).toContain("联机中：这里的模型设置只存手机");
    expect(button("用这个地址")).toBeUndefined();
  });
});
