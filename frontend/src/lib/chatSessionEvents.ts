import type { FinalizedMessage } from "../api/comfyui";
import type { ChatStreamEvent, VideoParams } from "../api/chatStreamProtocol";
import type { ChatMessage, MsgPart, PromptApproval, RegenerationSnapshot, RouteChoice } from "../types/chat";

export function upsertMessages(current: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const next = [...current];
  for (const message of incoming) {
    const index = next.findIndex((item) => item.id === message.id);
    if (index >= 0) next[index] = { ...next[index], ...message };
    else next.push(message);
  }
  return next;
}

export function workflowMessages(messages: FinalizedMessage[]): ChatMessage[] {
  return messages.map((message) => ({ ...message }));
}

export function agentImageMessage(
  url: string,
  id?: string,
  regeneration?: RegenerationSnapshot,
): ChatMessage {
  return {
    id: id || crypto.randomUUID(),
    role: "assistant",
    text: "",
    image: url,
    ...(regeneration ? { regeneration } : {}),
  };
}

export function agentVideoMessage(url: string, id?: string): ChatMessage {
  return { id: id || crypto.randomUUID(), role: "assistant", text: "", video: url };
}

export function inspirationMessage(card: {
  id?: string;
  title: string;
  content: string;
  sources: { title: string; url: string }[];
  images?: Array<{ thumb_url: string; full_url: string; source_url: string; width?: number; height?: number; title?: string }>;
  selected?: string[];
}): ChatMessage {
  return {
    id: card.id || crypto.randomUUID(),
    role: "assistant",
    text: "",
    inspiration: {
      title: card.title,
      content: card.content,
      sources: card.sources || [],
      images: card.images || [],
      selected: card.selected || [],
    },
  };
}

export function applyPromptApproval(
  current: ChatMessage[],
  approval: PromptApproval,
): ChatMessage[] {
  return current.map((message) =>
    message.id === approval.messageId || message.promptApproval?.id === approval.id
      ? { ...message, promptApproval: approval }
      : message,
  );
}

export function applyRouteChoice(
  current: ChatMessage[],
  routeChoice: RouteChoice,
): ChatMessage[] {
  return current.map((message) =>
    message.id === routeChoice.messageId || message.routeChoice?.id === routeChoice.id
      ? { ...message, routeChoice }
      : message,
  );
}

function appendDelta(message: ChatMessage, text: string): ChatMessage {
  if (!message.parts?.length) return { ...message, text: message.text + text };
  const parts = [...message.parts];
  const tail = parts[parts.length - 1];
  if (tail?.type === "text") parts[parts.length - 1] = { ...tail, text: (tail.text || "") + text };
  else parts.push({ type: "text", text });
  return { ...message, text: message.text + text, parts };
}

function appendMediaSlot(
  message: ChatMessage, slotId: string, offset?: number,
  lastFrameDesc?: string, videoPrompt?: string, videoParams?: VideoParams,
  lastFrameUrl?: string,
): ChatMessage {
  const existing = message.parts || (message.text ? [{ type: "text" as const, text: message.text }] : []);
  if (existing.some((part) => part.slotId === slotId)) return message;
  const slot = { type: "media-slot" as const, slotId, status: "pending" as const,
    ...(lastFrameDesc ? { lastFrameDesc } : {}),
    ...(lastFrameUrl ? { lastFrameUrl } : {}),
    ...(videoPrompt ? { videoPrompt } : {}),
    ...(videoParams ? { videoParams } : {}) };
  if (typeof offset === "number" && !message.parts) {
    const index = Math.max(0, Math.min(message.text.length, Math.round(offset)));
    const before = message.text.slice(0, index);
    const after = message.text.slice(index);
    // 图片块固定从高潮画面句后的新行开始；保留原正文换行数量，不改变文本内容。
    const slotPrefix = before && !before.endsWith("\n") ? "\n" : "";
    const slotSuffix = after && !after.startsWith("\n") ? "\n" : "";
    return {
      ...message,
      parts: [
        ...(index ? [{ type: "text" as const, text: before + slotPrefix }] : []),
        slot,
        ...(index < message.text.length
          ? [{ type: "text" as const, text: slotSuffix + after }]
          : []),
      ],
    };
  }
  return {
    ...message,
    parts: [...existing, slot],
  };
}

/** 音频对白槽：末尾追加 pending 槽并保留正文（纯文本消息先转成 text part，避免正文被槽位顶掉）。 */
export function appendAudioSlot(
  current: ChatMessage[],
  messageId: string,
  slotId: string,
  speaker: string,
  seq: number,
  total: number,
): ChatMessage[] {
  return current.map((message) => {
    if (message.id !== messageId) return message;
    const existing = message.parts || (message.text ? [{ type: "text" as const, text: message.text }] : []);
    if (existing.some((part) => part.slotId === slotId)) return message;
    return {
      ...message,
      parts: [...existing, {
        type: "media-slot" as const, slotId, status: "pending" as const,
        kind: "audio" as const, speaker, seq, total,
      }],
    };
  });
}

export function resolveMediaSlot(
  current: ChatMessage[], messageId: string, slotId: string, url: string,
  mediaType: "image" | "video" | "audio" = "image", regeneration?: RegenerationSnapshot,
  generationId?: string,
): ChatMessage[] {
  return current.map((message) => message.id !== messageId ? message : {
    ...message,
    parts: (message.parts || []).map((part) =>
      (part.type === "media-slot" || part.type === "image" || part.type === "video" || part.type === "audio")
        && part.slotId === slotId
        ? {
          type: mediaType, url, slotId, status: "ready" as const,
          // 音频分条元数据（角色名/序号/媒体类型提示）随槽位保留，供气泡与画布楼层按角色分条展示
          ...(part.type === "media-slot" && part.kind === "audio" ? {
            kind: part.kind,
            ...(part.speaker ? { speaker: part.speaker } : {}),
            ...(part.seq !== undefined ? { seq: part.seq } : {}),
            ...(part.total !== undefined ? { total: part.total } : {}),
          } : {}),
          // V1.5/B1：视频槽尾帧描述保留，供下一楼层 resolvePrevTailDesc 反查衔接
          ...(part.lastFrameDesc ? { lastFrameDesc: part.lastFrameDesc } : {}),
          // V1.5/F3：尾帧图地址保留，供转场视频 image 输入反查（坑F）
          ...(part.lastFrameUrl ? { lastFrameUrl: part.lastFrameUrl } : {}),
          // V1.5 默认开放：climax 视频提示词随槽位保留（无视频模板/模型也展示，供测试核对）
          ...(part.videoPrompt ? { videoPrompt: part.videoPrompt } : {}),
          // V1.5 默认开放：结构化视频参数随槽位保留（供测试核对参数是否上传）
          ...(part.videoParams ? { videoParams: part.videoParams } : {}),
          ...(regeneration ? { regeneration } : {}),
          ...(generationId ? { generationId } : {}),
        }
        : part),
  });
}

export function bindMediaSlotPrompt(
  current: ChatMessage[], messageId: string, slotId: string, promptId: string,
): ChatMessage[] {
  return current.map((message) => message.id !== messageId ? message : {
    ...message,
    parts: (message.parts || []).map((part) =>
      part.type === "media-slot" && part.slotId === slotId ? { ...part, promptId } : part),
  });
}

export function dropMediaSlot(
  current: ChatMessage[], messageId: string, slotId: string,
): ChatMessage[] {
  return current.map((message) => {
    if (message.id !== messageId || !message.parts?.length) return message;
    const parts: MsgPart[] = [];
    for (const part of message.parts) {
      if (part.type === "media-slot" && part.slotId === slotId) continue;
      const previous = parts[parts.length - 1];
      if (part.type === "text" && previous?.type === "text") {
        previous.text = (previous.text || "") + (part.text || "");
      } else if (part.type !== "text" || part.text) {
        parts.push({ ...part });
      }
    }
    return { ...message, parts: parts.length ? parts : undefined };
  });
}

export function pruneUnsubmittedMediaSlots(current: ChatMessage[]): {
  messages: ChatMessage[];
  removed: { messageId: string; slotId: string }[];
} {
  const removed = current.flatMap((message) => (message.parts || [])
    .filter((part) => part.type === "media-slot" && part.status === "pending"
      && !part.promptId && !!part.slotId)
    .map((part) => ({ messageId: message.id, slotId: part.slotId! })));
  const messages = removed.reduce(
    (items, item) => dropMediaSlot(items, item.messageId, item.slotId), current,
  );
  return { messages, removed };
}

export function restoreSubmittedMediaSlots(
  current: ChatMessage[], pending: readonly {
    prompt_id: string;
    createdAt?: number;
    target?: { messageId: string; slotId: string; background?: true };
  }[],
): ChatMessage[] {
  return pending.reduce((messages, item) => item.target
    ? bindMediaSlotPrompt(
      messages, item.target.messageId, item.target.slotId, item.prompt_id,
    )
    : messages, current);
}

export function reduceChatStreamEvent(
  current: ChatMessage[],
  botId: string,
  event: ChatStreamEvent,
): ChatMessage[] {
  switch (event.type) {
    case "trace":
      return current.map((message) => message.id === botId
        ? appendDelta(message, `${event.text}\n`)
        : message);
    case "delta":
      return current.map((message) => message.id === botId
        ? appendDelta(message, event.text)
        : message);
    case "replace":
      return current.map((message) => message.id === botId
        ? { ...message, text: event.text, parts: undefined }
        : message);
    case "route":
      return current.map((message) => message.id === botId
        ? { ...message, route: event.route }
        : message);
    case "image":
      return upsertMessages(current, [agentImageMessage(event.url, event.id, event.regeneration)]);
    case "video":
      return upsertMessages(current, [agentVideoMessage(event.url, event.id)]);
    case "inspiration":
      return upsertMessages(current, [inspirationMessage(event.card)]);
    case "approval":
      return applyPromptApproval(current, event.approval);
    case "route_choice":
      return applyRouteChoice(current, event.choice);
    case "error":
      return current.map((message) => message.id === botId
        ? { ...message, text: message.text || `对话失败：${event.message}` }
        : message);
    case "illustrate_request":
      return current.map((message) => message.id === botId
        ? appendMediaSlot(message, event.id || crypto.randomUUID(), event.offset, event.lastFrameDesc, event.videoPrompt, event.videoParams, event.lastFrameUrl)
        : message);
    case "audio_request":
      // 音频对白配音不入气泡流：由 useChatSession 逐角色提交 IndexTTS，完成后聚合到剧情楼层。
      return current;
    case "rag_status":
      // RAG 创建状态不入气泡流：由 useChatSession 派发右下角轻提示。
      return current;
    case "interrupted":
      return current;
  }
}
