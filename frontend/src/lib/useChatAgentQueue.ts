import { useEffect, useState } from "react";
import type { RichContent } from "../components/RichInput";
import {
  cancelChatQueueTask, enqueueChatQueueTask, listChatQueueTasks,
  type AgentInvocation,
} from "../api/ai";
import type { QueueItem } from "./generationLifecycle";
import { refreshChatBackgroundActivities } from "./chatBackgroundActivity";

export function useChatAgentQueue(threadId: string) {
  const [queued, setQueued] = useState<QueueItem[]>([]);
  const storageKey = `laf_chat_queue_content_${threadId}`;

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
      setQueued(tasks
        .filter((task) => task.status === "queued" || task.status === "running")
        .map((task) => ({
          id: task.id,
          text: task.status === "running" ? `发送中…${task.need ? "：" + task.need : ""}` : task.need,
          content: content[task.id] || { parts: [], text: task.need, images: [] },
        })));
    } catch { /* retain current projection while backend is unavailable */ }
  };

  const enqueue = (content: RichContent, invocation: AgentInvocation) => {
    if (threadId === "home") return;
    void enqueueChatQueueTask(invocation).then((response) => {
      if (response.task?.id) writeContent(response.task.id, content);
      void refresh();
      refreshChatBackgroundActivities();
    }).catch(() => {});
  };

  const remove = (id: string): QueueItem | undefined => {
    const item = queued.find((entry) => entry.id === id);
    writeContent(id);
    setQueued((current) => current.filter((entry) => entry.id !== id));
    void cancelChatQueueTask(id).catch(() => {});
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
