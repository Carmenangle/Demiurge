// 手机端形态开关与 PC 主机登记（M7）：PC 构建零行为（fetch 不发），手机端独立构建
// （VITE_MOBILE_PROFILE=1）保存 ComfyUI 地址时 POST /api/mobile/comfy-host。
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

const load = async () => import("./mobileProfile");

describe("syncMobileComfyHost", () => {
  it("PC 构建（开关未设）不发任何请求", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const mod = await load();
    mod.syncMobileComfyHost("http://192.168.1.9:8188");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("手机端构建 POST 登记端点并携带地址（同源 cookie 随请求）", async () => {
    vi.stubEnv("VITE_API_BASE", "/api");
    vi.stubEnv("VITE_MOBILE_PROFILE", "1");
    const fetchSpy = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchSpy);
    const mod = await load();
    mod.syncMobileComfyHost("http://192.168.1.9:8188");
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/mobile/comfy-host");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ url: "http://192.168.1.9:8188" });
  });

  it("登记失败静默（保存流程不中断）", async () => {
    vi.stubEnv("VITE_API_BASE", "/api");
    vi.stubEnv("VITE_MOBILE_PROFILE", "1");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("net down")));
    const mod = await load();
    expect(() => mod.syncMobileComfyHost("http://192.168.1.9:8188")).not.toThrow();
  });
});
