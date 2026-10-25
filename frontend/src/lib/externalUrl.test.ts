import { describe, expect, it } from "vitest";
import { safeExternalHref } from "./externalUrl";

describe("safeExternalHref", () => {
  it("http(s) 原样放行", () => {
    expect(safeExternalHref("https://www.pixiv.net/artworks/1")).toBe("https://www.pixiv.net/artworks/1");
    expect(safeExternalHref("http://example.com/a?b=1&c=2")).toBe("http://example.com/a?b=1&c=2");
  });

  it("scheme 大小写不敏感", () => {
    expect(safeExternalHref("HTTPS://example.com")).toBe("HTTPS://example.com");
  });

  it("危险 scheme 一律降级为 #", () => {
    expect(safeExternalHref("javascript:alert(1)")).toBe("#");
    expect(safeExternalHref("JaVaScRiPt:alert(1)")).toBe("#");
    expect(safeExternalHref("data:text/html,<script>alert(1)</script>")).toBe("#");
    expect(safeExternalHref("vbscript:msgbox")).toBe("#");
  });

  it("空值与非 http(s) 文本降级为 #", () => {
    expect(safeExternalHref(null)).toBe("#");
    expect(safeExternalHref(undefined)).toBe("#");
    expect(safeExternalHref("")).toBe("#");
    expect(safeExternalHref("  ")).toBe("#");
    expect(safeExternalHref("example.com/page")).toBe("#");
  });

  it("前导空白后仍是合法 http(s) 的放行", () => {
    expect(safeExternalHref("  https://example.com")).toBe("https://example.com");
  });
});
