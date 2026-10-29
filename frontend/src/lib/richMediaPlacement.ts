// 图文混排的哨兵定位（纯逻辑，供 ChatMessages 渲染层与单测共用）。
//
// 正文与媒体槽先拼成带私有哨兵 \uE000LAF_MEDIA_<n>\uE001 的字符串，再按哨兵切回顺序。
// 中间夹着显示层正则与宏替换两条链，它们只看正文、不知道哨兵的存在：
// 一旦某条正则把哨兵连同周围文字一起删掉，split 就找不到这个槽的位置，
// 该媒体在渲染层静默消失（用户实锤的「插图画出来了但对话里看不到」的第三种形状）。
// 口径与后端 chat_snapshot 一致：位置可以退让到正文尾部，但不许静默丢图。

/** split 结果：偶数项是正文片段，奇数项是媒体槽在 orderedMediaParts 里的下标（已转成数字）。 */
export type MediaChunk = string | number;

const SENTINEL_SPLIT = /\uE000LAF_MEDIA_(\d+)\uE001/g;

/** 按哨兵把正文切成「文本 / 槽下标」交替序列；无槽时返回空数组（调用方走纯正文渲染）。 */
export function splitMediaChunks(cleanText: string, mediaCount: number): MediaChunk[] {
  if (mediaCount <= 0) return [];
  return cleanText.split(SENTINEL_SPLIT).map((chunk, index) => (index % 2 === 1 ? Number(chunk) : chunk));
}

/** 返回哨兵已被吃掉、拿不到插入位置的槽下标，按原顺序交给调用方追加到正文尾部。 */
export function orphanMediaIndexes(chunks: MediaChunk[], mediaCount: number): number[] {
  const placed = new Set<number>();
  for (const chunk of chunks) {
    if (typeof chunk === "number") placed.add(chunk);
  }
  const orphans: number[] = [];
  for (let index = 0; index < mediaCount; index++) {
    if (!placed.has(index)) orphans.push(index);
  }
  return orphans;
}
