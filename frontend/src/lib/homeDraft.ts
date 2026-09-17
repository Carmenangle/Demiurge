// 首页(home)=临时草稿区：草稿存模块级内存变量，随浏览器进程存活——
// 页面刷新(进程重开)即重置为空，但应用运行期间切走首页再回来仍保留。不落 localStorage / 后端快照。
//
// ▍T2.2-B3-a（2026-09-17）：从 `lib/useChatSession.ts` 抽出的**跨模块共享状态单例**。
//
// 为什么必须抽：原先它是 `useChatSession.ts` 的模块级 `let homeDraft`，被**四个消费方**共享
// （加载链读、持久化 effect 写、`ConversationHistoryRuntime.persist` 回调写、`clearHome` 清）。
// 加载链即将外移到 `lib/useSessionLoad.ts` ⇒ 若状态留在视图模块，视图与 hook 会**各持一份**
// （症状：切走首页再回来草稿丢失、或写进的那份读不到）。
//
// 为什么用「函数读写」而不是 `export let`：ESM 的 import 绑定**对导入方只读**，跨模块重赋值
// 必须走 setter；用访问器也让「读的是**调用时**的值、不是模块加载期快照」这件事显式化
// （草稿在事件回调里被写、不一定伴随重渲染）。

import type { ChatMessage } from "../types/chat";

let homeDraft: ChatMessage[] = [];

/** 读当前草稿（**调用时**取值）。返回的是内部引用本身 —— 与原 `setMessages(homeDraft)` 语义一致。 */
export function readHomeDraft(): ChatMessage[] {
  return homeDraft;
}

/** 覆盖草稿（首页每次消息变化都整体覆盖，不做合并）。 */
export function writeHomeDraft(next: ChatMessage[]): void {
  homeDraft = next;
}

/** 清空草稿（首页右上角「清空」按钮）。 */
export function clearHomeDraft(): void {
  homeDraft = [];
}
