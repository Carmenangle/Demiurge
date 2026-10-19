// 瀑布流均列分配（2026-10-19）。
//
// 为什么不用 CSS `columns`：多列布局会把阅读顺序变成**列优先**（视觉上顺序乱），
// 且各列总高度只做粗略均衡 ⇒ 列底参差，暗色素材/超宽图混排时出现一排排空隙。
// 这里按「当前最矮的列」投放：列宽固定 ⇒ 一列的高度 ∝ Σ(1/宽高比)，
// 每次把图加进最矮的列，列底天然齐平，也不会改变左→右的阅读顺序。

/** 把 items 分配到 colCount 列，返回按列分组的二维数组（顺序＝投放顺序）。 */
export function distributeMasonry<T>(
  items: T[],
  colCount: number,
  ratioOf: (item: T) => number,
): T[][] {
  const n = Math.max(1, Math.floor(colCount) || 1);
  const cols: T[][] = Array.from({ length: n }, () => []);
  const heights = new Array<number>(n).fill(0);
  for (const item of items) {
    const raw = ratioOf(item);
    // 宽高比未知/非法按 1（方形）估，等图片 onLoad 回填真实值后会自动重排
    const ratio = Number.isFinite(raw) && raw > 0 ? raw : 1;
    let best = 0;
    for (let i = 1; i < n; i++) {
      if (heights[i] < heights[best] - 1e-9) best = i;
    }
    cols[best].push(item);
    heights[best] += 1 / ratio;
  }
  return cols;
}

/** 列高差（用于测试/自检）：最大列高 − 最小列高，越接近 0 越齐平。 */
export function columnSpread(ratios: number[], colCount: number): number {
  const cols = distributeMasonry(ratios, colCount, (r) => r);
  const heights = cols.map((col) => col.reduce((s, r) => s + 1 / r, 0));
  return Math.max(...heights) - Math.min(...heights);
}
