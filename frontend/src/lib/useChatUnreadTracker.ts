import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import type { ChatMessage } from "../types/chat";
import { appendUniqueMessageIds, changedAssistantMessageIds } from "./chatUnread";

// 稳定滚到底：立即 + 双 rAF（吸收懒加载图片/异步布局导致的高度变化）。
// 懒加载图片在滚到底后进入视口才触发加载，加载完成高度变大 → 靠 load 监听再跟一次。
function scrollStreamToBottom(stream: HTMLElement) {
  const setBottom = () => { stream.scrollTop = stream.scrollHeight; };
  setBottom();
  requestAnimationFrame(() => requestAnimationFrame(setBottom));
}

export function useChatUnreadTracker(
  threadId: string,
  messages: ChatMessage[],
  streamRef: MutableRefObject<HTMLDivElement | null>,
  atBottomRef: MutableRefObject<boolean>,
) {
  const versionsRef = useRef(new Map<string, string>());
  const trackedThreadRef = useRef<string | null>(null);
  const pendingIdsRef = useRef<string[]>([]);
  const [unreadIds, setUnreadIds] = useState<string[]>([]);

  const messageEndMarker = useCallback((id: string) => streamRef.current
    ?.querySelector<HTMLElement>(`[data-message-end="${CSS.escape(id)}"]`) || null, [streamRef]);

  const sync = useCallback(() => {
    const stream = streamRef.current;
    if (!stream) return;
    if (atBottomRef.current) {
      pendingIdsRef.current = [];
      setUnreadIds([]);
      return;
    }
    const viewportBottom = stream.getBoundingClientRect().bottom;
    const hidden = pendingIdsRef.current.filter((id) => {
      const marker = messageEndMarker(id);
      return marker && marker.getBoundingClientRect().top > viewportBottom + 1;
    });
    setUnreadIds((current) => current.length === hidden.length
      && current.every((id, index) => id === hidden[index]) ? current : hidden);
  }, [atBottomRef, messageEndMarker, streamRef]);

  const onScroll = useCallback(() => {
    const stream = streamRef.current;
    if (!stream) return;
    atBottomRef.current = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 80;
    if (atBottomRef.current) pendingIdsRef.current = [];
    else {
      const viewportBottom = stream.getBoundingClientRect().bottom;
      pendingIdsRef.current = pendingIdsRef.current.filter((id) => {
        const marker = messageEndMarker(id);
        return marker && marker.getBoundingClientRect().top > viewportBottom + 1;
      });
    }
    sync();
  }, [atBottomRef, messageEndMarker, streamRef, sync]);

  // 贴底时监听容器内媒体加载完成（img/video load 不冒泡，用捕获阶段），
  // 加载完成高度变大后自动跟随到底——解决进入会话时懒加载图片未加载导致停在中间。
  useEffect(() => {
    const stream = streamRef.current;
    if (!stream) return;
    const onMediaLoad = () => {
      if (atBottomRef.current) stream.scrollTop = stream.scrollHeight;
    };
    stream.addEventListener("load", onMediaLoad, true);
    return () => stream.removeEventListener("load", onMediaLoad, true);
  }, [streamRef, atBottomRef]);

  useEffect(() => {
    const stream = streamRef.current;
    if (!stream) return;
    const activity = changedAssistantMessageIds(versionsRef.current, messages);
    if (trackedThreadRef.current !== threadId) {
      trackedThreadRef.current = threadId;
      versionsRef.current = activity.versions;
      atBottomRef.current = true;
      pendingIdsRef.current = [];
      setUnreadIds([]);
      // 进入会话：稳定滚到底（历史消息异步加载完成后高度仍在变，靠 load 监听继续跟随）
      scrollStreamToBottom(stream);
      return;
    }
    versionsRef.current = activity.versions;
    if (atBottomRef.current) {
      scrollStreamToBottom(stream);
      pendingIdsRef.current = [];
      setUnreadIds([]);
      return;
    }
    pendingIdsRef.current = appendUniqueMessageIds(pendingIdsRef.current, activity.ids);
    requestAnimationFrame(sync);
  }, [atBottomRef, messages, streamRef, sync, threadId]);

  const jumpToFirst = useCallback(() => {
    const stream = streamRef.current;
    const id = unreadIds[0];
    if (!stream || !id) return;
    const anchor = stream.querySelector<HTMLElement>(`[data-message-id="${CSS.escape(id)}"]`);
    if (!anchor) return;
    pendingIdsRef.current = pendingIdsRef.current.filter((candidate) => candidate !== id);
    setUnreadIds((current) => current.filter((candidate) => candidate !== id));
    stream.scrollTo({ top: Math.max(0, anchor.offsetTop - 12), behavior: "smooth" });
  }, [streamRef, unreadIds]);

  return {
    unreadAgentIds: unreadIds,
    onStreamScroll: onScroll,
    syncUnreadAgentMessages: sync,
    jumpToFirstUnreadAgentMessage: jumpToFirst,
  };
}
