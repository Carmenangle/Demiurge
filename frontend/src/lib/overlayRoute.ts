// 弹窗返回链：hash 末尾每开一个弹窗追加一段 `/@<id>`。
// 为什么要有这一层：壳层返回键（MainActivity）只调 `webView.goBack()`，浏览器后退也只改 URL。
// 弹窗若只存在于 React 状态里，返回键会直接退掉底下的页面（手机端实锤：开着弹窗一按就退回
// 上一屏甚至退出）。每开一个弹窗压一条历史，返回键就自然是「先关弹窗、再退页面」。
//
// 分工：本模块负责「弹窗段」的解析/追加与历史记账；`viewRouting.parseRouteHash` 负责跳过
// 这些段做视图解析（`#/settings/@regex` 的 panel 仍是空串）。两边的约定只有 `@` 前缀一处。
import { useEffect, useRef } from "react";

const MARK = "@";

/** hash 里的弹窗段，自底向顶（先开在前）。 */
export function overlayIdsOf(hash: string): string[] {
  const raw = hash.replace(/^#\/?/, "");
  if (!raw) return [];
  return raw.split("/")
    .filter((seg) => seg.startsWith(MARK))
    .map((seg) => {
      try { return decodeURIComponent(seg.slice(1)); } catch { return seg.slice(1); }
    });
}

/** 去掉弹窗段后的视图 hash。 */
export function stripOverlaySegments(hash: string): string {
  const raw = hash.replace(/^#/, "");
  const kept = raw.split("/").filter((seg) => seg && !seg.startsWith(MARK));
  return kept.length ? `#/${kept.join("/")}` : "#";
}

/** 以给定弹窗链重建 hash（`ids` 为空即视图 hash）。 */
export function withOverlaySegments(hash: string, ids: readonly string[]): string {
  const base = stripOverlaySegments(hash);
  if (ids.length === 0) return base;
  const suffix = ids.map((id) => `/${MARK}${encodeURIComponent(id)}`).join("");
  return base === "#" ? `#${suffix}` : `${base}${suffix}`;
}

// 同 id 弹窗可能被 StrictMode 双挂载：用计数 + 延迟清理区分「真关」与「开发期重挂」。
const holds = new Map<string, number>();
const releaseTimers = new Map<string, ReturnType<typeof setTimeout>>();

function pushOverlay(id: string): void {
  const ids = overlayIdsOf(window.location.hash);
  if (ids.includes(id)) return;
  window.history.pushState(window.history.state, "", withOverlaySegments(window.location.hash, [...ids, id]));
}

function scheduleRelease(id: string): void {
  const pending = releaseTimers.get(id);
  if (pending) clearTimeout(pending);
  releaseTimers.set(id, setTimeout(() => {
    releaseTimers.delete(id);
    if ((holds.get(id) ?? 0) > 0) return;             // 双挂载的重挂：还是开着
    const ids = overlayIdsOf(window.location.hash);
    const at = ids.lastIndexOf(id);
    if (at < 0) return;                                // 已经被返回键弹掉了
    if (at === ids.length - 1) {
      // 正常关闭：退掉我们自己压的那一条，别留一格「看着没反应」的历史。
      window.history.back();
    } else {
      // 异常次序（父弹窗先关）：只摘掉自己那段，别把上层弹窗一起弹掉。
      window.history.replaceState(window.history.state, "", withOverlaySegments(window.location.hash, ids.filter((_, i) => i !== at)));
    }
  }, 0));
}

/** 弹窗开合接进返回链：`id` 非空即「开着」。关（`id` 变空/组件卸载）时回退历史。 */
export function useOverlayRoute(id: string | null, onClose: () => void): void {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!id) return;
    holds.set(id, (holds.get(id) ?? 0) + 1);
    // 一次后退同时触发 popstate 与 hashchange，两个事件都会看见「id 不在了」——只关一次。
    let fired = false;
    const onPop = () => {
      if (overlayIdsOf(window.location.hash).includes(id)) return;
      if (fired) return;
      fired = true;
      closeRef.current();
    };
    window.addEventListener("popstate", onPop);
    window.addEventListener("hashchange", onPop);
    pushOverlay(id);
    return () => {
      window.removeEventListener("popstate", onPop);
      window.removeEventListener("hashchange", onPop);
      const left = (holds.get(id) ?? 1) - 1;
      if (left > 0) { holds.set(id, left); return; }
      holds.delete(id);
      scheduleRelease(id);
    };
  }, [id]);
}
