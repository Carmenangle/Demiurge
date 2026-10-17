// waitForAgentRelease 行为测试（2026-10-14 A 方案）。
//
// 锁死三件事：① 释放即返回 true；② 超时仍占用返回 false（不永久卡死）；
// ③ 单次探测失败（网络抖动）不放弃、继续轮询。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { waitForAgentRelease } from "./agentReleaseWait";

describe("waitForAgentRelease", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("running 转 false 后返回 true", async () => {
    const states = [{ running: true }, { running: true }, { running: false }];
    let call = 0;
    const probe = vi.fn(async () => states[Math.min(call++, states.length - 1)]);

    const promise = waitForAgentRelease("repo-1", {
      fetchRunning: probe,
      intervalMs: 1_000,
      timeoutMs: 60_000,
    });
    await vi.advanceTimersByTimeAsync(2_000);
    await expect(promise).resolves.toBe(true);
    expect(probe).toHaveBeenCalledTimes(3);
  });

  it("一直 running 则超时返回 false（不永久卡死）", async () => {
    const probe = vi.fn(async () => ({ running: true }));

    const promise = waitForAgentRelease("repo-1", {
      fetchRunning: probe,
      intervalMs: 1_000,
      timeoutMs: 5_000,
    });
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(promise).resolves.toBe(false);
  });

  it("探测失败不放弃，恢复后仍能等到释放", async () => {
    const states = [
      { running: true },
      { __error: true },
      { running: false },
    ];
    let call = 0;
    const probe = vi.fn(async () => {
      const s = states[Math.min(call++, states.length - 1)];
      if ("__error" in s) throw new Error("network down");
      return s as { running: boolean };
    });

    const promise = waitForAgentRelease("repo-1", {
      fetchRunning: probe,
      intervalMs: 1_000,
      timeoutMs: 60_000,
    });
    await vi.advanceTimersByTimeAsync(2_000);
    await expect(promise).resolves.toBe(true);
    expect(probe).toHaveBeenCalledTimes(3);
  });

  it("onPoll 每次未释放的轮询都回调", async () => {
    const probe = vi.fn(async () => ({ running: false }));
    const onPoll = vi.fn();

    await waitForAgentRelease("repo-1", { fetchRunning: probe, onPoll });

    // 首次即释放：onPoll 不应被调用（没有发生「未释放的轮询」）。
    expect(onPoll).not.toHaveBeenCalled();
  });
});
