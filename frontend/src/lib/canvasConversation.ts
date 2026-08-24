// canvasConversation.ts — 画布内容 = 当前对话内容的投影（纯逻辑，无 React/IO，可单测）
//
// 需求契约：
//   1. 画布只显示「当前对话」里的实际产出（图/视频）+ 对应提示词；历史生成不进画布。
//   2. 历史生成需要用户主动「发送至对话」，才会进入对话、进而出现在画布。
//
// 实现：从 ChatMessage 列表提取媒体 URL 集合，再用它过滤 generation_store 的记录。
// generation 的 image_url 与消息里的 image/parts.url/media-slot 都走 local-view，路径一致可匹配。

import type { ChatMessage, MsgPart } from "../types/chat";
import type { GenLike } from "./canvasRuntime";

/** 归一化媒体 URL：local-view 取 path 参数，其余去掉 query。用于跨消息/生成记录比对。 */
export function normalizeMediaUrl(url: string): string {
  const u = (url || "").trim();
  if (!u) return "";
  try {
    const parsed = new URL(u, "http://local");
    const path = parsed.searchParams.get("path");
    if (path) return `path:${path}`;
    return `url:${parsed.pathname || u.split("?")[0]}`;
  } catch {
    return `url:${u.split("?")[0]}`;
  }
}

/** 从消息列表提取所有媒体 URL（image/video/audio/parts 里的 url/image/video）。 */
export function conversationMediaUrls(messages: ChatMessage[]): Set<string> {
  const urls = new Set<string>();
  for (const m of messages) {
    if (m.image) urls.add(normalizeMediaUrl(m.image));
    if (m.video) urls.add(normalizeMediaUrl(m.video));
    if (m.audio) urls.add(normalizeMediaUrl(m.audio));
    for (const part of m.parts || []) {
      if (part.url) urls.add(normalizeMediaUrl(part.url));
      if (part.image) urls.add(normalizeMediaUrl(part.image));
    }
  }
  return urls;
}

/** 从后端持久化 history（/ai/chat/history 的 items）提取媒体 URL。
 *  重进画布时内存 messages 可能尚未加载完，此来源保证「对话实际内容自动导入」不依赖前端状态。 */
export function conversationTurnUrls(turns: ReadonlyArray<{ images?: readonly string[] }>): Set<string> {
  const urls = new Set<string>();
  for (const t of turns) {
    for (const img of t.images || []) {
      if (img) urls.add(normalizeMediaUrl(img));
    }
  }
  return urls;
}

/**
 * 过滤生成记录：只保留其媒体 URL 出现在当前对话里的。
 * 对话为空时返回空（新作品/未绑定 = 画布为空，符合「没绑定则没有」）。
 */
export function filterGensByConversation<T extends GenLike>(gens: T[], urls: Set<string>): T[] {
  if (urls.size === 0) return [];
  return gens.filter((g) => {
    const candidates = [g.image_url, g.video_url, g.audio_url].filter((u): u is string => !!u);
    return candidates.some((u) => urls.has(normalizeMediaUrl(u)));
  });
}

// ===== 工作流模板节点投影（画布实时读取对话内容） =====
// 用户拍板：工作流模板节点与生成内容节点同机制——从对话消息实时投影，重启后
// 对话历史恢复 → 节点自动出现，不依赖一次性事件或 canvas.json 持久化。

/** 从对话消息投影工作流模板节点（同 templateId 只保留一张，用户拍板规则）。 */
export function projectWorkflowTools(messages: ChatMessage[]): Array<{
  id: string;             // wftool-<templateId> 稳定 id（重启后布局可对上）
  templateId: string;
  templateName: string;
  wfConfirmed: boolean;
  wfDraft: unknown;
  wfCaptured: unknown;
}> {
  const byTemplate = new Map<string, ChatMessage>();
  for (const m of messages) {
    if (!m.workflow) continue;
    // 同模板多张卡只保留一张（用户拍板：创建多个相同模板也还是那一种模板的节点）
    byTemplate.set(m.workflow.templateId, m);
  }
  return [...byTemplate.entries()].map(([templateId, m]) => ({
    id: `wftool-${templateId}`,
    templateId,
    templateName: m.workflow!.templateName || "工作流模板",
    wfConfirmed: !!m.workflow!.done,
    wfDraft: m.workflow!.draftGraph,
    wfCaptured: m.workflow!.capturedGraph,
  }));
}

/** 音频分条：角色名 + URL（按台词顺序，画布剧情楼层逐条播放） */
export interface AudioLineMedia {
  speaker: string;
  url: string;
}

/** 从消息提取媒体（封面图 / 视频 / 音频）。优先顶层字段，其次 parts 里的 image/video/audio 片段。 */
export function messageMedia(m: ChatMessage): { image: string; video: string; audio: string; audioLines: AudioLineMedia[] } {
  const audioLines: AudioLineMedia[] = (m.parts || [])
    .filter((p): p is MsgPart & { type: "audio"; url: string } => p.type === "audio" && !!p.url)
    .map((p) => ({ speaker: p.speaker || "", url: p.url }));
  const image = m.image || (m.parts || []).find((p) => p.type === "image" && p.url)?.url || "";
  const video = m.video || (m.parts || []).find((p) => p.type === "video" && p.url)?.url || "";
  const audio = m.audio || audioLines[0]?.url || "";
  return { image, video, audio, audioLines };
}

/**
 * 剧情节点判定（剧情专家标签 allowlist，不再靠文本启发式）：
 * - 只有调度主管分派的剧情路由（roleplay / answer，roleplay 内部再串
 *   world/recall/curator/judge 等剧情 Agent）产出的消息才是剧情楼层
 * - 生图/视频/反推/灵感/工具等其它路由、顶层媒体气泡（工作流/Agent/API
 *   产出的图/视频/音频，带提示词文本）、状态/Toast（system）、各类卡一律不是
 * - 无标签（route 为空）的旧消息无法确认是剧情产出 → 默认不是剧情楼层；
 *   旧快照由后端一次性迁移回填 route/system 标签（backfill_story_tags），
 *   之后所有消息都走标签判定，不再依赖任何内容猜测
 */
export function isStoryNode(m: ChatMessage): boolean {
  if (m.role !== "assistant") return false;
  if (!(m.text || "").trim()) return false;
  if (m.system) return false;
  if (m.workflow || m.inspiration || m.portsPlan || m.promptApproval || m.routeChoice) return false;
  // 顶层媒体 = 生成内容气泡（工作流/Agent/API 产出，带提示词文本），不是剧情楼层；
  // 自动插画走 parts 媒体槽，不影响剧情节点封面
  if (m.image || m.video || m.audio) return false;
  // 剧情专家标签：只有调度主管分派的剧情路由才是剧情楼层
  if (m.route) return m.route === "roleplay" || m.route === "answer";
  // 无标签：无法确认是剧情产出，默认不是剧情楼层
  return false;
}

/** 从对话消息投影剧情节点：每个楼层一条剧情文本消息 → 一个节点。 */
export function projectStoryNodes(messages: ChatMessage[]): Array<{
  id: string;        // story-<messageId> 稳定 id（重启后布局可对上）
  messageId: string;
  text: string;      // 原始正文（渲染时跑显示层正则）
  thinking?: string; // 思考块（详情面板展示，与对话模式 think 同源）
  image: string;     // 封面图（剧情自动插画；无则空，卡片降级 9:16 纯文本）
  video: string;     // 视频（有则节点左侧展示，支持全屏）
  audio: string;     // 音频（有则节点左侧展示；多分条时取第一条作封面）
  audioLines: AudioLineMedia[]; // 音频分条（角色名 + URL，按台词顺序，逐条播放）
  /** 剧情顺序（0-based，按消息数组顺序 = 剧情顺序）：画布序号徽章 / 自动时序线的真源 */
  index: number;
  /** 当前剧情楼层总数 */
  total: number;
}> {
  const out: Array<{ id: string; messageId: string; text: string; thinking?: string; image: string; video: string; audio: string; audioLines: AudioLineMedia[] }> = [];
  for (const m of messages) {
    if (!isStoryNode(m)) continue;
    const media = messageMedia(m);
    out.push({
      id: `story-${m.id}`,
      messageId: m.id,
      text: m.text || "",
      thinking: m.thinking,
      image: media.image,
      video: media.video,
      audio: media.audio,
      audioLines: media.audioLines,
    });
  }
  const total = out.length;
  return out.map((s, i) => ({ ...s, index: i, total }));
}

// ===== 灵感卡绑定过滤（画布初始状态只显示仓库绑定的角色卡/世界书/预设） =====

/** 灵感卡最小形状（canvasLayout.InspirationCardStored 的绑定相关字段） */
export interface InspCardRef {
  kind: string;
  sourceRef?: string;
}

/**
 * 判断一张灵感卡是否属于当前仓库的绑定集合。
 * - sourceRef 形如 `char:NAME` / `wb:BOOK:index` / `preset:NAME:index`
 * - 无 sourceRef（用户拖放/手建）视为「本仓库自有内容」，保留。
 */
export function isBoundInspirationCard(
  card: InspCardRef,
  boundCards: Set<string>,
  boundWorldbook: string,
  boundPreset: string,
): boolean {
  const ref = (card.sourceRef || "").trim();
  if (!ref) return true; // 用户自建/拖放素材，不参与绑定过滤
  if (ref.startsWith("char:")) {
    return boundCards.has(ref.slice("char:".length).trim());
  }
  if (ref.startsWith("wb:")) {
    const book = ref.slice("wb:".length).split(":")[0];
    return boundWorldbook !== "" && book === boundWorldbook;
  }
  if (ref.startsWith("preset:")) {
    const name = ref.slice("preset:".length).split(":")[0];
    return boundPreset !== "" && name === boundPreset;
  }
  return true; // 未知来源，保守保留
}

/** 过滤掉不属于当前仓库绑定集合的灵感卡（历史「全量导入」的迁移清理）。 */
export function pruneUnboundInspirationCards<T extends InspCardRef>(
  cards: T[],
  boundCards: Set<string>,
  boundWorldbook: string,
  boundPreset: string,
): T[] {
  return cards.filter((c) => isBoundInspirationCard(c, boundCards, boundWorldbook, boundPreset));
}
