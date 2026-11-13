// 全局模式分镜成片编排（2026-11-09）：全部 shot 视频 ready 后按 shotIndex 拼接成片。
// 从 useChatSession 外移，视图只负责接线；纯判定（收集/顺序/barrier）在 shotSequence.ts。
//
// 红线：任一镜 failed/pending ⇒ 不合并（保留各分镜），绝不产出残缺成片；
// 合并按 shotIndex 排序，不按完成序；同一条消息的同一序列只发起一次（重渲染不重复提交）。
// 成片失败槽可重新拼接（retryShotMerge）——重试前必须仍是「全部镜 ready」。
import { type Dispatch, type SetStateAction, useEffect, useRef } from "react";
import type { ChatMessage } from "../types/chat";
import { mergeShotVideos } from "../api/comfyui";
import { appendMergedVideoSlot, resolveMergedVideoSlot } from "./chatSessionEvents";
import { canMergeShotSequence, collectShotSequence, orderedShotUrls } from "./shotSequence";

export function useShotMerge(input: {
  messages: ChatMessage[];
  threadId: string;
  repoId?: string;
  outputDir: string;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
}) {
  const { messages, threadId, repoId, outputDir, setMessages } = input;
  const startedRef = useRef<Set<string>>(new Set());

  const runMerge = (messageId: string, sequenceId: string, urls: string[]) => {
    const mergedSlotId = `${messageId}:merged`;
    setMessages((current) => appendMergedVideoSlot(
      current, messageId, mergedSlotId, sequenceId,
    ));
    void mergeShotVideos({
      threadId, messageId, repoId: repoId!, outputDir, sequenceId, urls,
    }).then((result) => {
      setMessages((current) => resolveMergedVideoSlot(
        current, messageId, mergedSlotId, sequenceId, result.url,
      ));
    }).catch((error) => {
      setMessages((current) => appendMergedVideoSlot(
        current, messageId, mergedSlotId, sequenceId, "failed",
        error instanceof Error ? error.message : "成片拼接失败",
      ));
    });
  };

  useEffect(() => {
    if (!repoId || !outputDir) return;
    for (const message of messages) {
      if (message.role !== "assistant") continue;
      const state = collectShotSequence(messages, message.id);
      if (!state || !canMergeShotSequence(state)) continue;
      const key = `${message.id}:${state.sequenceId}`;
      if (startedRef.current.has(key)) continue;
      const urls = orderedShotUrls(state).filter(Boolean);
      if (urls.length !== state.total) continue;
      startedRef.current.add(key);
      runMerge(message.id, state.sequenceId, urls);
    }
  }, [messages, repoId, outputDir, threadId, setMessages]);

  /** 成片失败槽的重试：忽略成片槽本身，只要分镜仍全部 ready 就重拼。 */
  const retryShotMerge = (messageId: string): boolean => {
    if (!repoId || !outputDir) return false;
    const state = collectShotSequence(messages, messageId);
    if (!state) return false;
    const urls = orderedShotUrls(state).filter(Boolean);
    if (urls.length !== state.total) return false;
    const key = `${messageId}:${state.sequenceId}`;
    startedRef.current.delete(key);
    runMerge(messageId, state.sequenceId, urls);
    return true;
  };

  return { retryShotMerge };
}
