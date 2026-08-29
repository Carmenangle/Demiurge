// lib/stableDetailsOpen.ts — 让正文渲染出的 <details> 折叠条在 innerHTML 重写后保持展开状态。
//
// 背景（2026-08-29 用户验收）：预设显示正则把 AI 正文的 <think> 推演块折叠成
// <details><summary>思考过程</summary>…</details>。消息的任何内容变化（插画回填、
// 流式 delta、状态块更新）都会让 React 重写 dangerouslySetInnerHTML——全新的
// <details> 默认收起，用户展开几秒后就被「收起」。纯逻辑抽这里，可测（stub DOM）；
// React 接线在 useStableDetailsOpen。

/** details 的稳定键：summary 文本。同文本折叠条共享展开状态（行为一致可接受）。 */
export function detailsKey(d: Element): string {
  const summary = d.querySelector && d.querySelector("summary");
  const text = summary && summary.textContent ? summary.textContent : "";
  return text.trim();
}

/** toggle 事件委托：按键记录/清除展开状态（openRef 存储由调用方持有）。 */
export function trackDetailsToggle(
  target: unknown,
  store: Set<string>,
): void {
  const d = target as Element | null;
  if (!d || (d as { tagName?: string }).tagName !== "DETAILS") return;
  const key = detailsKey(d);
  if (!key) return;
  if ((d as HTMLDetailsElement).open) store.add(key);
  else store.delete(key);
}

/** innerHTML 重写后恢复展开状态：遍历容器内 details，按键重设 open。 */
export function restoreOpenDetails(
  root: { querySelectorAll: (sel: string) => { forEach: (cb: (d: HTMLDetailsElement) => void) => void } } | null,
  store: Set<string>,
): number {
  if (!root || store.size === 0) return 0;
  let restored = 0;
  root.querySelectorAll("details").forEach((d) => {
    const key = detailsKey(d);
    if (key && store.has(key) && !d.open) {
      d.open = true;
      restored += 1;
    }
  });
  return restored;
}
