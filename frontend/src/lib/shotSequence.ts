import type { ChatMessage } from "../types/chat";

export interface ShotVideoSlot {
  slotId: string;
  shotIndex: number;
  status: "pending" | "ready" | "failed";
  url?: string;
  error?: string;
}

export interface ShotSequenceState {
  sequenceId: string;
  total: number;
  videos: ShotVideoSlot[];
  hasMerged: boolean;
}

/** 从一条 assistant 消息的槽位还原分镜序列状态（恢复/合并 barrier 共用纯函数）。 */
export function collectShotSequence(
  messages: readonly ChatMessage[], messageId: string,
): ShotSequenceState | null {
  const message = messages.find((item) => item.id === messageId);
  if (!message) return null;
  let sequenceId = "";
  let total = 0;
  let hasMerged = false;
  const videos: ShotVideoSlot[] = [];
  for (const part of message.parts || []) {
    if (!part.shotSequenceId) continue;
    if (part.merged) {
      hasMerged = true;
      continue;
    }
    if ((part.kind !== "video" && part.type !== "video") || part.shotIndex === undefined) continue;
    if (!sequenceId) sequenceId = part.shotSequenceId;
    if (part.shotSequenceId !== sequenceId) continue;
    total = Math.max(total, part.shotTotal || 0);
    videos.push({
      slotId: part.slotId || "",
      shotIndex: part.shotIndex,
      status: (part.status || "pending") as ShotVideoSlot["status"],
      ...(part.url ? { url: part.url } : {}),
      ...(part.error ? { error: part.error } : {}),
    });
  }
  if (!sequenceId || total <= 0) return null;
  return { sequenceId, total, videos, hasMerged };
}

/** 全部 shotIndex 0..total-1 各有一支 ready 视频，且没有 failed/pending。 */
export function canMergeShotSequence(state: ShotSequenceState | null): boolean {
  if (!state || state.hasMerged) return false;
  if (state.videos.length < state.total) return false;
  const byIndex = new Map<number, ShotVideoSlot>();
  for (const video of state.videos) {
    const previous = byIndex.get(video.shotIndex);
    if (previous && previous.status === "ready") continue;
    byIndex.set(video.shotIndex, video);
    if (video.status === "failed") return false;
    if (video.status !== "ready" || !video.url) return false;
  }
  for (let index = 0; index < state.total; index += 1) {
    if (!byIndex.has(index)) return false;
  }
  return true;
}

/** 成片顺序只认 shotIndex（正文序），绝不按完成序/槽位顺序。 */
export function orderedShotUrls(state: ShotSequenceState): string[] {
  return [...state.videos]
    .sort((a, b) => a.shotIndex - b.shotIndex)
    .map((video) => video.url || "");
}
