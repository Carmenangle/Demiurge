// 聊天室未读（2026-11-09）：私聊/群聊各自一条线程，按发言人记录未读数。
// 只做本机提示：进入聊天室并看到底部后清空；切走时若还在生成，先记一条未读。
const KEY = "laf_chatroom_unread";

export interface ChatRoomUnread {
  total: number;
  speakers: Record<string, number>;
}

const EMPTY: ChatRoomUnread = { total: 0, speakers: {} };
const listeners = new Set<() => void>();

function load(): Record<string, ChatRoomUnread> {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "{}");
    if (!raw || typeof raw !== "object") return {};
    const out: Record<string, ChatRoomUnread> = {};
    for (const [threadId, value] of Object.entries(raw as Record<string, unknown>)) {
      if (!value || typeof value !== "object") continue;
      const item = value as Record<string, unknown>;
      const total = Number(item.total);
      if (!Number.isFinite(total) || total <= 0) continue;
      const speakers: Record<string, number> = {};
      if (item.speakers && typeof item.speakers === "object") {
        for (const [name, count] of Object.entries(item.speakers as Record<string, unknown>)) {
          const n = Number(count);
          if (name && Number.isFinite(n) && n > 0) speakers[name] = n;
        }
      }
      out[threadId] = { total, speakers };
    }
    return out;
  } catch {
    return {};
  }
}

let state: Record<string, ChatRoomUnread> = load();

function persist() {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* 本机提示，写失败不阻断 */ }
}

function notify() {
  for (const listener of listeners) listener();
}

export function getUnread(threadId: string): ChatRoomUnread {
  return state[threadId] || EMPTY;
}

export function addUnread(threadId: string, speaker: string, count = 1) {
  if (!threadId || count <= 0) return;
  const current = state[threadId] || EMPTY;
  const name = (speaker || "").trim();
  const speakers = { ...current.speakers };
  if (name) speakers[name] = (speakers[name] || 0) + count;
  state = { ...state, [threadId]: { total: current.total + count, speakers } };
  persist();
  notify();
}

export function clearUnread(threadId: string) {
  if (!threadId || !state[threadId]) return;
  const next = { ...state };
  delete next[threadId];
  state = next;
  persist();
  notify();
}

export function clearSpeakerUnread(threadId: string, speaker: string) {
  const current = state[threadId];
  if (!current) return;
  const name = (speaker || "").trim();
  if (!name || !current.speakers[name]) return;
  const speakers = { ...current.speakers };
  delete speakers[name];
  const total = Math.max(0, current.total - (current.speakers[name] || 0));
  const next = { ...state };
  if (total <= 0) delete next[threadId];
  else next[threadId] = { total, speakers };
  state = next;
  persist();
  notify();
}

export function subscribeUnread(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
