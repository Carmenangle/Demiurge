import { apiGet, apiPost } from "./client";

// MCP 服务器配置（与后端 routers/mcp.py 的 McpServer 对应）
export interface McpServer {
  id: string;
  name: string;
  type: "stdio" | "sse";   // stdio=本地命令 / sse=远程 URL
  command: string;         // stdio 用
  args: string[];          // stdio 用
  url: string;             // sse 用
  enabled: boolean;
}

export function listMcpServers() {
  return apiGet<McpServer[]>("/mcp");
}

export function saveMcpServers(servers: McpServer[]) {
  return apiPost<McpServer[]>("/mcp", servers);
}

// 测试连通性，返回该服务器暴露的工具名
export function testMcpServer(server: McpServer) {
  return apiPost<{ ok: boolean; tools: string[]; error: string }>("/mcp/test", { server });
}

// ===== Smithery 市场 =====
export interface SmitheryServer {
  qualifiedName: string;
  displayName: string;
  description: string;
  iconUrl?: string;
  useCount?: number;
  verified?: boolean;
  remote?: boolean;
}

export function searchSmithery(q: string, page = 1, pageSize = 20) {
  return apiGet<{ ok: boolean; servers: SmitheryServer[]; pagination: object; error: string }>(
    `/mcp/smithery/search?q=${encodeURIComponent(q)}&page=${page}&page_size=${pageSize}`,
  );
}

// 一键添加某 Smithery 服务器到本地 MCP 配置，返回更新后的列表
export function addSmithery(qualified_name: string, display_name = "") {
  return apiPost<McpServer[]>("/mcp/smithery/add", { qualified_name, display_name });
}
