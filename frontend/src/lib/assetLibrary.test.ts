// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const gallery = readFileSync(new URL("../components/RepoGallery.tsx", import.meta.url), "utf8");
const assetsView = readFileSync(new URL("../views/PlaceholderViews.tsx", import.meta.url), "utf8");

describe("资产库删除与恢复合同", () => {
  it("删除资产记录时保留本机图片", () => {
    expect(gallery).toContain("deleteDoc(g.id, rid, embed, removeFile)");
    expect(gallery).toContain("本机图片文件会保留");
  });

  it("资产页不得扫描本机目录自动恢复已删除记录", () => {
    expect(assetsView).not.toContain("syncExistingGenerations");
    expect(assetsView).not.toContain("sync-generations");
  });

  it("视觉描述只更新资产索引，语义搜索走 generation 专用端点", () => {
    expect(gallery).toContain("setGenerationDescription");
    expect(gallery).toContain("searchGenerations");
    expect(gallery).toContain("indexVisualGenerations");
    expect(gallery).not.toContain("appendMessage");
  });

  it("发送至对话框（仅图片）与发送至对话（完整配方）两个按钮并存且走 chatAppend", () => {
    // 修复后的 AppBody 不再传空函数：AssetsView 接收两个动作
    expect(assetsView).toContain("onSendToChat");
    expect(assetsView).toContain("onSendAsRecipe");
    // RepoGallery 两个按钮：gallery-send（对话框）+ gallery-send-recipe（对话）
    expect(gallery).toContain("gallery-send-recipe");
    expect(gallery).toContain("发送至对话框");
    expect(gallery).toContain("发送至对话");
    // 发送动作由上层（AppBody）通过 chatAppend 落盘，RepoGallery 本身不直接调用
    expect(gallery).not.toContain("chatAppend");
  });

  it("发送至对话按钮仅在提供 onSendAsRecipe 时显示（向后兼容）", () => {
    expect(gallery).toContain("enhanced && onSendAsRecipe &&");
    expect(gallery).toContain("enhanced && onSendToChat &&");
  });
});
