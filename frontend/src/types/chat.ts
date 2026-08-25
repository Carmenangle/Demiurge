import type { PortOp } from "../api/ai";

// 图文混排片段：文本/图片穿插渲染
export interface MsgPart {
  type: "text" | "image" | "video" | "audio" | "masked-image" | "media-slot";
  text?: string;  // type=text
  url?: string;   // type=image（dataURI 或 http URL）
  image?: string; // type=masked-image 的原图
  mask?: string;  // type=masked-image 的独立 Alpha 蒙版
  slotId?: string; // type=media-slot，异步图片/视频完成后据此原位替换
  generationId?: string; // 落库后的 generation 记录 ID（Visual CI 诊断用）
  status?: "pending" | "ready" | "failed";
  promptId?: string;
  error?: string;
  regeneration?: RegenerationSnapshot;
  /** media-slot 的媒体类型提示（占位文案/进度用；音频槽 = "audio"） */
  kind?: "image" | "video" | "audio";
  /** 音频分条：角色名（对话气泡与画布剧情楼层的分条标签） */
  speaker?: string;
  /** 音频分条：第几条（1-based，占位进度 x/y） */
  seq?: number;
  /** 音频分条：总条数 */
  total?: number;
}

export interface PromptApproval {
  id: string;
  messageId: string;
  kind: "image" | "video" | "img2img";
  originalPrompt: string;
  prompt: string;
  status: "pending" | "submitted" | "cancelled" | "failed";
  stage?: "prompt_review" | "rewrite_consent" | "delivery_unknown" | "request_failed";
  reason?: string;
}

export type AgentRoute = "answer" | "generate" | "img2img" | "analyze" | "video" | "inspire" | "tool_agent";

/** 消息实际走到的 Agent 路由（调度主管分派结果）。
 *  - 剧情节点 = roleplay / answer（roleplay 内部再串 world/recall/curator/judge）
 *  - 生成节点 = generate / img2img / video / analyze
 *  - 其余 = inspire / tool_agent / edit / clarify */
export type MessageRoute = AgentRoute | "roleplay" | "edit" | "clarify";

export interface RouteChoice {
  id: string;
  messageId: string;
  userMessageId: string;
  status: "pending" | "selected";
  selectedRoute?: AgentRoute;
  options: { route: AgentRoute; label: string }[];
}

export interface AiImageRegeneration {
  kind: "ai-image";
  prompt: string;
  images: string[];
  imageMask?: { image: string; mask: string };
  size: string;
  quality: "auto" | "low" | "medium" | "high";
  model: {
    baseUrl: string;
    modelName: string;
  };
}

export interface WorkflowRegeneration {
  kind: "workflow";
  graph: unknown;
  comfyuiUrl: string;
  outputNodeIds: string[];
  prompt: string;
  /** 生成元数据：模板名 / 主模型 / LoRA（卡片展示用） */
  templateName?: string;
  modelName?: string;
  loraNames?: string[];
}

export interface TemplateRegeneration {
  kind: "template";
  templateId: string;
  values: Record<string, unknown>;
  comfyuiUrl: string;
  outputNodeIds: string[];
  prompt: string;
  loras?: { name: string; weight: number }[];
  loraMode?: "none" | "single" | "multi";
  /** 角色 LoRA 生图时的主角名（用于后端可读命名 角色_轮次_序号）；非角色 LoRA 为空。 */
  characterLoraActor?: string;
}

export type RegenerationSnapshot = AiImageRegeneration | WorkflowRegeneration | TemplateRegeneration;

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  parts?: MsgPart[];   // 图文混排：有则优先按顺序渲染，文本/图片穿插
  thinking?: string;
  image?: string;
  video?: string;   // 生成的视频地址（mp4/webm/gif，用 <video> 渲染）
  audio?: string;   // 生成的音频地址（wav/mp3/flac…，用 <audio> 播放器渲染）
  regeneration?: RegenerationSnapshot; // 绑定该结果的不可变重生成参数，不含 API Key
  // 工作流节点卡：选中模板后把所选节点逐个提取，各自嵌入锁定的真实 ComfyUI 画布，纵向排列
  workflow?: {
    templateId: string;
    templateName: string;
    draftGraph: unknown | null;    // 可继续编辑的完整 ComfyUI UI workflow
    capturedGraph: unknown | null; // 原生 graphToPrompt 生成的 API prompt，仅供 /s
    done: boolean;
  };
  // 工作流输入口编排计划：AI 规划「各输入口放什么」，用户确认后写入画布
  portsPlan?: {
    cardId: string;            // 目标工作流卡的消息 id
    summary: string;
    ops: PortOp[];
    images: string[];          // 本轮随文图片（dataURI/URL），set_image 按 image_index 取用
    status: "pending" | "applied" | "ignored";
  };
  // 灵感卡：联网搜主题 → 整理成「标题+内容」中文总结（代码块样式，右下角可插入对话）
  inspiration?: {
    title: string;
    content: string;
    sources: { title: string; url: string }[];
    images?: Array<{ thumb_url: string; full_url: string; source_url: string; width?: number; height?: number; title?: string }>;
    selected?: string[];
  };
  // 风格模板/艺术化修饰后的独立提示词审批卡，可在历史中继续操作。
  promptApproval?: PromptApproval;
  // Supervisor 无法高置信分派时显示的最小候选选择卡。
  routeChoice?: RouteChoice;
  // 该条消息实际走到的 Agent 路由（调度主管分派结果，决定画布节点归属）。
  route?: MessageRoute;
  // 纯状态/Toast 提示（如「已提交到 ComfyUI…」），非剧情/生成正文，不投影为任何节点。
  system?: boolean;
}
