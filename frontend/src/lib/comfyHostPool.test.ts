import { beforeEach, describe, expect, it, vi } from "vitest";

const { probe } = vi.hoisted(() => ({ probe: vi.fn() }));
vi.mock("../api/comfyui", () => ({
  probeComfyHosts: (...args: unknown[]) => probe(...args),
}));

import { cachedHostLoads, pickComfyHost, refreshHostPool } from "./comfyHostPool";

const load = (url: string, score: number, online = true) => ({
  url, online, running: 0, pending: score, score, latency_ms: 1,
});

describe("comfyHostPool 多机分发", () => {
  beforeEach(() => {
    probe.mockReset();
    probe.mockResolvedValue({ items: [] });
  });

  it("探测后选最低 score 的在线机台", async () => {
    probe.mockResolvedValue({ items: [load("http://a", 4), load("http://b", 1)] });
    await refreshHostPool(["http://a", "http://b"]);
    expect(cachedHostLoads(["http://a", "http://b"])).toHaveLength(2);
    expect(pickComfyHost(["http://a", "http://b"])).toBe("http://b");
  });

  it("全部离线/未探测 ⇒ 回退池内第一台，不阻断提交", () => {
    expect(pickComfyHost(["http://fresh-a", "http://fresh-b"])).toBe("http://fresh-a");
  });

  it("全离线 ⇒ 也回退第一台", async () => {
    probe.mockResolvedValue({
      items: [load("http://off-a", 1, false), load("http://off-b", 1, false)],
    });
    await refreshHostPool(["http://off-a", "http://off-b"]);
    expect(pickComfyHost(["http://off-a", "http://off-b"])).toBe("http://off-a");
  });

  it("空池 ⇒ 空串", () => {
    expect(pickComfyHost([])).toBe("");
  });
});
