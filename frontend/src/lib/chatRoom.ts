// 聊天 / 群聊纯逻辑（2026-11-09）：线程键、成员解析、历史序列化、流事件投影。
// 与组件分离：群聊顺序/私聊取首位/历史带 speaker 都是可单测的合同，UI 只负责渲染。
import type { ChatRoomMessage } from "../api/chatStreamProtocol";
import type { ChatMessage } from "../types/chat";

export type ChatRoomMode = "chat" | "group_chat";

/**
 * 聊天头像替换的唯一判据（所有作品统一）：
 * 只有 `[AVATAR]`（角色认为有纪念意义的非常规装扮自拍）且开关开启才允许换头像；
 * 普通 `[SELFIE]` 永远不改，避免有纪念意义的头像被普通自拍顶掉。
 */
export function shouldUpdateChatAvatar(
  media: { avatar?: boolean } | null | undefined,
  enabled: boolean | undefined,
): boolean {
  return enabled !== false && media?.avatar === true;
}

/** 聊天区是否自动贴底：用户刚发消息，或本来就在底部；向上翻历史时不打断。 */
export function shouldStickChatToBottom(params: {
  lastRole?: ChatMessage["role"];
  atBottom: boolean;
}): boolean {
  return params.lastRole === "user" || params.atBottom;
}

/** 聊天/群聊会话线：私聊每个角色独立 `<repo>__chat__<角色段>`，群聊共用 `<repo>__group`。
 *  角色段保留可读名 + 短哈希（同名/截断不碰撞），后端 chat_snapshot 直接按 threadId 落盘。 */
export function chatRoomThreadId(
  repoId: string | undefined, mode: ChatRoomMode, speaker = "",
): string {
  const base = `${repoId || "home"}__${mode === "group_chat" ? "group" : "chat"}`;
  if (mode === "group_chat") return base;
  const segment = threadSegment(speaker);
  return segment ? `${base}__${segment}` : base;
}

function threadSegment(name: string): string {
  const raw = (name || "").trim();
  if (!raw) return "";
  const safe = raw
    .replace(/[\\/:*?"<>|\s]+/g, "_")
    .replace(/^[._]+|[._]+$/g, "")
    .slice(0, 24);
  let hash = 2166136261;
  for (const char of raw) {
    hash ^= char.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 16777619);
  }
  return `${safe || "char"}_${(hash >>> 0).toString(36).slice(0, 6)}`;
}

/** 头像条排序：只认各线程最后一条消息时间（新→旧），同值保持名册原顺序。
 *  打开/选中/未读状态不参与排序——避免进入私聊就把该角色顶到最前（2026-11-10 用户实锤）。 */
export function sortChatRoomMembers(
  names: readonly string[],
  activity: Readonly<Record<string, number>> = {},
): string[] {
  return names
    .map((name, index) => ({ name, index, at: activity[name] || 0 }))
    .sort((a, b) => b.at - a.at || a.index - b.index)
    .map((item) => item.name);
}

/** QQ 风格时间分隔：首条消息，或与上一条间隔 ≥10 分钟 / 跨天时显示。 */
export const CHAT_TIME_DIVIDER_SECONDS = 10 * 60;

function _chatDate(ts: number): Date {
  return new Date(ts * 1000);
}

export function shouldShowChatTimeDivider(ts?: number, previousTs?: number): boolean {
  if (typeof ts !== "number" || !Number.isFinite(ts) || ts <= 0) return false;
  if (typeof previousTs !== "number" || !Number.isFinite(previousTs) || previousTs <= 0) {
    return true;
  }
  if (ts - previousTs >= CHAT_TIME_DIVIDER_SECONDS) return true;
  return _chatDate(ts).toDateString() !== _chatDate(previousTs).toDateString();
}

/** 时间分隔文案：今天 `HH:mm`、昨天 `昨天 HH:mm`、更早 `MM-DD HH:mm`。 */
export function formatChatTimeDivider(
  ts?: number, now: number = Date.now() / 1000,
): string {
  if (typeof ts !== "number" || !Number.isFinite(ts) || ts <= 0) return "";
  const date = _chatDate(ts);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value: number) => String(value).padStart(2, "0");
  const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  const today = _chatDate(now);
  if (date.toDateString() === today.toDateString()) return clock;
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return `昨天 ${clock}`;
  if (date.getFullYear() === today.getFullYear()) {
    return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${clock}`;
  }
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}`
    + `-${pad(date.getDate())} ${clock}`;
}

/** 成员解析：显式勾选 ∩ 当前成员池（绑定卡或世界书名册）；无效/空则回退成员池首项。
 *  ⚠ 不能保留「不在成员池里的名字」：合集卡切换名册后，旧的开场卡名会把私聊锁在
 *  一个不存在的角色上（2026-11-10 实锤「堕落魔戒」被当成角色）。 */
export function resolveChatRoomMembers(
  mode: ChatRoomMode,
  cardNames: readonly string[],
  selected: readonly string[],
): string[] {
  const bound = [...new Set(cardNames.map((name) => name.trim()).filter(Boolean))];
  const picked = [...new Set(selected.map((name) => name.trim()).filter(Boolean))];
  const allowed = picked.filter((name) => bound.includes(name));
  const members = allowed.length > 0 ? allowed : bound;
  return mode === "chat" ? members.slice(0, 1) : members;
}

/** 默认勾选：私聊取开场卡/首卡；群聊全选。 */
export function defaultChatRoomSelection(
  mode: ChatRoomMode,
  cardNames: readonly string[],
  openingCardName = "",
): string[] {
  const bound = [...new Set(cardNames.map((name) => name.trim()).filter(Boolean))];
  if (mode === "group_chat") return bound;
  const opening = bound.includes(openingCardName) ? openingCardName : bound[0];
  return opening ? [opening] : [];
}

/** 入模历史：整段对话都进入上下文（不是剧情式单轮）；assistant 必须带 `【speaker】`
 *  前缀，否则群聊里分不清谁说过什么。 */
export function buildChatRoomHistory(
  messages: readonly ChatMessage[],
): { role: "user" | "assistant"; content: string }[] {
  const out: { role: "user" | "assistant"; content: string }[] = [];
  for (const message of messages) {
    if (message.role !== "user" && message.role !== "assistant") continue;
    // 「戳了戳你」是动作提示，不是角色说过的话，不进模型历史。
    if (message.poke) continue;
    // 旧角色消息里的整行括号动作不进模型历史，避免模型照着重演（用户原话不动）。
    const raw = (message.text || "").trim();
    const text = message.role === "assistant" ? sanitizeChatText(raw) : raw;
    if (!text) continue;
    out.push({
      role: message.role,
      content: message.role === "assistant" && message.speaker
        ? `【${message.speaker}】${text}`
        : text,
    });
  }
  return out;
}

/** 聊天正文清洗：媒体标记/动作控制行永不进气泡，多余空行压成一个空行。
 *
 * 2026-11-10 用户实锤：模型边流式边吐出 `[SELFIE: …]`，后端在整段生成完才拆分，
 * 期间前端已经把它显示出来了；切屏/截图时就会看到裸露的提示词。
 * 这里在展示层做兜底（完整标记 + 流式中未闭合的半个标记都剥掉），后端仍负责落盘。
 */
const _MEDIA_MARKER_RE = /\[(?:SELFIE|AVATAR|VIDEO|动作)\s*[:：][^\]]*\]/gi;
const _OPEN_MEDIA_MARKER_RE = /\[(?:SELFIE|AVATAR|VIDEO|动作)[^\]]*$/i;
// 整行括号动作（「（把手机重新举起来，对着镜头偏了偏头）」）不进聊天气泡：
// 后端 `chat_room._strip_stage_directions` 负责新消息的权威清洗，这里兜底历史快照/流式。
const _WHOLE_STAGE_DIRECTION_RE = /^\s*[（(][^（）()]{4,160}[）)]\s*$/;
// 纯媒体回复的占位文案 / 空回复兜底是聊天语义，不是动作描写，必须保留。
const _KEEP_PAREN_LINES = new Set(["（发来一张自拍）", "（发来一段视频）", "（无回复）"]);

function stripWholeLineStageDirections(text: string): string {
  const lines = text.split("\n");
  const kept = lines.filter((line) => {
    const trimmed = line.trim();
    if (_KEEP_PAREN_LINES.has(trimmed)) return true;
    return !_WHOLE_STAGE_DIRECTION_RE.test(trimmed);
  });
  return kept.length === lines.length ? text : kept.join("\n");
}

export function sanitizeChatText(text: string): string {
  const cleaned = stripWholeLineStageDirections(
    (text || "")
      .replace(_MEDIA_MARKER_RE, "")
      .replace(_OPEN_MEDIA_MARKER_RE, ""),
  )
    .replace(/\n{3,}/g, "\n\n")
    .replace(/[ \t]+\n/g, "\n");
  return cleaned.trim();
}

/**
 * 已处理的自发媒体请求（按线程持久化）：快照里的 media_request 只保留请求本身，
 * 前端打开聊天时补拍一次后必须记住，否则每次刷新都会重复烧一张图 / 重换头像。
 */
const _HANDLED_MEDIA_KEY = "laf_chat_media_handled";

export function loadHandledChatMedia(threadId: string): Set<string> {
  try {
    const raw = JSON.parse(
      localStorage.getItem(`${_HANDLED_MEDIA_KEY}:${threadId}`) || "[]",
    );
    return new Set(Array.isArray(raw) ? raw.filter((item) => typeof item === "string") : []);
  } catch {
    return new Set();
  }
}

export function markHandledChatMedia(threadId: string, messageId: string): void {
  if (!threadId || !messageId) return;
  try {
    const handled = loadHandledChatMedia(threadId);
    handled.add(messageId);
    localStorage.setItem(
      `${_HANDLED_MEDIA_KEY}:${threadId}`,
      JSON.stringify([...handled].slice(-200)),
    );
  } catch { /* 本地存储不可用时最多多拍一次，不影响聊天 */ }
}

/** 流事件 → ChatMessage；id 由后端生成时直接沿用（刷新前后同一身份）。 */
export function chatRoomMessagesToChat(
  messages: readonly ChatRoomMessage[],
  newId: () => string = () => crypto.randomUUID(),
): ChatMessage[] {
  return messages.map((message) => ({
    id: message.id || newId(),
    role: "assistant" as const,
    speaker: message.speaker,
    text: sanitizeChatText(message.text),
    ...(typeof message.ts === "number" && Number.isFinite(message.ts) && message.ts > 0
      ? { ts: message.ts }
      : {}),
    ...(message.poke ? { poke: true } : {}),
    ...(message.media_request ? { mediaRequest: message.media_request } : {}),
  })).filter((message) =>
    // 整条只有括号动作（清洗后为空）且没有媒体/戳一戳 ⇒ 不生成空气泡。
    !!message.text || !!message.mediaRequest || !!message.poke);
}

/**
 * 生成中的快照兜底合并（2026-11-10）：SSE 偶发丢帧/断流时，服务端快照仍会落最终消息。
 * 以快照顺序为准；同 id 优先保留本地更长的文本（避免半成品快照把已流式显示的内容截短）；
 * 本地独有消息（用户刚发、流式气泡）保留在末尾，不因快照暂未落盘而消失。
 */
export function mergeSnapshotMessages(
  current: readonly ChatMessage[],
  snapshot: readonly ChatMessage[],
): ChatMessage[] {
  const snapshotIds = new Set(snapshot.map((message) => message.id));
  const currentById = new Map(current.map((message) => [message.id, message]));
  const merged = snapshot.map((message) => {
    const local = currentById.get(message.id);
    const chosen = local && (local.text?.length || 0) > (message.text?.length || 0)
      ? local
      : message;
    // 旧快照没有 ts：保留本地流式气泡的时间，避免收尾合并后时间标签消失。
    const ts = message.ts ?? local?.ts;
    return ts && chosen.ts !== ts ? { ...chosen, ts } : chosen;
  });
  return [...merged, ...current.filter((message) => !snapshotIds.has(message.id))];
}

/** 按 id upsert（同一批事件重复到达不产生重复气泡）。 */
export function mergeChatRoomMessages(
  current: readonly ChatMessage[],
  incoming: readonly ChatMessage[],
): ChatMessage[] {
  const next = [...current];
  for (const message of incoming) {
    const index = next.findIndex((item) => item.id === message.id);
    if (index >= 0) next[index] = { ...next[index], ...message };
    else next.push(message);
  }
  return next;
}

/** 私聊上下文压力（2026-11-10）：用户口径「对话堆叠要有压缩提醒」。
 *  只统计真正会进模型的文本（system 提示与戳一戳不算）；达到阈值时聊天室顶部提醒，
 *  full 时更强烈建议压缩。压缩会把原文归档，并把当前会话换成一条【历史摘要】。 */
export const CHAT_CONTEXT_WARN_CHARS = 12000;
export const CHAT_CONTEXT_WARN_COUNT = 40;
export const CHAT_CONTEXT_FULL_CHARS = 24000;
export const CHAT_CONTEXT_FULL_COUNT = 80;

export type ChatContextLevel = "ok" | "warn" | "full";

export interface ChatContextPressure {
  chars: number;
  count: number;
  level: ChatContextLevel;
}

/** 估算聊天室上下文压力：字符数 + 条数取较高等级，避免短消息刷屏或长文堆积被漏判。
 *  `maxTokens` 为设置里的历史上下文预算；>0 时阈值按 60%/90% 动态算，
 *  预算调大后不会在 12k/24k 字就过早提醒。0=沿用旧固定阈值。
 *  `compactTriggerPct` 是服务端自动摘要的触发百分比（默认 80）：只统计最新一段
 *  「服务端还会原样保留」的尾部，更早的消息已被滚动摘要/机械裁剪，不再计入压力，
 *  否则几十万字的旧记录会永远报「上下文已满」。 */
export function chatContextPressure(
  messages: readonly ChatMessage[],
  maxTokens = 0,
  compactTriggerPct = 80,
): ChatContextPressure {
  const warnChars = maxTokens > 0
    ? Math.max(4_000, Math.round(maxTokens * 0.6))
    : CHAT_CONTEXT_WARN_CHARS;
  const fullChars = maxTokens > 0
    ? Math.max(warnChars + 1_000, Math.round(maxTokens * 0.9))
    : CHAT_CONTEXT_FULL_CHARS;
  const warnCount = maxTokens > 0
    ? Math.max(CHAT_CONTEXT_WARN_COUNT, Math.round(maxTokens / 200))
    : CHAT_CONTEXT_WARN_COUNT;
  const fullCount = maxTokens > 0 ? warnCount * 2 : CHAT_CONTEXT_FULL_COUNT;
  const trigger = Number.isFinite(compactTriggerPct) && compactTriggerPct > 0
    ? compactTriggerPct
    : 100;
  const tailCap = maxTokens > 0
    ? Math.max(1_000, Math.round(maxTokens * trigger / 100))
    : CHAT_CONTEXT_FULL_CHARS;
  let chars = 0;
  let count = 0;
  // 从最新消息向前累计：到自动摘要触发线就停，旧消息不计入压力。
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.system || message.poke) continue;
    const text = (message.text || "").trim();
    if (!text) continue;
    chars += text.length;
    count += 1;
    if (chars >= tailCap) break;
  }
  const level: ChatContextLevel =
    chars >= fullChars || count >= fullCount
      ? "full"
      : chars >= warnChars || count >= warnCount
        ? "warn"
        : "ok";
  return { chars, count, level };
}
