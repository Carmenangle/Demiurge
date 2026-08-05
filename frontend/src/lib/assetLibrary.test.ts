// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const gallery = readFileSync(new URL("../components/RepoGallery.tsx", import.meta.url), "utf8");
const assetsView = readFileSync(new URL("../views/PlaceholderViews.tsx", import.meta.url), "utf8");

describe("资产库删除与恢复合同", () => {
  it("删除资产记录时保留本机图片", () => {
    expect(gallery).toContain("deleteDoc(g.id, rid, embed, false)");
    expect(gallery).toContain("本机图片文件会保留");
  });

  it("资产页不得扫描本机目录自动恢复已删除记录", () => {
    expect(assetsView).not.toContain("syncExistingGenerations");
    expect(assetsView).not.toContain("sync-generations");
  });
});
