import { apiGet, apiPost } from "./client";

export interface AgentTools {
  generate_image: boolean;
  generate_video: boolean;
  image_to_image: boolean;
  analyze_image: boolean;
  search_inspiration: boolean;
}

// 多 Agent 预设：一套人设/行为配置，对话时可切换
export interface Agent {
  id: string;
  name: string;
  systemPrompt: string;
  memory: string;
  temperature: number | null;
  topP: number | null;
  maxTokens: number | null;
  tools: AgentTools;
  mcpServerIds: string[];   // 选中启用的 MCP 服务器 id（空=都不用）
  skillIds: string[];       // 选中启用的技能 id（空=都不用）
  isDefault: boolean;
  enabled: boolean;
}

export function listAgents() {
  return apiGet<Agent[]>("/agents");
}

export function saveAgents(agents: Agent[]) {
  return apiPost<Agent[]>("/agents", agents);
}

// 内置默认系统提示词（普通对话优先 + 显式工具调用规则）
export function defaultPrompt() {
  return apiGet<{ prompt: string }>("/agents/default-prompt");
}

export const DEFAULT_TOOLS: AgentTools = {
  generate_image: true, generate_video: true, image_to_image: true, analyze_image: true,
  search_inspiration: true,
};
