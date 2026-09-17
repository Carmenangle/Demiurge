// 进入仓库/切换时加载消息（T2.2-B3-b：2026-09-17 从 lib/useChatSession.ts 外移）。
//
// ▍搬移原则：只搬收口 —— 加载 `useEffect` 体、其上语义注释与 `loadOpeningMessage` 与搬移前**逐字一致**
//   （同层级、零去缩进）；需要的 hook 闭包符号以**同名 deps** 注入（threadId / chatKey / repo / setMessages /
//   loadedRef / messagesRef / recoveryActiveRef / activeThreadRef / recoveryTokenRef / agentBusyRef /
//   bgRunningRef / abortRef / scenarioSnapshotTurnRef / snapTimer / wsUnsubRef / workflowRuntime /
//   startAgentRecovery / settings / openingCardName / effectivePersona / dispatch，共 21 名）；
//   其余自由符号（fetchSnapshot / fetchAgentRunning / fetchHistory / saveSnapshot / reportIllustrationFailure /
//   restoreSubmittedMediaSlots / pruneUnsubmittedMediaSlots / normalizeInspirationCard /
//   recoverCompactedSummaryImage / resolveInitialHistory / shouldRecoverAgentRun / deletedMessageTombstones /
//   releaseAgentStream / substituteMacros / characterDetail / readHomeDraft）由本模块自带 import。
//   ⇒「行为面 = 搬移前」由「逐字搬移锁 + 直断言行为网」两件互补证据承担（useSessionLoad.test.tsx）。
//
// ▍红线（改动即破行为面）：
//   ① 依赖数组保持 `[threadId]` ——「重订阅时机」就是本接缝的行为面；threadId 变化才重新走三级兜底；
//   ② 加载块内的**三级兜底顺序**（本地 localStorage → 后端快照 → langgraph 历史）与
//      `loadedRef` 置位、切回竞态短轮询（8×2500ms）逐字保留；
//   ③ `maybeStartBgPoll` 必须**无论快照是否有内容都执行**（放在兜底 early-return 之后即后台化失效）；
//   ④ 清理函数按序复位 refs、关进度 WS、`dispatch({ t: "reset" })` 逐字保留；
//   ⑤ 调用点必须留在视图内**原 effect 的位置**（hook 调用序 = effect 执行序）。

import { type Dispatch, type MutableRefObject, type SetStateAction, useEffect } from "react";
import type { ChatMessage, MsgPart } from "../types/chat";
import type { Repo } from "../stores/repos";
import type { useSettings, UserPersona } from "../stores/settings";
import { characterDetail } from "../api/characters";
import { fetchAgentRunning, fetchHistory, fetchSnapshot, reportIllustrationFailure, saveSnapshot } from "../api/ai";
import { normalizeInspirationCard } from "../api/chatStreamProtocol";
import { substituteMacros } from "./chatMacros";
import { recoverCompactedSummaryImage } from "./contextManagement";
import { resolveInitialHistory } from "./conversationHistoryRuntime";
import { pruneUnsubmittedMediaSlots, restoreSubmittedMediaSlots } from "./chatSessionEvents";
import { shouldRecoverAgentRun } from "./agentRecovery";
import { releaseAgentStream, type ActiveAgentStream } from "./agentStreamLifecycle";
import type { GenAction } from "./generationLifecycle";
import type { WorkflowGenerationRuntime } from "./workflowGenerationRuntime";
import { readHomeDraft } from "./homeDraft";

// 卡即作品开场白：读关联角色卡的 first_mes，作首条 bot 消息，替换 {{char}}/{{user}} 宏。读不到/无返回空串。
async function loadOpeningMessage(
  characterDir: string, cardName: string, userName: string,
): Promise<string> {
  try {
    const card = await characterDetail(characterDir, cardName);
    const first = card?.first_mes;
    const raw = typeof first === "string" ? first.trim() : "";
    return raw ? substituteMacros(raw, cardName, userName) : "";
  } catch {
    return "";
  }
}

export interface SessionLoadDeps {
  threadId: string;
  chatKey: string;
  repo?: Repo;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  loadedRef: MutableRefObject<boolean>;
  messagesRef: MutableRefObject<ChatMessage[]>;
  recoveryActiveRef: MutableRefObject<boolean>;
  activeThreadRef: MutableRefObject<string>;
  recoveryTokenRef: MutableRefObject<number>;
  agentBusyRef: MutableRefObject<boolean>;
  bgRunningRef: MutableRefObject<boolean>;
  abortRef: MutableRefObject<ActiveAgentStream | null>;
  scenarioSnapshotTurnRef: MutableRefObject<number>;
  snapTimer: MutableRefObject<ReturnType<typeof setTimeout> | null>;
  wsUnsubRef: MutableRefObject<(() => void) | null>;
  workflowRuntime: WorkflowGenerationRuntime;
  startAgentRecovery: (targetThread?: string, targetRepoId?: string) => void;
  settings: ReturnType<typeof useSettings>["settings"];
  openingCardName: string;
  effectivePersona: UserPersona;
  dispatch: Dispatch<GenAction>;
}

export function useSessionLoad({
  threadId, chatKey, repo, setMessages, loadedRef, messagesRef, recoveryActiveRef,
  activeThreadRef, recoveryTokenRef, agentBusyRef, bgRunningRef, abortRef,
  scenarioSnapshotTurnRef, snapTimer, wsUnsubRef, workflowRuntime, startAgentRecovery,
  settings, openingCardName, effectivePersona, dispatch,
}: SessionLoadDeps) {
  // 进入仓库/切换时加载消息，三级兜底：本地 localStorage → 后端消息流快照 → langgraph 对话历史。
  useEffect(() => {
    let alive = true;
    loadedRef.current = false;
    let shownLocal = false;
    // 首页(home)=临时草稿区：从模块级 homeDraft 恢复（进程内切走切回保留，页面刷新即空）。
    // 不读 localStorage / 后端快照 / 后端历史。仅保留后台轮询——生成中切回来仍要看到进度。
    const isHome = threadId === "home";
    const local = isHome ? null : localStorage.getItem(chatKey);
    if (local) {
      try {
        const arr = JSON.parse(local) as ChatMessage[];
        if (arr.length > 0) {
          setMessages(arr);
          shownLocal = true;
        }
      } catch { /* 本地损坏则走后端兜底 */ }
    }
    // 切回/刷新时若后台仍有生成任务在跑，轮询快照等其落盘后自动回显。
    // 必须无论快照是否已有内容都执行——正常对话后快照必然非空，若放在
    // 加载兜底的 early-return 之后就永远跑不到，等于后台化失效。
    const maybeStartBgPoll = async () => {
      try {
        if (!shouldRecoverAgentRun(threadId)) return;
        if (!alive || recoveryActiveRef.current) return;
        // 切回/刷新竞态修复（2026-09-03 实锤）：后端可能恰好在快照拉取前后
        // 落盘计划卡。无论 running 与否都短轮询几轮快照——捡到新消息就渲染，
        // 期间发现仍在 running 则转完整后台恢复。
        for (let i = 0; i < 8; i++) {
          if (!alive) return;
          try {
            const st = await fetchAgentRunning(threadId);
            if (!alive) return;
            if (st.running) {
              startAgentRecovery(threadId, repo?.id);
              return;
            }
            const snap = await fetchSnapshot(threadId);
            if (!alive) return;
            const items = (snap.items || []) as ChatMessage[];
            if (items.length > messagesRef.current.length) {
              try {
                const restored = restoreSubmittedMediaSlots(
                  items.map((m) => m.inspiration
                    ? { ...m, inspiration: normalizeInspirationCard(m.inspiration as unknown as Record<string, unknown>) }
                    : m),
                  workflowRuntime.list(),
                );
                setMessages(pruneUnsubmittedMediaSlots(restored).messages);
                try { localStorage.setItem(chatKey, JSON.stringify(items)); } catch { /* ignore */ }
              } catch { setMessages(items); }
              return;
            }
            if (items.length > 0) return; // 快照已有内容且未增长，视为稳定
          } catch { /* 单轮失败继续 */ }
          await new Promise((r) => setTimeout(r, 2500));
        }
      } catch { /* 状态接口失败，忽略 */ }
    };
    (async () => {
      // 首页临时草稿区：从 homeDraft 恢复（进程内切回保留，刷新即空），仅接后台轮询（生成中切回可见进度）。
      if (isHome) {
        if (readHomeDraft().length > 0) setMessages(readHomeDraft());
        loadedRef.current = true;
        await maybeStartBgPoll();
        return;
      }
      try {
        const snap = await fetchSnapshot(threadId);
        if (!alive) return;
        if (snap.items && snap.items.length > 0) {
          const snapshotMessages = restoreSubmittedMediaSlots(
            (snap.items as ChatMessage[]).map((m) => m.inspiration
              ? { ...m, inspiration: normalizeInspirationCard(m.inspiration as unknown as Record<string, unknown>) }
              : m),
            workflowRuntime.list(),
          );
          const pruned = pruneUnsubmittedMediaSlots(snapshotMessages);
          let restoredMessages = pruned.messages;
          for (const item of pruned.removed) {
            void reportIllustrationFailure({
              threadId, repoId: repo?.id || threadId,
              messageId: item.messageId, slotId: item.slotId,
              stage: "resume_unsubmitted", error: "页面恢复时发现未提交到 ComfyUI 的孤儿插画槽",
            }).catch(() => undefined);
          }
          const needsSummaryImage = snapshotMessages.length === 1
            && snapshotMessages[0].text.startsWith("【历史摘要】")
            && !snapshotMessages[0].image
            && !snapshotMessages[0].parts?.some((part) => part.type === "image");
          if (needsSummaryImage) {
            try {
              const history = await fetchHistory(threadId);
              restoredMessages = recoverCompactedSummaryImage(snapshotMessages, history.items || []);
            } catch { /* 旧摘要修复失败时仍显示原摘要 */ }
          }
          setMessages(restoredMessages);
          if (restoredMessages !== snapshotMessages) {
            try { localStorage.setItem(chatKey, JSON.stringify(restoredMessages)); } catch { /* ignore */ }
            saveSnapshot(threadId, restoredMessages).catch(() => {});
          }
          loadedRef.current = true;
          await maybeStartBgPoll();
          return;
        }
        // 成功返回的空快照是当前 UUID 的历史真源；仅请求失败时才允许本地缓存兜底。
        const initialHistory = resolveInitialHistory(
          snap.items as ChatMessage[], shownLocal ? messagesRef.current : [],
        );
        if (initialHistory.length === 0) {
          shownLocal = false;
          setMessages([]);
          try { localStorage.removeItem(chatKey); } catch { /* ignore */ }
        }
      } catch { /* 快照接口失败，继续兜底 */ }
      if (shownLocal) { loadedRef.current = true; await maybeStartBgPoll(); return; }  // 本地已渲染、后端无快照
      try {
        const r = await fetchHistory(threadId);
        if (!alive) return;
        const historyMessages = (r.items || []).map((m) => {
          const imgs = m.images || [];
          const parts: MsgPart[] = [];
          if (m.content) parts.push({ type: "text", text: m.content });
          for (const u of imgs) parts.push({ type: "image", url: u });
          return {
            id: crypto.randomUUID(),
            role: m.role === "assistant" ? "assistant" : "user",
            text: m.content,
            parts: parts.length > 0 ? parts : undefined,
          } as ChatMessage;
        });
        // 卡即作品：全无历史(本地/快照/后端皆空)且关联角色卡 → 用卡的 first_mes 作开场白首条。
        // 落一次后进快照，下次从快照恢复，不再重复注入（幂等）。
        if (historyMessages.length === 0 && openingCardName && settings.characterDir) {
          const opening = await loadOpeningMessage(settings.characterDir, openingCardName, effectivePersona.name || "");
          if (!alive) return;
          if (opening) {
            setMessages([{ id: crypto.randomUUID(), role: "assistant", text: opening }]);
          } else {
            setMessages(historyMessages);
          }
        } else {
          setMessages(historyMessages);
        }
      } catch { /* 后端未起/无历史，保持空 */ }
      finally { if (alive) loadedRef.current = true; }
      await maybeStartBgPoll();
    })();
    return () => {
      scenarioSnapshotTurnRef.current = -1;
      alive = false;
      activeThreadRef.current = "";
      recoveryTokenRef.current += 1;
      recoveryActiveRef.current = false;
      agentBusyRef.current = false;
      bgRunningRef.current = false;
      abortRef.current = releaseAgentStream(abortRef.current, "navigation");
      if (snapTimer.current) { clearTimeout(snapTimer.current); snapTimer.current = null; }
      wsUnsubRef.current?.(); wsUnsubRef.current = null;  // 关进度 WS，避免切仓库泄漏连接
      dispatch({ t: "reset" });  // 清空生成状态 + 待发队列，避免串到新仓库
    };
  }, [threadId]);
}

