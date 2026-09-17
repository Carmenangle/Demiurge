// 计划采集产物「逐张实时上屏」订阅（T2.2-B1：2026-09-17 从 lib/useChatSession.ts 外移）。
//
// ▍搬移原则：只搬收口 —— useEffect 体与搬移前**逐字一致**（同层级、零去缩进），其上语义注释一并随体迁入；
//   需要的 hook 闭包符号以**同名 deps** 解构注入（threadId / recoveryActiveRef / agentBusyRef /
//   activeThreadRef / setMessages，共 5 名）；其余符号（fetchSnapshot / deletedMessageTombstones /
//   pickFreshAssistantMedia / appendUnknownMessages / subscribePlanTaskActivities）由本模块自带 import。
//   ⇒「行为面 = 搬移前」由「逐字搬移锁 + 直断言行为网」两件互补证据承担（usePlanTaskFeed.test.tsx）。
//
// ▍红线（改动即破行为面）：
//   ① 依赖数组保持 `[threadId]` —— 「重订阅时机」就是本接缝的行为面；
//   ② 调用点必须留在视图内**原 effect 的位置**（hook 调用序 = effect 执行序）；
//   ③ 轮询节奏（2000ms / settleLeft 60 与 6）与 `laf-generation-saved` 派发条件逐字保留。

import { type Dispatch, type MutableRefObject, type SetStateAction, useEffect } from "react";
import type { ChatMessage } from "../types/chat";
import { fetchSnapshot } from "../api/ai";
import { deletedMessageTombstones } from "./deletedMessageTombstones";
import { subscribePlanTaskActivities } from "./planTaskActivity";
import { appendUnknownMessages, pickFreshAssistantMedia } from "./chatSessionEvents";

export interface PlanTaskFeedDeps {
  threadId: string;
  recoveryActiveRef: MutableRefObject<boolean>;
  agentBusyRef: MutableRefObject<boolean>;
  activeThreadRef: MutableRefObject<string>;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
}

export function usePlanTaskFeed({
  threadId, recoveryActiveRef, agentBusyRef, activeThreadRef, setMessages,
}: PlanTaskFeedDeps) {
  // 计划采集产物「逐张实时上屏」（2026-09-04 唐柚 14 套画像）：计划任务在本会话执行时，
  // 后端按图逐条把 plan_collect 消息（图片+套装名+完整提示词）写进对话快照。这里订阅本会话
  // 的计划任务活动，任务运行/终态扫尾期间周期增量拉快照，只把新增 id 的消息追加上屏——
  // 页内停留即「生成一张发一张」，无需刷新。绝不覆盖本地已有消息（不打扰流式正文/本地编辑），
  // Agent 流式/恢复流程在跑时让路，由其自身回显。
  // 画布/资产桥（2026-09-09）：新上屏的 assistant 图片消息 = 后端已把该产物入 RAG。
  // 派发 laf-generation-saved（/w 等生成落库同款事件）→ 画布节点与资产网格即时出现该产物，
  // 不再等计划全部完成/页面刷新（此前缺桥 → 「产物不进画布直到全部生成完」）。
  useEffect(() => {
    if (threadId === "home") return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let settleLeft = 0;                      // >0 = 还需继续轮询的轮数（任务终态后扫尾捡走最后图）
    const seenIds = new Set<string>();       // 本轮 effect 已上屏的消息 id（去重，防刷新事件风暴）
    const RUNNING = new Set(["queued", "running"]);
    const ENDED = new Set(["done", "partial", "error", "cancelled", "blocked"]);
    const tick = async () => {
      timer = null;
      if (recoveryActiveRef.current || agentBusyRef.current || activeThreadRef.current !== threadId) {
        if (alive && settleLeft > 0) scheduleNext();   // 忙/切走时顺延，不丢扫尾
        return;
      }
      let freshMedia: ChatMessage[] = [];
      try {
        const snap = await fetchSnapshot(threadId);
        if (!alive || activeThreadRef.current !== threadId) return;
        const items = deletedMessageTombstones.filterDeleted(
          threadId, (snap.items || []) as ChatMessage[],
        );
        freshMedia = pickFreshAssistantMedia(items, seenIds);
        setMessages((current) => appendUnknownMessages(current, items));
      } catch { /* 单轮失败顺延下一轮 */ }
      if (freshMedia.length > 0) {
        try {
          window.dispatchEvent(new CustomEvent("laf-generation-saved", { detail: threadId }));
        } catch { /* 非关键事件，失败不影响消息上屏 */ }
      }
      if (alive && settleLeft > 0) { settleLeft -= 1; scheduleNext(); }
    };
    const scheduleNext = () => {
      if (!alive || settleLeft <= 0) return;
      timer = setTimeout(() => { void tick(); }, 2000);
    };
    const unsub = subscribePlanTaskActivities((activities) => {
      if (!alive || activeThreadRef.current !== threadId) return;
      const mine = activities.filter((a) => a.repo_id === threadId);
      const running = mine.some((a) => RUNNING.has(a.status));
      const ended = mine.some((a) => ENDED.has(a.status));
      if (!running && !ended) {            // 本会话无计划任务（或已被挤出列表）→ 停
        settleLeft = 0;
        if (timer) { clearTimeout(timer); timer = null; }
        return;
      }
      settleLeft = running ? 60 : 6;       // 运行中持续轮询；终态后再扫 6 轮（~12s）捡尾
      if (!timer) scheduleNext();
    });
    return () => { alive = false; if (timer) clearTimeout(timer); unsub(); };
  }, [threadId]);

}
