// 灵感卡图片自动预勾判据（2026-09-11 用户取舍口径）：
// 「构图能展示 3/4 身到全身的才采纳」。3/4 身以上构图必然是竖图
// （高 ≥ 宽 × 1.25）；脸部特写/方形头像 ≈ 1:1，横版宣传图/新闻头图更扁，
// 一律不预勾。纯函数，供 InspirationCard 的缩略图 onLoad 判定。
export const BODY_SHOT_MIN_RATIO = 1.25;

export function isBodyShotRatio(naturalWidth: number, naturalHeight: number): boolean {
  if (!naturalWidth || !naturalHeight) return false;
  return naturalHeight / naturalWidth >= BODY_SHOT_MIN_RATIO;
}
