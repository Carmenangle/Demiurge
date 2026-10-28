// @vitest-environment jsdom
// 手机端联机检测弹窗的真挂载行为网（jsdom + React 19 act + 真定时器驱动轮询）。
// 用户口径逐条钉住：填了电脑地址并开始联机才检测 ⇒ 连着不出现 ⇒ 中断就跳出 ⇒
// 「知道了」只压同一次故障 ⇒ PC 构建既不轮询也不弹（桌面后端根本没有 /api/mobile/*）。
// ⚠ 不用 fake timer：`vi.useFakeTimers()` 会让 React 的 async act 与 fetch 微任务链一起停摆，
// 表现为「fetch 一次都没被调用」的假象红（本轮实测）。
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PC = "http://192.0.2.10:8010";
const COMFY = "http://192.0.2.9:8188";
const POLL_MS = 1;
const ROUND_MS = 15;

type Patch = Record<string, unknown>;

function onlineStatus(patch: Patch = {}): Patch {
  return {
    active: true,
    base_url: PC,
    reachable: true,
    latency_ms: 9,
    comfyui_url: COMFY,
    comfyui_running: true,
    detail: "",
    ...patch,
  };
}

function source(rel: string): string {
  return readFileSync(new URL(rel, import.meta.url), "utf8");
}

let container: HTMLDivElement | null = null;
let root: Root | null = null;
// 探针返回值与「本机后端是否问不通」由用例随时改，改完跑一轮 round() 就是下一轮探针结果。
let payload: Patch = onlineStatus();
let probeFails = false;

async function mount(profile: "0" | "1" = "1") {
  payload = onlineStatus();
  probeFails = false;
  vi.resetModules();
  vi.stubEnv("VITE_MOBILE_PROFILE", profile);
  vi.stubEnv("VITE_API_BASE", "/api");
  const spy = vi.fn(async (_url: string, _init?: unknown) => {
    if (probeFails) throw new Error("本机后端正在重启");
    return { ok: true, json: async () => payload };
  });
  vi.stubGlobal("fetch", spy);
  const { MobileOnlineWatch } = await import("./MobileOnlineWatch");
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MobileOnlineWatch pollMs={POLL_MS} />);
  });
  await round();
  return spy;
}

/** 放足够多个 tick 出去：轮询链自己按 pollMs 续跑，一轮后断言稳定态。 */
async function round() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, ROUND_MS)); });
}

function text(): string {
  return container?.textContent ?? "";
}

function dismissButton(): HTMLButtonElement | null {
  const buttons = Array.from(container?.querySelectorAll("button") ?? []);
  return buttons.find((button) => (button.textContent ?? "").includes("知道了")) ?? null;
}

afterEach(() => {
  act(() => { root?.unmount(); });
  root = null;
  container?.remove();
  container = null;
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("MobileOnlineWatch", () => {
  it("问本机体检端点；联机全绿时不渲染任何弹窗", async () => {
    payload = onlineStatus();
    const spy = await mount();
    expect(spy.mock.calls.length).toBeGreaterThan(0);
    expect(String(spy.mock.calls[0][0])).toContain("/mobile/online-status");
    expect(text()).toBe("");
  });

  it("电脑没应答 ⇒ 跳出「联机中断」弹窗，点名电脑地址", async () => {
    await mount();
    payload = onlineStatus({ reachable: false, latency_ms: null, detail: "连不上电脑：确认同一 Wi‑Fi" });
    await round();
    expect(text()).toContain("与电脑的联机中断了");
    expect(text()).toContain("连不上电脑");
    expect(text()).toContain(PC);
    expect(dismissButton()).not.toBeNull();
  });

  it("电脑在线但 ComfyUI 没按地址开着 ⇒ 跳出「ComfyUI 没开着」并带地址", async () => {
    await mount();
    payload = onlineStatus({ comfyui_running: false, detail: `地址 ${COMFY} 上没有响应中的 ComfyUI` });
    await round();
    expect(text()).toContain("ComfyUI 没开着");
    expect(text()).toContain(COMFY);
  });

  it("没启用联机（填了地址但没开始连接）⇒ 不弹", async () => {
    await mount();
    payload = onlineStatus({ active: false, reachable: false, comfyui_running: false });
    await round();
    expect(text()).toBe("");
  });

  it("点「知道了」只压同一次故障；恢复后再断才重新弹", async () => {
    await mount();
    payload = onlineStatus({ reachable: false, detail: "连不上电脑" });
    await round();
    expect(text()).toContain("与电脑的联机中断了");
    const button = dismissButton();
    if (!button) throw new Error("缺少「知道了」按钮");
    act(() => { button.click(); });
    expect(text()).toBe("");
    // 同一次故障的后续轮次：保持静默，不反复打扰
    await round();
    expect(text()).toBe("");
    // 恢复 → 再断：新的一次故障，重新提醒
    payload = onlineStatus();
    await round();
    expect(text()).toBe("");
    payload = onlineStatus({ reachable: false, detail: "连不上电脑" });
    await round();
    expect(text()).toContain("与电脑的联机中断了");
  });

  it("断点换了（电脑断了 → 只是 ComfyUI 断了）⇒ 提示换新原因", async () => {
    await mount();
    payload = onlineStatus({ reachable: false, detail: "连不上电脑" });
    await round();
    expect(text()).toContain("与电脑的联机中断了");
    payload = onlineStatus({ comfyui_running: false, detail: `地址 ${COMFY} 上没有响应中的 ComfyUI` });
    await round();
    expect(text()).toContain("ComfyUI 没开着");
  });

  it("本机探针问不通（手机后端在重启）⇒ 保持原状，不制造假警", async () => {
    await mount();
    probeFails = true;
    await round();
    expect(text()).toBe("");
  });

  it("PC 构建：不轮询也不弹（这组端点在桌面后端不存在）", async () => {
    payload = onlineStatus({ reachable: false, detail: "连不上电脑" });
    const spy = await mount("0");
    expect(spy.mock.calls.length).toBe(0);
    payload = onlineStatus({ reachable: false, detail: "连不上电脑" });
    await round();
    expect(text()).toBe("");
  });

  it("检测器挂在 App 顶层：跨页面常驻，漏挂就没有提示（源码守卫）", () => {
    expect(source("../App.tsx")).toContain("<MobileOnlineWatch");
  });
});
