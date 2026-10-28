import { describe, expect, it } from "vitest";
import { columnSpread, distributeMasonry, masonryColumnCount } from "./masonry";

// 复刻真实库的宽高比谱：服设/原创里 0.56（竖长）～2.6（超宽）混排，
// 还有一批 1:1 的暗色纹理/节点图集——正是 CSS columns 列底参差最严重的场景。
const REAL_MIX = [
  2.46, 1.78, 2.33, 2.41, 2.40, 2.33, 2.33, 0.56, 2.37, 2.33, 2.60, 1.78,
  1.00, 1.00, 1.00, 0.62, 1.78, 2.39, 1.75, 2.29,
];
const COL = 6;
const heightsOf = (ratios: number[]) => ratios.reduce((s, r) => s + 1 / r, 0);

describe("distributeMasonry", () => {
  it("列数等于请求列数，且不丢项", () => {
    const cols = distributeMasonry(REAL_MIX, COL, (r) => r);
    expect(cols).toHaveLength(COL);
    expect(cols.flat()).toHaveLength(REAL_MIX.length);
    expect([...cols.flat()].sort((a, b) => a - b)).toEqual([...REAL_MIX].sort((a, b) => a - b));
    // 不允许空列（朴素顺序切块在 20/6 时会切出空列，视觉上就是一排缺口）
    for (const c of cols) expect(c.length).toBeGreaterThan(0);
  });

  it("每列内部保持原有顺序（投放序不被重排）", () => {
    const cols = distributeMasonry(["a", "b", "c", "d", "e"], 2, () => 1);
    expect(cols[0]).toEqual(["a", "c", "e"]);
    expect(cols[1]).toEqual(["b", "d"]);
    // 注意：瀑布流的阅读顺序天然是「列优先」（CSS columns 同样如此），
    // 这里只保证同一列内不换序——那才是用户感知的「顺序乱」。
  });

  it("列底齐平：真实宽高比谱下远优于朴素顺序切块", () => {
    const balanced = columnSpread(REAL_MIX, COL);
    const per = Math.ceil(REAL_MIX.length / COL);
    const chunkedHeights = Array.from({ length: COL }, (_, i) =>
      heightsOf(REAL_MIX.slice(i * per, i * per + per)));
    const chunked = Math.max(...chunkedHeights) - Math.min(...chunkedHeights);
    const ideal = heightsOf(REAL_MIX) / COL;
    const maxItem = Math.max(...REAL_MIX.map((r) => 1 / r));

    // 真机校准值：均列 spread 0.633 / 朴素 4.613（约 7×）；理想列高 2.212
    expect(balanced).toBeLessThan(chunked / 3);
    // 不均衡度小于「一张最高图」的高度 ⇒ 视觉上看不出列底参差
    expect(balanced).toBeLessThan(maxItem);
    // 相对不均衡 < 30%
    expect(balanced / ideal).toBeLessThan(0.3);
  });

  it("竖长图与超宽图交替：不会堆成一列，列高接近理想值", () => {
    // 4 张 1:3 竖长 + 4 张 3:1 超宽交替出现（贪心最不利的输入）
    const ratios = [3, 1 / 3, 3, 1 / 3, 3, 1 / 3, 3, 1 / 3];
    const cols = distributeMasonry(ratios, 2, (r) => r);
    const heights = cols.map(heightsOf);
    const ideal = heightsOf(ratios) / 2;
    expect(cols.flat()).toHaveLength(8);
    // 贪心在此输入下会给 5/3 的分割（已知局限），但列高仍应收敛到理想值 ±10%
    expect(Math.abs(heights[0] - ideal) / ideal).toBeLessThan(0.1);
    expect(Math.abs(heights[1] - ideal) / ideal).toBeLessThan(0.1);
  });

  it("宽高比未知按 1 估（图片未加载完也不崩、不倾斜）", () => {
    const cols = distributeMasonry(["x", "y", "z"], 3, () => Number.NaN);
    expect(cols.flat()).toEqual(["x", "y", "z"]);
    expect(cols[0]).toEqual(["x"]);
  });

  it("非法列数退化为 1 列（不产生空列/丢项）", () => {
    expect(distributeMasonry([1, 2, 3], 0, (r) => r)).toEqual([[1, 2, 3]]);
    expect(distributeMasonry([1, 2, 3], -5, (r) => r)).toEqual([[1, 2, 3]]);
    expect(distributeMasonry([], 3, (r) => r)).toEqual([[], [], []]);
  });

  it("ratio 为 0/负数时也按 1 估（除零不会产出 Infinity 列高）", () => {
    const cols = distributeMasonry(["a", "b"], 2, (item) => (item === "a" ? 0 : -2));
    expect(cols.flat()).toEqual(["a", "b"]);
  });
});

describe("masonryColumnCount（2026-10-27 用户报「手机上图被压得很小」）", () => {
  it("断点与旧 CSS columns 同口径，另补 480px 以下的两列", () => {
    expect(masonryColumnCount(390)).toBe(2);
    expect(masonryColumnCount(480)).toBe(2);
    expect(masonryColumnCount(481)).toBe(3);
    expect(masonryColumnCount(640)).toBe(3);
    expect(masonryColumnCount(641)).toBe(4);
    expect(masonryColumnCount(900)).toBe(4);
    expect(masonryColumnCount(901)).toBe(6);
  });

  it("宽度未知/非法（容器还没测量）退回桌面 6 列，不会把图放成两张巨图", () => {
    expect(masonryColumnCount(0)).toBe(6);
    expect(masonryColumnCount(Number.NaN)).toBe(6);
    expect(masonryColumnCount(-1)).toBe(6);
  });

  it("390px 竖屏：6 列 50px → 2 列 177px，放大约 3.5 倍（用户要求 ≥2.5 倍）", () => {
    // 实测容器宽 362（390 减去页面左右各 14），列间距桌面 12 / 手机 8
    const cellAt = (cols: number, gap: number) => (362 - gap * (cols - 1)) / cols;
    const before = cellAt(6, 12); // 旧行为恒 6 列（390px 下每格 50px）
    const after = cellAt(masonryColumnCount(390), 8);
    expect(before).toBeCloseTo(50.3, 1);
    expect(after).toBeCloseTo(177, 0);
    expect(after / before).toBeGreaterThan(2.5);
  });
});
