import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  comfyStatus: vi.fn(),
  startComfy: vi.fn(),
  probeComfyHosts: vi.fn(),
}));

vi.mock("../api/comfyui", () => mocks);

import {
  configureComfyAutoStart, ensureComfyReady, getComfyWaitState,
  resetComfyReadinessForTests, subscribeComfyWait, waitForComfyIdle,
} from "./comfyReadiness";

describe("ComfyUI 就绪等待队列", () => {
  beforeEach(() => {
    resetComfyReadinessForTests();
    mocks.comfyStatus.mockReset();
    mocks.startComfy.mockReset();
    mocks.probeComfyHosts.mockReset();
  });

  it("未启动时自动拉起；并发任务共享一次启动，就绪后一起继续", async () => {
    configureComfyAutoStart("D:/ComfyUI", "");
    let probes = 0;
    mocks.comfyStatus.mockImplementation(async () => ({ running: ++probes >= 3 }));
    mocks.startComfy.mockResolvedValue({ running: true });
    const seen: string[] = [];
    subscribeComfyWait((state) => seen.push(state.message));

    const first = ensureComfyReady("http://127.0.0.1:8188", { timeoutMs: 3000, pollMs: 40 });
    const second = ensureComfyReady("http://127.0.0.1:8188", { timeoutMs: 3000, pollMs: 40 });

    await expect(Promise.all([first, second])).resolves.toEqual([true, true]);
    expect(mocks.startComfy).toHaveBeenCalledTimes(1);
    expect(probes).toBeGreaterThanOrEqual(3);
    expect(seen.some((message) => message.includes("正在启动"))).toBe(true);
    expect(getComfyWaitState().waiting).toBe(false);
  });

  it("没有自动启动路径时只排队等待；超时返回 false 且不清空状态说明", async () => {
    mocks.comfyStatus.mockResolvedValue({ running: false });

    const ok = await ensureComfyReady("http://127.0.0.1:8188", {
      timeoutMs: 120, pollMs: 20,
    });

    expect(ok).toBe(false);
    expect(mocks.startComfy).not.toHaveBeenCalled();
    expect(getComfyWaitState().message).toContain("启动超时");
  });

  it("队列忙时继续等，running/pending 归零后才算 GPU 空闲", async () => {
    let probes = 0;
    mocks.probeComfyHosts.mockImplementation(async () => ({
      items: [{
        url: "http://127.0.0.1:8188", online: true,
        running: ++probes < 3 ? 1 : 0, pending: 0, score: 1, latency_ms: 1,
      }],
    }));
    const seen: number[] = [];

    const ok = await waitForComfyIdle("http://127.0.0.1:8188", {
      timeoutMs: 3000, pollMs: 20,
      onWait: (state) => seen.push(state.running),
    });

    expect(ok).toBe(true);
    expect(probes).toBeGreaterThanOrEqual(3);
    expect(seen).toContain(1);
  });

  it("GPU 一直忙或 ComfyUI 不可达时超时返回 false，不误判空闲", async () => {
    mocks.probeComfyHosts.mockRejectedValue(new Error("Connection error"));

    const ok = await waitForComfyIdle("http://127.0.0.1:8188", {
      timeoutMs: 120, pollMs: 20,
    });

    expect(ok).toBe(false);
  });

  it("用户取消拍摄时立即停止等 GPU 空闲", async () => {
    mocks.probeComfyHosts.mockResolvedValue({
      items: [{
        url: "http://127.0.0.1:8188", online: true,
        running: 1, pending: 0, score: 2, latency_ms: 1,
      }],
    });
    let checks = 0;

    const ok = await waitForComfyIdle("http://127.0.0.1:8188", {
      timeoutMs: 3000, pollMs: 20,
      shouldStop: () => { checks += 1; return checks > 1; },
    });

    expect(ok).toBe(false);
    expect(checks).toBeGreaterThanOrEqual(2);
  });
});
