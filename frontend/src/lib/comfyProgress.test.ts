// @vitest-environment jsdom
// 进度 WS 地址构造：PC 构建直连 ComfyUI /ws（行为逐字不变）；手机代理构建
// （VITE_COMFY_PROGRESS_PROXY=1）改打同源后端 /api/comfyui/ws 并携带目标 url。
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

const loadWsUrl = async () => {
  const mod = await import("./comfyProgress");
  return mod.wsUrl;
};

describe("wsUrl", () => {
  it("PC 构建（开关未设）直连 ComfyUI /ws，行为逐字不变", async () => {
    const wsUrl = await loadWsUrl();
    expect(wsUrl("http://127.0.0.1:8188", "cid")).toBe("ws://127.0.0.1:8188/ws?clientId=cid");
    expect(wsUrl("http://127.0.0.1:8188/", "cid")).toBe("ws://127.0.0.1:8188/ws?clientId=cid");
    expect(wsUrl("127.0.0.1:8188", "c 1")).toBe(
      "ws://127.0.0.1:8188/ws?clientId=" + encodeURIComponent("c 1"),
    );
  });

  it("代理构建走同源后端端点并携带目标 url（由后端按白名单校验）", async () => {
    vi.stubEnv("VITE_COMFY_PROGRESS_PROXY", "1");
    const wsUrl = await loadWsUrl();
    const url = wsUrl("http://192.168.1.9:8188", "c 1");
    const self = location.origin.replace(/^http/i, "ws");
    expect(url).toBe(
      `${self}/api/comfyui/ws?clientId=${encodeURIComponent("c 1")}`
      + `&url=${encodeURIComponent("http://192.168.1.9:8188")}`,
    );
  });
});
