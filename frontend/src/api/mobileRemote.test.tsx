// 手机端联机同库：设置面板只在手机独立构建出现；客户端端点固定走本机 /api/mobile/remote。
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

const CONFIG = {
  supported: true,
  enabled: true,
  active: true,
  base_url: "http://192.168.1.6:8010",
  token_set: true,
};

function stubFetch(payload: unknown) {
  const spy = vi.fn().mockResolvedValue({ ok: true, json: async () => payload });
  vi.stubGlobal("fetch", spy);
  return spy;
}

describe("mobileRemote 客户端", () => {
  it("读取/保存/测试都走 /mobile/remote，保存体带地址与令牌", async () => {
    vi.stubEnv("VITE_API_BASE", "/api");
    const spy = stubFetch({ ...CONFIG, reachable: true, detail: "", latency_ms: 12, health: null });
    const mod = await import("./mobileRemote");

    await mod.loadMobileRemote();
    await mod.saveMobileRemote("http://192.168.1.6:8010", "tok-123456", true);
    await mod.testMobileRemote();

    const urls = spy.mock.calls.map(([url]) => String(url));
    expect(urls).toEqual([
      "/api/mobile/remote",
      "/api/mobile/remote",
      "/api/mobile/remote/test",
    ]);
    expect(spy.mock.calls[0][1]?.method ?? "GET").toBe("GET");
    expect(JSON.parse(spy.mock.calls[1][1].body)).toEqual({
      base_url: "http://192.168.1.6:8010",
      token: "tok-123456",
      enabled: true,
    });
    expect(JSON.parse(spy.mock.calls[2][1].body)).toEqual({});
  });
});

describe("DeviceSyncPanel · 联机同库区", () => {
  it("手机端构建渲染联机同库区与地址/令牌输入", async () => {
    vi.stubEnv("VITE_MOBILE_PROFILE", "1");
    const { DeviceSyncPanel } = await import("../views/settings/DeviceSyncPanel");
    const html = renderToStaticMarkup(<DeviceSyncPanel />);
    expect(html).toContain("联机同库");
    expect(html).toContain("电脑地址");
    expect(html).toContain("访问令牌");
    expect(html).toContain("start-lan.bat");
    expect(html).toContain("离线资源同步");
  });

  it("PC 构建不渲染联机同库区（桌面后端没有这组端点）", async () => {
    vi.stubEnv("VITE_MOBILE_PROFILE", "0");
    const { DeviceSyncPanel } = await import("../views/settings/DeviceSyncPanel");
    const html = renderToStaticMarkup(<DeviceSyncPanel />);
    expect(html).not.toContain("联机同库");
    expect(html).toContain("离线资源同步");
  });
});
