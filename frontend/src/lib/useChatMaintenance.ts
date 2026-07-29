import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { clearCache as clearCacheApi, compactHistory, saveSnapshot } from "../api/ai";
import type { ChatMessage } from "../types/chat";
import {
  compactedChatMessage,
  contextTokenEstimate,
  nextContextReminderBucket,
} from "./contextManagement";

type Model = { baseUrl: string; apiKey: string; modelName: string };
type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

interface ChatMaintenanceDeps {
  threadId: string;
  messages: ChatMessage[];
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  isBusy: boolean;       // 包含 wfRunning，用于 compact
  isStreaming: boolean;  // 仅 AI 流式中，用于 clearCache（工作流运行时仍可清）
  chat: Model;
  embed: Model;
  outputDir: string;
  reminderTokens: number;
  askConfirm: (message: string) => Promise<boolean>;
  cancelPendingSnapshot: () => void;
  storage?: StorageLike;
}

export function useChatMaintenance({
  threadId,
  messages,
  setMessages,
  isBusy,
  isStreaming,
  chat,
  embed,
  outputDir,
  reminderTokens,
  askConfirm,
  cancelPendingSnapshot,
  storage = localStorage,
}: ChatMaintenanceDeps) {
  const reminderKey = `laf_context_token_reminder_${threadId}_${reminderTokens}`;
  const chatKey = `laf_chat_${threadId}`;
  const [contextReminder, setContextReminder] = useState<{ bucket: number; tokens: number } | null>(null);
  const [compacting, setCompacting] = useState(false);

  useEffect(() => {
    const tokens = contextTokenEstimate(messages);
    let lastBucket = 0;
    try { lastBucket = Number(storage.getItem(reminderKey) || "0") || 0; } catch { /* ignore */ }
    const bucket = nextContextReminderBucket(tokens, lastBucket, reminderTokens);
    setContextReminder(bucket === null ? null : { bucket, tokens });
  }, [messages, reminderKey, reminderTokens, storage]);

  const dismissContextReminder = () => {
    if (!contextReminder) return;
    try { storage.setItem(reminderKey, String(contextReminder.bucket)); } catch { /* ignore */ }
    setContextReminder(null);
  };

  const resetContextReminder = () => {
    try { storage.setItem(reminderKey, "0"); } catch { /* ignore */ }
    setContextReminder(null);
  };

  const pushError = (text: string) => {
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "assistant", text },
    ]);
  };

  const compact = async (): Promise<boolean> => {
    if (isBusy || compacting) return false;
    const confirmed = await askConfirm(
      "压缩会总结本仓库从第一条到最后一条的完整会话，不受普通 Agent 最近 6+6 条读取范围限制。\n"
      + "旧消息将替换为一条“摘要文本 + 最后成果图”；资产库和知识库不会删除。确定压缩吗？",
    );
    if (!confirmed) return false;
    setCompacting(true);
    try {
      cancelPendingSnapshot();
      await saveSnapshot(threadId, messages);
      const result = await compactHistory(threadId, chat, embed);
      if (result.ok && result.message) {
        setMessages([compactedChatMessage(result.message)]);
        resetContextReminder();
        return true;
      }
      pushError("压缩失败：没有可压缩的内容或摘要为空。");
      return false;
    } catch (error) {
      pushError("压缩失败：" + (error as Error).message);
      return false;
    } finally {
      setCompacting(false);
    }
  };

  const clearCache = async (): Promise<boolean> => {
    if (isStreaming || compacting) return false;
    const confirmed = await askConfirm(
      "清除缓存会清空当前对话内容，并删除本仓库上传的参考图（reference 文件夹）。\n"
      + "已生成的图片（资产库）和知识库内容都会保留，不受影响。确定清除吗？",
    );
    if (!confirmed) return false;
    try {
      await clearCacheApi(threadId, outputDir);
      setMessages([]);
      resetContextReminder();
      try { storage.removeItem(chatKey); } catch { /* ignore */ }
      return true;
    } catch (error) {
      pushError("清除缓存失败：" + (error as Error).message);
      return false;
    }
  };

  return { compact, compacting, clearCache, contextReminder, dismissContextReminder };
}
