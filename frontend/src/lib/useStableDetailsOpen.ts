// lib/useStableDetailsOpen.ts — stableDetailsOpen 纯逻辑的 React 接线（单容器场景）。
// 用法：<div ref={containerRef} dangerouslySetInnerHTML={{ __html: html }} />
// innerHTML 重写后（html 引用变化）自动恢复用户展开过的折叠条。
// toggle 事件不冒泡，但 capture 阶段可达祖先——在容器上原生委托，绕过 React 合成事件
// 类型不含 onToggleCapture 的限制（与 ChatMessages.tsx 的多容器手写版同款语义）。
import { useLayoutEffect, useRef } from "react";
import { restoreOpenDetails, trackDetailsToggle } from "./stableDetailsOpen";

export function useStableDetailsOpen(html: string) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const openKeysRef = useRef<Set<string>>(new Set());

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    restoreOpenDetails(el, openKeysRef.current);
    if (!el.dataset.detailsBound) {
      el.dataset.detailsBound = "1";
      el.addEventListener("toggle", (e) => trackDetailsToggle(e.target, openKeysRef.current), true);
    }
  }, [html]);

  return containerRef;
}
