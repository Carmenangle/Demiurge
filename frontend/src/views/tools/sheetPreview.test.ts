import { describe, expect, it } from "vitest";
import { sheetLayout } from "./sheetPreview";

// 这些期望值来自后端 services/gif_sprite.compose_sheet 的公式：
//   宽 = per*cw + pad*(per+1)，高 = rows*ch + pad*(rows+1)
// 预览要跟导出的图逐像素一致，所以布局算式在这里钉死；
// 改后端 compose 时这个测试会先红，提醒同步改预览。
describe("sheetLayout 与后端 compose_sheet 对齐", () => {
  it("cols<=0 时全铺一行", () => {
    expect(sheetLayout(6, 32, 32, 0, 0)).toEqual({
      cols: 6, rows: 1, width: 192, height: 32,
    });
  });

  it("padding 含外缘，不是只加在帧之间", () => {
    // 6 帧 3 列 pad=4：宽 3*48 + 4*4 = 160，高 2*48 + 3*4 = 108
    expect(sheetLayout(6, 48, 48, 3, 4)).toEqual({
      cols: 3, rows: 2, width: 160, height: 108,
    });
  });

  it("帧数不满最后一行时仍按整行算高", () => {
    // 5 帧 3 列 → 2 行，末行留一个空格
    expect(sheetLayout(5, 10, 10, 3, 0)).toEqual({
      cols: 3, rows: 2, width: 30, height: 20,
    });
  });

  it("cols 大于帧数时不夹到帧数，照样留空格", () => {
    // 后端是 max(1, cols)，不是 min(cols, len)
    expect(sheetLayout(2, 10, 10, 5, 0)).toEqual({
      cols: 5, rows: 1, width: 50, height: 10,
    });
  });

  it("padding 负数按 0 处理", () => {
    expect(sheetLayout(4, 8, 8, 2, -3)).toEqual({
      cols: 2, rows: 2, width: 16, height: 16,
    });
  });
});
