import { apiGet, apiPost } from "./client";

// 技能扩展：可开关的提示词注入片段，启用后拼进智能体 system_prompt
export interface Skill {
  id: string;
  name: string;
  enabled: boolean;
  prompt_fragment: string;
}

export function listSkills() {
  return apiGet<Skill[]>("/skills");
}

export function saveSkills(skills: Skill[]) {
  return apiPost<Skill[]>("/skills", skills);
}

// ===== Smithery 技能市场 =====
export interface SmitherySkill {
  namespace: string;
  slug: string;
  displayName: string;
  description: string;
  categories?: string[];
  verified?: boolean;
  totalActivations?: number;
  servers?: unknown[];
}

export function searchSmitherySkills(q: string, page = 1, pageSize = 20) {
  return apiGet<{ ok: boolean; skills: SmitherySkill[]; pagination: object; error: string }>(
    `/skills/smithery/search?q=${encodeURIComponent(q)}&page=${page}&page_size=${pageSize}`,
  );
}

// 一键添加：取 prompt 存为本地技能。返回 {skills, dependsServers:[MCP名]}
export function addSmitherySkill(namespace: string, slug: string, display_name = "") {
  return apiPost<{ ok: boolean; skills: Skill[]; dependsServers: string[]; error: string }>(
    "/skills/smithery/add", { namespace, slug, display_name },
  );
}
