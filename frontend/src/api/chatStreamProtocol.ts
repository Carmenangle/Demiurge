import type { ArtifactMeta, MessageRoute, PromptApproval, RegenerationSnapshot, RouteChoice } from "../types/chat";

export const CHAT_STREAM_PROTOCOL = "laf-chat-stream" as const;
export const CHAT_STREAM_VERSION = 1 as const;

export interface InspirationImage {
  thumb_url: string;
  full_url: string;
  source_url: string;
  width?: number;
  height?: number;
  title?: string;
}

export interface StreamInspirationCard {
  id?: string;
  title: string;
  content: string;
  sources: { title: string; url: string }[];
  images?: InspirationImage[];
  selected?: string[];
}

export interface IllustrationSceneSpec {
  narrative: string;
  protected_narrative?: string;
  repo_id?: string;
  thread_id?: string;
  turn_id?: string;
  draft_prompt: string;
  appearance?: string;
  wardrobe: string;
  locale: string;
  actors: string[];
  subjects?: Array<{ name: string; description?: string; weight?: number }>;
  rating: "sfw" | "nsfw";
  character_lora?: boolean;
  profile?: string;
  profile_prompt?: string;
  negative_prompt?: string;
  aspect_ratio?: "1:1" | "2:3" | "3:2" | "3:4" | "4:3" | "9:16" | "16:9";
  camera?: string;
  composition?: string;
}

export interface AudioDialogueLine {
  speaker: string;
  text: string;
  emotion?: Record<string, number>;
}

/** 第 99 刀：对白时间轴（firstlast 正片；拼接音频只作参考，台词按拍窗口重说） */
export interface VideoDialogueItem {
  start: number;
  end: number;
  label: string;
  speaker: string;
  speaker_id: string;
  text: string;
  /** true=台词在正文引语里逐字命中；false=按顺序兜底铺到空余镜头窗口 */
  exact: boolean;
}

export interface VideoDialogue {
  ref_label: string;
  speakers: Record<string, string>;
  timeline: VideoDialogueItem[];
  /** 可直接追加进视频提示词的渲染块（只有模板暴露参考音频槽时才追加） */
  text: string;
  warnings: string[];
  line_count: number;
}

/** V1.5 默认开放：结构化视频参数（后端 dry-run 组装结果，供测试核对参数是否上传） */
export interface VideoParams {
  mode: "climax" | "firstlast" | "transition" | "shot_cut" | "shot_move" | "shot_move_single";
  model: string;
  size: string;
  /** 剧情判定的秒数（0/缺省=未判定，工作流保留模板默认） */
  duration?: number;
  endpoint: string;
  images: string[];
  reference_binding: Record<string, string>;
  warnings: string[];
}

/** 聊天/群聊：一个角色的单条回复（id 由后端生成，前端按 id upsert）。 */
export interface ChatRoomMediaRequest {
  kind: "image" | "video";
  prompt: string;
  /** 非常规装扮自拍：生成成功后自动替换该角色的聊天头像。 */
  avatar?: boolean;
  /** 角色固定外貌（后端从世界书条目/角色卡提取，已去掉当前穿着）。 */
  appearance?: string;
}

export interface ChatRoomMessage {
  id?: string;
  speaker: string;
  text: string;
  /** 消息时间（epoch 秒）；服务端落盘与 SSE 同源，前端据此显示时间与排序。 */
  ts?: number;
  /** QQ 式轻动作：true＝这条是「戳了戳你」，前端渲染成居中提示而不是气泡。 */
  poke?: boolean;
  /** 角色自发请求的自拍/视频；前端用现有生图/视频通道生成后插回聊天。 */
  media_request?: ChatRoomMediaRequest;
}

export type ChatStreamEvent =
  | { type: "trace"; text: string; detail?: string }
  | { type: "delta"; text: string }
  | { type: "thinking"; text: string }
  | { type: "replace"; text: string }
  | { type: "route"; route: MessageRoute }
  | { type: "image"; url: string; id?: string; regeneration?: RegenerationSnapshot }
  | { type: "video"; url: string; id?: string }
  | { type: "illustrate_request"; prompt: string; motion: number; actors: string[]; sceneSpec?: IllustrationSceneSpec; id?: string; offset?: number; turnId?: string; videoMode?: "climax" | "firstlast" | "shots"; firstFrameDesc?: string; lastFrameDesc?: string; prevTailDesc?: string; lastFrameUrl?: string; transition?: "reuse" | "regenerate" | "ambiguous"; videoPrompt?: string; videoParams?: VideoParams; transitionVideoPrompt?: string; transitionVideoParams?: VideoParams; videoDialogue?: VideoDialogue; bridgePrevSlotId?: string; batchIndex?: number; shotSequenceId?: string; shotIndex?: number; shotTotal?: number; shotType?: "establish" | "cut" | "move" | "move_single"; shotDurationS?: number; imageKey?: string; incomingShotIndex?: number; incomingShotType?: "move" }
  | { type: "audio_request"; lines: AudioDialogueLine[]; id?: string }
  | { type: "chat_messages"; messages: ChatRoomMessage[] }
  | {
      type: "media_request"; kind: "image" | "video"; prompt: string;
      avatar?: boolean; appearance?: string;
    }
  | { type: "rag_status"; state: string; kind: string; count?: number }
  | { type: "inspiration"; card: StreamInspirationCard }
  | { type: "approval"; approval: PromptApproval }
  | { type: "route_choice"; choice: RouteChoice }
  | { type: "artifacts"; items: ArtifactMeta[] }
  | { type: "interrupted" }
  | { type: "error"; message: string };

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`对话流协议错误：${label} 不是对象`);
  }
  return value as Record<string, unknown>;
}

function requiredString(data: Record<string, unknown>, key: string): string {
  if (typeof data[key] !== "string") {
    throw new Error(`对话流协议错误：缺少字符串字段 data.${key}`);
  }
  return data[key];
}

// 灵感卡字段归一化：新结构 {title, content, sources, images?, selected?} 为准；兼容旧结构 {query, prompt, tags, sources}。
export function normalizeInspirationCard(card: Record<string, unknown>): StreamInspirationCard {
  const title = typeof card.title === "string" && card.title
    ? card.title
    : typeof card.query === "string" ? card.query : "";
  const content = typeof card.content === "string" && card.content
    ? card.content
    : typeof card.prompt === "string" ? card.prompt : "";
  const sources = Array.isArray(card.sources)
    ? card.sources as { title: string; url: string }[]
    : [];
  const images = Array.isArray(card.images)
    ? card.images as StreamInspirationCard["images"]
    : [];
  const selected = Array.isArray(card.selected)
    ? card.selected.map((u) => String(u))
    : [];
  return { ...(typeof card.id === "string" ? { id: card.id } : {}), title, content, sources, images, selected };
}

export function decodeChatStreamEvent(value: unknown): ChatStreamEvent {
  const envelope = record(value, "事件");
  if (envelope.protocol !== CHAT_STREAM_PROTOCOL || envelope.version !== CHAT_STREAM_VERSION) {
    throw new Error(
      `不支持的对话流协议：${String(envelope.protocol || "unknown")} v${String(envelope.version ?? "?")}`,
    );
  }
  const data = record(envelope.data, "data");
  switch (envelope.type) {
    case "trace":
      // 2026-09-06：detail=执行详情（思考文本/工具参数/结果摘要），旧后端不带 → 无此字段
      return {
        type: "trace",
        text: requiredString(data, "text"),
        ...(typeof data.detail === "string" && data.detail ? { detail: data.detail } : {}),
      };
    case "delta":
      return { type: "delta", text: requiredString(data, "text") };
    case "thinking":
      // 思考全公开（2026-08-31 晚）：think 流式增量进独立思考面板，不混入正文。
      return { type: "thinking", text: requiredString(data, "text") };
    case "replace":
      return { type: "replace", text: requiredString(data, "text") };
    case "route":
      return { type: "route", route: requiredString(data, "route") as MessageRoute };
    case "image":
      return {
        type: "image",
        url: requiredString(data, "url"),
        ...(typeof data.id === "string" ? { id: data.id } : {}),
        ...(data.regeneration ? { regeneration: data.regeneration as RegenerationSnapshot } : {}),
      };
    case "video":
      return {
        type: "video",
        url: requiredString(data, "url"),
        ...(typeof data.id === "string" ? { id: data.id } : {}),
      };
    case "illustrate_request":
      return {
        type: "illustrate_request",
        prompt: requiredString(data, "prompt"),
        motion: typeof data.motion === "number" ? data.motion : 0,
        actors: Array.isArray(data.actors) ? data.actors.map((a) => String(a)) : [],
        ...(data.scene_spec && typeof data.scene_spec === "object"
          ? { sceneSpec: data.scene_spec as IllustrationSceneSpec }
          : {}),
        ...(typeof data.id === "string" ? { id: data.id } : {}),
        ...(typeof data.offset === "number" ? { offset: data.offset } : {}),
        ...(typeof data.turn_id === "string" ? { turnId: data.turn_id } : {}),
        // V1.5/B1 视频协议可选字段：宽松透传（旧后端不带 → 无这些字段）
        ...(data.video_mode === "climax" || data.video_mode === "firstlast"
          || data.video_mode === "shots"
          ? { videoMode: data.video_mode }
          : {}),
        ...(typeof data.first_frame_desc === "string" ? { firstFrameDesc: data.first_frame_desc } : {}),
        ...(typeof data.last_frame_desc === "string" ? { lastFrameDesc: data.last_frame_desc } : {}),
        ...(typeof data.prev_tail_desc === "string" ? { prevTailDesc: data.prev_tail_desc } : {}),
        ...(typeof data.last_frame_url === "string" ? { lastFrameUrl: data.last_frame_url } : {}),
        // V1.5/W2：首帧复用决策合并结果（三态）宽松解码（旧后端不带 → 无此字段）
        ...(data.transition === "reuse" || data.transition === "regenerate" || data.transition === "ambiguous"
          ? { transition: data.transition }
          : {}),
        // V1.5 默认开放：climax 视频提示词随事件下发（无视频模板也生成，供测试核对）
        ...(typeof data.video_prompt === "string" ? { videoPrompt: data.video_prompt } : {}),
        // V1.5 默认开放：结构化视频参数（dry-run 组装结果，供测试核对参数是否上传）
        ...(data.video_params && typeof data.video_params === "object"
          ? { videoParams: data.video_params as VideoParams }
          : {}),
        // W3 转场视频：转场提示词 + 参数（firstlast + transition≠reuse 时后端下发）
        ...(typeof data.transition_video_prompt === "string"
          ? { transitionVideoPrompt: data.transition_video_prompt }
          : {}),
        ...(data.transition_video_params && typeof data.transition_video_params === "object"
          ? { transitionVideoParams: data.transition_video_params as VideoParams }
          : {}),
        // 第 99 刀：对白时间轴（firstlast + 本轮有 TTS 台词才下发；无 text 视为缺省）
        ...(data.video_dialogue && typeof data.video_dialogue === "object"
          && typeof (data.video_dialogue as Record<string, unknown>).text === "string"
          ? { videoDialogue: data.video_dialogue as VideoDialogue }
          : {}),
        // 第 74 刀 段桥：图片1 取自哪一张**成图槽**（正文序里的前一张画面）。
        // 有了它，前端不必另生成一张「上一画面」近似图 —— 用户口径「前面的图片放首帧」。
        ...(typeof data.bridge_prev_slot === "string" && data.bridge_prev_slot
          ? { bridgePrevSlotId: data.bridge_prev_slot }
          : {}),
        // 第 74 刀：批次序号（第 2 张起才有；主图不带 ⇒ 落点口径逐字不变）
        ...(typeof data.batch_index === "number" ? { batchIndex: data.batch_index } : {}),
        // 2026-11-09 全局模式分镜序列：镜头顺序/归属（旧后端不带 ⇒ 不建序列）
        ...(typeof data.shot_sequence_id === "string" && data.shot_sequence_id
          ? { shotSequenceId: data.shot_sequence_id } : {}),
        ...(typeof data.shot_index === "number" ? { shotIndex: data.shot_index } : {}),
        ...(typeof data.shot_total === "number" ? { shotTotal: data.shot_total } : {}),
        ...(data.shot_type === "cut" || data.shot_type === "move" || data.shot_type === "move_single"
          ? { shotType: data.shot_type } : {}),
        ...(typeof data.shot_duration_s === "number" ? { shotDurationS: data.shot_duration_s } : {}),
        ...(typeof data.image_key === "string" && data.image_key
          ? { imageKey: data.image_key } : {}),
        ...(typeof data.incoming_shot_index === "number"
          ? { incomingShotIndex: data.incoming_shot_index } : {}),
        ...(data.incoming_shot_type === "move" ? { incomingShotType: "move" as const } : {}),
      };
    case "audio_request":
      return {
        type: "audio_request",
        lines: Array.isArray(data.lines)
          ? data.lines
              .map((item) => {
                if (!item || typeof item !== "object") return null;
                const line = item as Record<string, unknown>;
                const speaker = typeof line.speaker === "string" ? line.speaker : "";
                const text = typeof line.text === "string" ? line.text : "";
                if (!speaker || !text) return null;
                return {
                  speaker,
                  text,
                  ...(line.emotion && typeof line.emotion === "object"
                    ? { emotion: line.emotion as Record<string, number> }
                    : {}),
                };
              })
              .filter((line): line is AudioDialogueLine => line !== null)
          : [],
        ...(typeof data.id === "string" ? { id: data.id } : {}),
      };
    case "chat_messages":
      // 2026-11-09 聊天/群聊：逐角色回复一次性下发；空文本/空 speaker 丢弃（与后端编码同口径）。
      return {
        type: "chat_messages",
        messages: Array.isArray(data.messages)
          ? data.messages
              .map((item): ChatRoomMessage | null => {
                if (!item || typeof item !== "object") return null;
                const message = item as Record<string, unknown>;
                const text = typeof message.text === "string" ? message.text.trim() : "";
                if (!text) return null;
                const speaker = typeof message.speaker === "string" && message.speaker
                  ? message.speaker : "助手";
                const media = message.media_request;
                const mediaRequest = media && typeof media === "object"
                  && (media as Record<string, unknown>).kind !== undefined
                  && typeof (media as Record<string, unknown>).prompt === "string"
                  && ((media as Record<string, unknown>).kind === "image"
                    || (media as Record<string, unknown>).kind === "video")
                  ? {
                      kind: (media as Record<string, unknown>).kind as "image" | "video",
                      prompt: String((media as Record<string, unknown>).prompt).trim(),
                      ...((media as Record<string, unknown>).avatar === true
                        ? { avatar: true } : {}),
                      ...(typeof (media as Record<string, unknown>).appearance === "string"
                        && (media as Record<string, unknown>).appearance
                        ? { appearance: String((media as Record<string, unknown>).appearance).trim() }
                        : {}),
                    }
                  : undefined;
                return {
                  speaker,
                  text,
                  ...(typeof message.id === "string" && message.id ? { id: message.id } : {}),
                  ...(mediaRequest && mediaRequest.prompt ? { media_request: mediaRequest } : {}),
                };
              })
              .filter((item): item is ChatRoomMessage => item !== null)
          : [],
      };
    case "media_request": {
      const kind = data.kind === "image" || data.kind === "video" ? data.kind : null;
      const prompt = typeof data.prompt === "string" ? data.prompt.trim() : "";
      if (!kind || !prompt) throw new Error("media_request 缺少 kind 或 prompt");
      return {
        type: "media_request", kind, prompt,
        ...(data.avatar === true ? { avatar: true } : {}),
        ...(typeof data.appearance === "string" && data.appearance
          ? { appearance: data.appearance.trim() } : {}),
      };
    }
    case "rag_status":
      return {
        type: "rag_status",
        state: requiredString(data, "state"),
        kind: typeof data.kind === "string" ? data.kind : "",
        ...(typeof data.count === "number" ? { count: data.count } : {}),
      };
    case "inspiration":
      return { type: "inspiration", card: normalizeInspirationCard(record(data.card, "data.card")) };
    case "approval":
      return { type: "approval", approval: record(data.approval, "data.approval") as unknown as PromptApproval };
    case "route_choice":
      return { type: "route_choice", choice: record(data.choice, "data.choice") as unknown as RouteChoice };
    case "artifacts":
      // 2026-09-08：fabric done 交付物元数据（主卡/世界书），宽松过筛（未知项丢弃）
      return {
        type: "artifacts",
        items: Array.isArray(data.items)
          ? data.items
              .map((item): ArtifactMeta | null => {
                if (!item || typeof item !== "object") return null;
                const art = item as Record<string, unknown>;
                if (typeof art.path !== "string" || !art.path) return null;
                const kind = art.kind === "card" || art.kind === "worldbook" ? art.kind : "file";
                return {
                  kind,
                  name: typeof art.name === "string" && art.name ? art.name : String(art.path).split(/[\/]/).pop() || "产物",
                  path: art.path,
                  ...(typeof art.size === "number" ? { size: art.size } : {}),
                  ...(typeof art.mtime === "number" ? { mtime: art.mtime } : {}),
                };
              })
              .filter((item): item is ArtifactMeta => item !== null)
          : [],
      };
    case "interrupted":
      return { type: "interrupted" };
    case "error":
      return { type: "error", message: requiredString(data, "message") };
    default:
      throw new Error(`不支持的对话流事件：${String(envelope.type)}`);
  }
}
