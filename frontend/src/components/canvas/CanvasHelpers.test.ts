// canvas/CanvasHelpers.test.ts — 辅助线坐标变换 + parseDim 单元测试
import { describe, it, expect } from "vitest";

// 纯函数：从 component 里提取出来以便测试
function parseDim(v: unknown): number | undefined {
  if (typeof v === "number") return v;
  if (typeof v === "string") { const n = parseFloat(v); return isNaN(n) ? undefined : n; }
  return undefined;
}

/** 流坐标 → 屏幕坐标（模拟 ReactFlow viewport transform） */
function flowToScreen(flowX: number, flowY: number, vp: { x: number; y: number; scale: number }) {
  return { x: flowX * vp.scale + vp.x, y: flowY * vp.scale + vp.y };
}

/** 计算节点中心点 */
function nodeCenter(pos: { x: number; y: number }, w: number, h: number) {
  return { x: pos.x + w / 2, y: pos.y + h / 2 };
}

describe("parseDim", () => {
  it("解析数字", () => { expect(parseDim(240)).toBe(240); });
  it("解析字符串 px", () => { expect(parseDim("180px")).toBe(180); });
  it("解析字符串纯数字", () => { expect(parseDim("320")).toBe(320); });
  it("undefined → undefined", () => { expect(parseDim(undefined)).toBeUndefined(); });
  it("无效字符串 → undefined", () => { expect(parseDim("abc")).toBeUndefined(); });
});

describe("flowToScreen", () => {
  it("zoom=1, pan=0 → 不变", () => {
    const vp = { x: 0, y: 0, scale: 1 };
    expect(flowToScreen(500, 300, vp)).toEqual({ x: 500, y: 300 });
  });
  it("zoom=0.5, pan=100,50 → 缩放+平移", () => {
    const vp = { x: 100, y: 50, scale: 0.5 };
    expect(flowToScreen(500, 300, vp)).toEqual({ x: 350, y: 200 });
  });
  it("zoom=2, pan=-200 → 放大+平移", () => {
    const vp = { x: -200, y: 0, scale: 2 };
    expect(flowToScreen(100, 100, vp)).toEqual({ x: 0, y: 200 });
  });
});

describe("nodeCenter", () => {
  it("默认 CARD_W=240, h=100", () => {
    const c = nodeCenter({ x: 100, y: 200 }, 240, 100);
    expect(c).toEqual({ x: 220, y: 250 });
  });
  it("灵感卡 180×320", () => {
    const c = nodeCenter({ x: 50, y: 80 }, 180, 320);
    expect(c).toEqual({ x: 140, y: 240 });
  });
  it("拉伸后 400×500", () => {
    const c = nodeCenter({ x: 0, y: 0 }, 400, 500);
    expect(c).toEqual({ x: 200, y: 250 });
  });
});

describe("中心线吸附逻辑", () => {
  it("两个节点中心对齐时 snapX=目标中心", () => {
    // 拖拽节点 A 宽 240，目标节点 B 宽 180
    // 目标 B 在 pos(500,248), 中心 = (500+90, 248+160) = (590, 408)
    const targetW = 180, targetH = 320;
    const targetPos = { x: 500, y: 248 };
    const tc = nodeCenter(targetPos, targetW, targetH);
    expect(tc).toEqual({ x: 590, y: 408 });

    // 把 A 拖到 B 的中心对齐位置
    const draggedW = 240, draggedH = 100;
    const alignedPos = { x: tc.x - draggedW / 2, y: tc.y - draggedH / 2 };
    const dc = nodeCenter(alignedPos, draggedW, draggedH);
    expect(dc.x).toBe(tc.x);
    expect(dc.y).toBe(tc.y);
  });

  it("吸附后拖拽节点位置修正", () => {
    const targetCenterX = 590;
    const draggedW = 240;
    // snapX = targetCenterX, nx = snapX - draggedW/2
    const nx = targetCenterX - draggedW / 2;
    expect(nx).toBe(470);
    // 验证修正后拖拽节点中心 = 目标中心
    expect(nx + draggedW / 2).toBe(targetCenterX);
  });
});

describe("GuidesOverlay 坐标变换", () => {
  it("辅助线 screenX 与节点屏幕位置一致", () => {
    const guides = { x: 500, y: 300 };
    const vp = { x: 100, y: 50, scale: 0.5 };
    const screen = flowToScreen(guides.x!, guides.y!, vp);
    // 节点在 flow(500,300) 的屏幕位置
    expect(screen.x).toBe(350);
    expect(screen.y).toBe(200);
    // 辅助线应画在相同位置
  });
});