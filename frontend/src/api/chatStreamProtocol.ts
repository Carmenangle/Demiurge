import type { MessageRoute, PromptApproval, RegenerationSnapshot, RouteChoice } from "../types/chat";

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

/** V1.5 默认开放：结构化视频参数（后端 dry-run 组装结果，供测试核对参数是否上传） */
export interface VideoParams {
  mode: "climax" | "firstlast" | "transition";
  model: string;
  size: string;
  endpoint: string;
  images: string[];
  reference_binding: Record<string, string>;
  warnings: string[];
}

export type ChatStreamEvent =
  | { type: "trace"; text: string }
  | { type: "delta"; text: string }
  | { type: "thinking"; text: string }
  | { type: "replace"; text: string }
  | { type: "route"; route: MessageRoute }
  | { type: "image"; url: string; id?: string; regeneration?: RegenerationSnapshot }
  | { type: "video"; url: string; id?: string }
  | { type: "illustrate_request"; prompt: string; motion: number; actors: string[]; sceneSpec?: IllustrationSceneSpec; id?: string; offset?: number; turnId?: string; videoMode?: "climax" | "firstlast"; firstFrameDesc?: string; lastFrameDesc?: string; prevTailDesc?: string; lastFrameUrl?: string; transition?: "reuse" | "regenerate" | "ambiguous"; videoPrompt?: string; videoParams?: VideoParams; transitionVideoPrompt?: string; transitionVideoParams?: VideoParams }
  | { type: "audio_request"; lines: AudioDialogueLine[]; id?: string }
  | { type: "rag_status"; state: string; kind: string; count?: number }
  | { type: "inspiration"; card: StreamInspirationCard }
  | { type: "approval"; approval: PromptApproval }
  | { type: "route_choice"; choice: RouteChoice }
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
      return { type: "trace", text: requiredString(data, "text") };
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
    case "interrupted":
      return { type: "interrupted" };
    case "error":
      return { type: "error", message: requiredString(data, "message") };
    default:
      throw new Error(`不支持的对话流事件：${String(envelope.type)}`);
  }
}
