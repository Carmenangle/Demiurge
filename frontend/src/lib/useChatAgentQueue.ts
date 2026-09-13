import { useEffect, useRef, useState, type MutableRefObject } from "react";
import type { RichContent } from "../components/RichInput";
import {
  cancelChatQueueTask, enqueueChatQueueTask, listChatQueueTasks,
  type AgentInvocation,
} from "../api/ai";
import type { QueueItem } from "./generationLifecycle";
import { refreshChatBackgroundActivities } from "./chatBackgroundActivity";
import type { ChatQueueTask } from "../api/ai";

// 活跃任务 id 集合（queued/running）
export function activeTaskIds(tasks: ChatQueueTask[]): Set<string> {
  return new Set(
    tasks.filter((t) => t.status === "queued" || t.status === "running").map((t) => t.id),
  );
}

// 检测本次 refresh 中从活跃变为终态（完成/取消/失败）的任务 id。
// 返回非空即表示有队列消息落盘完成，调用方应刷新对话历史把 headless 回复刷出来。
// 仅当任务出现在本次列表且状态为终态才算 settled：避免列表截断/清理导致的误判。
export function settledTaskIds(prevActive: Set<string>, tasks: ChatQueueTask[]): string[] {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  return [...prevActive].filter((id) => {
    const t = byId.get(id);
    return t !== undefined && t.status !== "queued" && t.status !== "running";
  });
}

export function useChatAgentQueue(
  threadId: string,
  onSettledRef?: MutableRefObject<(() => void) | undefined>,
) {
  const [queued, setQueued] = useState<QueueItem[]>([]);
  const storageKey = `laf_chat_queue_content_${threadId}`;
  // 记录上一次 refresh 看到的活跃任务 id，检测到任务完成（从队列消失）时通知外部刷新对话
  const lastActiveRef = useRef<Set<string>>(new Set());
  // 本地已移除（编辑取回/删除）的任务 id：后端取消完成前，refresh 不得把它们重新放回队列
  const dismissedRef = useRef<Set<string>>(new Set());
  const fireSettled = () => {
    if (onSettledRef && onSettledRef.current) onSettledRef.current();
  };

  const readContent = (): Record<string, RichContent> => {
    try { return JSON.parse(localStorage.getItem(storageKey) || "{}"); } catch { return {}; }
  };
  const writeContent = (taskId: string, content?: RichContent) => {
    try {
      const values = readContent();
      if (content) values[taskId] = content;
      else delete values[taskId];
      localStorage.setItem(storageKey, JSON.stringify(values));
    } catch { /* UI can fall back to task text */ }
  };
  const refresh = async () => {
    if (threadId === "home") { setQueued([]); return; }
    try {
      const { tasks } = await listChatQueueTasks(threadId);
      const content = readContent();
      const activeIds = activeTaskIds(tasks);
      // 后端已不再返回已 dismiss 的任务（取消完成/被清理）时，从过滤集清除防无限累积
      if (dismissedRef.current.size > 0) {
        const stillListed = new Set(tasks.map((t) => t.id));
        for (const id of [...dismissedRef.current]) {
          if (!stillListed.has(id)) dismissedRef.current.delete(id);
        }
      }
      // 检测到有任务完成/取消：通知外部重新拉取对话（headless 执行的回复需要刷出来）
      const settled = settledTaskIds(lastActiveRef.current, tasks);
      lastActiveRef.current = activeIds;
      if (settled.length > 0 && activeIds.size > 0) {
        // 仍有活跃任务时（串行队列），先不刷新，等最后一个完成再整体刷新
      } else if (settled.length > 0) {
        fireSettled();
      }
      setQueued(tasks
        .filter((task) => (task.status === "queued" || task.status === "running") && !dismissedRef.current.has(task.id))
        .map((task) => {
          const saved = content[task.id];
          // 本地映射缺失时用后端 payload 摘要恢复（图片 URL 在后端也有备份）
          const fallback: RichContent = {
            parts: [],
            text: task.message || task.need || "",
            images: Array.isArray(task.images) ? task.images : [],
          };
          return {
            id: task.id,
            text: task.status === "running" ? `发送中…${task.need ? "：" + task.need : ""}` : task.need,
            content: saved || fallback,
          };
        }));
    } catch { /* retain current projection while backend is unavailable */ }
  };

  const enqueue = (content: RichContent, invocation: AgentInvocation) => {
    if (threadId === "home") return;
    // 先写本地内容映射再发请求：即使请求慢/失败，编辑回填也有完整图文
    // （图片 URL 只在本地映射里，后端 payload 只存 message 文本）。
    const localId = crypto.randomUUID();
    writeContent(localId, content);
    void enqueueChatQueueTask(invocation).then((response) => {
      if (response.task?.id) {
        // 以真实 task id 为准：迁移本地映射键，避免临时键残留
        const values = readContent();
        if (values[localId]) {
          delete values[localId];
          values[response.task.id] = content;
          try { localStorage.setItem(storageKey, JSON.stringify(values)); } catch { /* ignore */ }
        }
      }
      void refresh();
      refreshChatBackgroundActivities();
    }).catch(() => {
      // API 失败：清理乐观写入的本地映射，避免孤儿条目
      writeContent(localId);
    });
  };

  const remove = (id: string): QueueItem | undefined => {
    const item = queued.find((entry) => entry.id === id);
    writeContent(id);
    setQueued((current) => current.filter((entry) => entry.id !== id));
    void cancelChatQueueTask(id).catch(() => {});
    // 本地已移除的任务 id：后续 refresh 即使后端取消未完成也不得重新放回队列
    dismissedRef.current.add(id);
    void refresh();
    refreshChatBackgroundActivities();
    return item;
  };

  useEffect(() => {
    void refresh();
    if (threadId === "home") return;
    const timer = setInterval(() => { void refresh(); }, 2000);
    return () => clearInterval(timer);
  }, [threadId]); // eslint-disable-line react-hooks/exhaustive-deps

  return { queued, enqueue, remove, refresh };
}
