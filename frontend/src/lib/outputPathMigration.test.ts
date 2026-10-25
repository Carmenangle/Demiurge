import { describe, expect, it } from "vitest";
import { relocateLocalViewUrl } from "./outputPathMigration";

describe("relocateLocalViewUrl", () => {
  it("rewrites a local asset below the old output root", () => {
    const original = "http://127.0.0.1:8010/api/comfyui/local-view?path="
      + encodeURIComponent("D:\\old\\repo\\a.png");
    const moved = relocateLocalViewUrl(original, "D:\\old", "E:\\new");

    expect(new URL(moved!).searchParams.get("path")).toBe("E:\\new\\repo\\a.png");
  });

  it("does not rewrite unrelated files", () => {
    const original = "http://127.0.0.1:8010/api/comfyui/local-view?path="
      + encodeURIComponent("D:\\other\\a.png");

    expect(relocateLocalViewUrl(original, "D:\\old", "E:\\new")).toBe(original);
  });

  it("rewrites root-relative wire form and keeps it relative", () => {
    // 后端自 2026-10-24 起产出根相对地址；`new URL` 无 base 会抛，静默不迁移＝改完输出目录封面仍指旧盘
    const original = "/api/comfyui/local-view?path="
      + encodeURIComponent("D:\\old\\repo\\a.png");
    const moved = relocateLocalViewUrl(original, "D:\\old", "E:\\new");

    expect(moved).toBe("/api/comfyui/local-view?path="
      + encodeURIComponent("E:\\new\\repo\\a.png"));
  });

  it("leaves protocol-relative and unparseable input untouched", () => {
    // `//host/...` 不是自家根相对，不能被改写成同主机地址（SSRF 面）；解析不了的原样返回
    const protocolRelative = "//other-host/api/comfyui/local-view?path="
      + encodeURIComponent("D:\\old\\a.png");
    expect(relocateLocalViewUrl(protocolRelative, "D:\\old", "E:\\new")).toBe(protocolRelative);
  });
});
