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
  base_url: "http://192.0.2.10:8010",
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
    await mod.saveMobileRemote("http://192.0.2.10:8010", "tok-123456", true);
    await mod.testMobileRemote();

    const urls = spy.mock.calls.map(([url]) => String(url));
    expect(urls).toEqual([
      "/api/mobile/remote",
      "/api/mobile/remote",
      "/api/mobile/remote/test",
    ]);
    expect(spy.mock.calls[0][1]?.method ?? "GET").toBe("GET");
    expect(JSON.parse(spy.mock.calls[1][1].body)).toEqual({
      base_url: "http://192.0.2.10:8010",
      token: "tok-123456",
      enabled: true,
    });
    expect(JSON.parse(spy.mock.calls[2][1].body)).toEqual({});
  });

  it("联机体检走本机 /mobile/online-status，并且带超时（轮询断链就等于永不提醒）", async () => {
    vi.stubEnv("VITE_API_BASE", "/api");
    const spy = stubFetch({
      active: true, base_url: "http://192.0.2.10:8010", reachable: true, latency_ms: 9,
      comfyui_url: "http://192.0.2.9:8188", comfyui_running: true, detail: "",
    });
    const mod = await import("./mobileRemote");

    const status = await mod.getMobileOnlineStatus();

    expect(String(spy.mock.calls[0][0])).toBe("/api/mobile/online-status");
    expect(spy.mock.calls[0][1]?.method ?? "GET").toBe("GET");
    // 超时用 AbortController 实现：signal 缺席就意味着没带超时
    expect(spy.mock.calls[0][1]?.signal).toBeTruthy();
    expect(status.comfyui_running).toBe(true);
  });

  it("在线同步：状态查询与开始都走 /mobile/sync，开始体只带 overwrite", async () => {
    vi.stubEnv("VITE_API_BASE", "/api");
    const spy = stubFetch({ state: "idle" });
    const mod = await import("./mobileRemote");

    await mod.getOnlineSyncStatus();
    await mod.startOnlineSync(true);

    const urls = spy.mock.calls.map(([url]) => String(url));
    expect(urls).toEqual(["/api/mobile/sync", "/api/mobile/sync"]);
    expect(spy.mock.calls[0][1]?.method ?? "GET").toBe("GET");
    expect(spy.mock.calls[1][1].method).toBe("POST");
    // 源地址/令牌只取本机配置：前端不得把目标地址写进请求体
    expect(JSON.parse(spy.mock.calls[1][1].body)).toEqual({ overwrite: true });
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
    // 问题3（2026-10-27）：占位符不得写死某个局域网 IP（会被当成「泄露我的电脑 IP」）
    expect(html).not.toMatch(/192\.168\./);
    expect(html).toContain("电脑IP");
    // 联机检测弹窗（2026-10-27 问题 1）要在本页说明「何时会弹、正常不打扰」，否则弹窗像凭空出现
    expect(html).toContain("会自动跳出提示");
    expect(html).toContain("连接正常时不打扰");
  });

  it("PC 构建不渲染联机同库区（桌面后端没有这组端点）", async () => {
    vi.stubEnv("VITE_MOBILE_PROFILE", "0");
    const { DeviceSyncPanel } = await import("../views/settings/DeviceSyncPanel");
    const html = renderToStaticMarkup(<DeviceSyncPanel />);
    expect(html).not.toContain("联机同库");
    expect(html).toContain("离线资源同步");
  });
});

describe("DeviceSyncPanel · 在线同步区", () => {
  it("OnlineSyncSection 渲染同步按钮与「不含密钥」说明", async () => {
    const { OnlineSyncSection } = await import("../views/settings/DeviceSyncPanel");
    const html = renderToStaticMarkup(<OnlineSyncSection />);
    expect(html).toContain("在线同步到本机");
    expect(html).toContain("从电脑同步到本机");
    expect(html).toContain("不含 API 密钥");
  });

  it("联机中提示指向在线同步；未联机时面板本体不出现（先连电脑再拉包）", async () => {
    vi.stubEnv("VITE_MOBILE_PROFILE", "1");
    const { DeviceSyncPanel } = await import("../views/settings/DeviceSyncPanel");
    const html = renderToStaticMarkup(<DeviceSyncPanel />);
    expect(html).toContain("在线同步到本机");
    expect(html).toContain("联机中隐藏");
    // SSR 下未联机（remoteActive=false）不得出现同步按钮
    expect(html).not.toContain("从电脑同步到本机");
  });
});
