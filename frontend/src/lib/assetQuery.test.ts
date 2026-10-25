// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { parseAssetQuery, removeTagFromQuery } from "./assetQuery";

const gallery = readFileSync(new URL("../components/RepoGallery.tsx", import.meta.url), "utf8");
// 2026-10-24 api/ai 按资源域拆分：结构化搜索/回收站合同现居 ai/rag.ts。
const ragApi = readFileSync(new URL("../api/ai/rag.ts", import.meta.url), "utf8");

describe("tag: 语法解析（P1-3）", () => {
  it("tag: 词项进 tags，其余进自由文本；多标签去重保序", () => {
    expect(parseAssetQuery("tag:1girl tag:solo 森林")).toEqual({
      tags: ["1girl", "solo"], text: "森林",
    });
    expect(parseAssetQuery("tag:1girl,tag:1girl")).toEqual({ tags: ["1girl"], text: "" });
    // 多词标签用引号（danbooru 标签带空格，裸 token 会被空格拆开）
    expect(parseAssetQuery('TAG:"BanG Dream"')).toEqual({ tags: ["BanG Dream"], text: "" });
  });

  it("无 tag: 前缀＝旧口径自由文本（仓库名/提示词搜索不丢）", () => {
    expect(parseAssetQuery("1girl，solo")).toEqual({ tags: [], text: "1girl solo" });
    expect(parseAssetQuery("  ")).toEqual({ tags: [], text: "" });
  });

  it("chips × 只删对应 tag 词项，其余原样", () => {
    expect(removeTagFromQuery("tag:1girl，tag:solo，森林", "1girl")).toBe("tag:solo，森林");
    expect(removeTagFromQuery("tag:1girl", "1girl")).toBe("");
    expect(removeTagFromQuery("森林，tag:1girl", "1girl")).toBe("森林");
  });
});

describe("P1 前端接线契约", () => {
  it("结构化搜索走 /search-assets（FTS5），不与语义搜索混用", () => {
    expect(ragApi).toContain('"/rag/search-assets"');
    expect(ragApi).toContain("export function searchAssets");
    expect(gallery).toContain("searchAssets(repoIds, embed");
    expect(gallery).toContain("parseAssetQuery");
  });

  it("回收站接四个端点（列表/恢复/彻底删除/清空）", () => {
    expect(ragApi).toContain('"/rag/assets/trash"');
    expect(ragApi).toContain('"/rag/assets/restore"');
    expect(ragApi).toContain('"/rag/assets/purge"');
    expect(ragApi).toContain('"/rag/assets/trash-clear"');
  });

  it("回收站视图：恢复/彻底删除按钮 + 清空 + 退出", () => {
    expect(gallery).toContain("restoreAsset(");
    expect(gallery).toContain("purgeAsset(");
    expect(gallery).toContain("clearTrash(");
    expect(gallery).toContain("回收站");
  });
});
