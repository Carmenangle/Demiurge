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
});
