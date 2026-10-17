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

describe("P0-1 资产列表 keyset 分页（RepoGallery 契约）", () => {
  it("消费后端 has_more，翻页游标用列表末条 created_at（beforeTs）", () => {
    expect(gallery).toContain("has_more === true");
    expect(gallery).toContain("beforeTs: cursor");
    expect(gallery).toContain("loadMore");
  });

  it("分页只在资产库单仓库且未升级全量时启用；搜索/筛选触发一次全量（sticky）", () => {
    expect(gallery).toContain("const needFull = !enhanced || repoIds.length !== 1 || fullyLoaded;");
    expect(gallery).toContain("const filtering = !!tagQuery.trim() || semanticMode || mediaFilter !== \"all\";");
  });

  it("切仓库重置回分页浏览态（不沿用上一个仓库的 fullyLoaded）", () => {
    expect(gallery).toContain("prevKeyRef.current !== key");
    expect(gallery).toContain("cancel = refresh(1, false);");
  });

  it("自动描述在分页态先升级全量再执行（不只看已加载页）", () => {
    expect(gallery).toContain("setPendingDescribe(true)");
    expect(gallery).toContain("if (!pendingDescribe || !fullyLoaded || loading) return;");
  });
});

describe("P0-2 缩略图派生层（RepoGallery 契约）", () => {
  it("网格优先缩略图，无 thumbUrl 回落原图", () => {
    expect(gallery).toContain("src={g.thumbUrl || g.image_url}");
  });

  it("详情弹窗仍取原图（thumbUrl 只用于网格）", () => {
    expect(gallery).toContain("<img src={active.image_url}");
  });

  it("工具栏提供补缩略图入口（新图入库时已自动生成，此为历史补生成）", () => {
    expect(gallery).toContain("buildThumbs");
    expect(gallery).toContain("补缩略图");
  });
});

describe("P0-3 WD14 反推打标（RepoGallery 契约）", () => {
  it("标签编辑条提供 WD14 反推入口（与 AI 打标并列）", () => {
    expect(gallery).toContain("onWd14");
    expect(gallery).toContain("wd14TagActive");
    expect(gallery).toContain("WD14 反推");
  });

  it("反推打标走「快照」而非合并（2026-10-17 定案）：tags＝本次快照结果，另拉快照列表", () => {
    expect(gallery).toContain("await tagGeneration(taggerModel, g.id, repoId, embed)");
    expect(gallery).toContain("tags: result.tags");                       // 不合并旧标签
    expect(gallery).toContain("listTagSnapshots(id, repoId, embed)");    // 打标后刷新版本列表
    expect(gallery).toContain("activateSnapshot");                        // 版本可切换
  });
});
