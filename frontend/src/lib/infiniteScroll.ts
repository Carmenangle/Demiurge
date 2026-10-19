// 无限滚动的几何判定（2026-10-19）。
//
// 为什么不直接用 IntersectionObserver：本应用的滚动条在 `.app-body`（overflow:auto）
// 这层内层容器上，而不是 window。IO 的 root 默认是视口，跨嵌套滚动容器 + 祖先裁剪时
// 回调可能永不触发——用户实锤「滚到最下方还是不加载下一页」。
// 改成直接量哨兵到视口底的距离：与「谁在滚」无关，任何滚动容器都成立。

/** 哨兵是否已进入「视口底 − threshold」触发带。 */
export function shouldLoadMore(
  sentinelTop: number,
  viewportHeight: number,
  threshold = 600,
): boolean {
  if (!Number.isFinite(sentinelTop) || !Number.isFinite(viewportHeight)) return false;
  if (viewportHeight <= 0) return false;
  return sentinelTop - viewportHeight < threshold;
}
