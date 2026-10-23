import { describe, expect, it } from "vitest";
import { effectiveGlobalProxyUrl, globalProxyAddress, resolveModelProxy, normalizeProxyMode } from "./modelProxy";

describe("模型三级代理", () => {
  it("缺省按「继承全局」处理，跟随探测结果", () => {
    // 2026-10-20 用户定案（方案 C）：未设置 = 跟随探测，不再是「强制走代理」。
    // 旧默认 "on" 会让从没设过 proxyMode 的模型无视 proxyEnabled 强制走代理，
    // 与「能检测当前代理环境自由切换」的产品语义相反。
    expect(normalizeProxyMode(undefined)).toBe("inherit");
    expect(normalizeProxyMode(null)).toBe("inherit");
    // 探测判定不可用 ⇒ 直连；可用 ⇒ 用全局地址
    expect(resolveModelProxy(undefined, "http://127.0.0.1:7897", false)).toBe("");
    expect(resolveModelProxy(undefined, "http://127.0.0.1:7897", true)).toBe("http://127.0.0.1:7897");
  });

  it("显式「使用代理」是用户强制，探测不可用也照走", () => {
    expect(resolveModelProxy("on", "http://127.0.0.1:7897", false)).toBe("http://127.0.0.1:7897");
  });

  it("直连和继承全局语义互不混淆", () => {
    expect(resolveModelProxy("off", "http://proxy", true)).toBe("");
    expect(resolveModelProxy("inherit", "http://proxy", false)).toBe("");
    expect(resolveModelProxy("inherit", "http://proxy", true)).toBe("http://proxy");
  });
});

describe("生效全局代理取用口（2026-09-10 继承全局升级）", () => {
  it("探测判定不可用时一律直连，忽略任何地址", () => {
    expect(effectiveGlobalProxyUrl({
      proxyEnabled: false, proxyUrl: "http://127.0.0.1:7897", systemProxyUrl: "http://127.0.0.1:7890",
    })).toBe("");
  });

  it("系统代理可用时优先于手填地址", () => {
    expect(effectiveGlobalProxyUrl({
      proxyEnabled: true, proxyUrl: "http://127.0.0.1:7897", systemProxyUrl: "http://127.0.0.1:7890",
    })).toBe("http://127.0.0.1:7890");
  });

  it("没有系统代理时回退手填地址", () => {
    expect(effectiveGlobalProxyUrl({
      proxyEnabled: true, proxyUrl: "http://127.0.0.1:7897", systemProxyUrl: "",
    })).toBe("http://127.0.0.1:7897");
  });

  it("两者都空时返回空串（直连）", () => {
    expect(effectiveGlobalProxyUrl({ proxyEnabled: true })).toBe("");
  });

  it("地址选择：系统代理优先，其次手填兜底（trim）", () => {
    expect(globalProxyAddress({ proxyUrl: "http://127.0.0.1:7897" })).toBe("http://127.0.0.1:7897");
    expect(globalProxyAddress({
      proxyUrl: "http://127.0.0.1:7897", systemProxyUrl: "http://127.0.0.1:7890",
    })).toBe("http://127.0.0.1:7890");
    expect(globalProxyAddress({ proxyUrl: "  http://127.0.0.1:7897  " })).toBe("http://127.0.0.1:7897");
  });

  it("模型级 off 仍然直连、inherit 受探测约束", () => {
    const settings = { proxyEnabled: true, proxyUrl: "", systemProxyUrl: "http://127.0.0.1:7890" };
    expect(resolveModelProxy("off", globalProxyAddress(settings), settings.proxyEnabled)).toBe("");
    expect(resolveModelProxy("inherit", globalProxyAddress(settings), settings.proxyEnabled))
      .toBe("http://127.0.0.1:7890");
    const unavailable = { proxyEnabled: false, proxyUrl: "http://127.0.0.1:7897" };
    expect(resolveModelProxy("inherit", globalProxyAddress(unavailable), unavailable.proxyEnabled)).toBe("");
  });

  it("模型级「使用代理」是显式强制，不受探测判定约束（既有语义，勿回退）", () => {
    // 回归：2026-09-10 升级时曾把模型级也改成受 proxyEnabled 约束 → 用户显式选「使用代理」
    // 会被探测结果静默降级成直连。地址选择（globalProxyAddress）与是否启用必须分离。
    const unavailable = { proxyEnabled: false, proxyUrl: "http://127.0.0.1:7897" };
    expect(resolveModelProxy("on", globalProxyAddress(unavailable), unavailable.proxyEnabled))
      .toBe("http://127.0.0.1:7897");
  });
});
