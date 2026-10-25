/**
 * 「这份场景清单实际读了哪些 WE 属性」的共享登记处。
 *
 * 真源是渲染清单的 `userBindings`（见 vendor/we-scene-manifest）：只有清单里出现过
 * 的属性，改它才会改变画面。壁纸层每次解码/热重建完成后把结果登记在这里，选项面板
 * 据此过滤控件——否则作者留在工程里、场景根本不消费的键（各种开关、广告、UI 色）
 * 也会摆到用户面前，改了没反应。
 */
import { useSyncExternalStore } from "react";

const registry = new Map<string, string[]>();
const listeners = new Set<() => void>();

function sameKeys(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((key, index) => key === b[index]);
}

/** 同一来源重复登记同一组键时不发通知：滑杆每拖一格都会走这里。 */
export function publishSceneBindings(source: string, bindings: readonly string[]): void {
  const next = Array.from(new Set(bindings)).sort();
  const current = registry.get(source);
  if (current && sameKeys(current, next)) return;
  registry.set(source, next);
  for (const listener of listeners) listener();
}

export function sceneBindingsFor(source: string | undefined | null): string[] | null {
  if (!source) return null;
  return registry.get(source) ?? null;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** null=场景渲染器还没就绪（清单没解出来），空数组=这份作品没有可调属性。 */
export function useSceneBindings(source: string | undefined | null): string[] | null {
  return useSyncExternalStore(subscribe, () => sceneBindingsFor(source));
}
