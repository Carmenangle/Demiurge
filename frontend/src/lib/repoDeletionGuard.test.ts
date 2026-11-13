// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

import { folderRemovalFailures, repoDeletionBlockReason, type DeletionProbes } from "./repoDeletionGuard";

// 删仓库的三道闸（2026-11-05 防误删 ③）。核心不是「查到就拦」——那本来就有——
// 而是**查不到也拦**：改前两处 `catch {}` 一个继续往下查资产、一个直接按「无资产」放行，
// 于是「后端抖一下」就能删掉一个正在生成/已有资产的作品。这两条是本次的唯一新增行为。

const IDS = ["仓库甲", "作品乙"];

function probes(over: Partial<DeletionProbes> = {}): DeletionProbes {
  return {
    hasLocalPending: () => false,
    hasServerPending: async () => false,
    isAgentRunning: async () => false,
    hasAssets: async () => false,
    ...over,
  };
}

describe("repoDeletionBlockReason", () => {
  it("三道闸全绿 ⇒ 空串（可以删）", async () => {
    expect(await repoDeletionBlockReason("仓库甲", IDS, probes())).toBe("");
  });

  it("本地已有未归档生图任务 ⇒ 阻断，且不再多跑一次服务端查询", async () => {
    const hasServerPending = vi.fn(async () => true);
    const reason = await repoDeletionBlockReason("仓库甲", IDS, probes({
      hasLocalPending: () => true,
      hasServerPending,
    }));
    expect(reason).toContain("还有生图任务未完成");
    expect(hasServerPending).not.toHaveBeenCalled();   // || 短路：本机已经挡住了
  });

  it("任一子会话的剧情任务在跑 ⇒ 阻断", async () => {
    const reason = await repoDeletionBlockReason("仓库甲", IDS, probes({
      isAgentRunning: async (id) => id === "作品乙",
    }));
    expect(reason).toContain("还有剧情或插画任务在运行");
  });

  it("任务状态查不到 ⇒ 阻断（改前是静默继续，这条是本次缺陷的本体）", async () => {
    const reason = await repoDeletionBlockReason("仓库甲", IDS, probes({
      isAgentRunning: async () => { throw new Error("后端没应答"); },
    }));
    expect(reason).toContain("任务状态查不到，已阻止删除");
    expect(reason).toContain("后端没应答");
  });

  it("资产清单查不到 ⇒ 阻断（改前「按无资产处理」＝直接删）", async () => {
    const reason = await repoDeletionBlockReason("仓库甲", IDS, probes({
      hasAssets: async () => { throw new Error("资产库不可用"); },
    }));
    expect(reason).toContain("资产清单查不到，已阻止删除");
    expect(reason).toContain("资产库不可用");
  });

  it("资产库里已有生成图 ⇒ 阻断并指路先去资产库删图", async () => {
    const reason = await repoDeletionBlockReason("仓库甲", IDS, probes({
      hasAssets: async (id) => id === "仓库甲",
    }));
    expect(reason).toContain("里还有生成图（资产）");
    expect(reason).toContain("请先在资产库删除这些图片");
  });
});

describe("folderRemovalFailures", () => {
  it("error 为空串＝文件夹本来就不存在，不算失败", () => {
    expect(folderRemovalFailures([
      { name: "仓库甲", error: "" },
      { name: "作品乙", error: "" },
    ])).toEqual([]);
  });

  it("删一半（文件被占用）必须带着文件夹名报出来", () => {
    expect(folderRemovalFailures([
      { name: "仓库甲", error: "" },
      { name: "作品乙", error: "删除作品文件夹失败（可能有文件被占用）：Access is denied" },
    ])).toEqual(["作品乙：删除作品文件夹失败（可能有文件被占用）：Access is denied"]);
  });
});

it("App.tsx 仍走这两道接缝（防止有人改回 onConfirm 里内联 catch 吞掉）", () => {
  const src = readFileSync(new URL("../App.tsx", import.meta.url), "utf8");
  expect(src).toContain("repoDeletionBlockReason(");
  expect(src).toContain("folderRemovalFailures(");
  expect(src).not.toContain("查询失败按无资产处理");
});
