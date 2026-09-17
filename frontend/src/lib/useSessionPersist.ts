// 消息持久化链（T2.2-B4-b：2026-09-17 从 lib/useChatSession.ts 外移）。
//
// 职责：消息变化 → 600ms 防抖 →「localStorage 轻量快取 + 后端快照」双写；首页(home) 只写模块级草稿区。
// 前置：`persistDataUri`（data:URI 大图落盘转 local-view 小地址）与 `slimSnapshot` 瘦身链。
//
// ▍搬移原则：只搬收口 —— `persistDataUri` / `slimSnapshot` / 防抖持久化 `useEffect` 与三段注释
//   与搬移前**逐字一致**（同层级、零去缩进）；闭包符号以**同名 deps** 注入
//   （threadId / chatKey / repo / settings / messages / setMessages / loadedRef / messagesRef /
//   activeThreadRef / agentBusyRef / bgRunningRef / snapTimer / scenarioSnapshotTurnRef /
//   streamingId / regeneratingIds，共 15 名）；其余自由符号（saveLocalSrc / localViewUrl /
//   saveSnapshot / createScenarioSnapshot / slimSnapshot（as `slimSnapshotPure`）/ acceptSlimmedMessages /
//   canCommitSnapshot / writeHomeDraft）由本模块自带 import。⇒ 行为面由 useSessionPersist.test.tsx 承担。
//
// ▍红线（改动即破行为面）：
//   ① 依赖数组逐字保持 `[messages, chatKey, threadId, settings.outputDir, repo?.id, streamingId,
//      regeneratingIds.size]` ——「重订阅时机」就是本接缝的行为面；
//   ② 防抖 600ms 与**两道** `canCommitSnapshot` 闸（`await slimSnapshot` 前 / 后）逐字保留：
//      后一道守的是「图片落盘期间又追加消息 / 开始新 Agent / 切换仓库」——旧任务连 localStorage 都不得写；
//   ③ home 分支只写 `writeHomeDraft` 并 **early-return**（不落 localStorage / 后端快照）；
//   ④ `tid === threadId` 才 `setMessages`（线程已切走则不许回写内存）；
//   ⑤ 调用点必须留在视图内**原 effect 的位置**（hook 调用序＝ effect 执行序）。

import { type Dispatch, type MutableRefObject, type SetStateAction, useEffect } from "react";
import type { ChatMessage } from "../types/chat";
import type { Repo } from "../stores/repos";
import type { useSettings } from "../stores/settings";
import { createScenarioSnapshot } from "../api/scenario";
import { localViewUrl, saveLocalSrc } from "../api/comfyui";
import { saveSnapshot } from "../api/ai";
import { acceptSlimmedMessages, canCommitSnapshot, slimSnapshot as slimSnapshotPure } from "./chatGeneration";
import { writeHomeDraft } from "./homeDraft";

export interface SessionPersistDeps {
  threadId: string;
  chatKey: string;
  repo?: Repo;
  settings: ReturnType<typeof useSettings>["settings"];
  messages: ChatMessage[];
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  loadedRef: MutableRefObject<boolean>;
  messagesRef: MutableRefObject<ChatMessage[]>;
  activeThreadRef: MutableRefObject<string>;
  agentBusyRef: MutableRefObject<boolean>;
  bgRunningRef: MutableRefObject<boolean>;
  snapTimer: MutableRefObject<ReturnType<typeof setTimeout> | null>;
  scenarioSnapshotTurnRef: MutableRefObject<number>;
  streamingId: string | null;
  regeneratingIds: Set<string>;
}

export function useSessionPersist({
  threadId, chatKey, repo, settings, messages, setMessages, loadedRef, messagesRef,
  activeThreadRef, agentBusyRef, bgRunningRef, snapTimer, scenarioSnapshotTurnRef,
  streamingId, regeneratingIds,
}: SessionPersistDeps): void {
  // 存快照前给图片瘦身：把用户上传的 data:URI 大图落盘转 local-view 小地址。
  // data:URI 只来自用户上传的参考图 → 落 reference/ 子夹，与生成图（仓库根目录）分开。
  const persistDataUri = async (src: string): Promise<string> => {
    if (typeof src === "string" && src.startsWith("data:") && settings.outputDir && repo?.id) {
      try {
        const s = await saveLocalSrc({ src, repoId: repo.id, outputDir: settings.outputDir, subdir: "reference" });
        return localViewUrl(s.path);
      } catch { /* 保留原图 */ }
    }
    return src;
  };
  const slimSnapshot = (msgs: ChatMessage[]) => slimSnapshotPure(msgs, persistDataUri);

  // 消息变化时持久化：本地快取 + 后端快照统一防抖；本地也写瘦身后内容，避免 dataURI 大图撑爆 localStorage。
  // 首页(home)=临时草稿区：只写模块级 homeDraft（进程内切走切回保留，刷新即空），不落 localStorage / 后端快照。
  useEffect(() => {
    if (!loadedRef.current) return;  // 加载完成前不写，防止空数组覆盖
    if (threadId === "home") { writeHomeDraft(messages); return; }  // 首页草稿区：仅存内存
    if (snapTimer.current) clearTimeout(snapTimer.current);
    const tid = threadId;
    const original = messages;
    snapTimer.current = setTimeout(async () => {
      if (bgRunningRef.current || streamingId || regeneratingIds.size > 0) return;
      if (!canCommitSnapshot(
        messagesRef.current, original, activeThreadRef.current, tid,
      )) return;
      // 流式正文或重生成尚未终态时不能创建世界状态快照，否则会把半截正文与未完成写回固化。
      const full = await slimSnapshot(original);
      // 图片落盘可能耗时；期间若追加了用户消息、开始新 Agent 或切换仓库，
      // 旧任务连 localStorage/后端都不得写，不能只保护 React 内存。
      if (agentBusyRef.current || bgRunningRef.current || !canCommitSnapshot(
        messagesRef.current, original, activeThreadRef.current, tid,
      )) return;
      // localStorage 只存轻量快取：去掉 capturedGraph，parts / portsPlan 里的 dataURI 已被 slimSnapshot 转成本地 URL。
      const slim = full.map((m) =>
        m.workflow ? { ...m, workflow: { ...m.workflow, capturedGraph: null } } : m,
      );
      try {
        localStorage.setItem(chatKey, JSON.stringify(slim));
      } catch { /* 超额等忽略 */ }
      saveSnapshot(tid, full).then(() => {
        const turn = full.filter((message) => message.role === "assistant"
          && Boolean((message.text || "").trim())).length;
        if (settings.outputDir && repo?.id && turn > scenarioSnapshotTurnRef.current) {
          scenarioSnapshotTurnRef.current = turn;
          void createScenarioSnapshot({
            output_dir: settings.outputDir, repo_id: repo.id, turn,
            label: "自动回合快照", dedupe_key: `turn:${turn}`,
          }).catch(() => { scenarioSnapshotTurnRef.current = turn - 1; });
        }
      }).catch(() => {});  // 后端未起则忽略本地仍在
      if (tid === threadId && JSON.stringify(full) !== JSON.stringify(original)) {
        setMessages((current) => acceptSlimmedMessages(current, original, full));
      }
    }, 600);
  }, [messages, chatKey, threadId, settings.outputDir, repo?.id, streamingId, regeneratingIds.size]);
}
