import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import type { ChatMessage } from "../types/chat";
import { appendUniqueMessageIds, changedAssistantMessageIds } from "./chatUnread";

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
      return;
    }
    versionsRef.current = activity.versions;
    if (atBottomRef.current) {
      stream.scrollTop = stream.scrollHeight;
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
