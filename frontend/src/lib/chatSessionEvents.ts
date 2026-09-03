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

/** 执行过程行上限：重试循环等极端场景下防内存膨胀（超出丢最旧）。 */
const AGENT_TRACE_MAX = 400;

function appendAgentTrace(message: ChatMessage, line: string): ChatMessage {
  const lines = [...(message.agentTrace || []), line];
  if (lines.length > AGENT_TRACE_MAX) lines.splice(0, lines.length - AGENT_TRACE_MAX);
  return { ...message, agentTrace: lines };
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
  if (typeof offset === "number") {
    const index = Math.max(0, Math.min(message.text.length, Math.round(offset)));
    // parts 已存在（多插画/图文混排）时同样按 offset 定位插入，保留已有槽与顺序：
    // 遍历文本段累计长度，把新槽切进 offset 落在的那一段；offset 超出文本才 append。
    const parts: MsgPart[] = [];
    let consumed = 0;
    let inserted = false;
    for (const part of existing) {
      if (part.type === "text") {
        const start = consumed;
        const end = consumed + (part.text || "").length;
        if (!inserted && index >= start && index <= end) {
          const cut = index - start;
          const before = (part.text || "").slice(0, cut);
          const after = (part.text || "").slice(cut);
          // 图片块固定从高潮画面句后的新行开始；保留原正文换行数量，不改变文本内容。
          const slotPrefix = before && !before.endsWith("\n") ? "\n" : "";
          const slotSuffix = after && !after.startsWith("\n") ? "\n" : "";
          if (before) parts.push({ ...part, text: before + slotPrefix });
          parts.push(slot);
          if (after) parts.push({ ...part, text: slotSuffix + after });
          inserted = true;
        } else {
          parts.push(part);
        }
        consumed = end;
      } else {
        parts.push(part);
      }
    }
    if (!inserted) parts.push(slot);
    return { ...message, parts };
  }
  return {
    ...message,
    parts: [...existing, slot],
  };
}

export function appendTransitionSlot(
  current: ChatMessage[], messageId: string, slotId: string,
  videoPrompt?: string, videoParams?: VideoParams,
): ChatMessage[] {
  return current.map((message) => {
    if (message.id !== messageId) return message;
    const existing = message.parts || (message.text ? [{ type: "text" as const, text: message.text }] : []);
    if (existing.some((part) => part.slotId === slotId)) return message;
    return {
      ...message,
      parts: [...existing, {
        type: "media-slot" as const, slotId, status: "pending" as const, kind: "video" as const,
        ...(videoPrompt ? { videoPrompt } : {}),
        ...(videoParams ? { videoParams } : {}),
      }],
    };
  });
}

/** 首尾帧副槽（V1.6/P5+ 独立图片模式）：末尾追加 pending 图片槽并保留正文。 */
export function appendImageSlot(
  current: ChatMessage[], messageId: string, slotId: string, prompt?: string,
): ChatMessage[] {
  return current.map((message) => {
    if (message.id !== messageId) return message;
    const existing = message.parts || (message.text ? [{ type: "text" as const, text: message.text }] : []);
    if (existing.some((part) => part.slotId === slotId)) return message;
    return {
      ...message,
      parts: [...existing, {
        type: "media-slot" as const, slotId, status: "pending" as const, kind: "image" as const,
        ...(prompt ? { videoPrompt: prompt } : {}),
      }],
    };
  });
}

/**
 * 插画槽失败标记（2026-08-29 用户需求）：不再删除槽位，而是保留为 failed 态并附错误信息
 * 与重试参数快照——楼层上显示失败原因与「重新生成」按钮。
 */
export function markMediaSlotFailed(
  current: ChatMessage[], messageId: string, slotId: string,
  stage: string, error: string, retryArgs?: unknown[],
): ChatMessage[] {
  return current.map((message) => {
    if (message.id !== messageId) return message;
    const parts = message.parts || (message.text ? [{ type: "text" as const, text: message.text }] : []);
    const index = parts.findIndex((part) => part.slotId === slotId);
    if (index === -1) return message;
    const next = [...parts];
    next[index] = {
      ...next[index],
      status: "failed" as const,
      kind: (next[index].kind ?? "image") as "image" | "audio" | "video",
      error: `[${stage}] ${error}`,
      ...(retryArgs?.length ? { retryArgs } : {}),
    };
    return { ...message, parts: next };
  });
}

/**
 * 失败槽「重新生成」即时反馈（2026-08-30 用户反馈「点了没反应」实锤）：
 * 重试链路要走帧编译+提交（秒级），期间槽位必须先回到 pending 并清掉旧错误，
 * 否则槽位停在 failed、错误文字逐字不变（如 ComfyUI 未启动时反复重试），用户无从感知重试已发生。
 * retryArgs 保留在槽位上：重试再次失败时 markMediaSlotFailed 会带新错误+原快照，按钮仍在。
 */
export function resetMediaSlotForRetry(
  current: ChatMessage[], messageId: string, slotId: string,
): ChatMessage[] {
  return current.map((message) => {
    if (message.id !== messageId) return message;
    return {
      ...message,
      parts: (message.parts || []).map((part) =>
        part.type === "media-slot" && part.slotId === slotId && part.status === "failed"
          ? { ...part, status: "pending" as const, error: undefined }
          : part),
    };
  });
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
  return current.map((message) => {
    if (message.id !== messageId) return message;
    const base = message.parts || (message.text ? [{ type: "text" as const, text: message.text }] : []);
    const parts = [...base];
    const index = parts.findIndex((part) =>
      (part.type === "media-slot" || part.type === "image" || part.type === "video" || part.type === "audio")
        && part.slotId === slotId);
    const part = index >= 0 ? parts[index] : undefined;
    const resolved = {
      type: mediaType, url, slotId, status: "ready" as const,
      // 音频分条元数据（角色名/序号/媒体类型提示）随槽位保留，供气泡与画布楼层按角色分条展示
      ...(part?.type === "media-slot" && part.kind === "audio" ? {
        kind: part.kind,
        ...(part.speaker ? { speaker: part.speaker } : {}),
        ...(part.seq !== undefined ? { seq: part.seq } : {}),
        ...(part.total !== undefined ? { total: part.total } : {}),
      } : {}),
      // V1.5/B1：视频槽尾帧描述保留，供下一楼层 resolvePrevTailDesc 反查衔接
      ...(part?.lastFrameDesc ? { lastFrameDesc: part.lastFrameDesc } : {}),
      // V1.5/F3：尾帧图地址保留，供转场视频 image 输入反查（坑F）
      ...(part?.lastFrameUrl ? { lastFrameUrl: part.lastFrameUrl } : {}),
      // V1.5 默认开放：climax 视频提示词随槽位保留（无视频模板/模型也展示，供测试核对）
      ...(part?.videoPrompt ? { videoPrompt: part.videoPrompt } : {}),
      // V1.5 默认开放：结构化视频参数随槽位保留（供测试核对参数是否上传）
      ...(part?.videoParams ? { videoParams: part.videoParams } : {}),
      ...(regeneration ? { regeneration } : {}),
      ...(generationId ? { generationId } : {}),
    };
    if (index >= 0) parts[index] = resolved;
    else parts.push(resolved);  // 槽意外丢失也补插，绝不让已生成的图成为孤儿
    return { ...message, parts };
  });
}

export function bindMediaSlotPrompt(
  current: ChatMessage[], messageId: string, slotId: string, promptId: string,
): ChatMessage[] {
  return current.map((message) => {
    if (message.id !== messageId) return message;
    const parts = [...(message.parts || (message.text ? [{ type: "text" as const, text: message.text }] : []))];
    const index = parts.findIndex((part) => part.type === "media-slot" && part.slotId === slotId);
    if (index >= 0) {
      parts[index] = { ...parts[index], promptId };
    } else {
      // 槽意外丢失时补建 pending 槽（占位卡片），等 pollResult 完成后 resolveMediaSlot
      // 补插结果——2026-08-31 深夜实锤：槽丢失导致生成图成孤儿不插入对话。
      parts.push({ type: "media-slot", slotId, status: "pending", kind: "image", promptId });
    }
    return { ...message, parts };
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

export function failMediaSlot(
  current: ChatMessage[], messageId: string, slotId: string, error: string,
): ChatMessage[] {
  return current.map((message) => message.id !== messageId ? message : {
    ...message,
    parts: (message.parts || []).map((part) =>
      part.type === "media-slot" && part.slotId === slotId
        ? { ...part, status: "failed" as const, error, promptId: undefined }
        : part),
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
      // 非流式 agent 过程行（智能编造/计划编译思考、工具、步骤、重试）进独立
      // 「执行过程」面板，不污染最终正文；replace 后保留，供完成后回看（2026-09-03）。
      return current.map((message) => message.id === botId
        ? appendAgentTrace(message, event.text)
        : message);
    case "delta":
      return current.map((message) => message.id === botId
        ? appendDelta(message, event.text)
        : message);
    case "thinking":
      // 思考全公开（2026-08-31 晚）：think 增量进 thinking 字段（思考面板展示），
      // 正文 text 不受影响；replace 时保留，供完成后回看。
      return current.map((message) => message.id === botId
        ? { ...message, thinking: `${message.thinking ?? ""}${event.text}` }
        : message);
    case "replace":
      // 成功完整生成 → 清除流式思考面板（2026-08-31 深夜用户定案：成功就不留
      // 思考过程，失败才保留供诊断）。最终正文里的 <think> 由渲染层隐藏。
      // agentTrace（执行过程）刻意保留：非流式 agent 完成后仍可回看（2026-09-03）。
      return current.map((message) => message.id === botId
        ? { ...message, text: event.text, parts: undefined, thinking: undefined }
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
