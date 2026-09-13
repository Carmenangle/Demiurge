import { describe, expect, it } from "vitest";
import { isBodyShotRatio } from "./inspirationAutoPick";

describe("isBodyShotRatio（灵感卡自动预勾：3/4 身~全身=竖图才采纳）", () => {
  it("全身立绘（明显竖图）命中", () => {
    expect(isBodyShotRatio(700, 1000)).toBe(true);   // 1.43
    expect(isBodyShotRatio(600, 1200)).toBe(true);   // 2.0
  });

  it("3/4 身构图（恰在阈值 1.25）命中", () => {
    expect(isBodyShotRatio(800, 1000)).toBe(true);   // 1.25 ≥ 1.25
  });

  it("脸部特写/方形头像不命中", () => {
    expect(isBodyShotRatio(1000, 1000)).toBe(false); // 1:1
    expect(isBodyShotRatio(1000, 1100)).toBe(false); // 1.1
  });

  it("横版宣传图/新闻头图不命中", () => {
    expect(isBodyShotRatio(1200, 630)).toBe(false);
  });

  it("尺寸不可得（0）不命中", () => {
    expect(isBodyShotRatio(0, 1000)).toBe(false);
    expect(isBodyShotRatio(1000, 0)).toBe(false);
  });
});
