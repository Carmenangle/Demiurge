import type { PortOp } from "../api/ai";

// 图文混排片段：文本/图片穿插渲染
export interface MsgPart {
  type: "text" | "image" | "masked-image";
  text?: string;  // type=text
  url?: string;   // type=image（dataURI 或 http URL）
  image?: string; // type=masked-image 的原图
  mask?: string;  // type=masked-image 的独立 Alpha 蒙版
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
}

export type RegenerationSnapshot = AiImageRegeneration | WorkflowRegeneration;

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  parts?: MsgPart[];   // 图文混排：有则优先按顺序渲染，文本/图片穿插
  thinking?: string;
  image?: string;
  video?: string;   // 生成的视频地址（mp4/webm/gif，用 <video> 渲染）
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
  // 灵感卡：联网搜服装/发型/画风等 → 提炼的提示词（代码块样式，右下角可插入对话）
  inspiration?: {
    query: string;
    prompt: string;
    tags: string[];
    sources: { title: string; url: string }[];
  };
  // 风格模板/艺术化修饰后的独立提示词审批卡，可在历史中继续操作。
  promptApproval?: PromptApproval;
  // Supervisor 无法高置信分派时显示的最小候选选择卡。
  routeChoice?: RouteChoice;
}
