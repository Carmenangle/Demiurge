import { describe, expect, it } from "vitest";
import { resolveModelProxy, normalizeProxyMode } from "./modelProxy";

describe("模型三级代理", () => {
  it("缺省按使用代理处理", () => {
    expect(normalizeProxyMode(undefined)).toBe("on");
    expect(resolveModelProxy(undefined, "http://127.0.0.1:7897", false)).toBe("http://127.0.0.1:7897");
  });

  it("直连和继承全局语义互不混淆", () => {
    expect(resolveModelProxy("off", "http://proxy", true)).toBe("");
    expect(resolveModelProxy("inherit", "http://proxy", false)).toBe("");
    expect(resolveModelProxy("inherit", "http://proxy", true)).toBe("http://proxy");
  });
});
