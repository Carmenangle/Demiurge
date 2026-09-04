import { apiGet, apiPost, apiPut } from "./client";

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

// ③ 内置智能体：图里所有默认 Agent 的元数据 + 默认值 + 当前生效值（含覆盖）
export type BuiltinKind = "llm" | "rules" | "specialist";
export interface BuiltinAgent {
  id: string;
  name: string;
  kind: BuiltinKind;
  role: string;
  tools: string[];
  editable: string[];              // 可覆盖字段名（llm: systemPrompt/temperature；rules: gateFloor/gateBaseRate/tiers）
  defaults: Record<string, unknown>;
  effective: Record<string, unknown>;  // 默认叠加用户覆盖后的生效值
}
// 覆盖表：{agent_id: {field: value}}
export type BuiltinOverrides = Record<string, Record<string, unknown>>;

export function listBuiltinAgents() {
  return apiGet<BuiltinAgent[]>("/agents/builtin");
}

export function saveBuiltinOverrides(overrides: BuiltinOverrides) {
  return apiPost<BuiltinAgent[]>("/agents/builtin", overrides);
}

// ④ 固化知识库（DATA_DIR/agent_knowledge/*.md，智能编造 Agent 规范）
export interface KnowledgeDoc {
  name: string;      // 文件主名（展示/读取用）
  file: string;      // 完整文件名（xxx.md）
  size: number;      // 字节
  mtime: number;     // 毫秒时间戳
  truncated: boolean; // 超出单文档注入/预览上限
  // 以下仅带 frontmatter（skill）的技能文档才有；普通知识文档无这些键
  skill?: string;    // 技能名（kebab-case，smart 模式按此触发装载）
  whenToUse?: string; // 触发描述（注入目录时展示，供模型判断何时 load_doc）
  tools?: string[];  // 工具清单（skill 可调用的能力）
}
export interface KnowledgeDocContent extends KnowledgeDoc {
  content: string;   // 正文（技能文档=去 frontmatter；普通知识=全文；超长截断到单文档上限）
}

// 注入模式配置：smart=技能目录+按需 load_doc（默认）；always=全量常驻（老行为）
export type KnowledgeMode = "smart" | "always";
export interface KnowledgeConfig {
  mode: KnowledgeMode;
  always_docs: string[];   // smart 模式下强制常驻注入的技能文档主名
}

export function listAgentKnowledge() {
  return apiGet<KnowledgeDoc[]>("/agents/knowledge");
}

export function readAgentKnowledge(name: string) {
  return apiGet<KnowledgeDocContent>(`/agents/knowledge/${encodeURIComponent(name)}`);
}

export function getKnowledgeConfig() {
  return apiGet<Partial<KnowledgeConfig>>("/agents/knowledge/config");
}

export function saveKnowledgeConfig(cfg: KnowledgeConfig) {
  return apiPut<KnowledgeConfig>("/agents/knowledge/config", cfg);
}

export const DEFAULT_TOOLS: AgentTools = {
  generate_image: true, generate_video: true, image_to_image: true, analyze_image: true,
  search_inspiration: true,
};
