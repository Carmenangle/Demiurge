import { describe, it, expect } from "vitest";
import { NODE_VERIFY_GAPS_MS, isSoloNodeGraph, isNodeSizeFor } from "./workflowNodeGuard";

const graph = (nodes: unknown[]) => ({ workflow: { nodes } });

describe("isSoloNodeGraph", () => {
  it("单节点且 id 匹配 → 通过", () => {
    expect(isSoloNodeGraph(graph([{ id: 20, type: "EmptyLatentImage" }]), "20")).toBe(true);
    // UI 格式 id 是数字、exposedIds 是字符串，必须能跨类型比对
    expect(isSoloNodeGraph(graph([{ id: 20 }]), 20 as unknown as string)).toBe(true);
  });

  it("单节点但 id 是别的节点 → 判失败（节点3画布里画了节点2）", () => {
    expect(isSoloNodeGraph(graph([{ id: 18, type: "KSampler" }]), "20")).toBe(false);
  });

  it("节点数不为 1 → 判失败（keepOnly 未生效/整图残留）", () => {
    expect(isSoloNodeGraph(graph([]), "20")).toBe(false);
    expect(isSoloNodeGraph(graph([{ id: 20 }, { id: 18 }]), "20")).toBe(false);
  });

  it("载荷缺失/结构异常 → 判失败，不误放行", () => {
    expect(isSoloNodeGraph(null, "20")).toBe(false);
    expect(isSoloNodeGraph(undefined, "20")).toBe(false);
    expect(isSoloNodeGraph({}, "20")).toBe(false);
    expect(isSoloNodeGraph({ workflow: {} }, "20")).toBe(false);
  });
});

describe("isNodeSizeFor", () => {
  it("id 匹配 → 采信该尺寸", () => {
    expect(isNodeSizeFor({ id: 20, w: 270, h: 136 }, "20")).toBe(true);
  });
  it("id 不匹配 → 丢弃，避免用别的节点尺寸改本卡比例", () => {
    expect(isNodeSizeFor({ id: 18, w: 270, h: 136 }, "20")).toBe(false);
  });
  it("老扩展不回传 id → 放行（向后兼容）", () => {
    expect(isNodeSizeFor({ w: 270, h: 136 }, "20")).toBe(true);
    expect(isNodeSizeFor(null, "20")).toBe(true);
  });
});

describe("NODE_VERIFY_GAPS_MS", () => {
  it("首检 2.2s 且间隔递增，覆盖迟到的会话恢复", () => {
    expect(NODE_VERIFY_GAPS_MS[0]).toBe(2200);
    for (let i = 1; i < NODE_VERIFY_GAPS_MS.length; i++) {
      expect(NODE_VERIFY_GAPS_MS[i]).toBeGreaterThanOrEqual(NODE_VERIFY_GAPS_MS[i - 1]);
    }
    const total = NODE_VERIFY_GAPS_MS.reduce((a, b) => a + b, 0);
    expect(total).toBeGreaterThan(30000); // 至少覆盖 30s 的迟到恢复窗口
  });
});
