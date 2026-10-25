// AI 搭工作流（原 api/ai.ts 搭建段，2026-10-24 按资源域拆分）：
// 节点知识库同步/检索 + 三种搭建通路（workflow/module/direct）+ 顾问 plan + 骨架 + 搭建会话 + 后台任务队列。
import { apiGet, apiPost } from "../client";
import {
  WORKFLOW_BUILD_EXECUTE_TIMEOUT_MS,
  WORKFLOW_BUILD_PLAN_TIMEOUT_MS,
} from "../../lib/workflowBuildExecution";
import { chatBody, sseEmbed, POLL_TIMEOUT_MS, type Chat, type ChatCfg, type Embed } from "./wire";

// 启动后台同步：扫描 ComfyUI 已装节点入库。立即返回总包数，进度经 syncProgress 轮询。
export function syncNodes(embed: Embed, comfyUrl: string, full = false) {
  return apiPost<{ total_packs: number; already_running: boolean }>(
    "/ai/nodes/sync",
    { ...sseEmbed(embed), comfy_url: comfyUrl, full },
  );
}

export interface SyncProgress {
  running: boolean; done: number; total: number; current: string;
  synced: number; skipped: number; failed: number; failures: string[];
  error: string; finished: boolean;
}
// 同步进度快照（轮询）
export function syncProgress() {
  return apiGet<SyncProgress>("/ai/nodes/sync-progress");
}

// 节点知识库现状（包数 + 节点数）
export function nodeStats(embed: Embed) {
  return apiPost<{ packs: number; nodes: number }>("/ai/nodes/stats", sseEmbed(embed));
}

export interface NodePackItem { id: string; title: string; node_count: number; python_module: string; }
export interface NodePackDetail extends NodePackItem { content: string; node_names: string[]; categories: string[]; }

// 全部节点包列表（管理页）
export function listNodePacks(embed: Embed) {
  return apiPost<{ packs: NodePackItem[] }>("/ai/nodes/packs", sseEmbed(embed));
}
// 单个包完整内容（含用途正文）
export function getNodePack(embed: Embed, packId: string) {
  return apiPost<NodePackDetail>("/ai/nodes/pack", { ...sseEmbed(embed), pack_id: packId });
}
// 修订某包用途正文并重嵌入
export function updateNodePackContent(embed: Embed, packId: string, content: string) {
  return apiPost<{ ok: boolean }>("/ai/nodes/pack/update", { ...sseEmbed(embed), pack_id: packId, content });
}

export interface BuildResult {
  ok: boolean;
  path: string;
  graph: Record<string, unknown>;
  errors: string[];
  warnings?: string[];   // 非阻断提示（如断链孤岛：节点还没接进主链）
  missing_nodes?: string[];  // 本机没装、已从图里移除的节点类型（供「去安装」按钮）
  alternatives?: Record<string, string[]>;  // {缺失节点: [本机同类平替...]}（供「用平替重搭」）
}

export type BuildTurn = { role: "user" | "assistant"; text: string };

// 按需求自动搭工作流：检索节点→AI 生成→校验重试→（可选）落盘到 workflowDir
// currentGraph 非空=在当前画布基础上增量改；save=false 只回图不落盘（多轮迭代中途）
export function buildWorkflow(args: {
  need: string; chat: ChatCfg; embed: Embed; comfyUrl: string; workflowDir: string; name?: string;
  currentGraph?: Record<string, unknown>; save?: boolean; history?: BuildTurn[]; proxy?: string; signal?: AbortSignal;
}) {
  return apiPost<BuildResult>("/ai/build", {
    base_url: args.chat.baseUrl, api_key: args.chat.apiKey, model: args.chat.modelName, proxy: args.proxy || "",
    ...sseEmbed(args.embed),
    need: args.need, comfy_url: args.comfyUrl, workflow_dir: args.workflowDir, name: args.name || "",
    current_graph: args.currentGraph || {}, save: args.save !== false, history: args.history || [],
  }, WORKFLOW_BUILD_EXECUTE_TIMEOUT_MS, args.signal);
}

// 分模块增量搭建：冻结当前图，AI 只出新模块+锚点，后端合并进整图。返回合并后完整图，前端写回画布。
export function buildModule(args: {
  need: string; chat: ChatCfg; embed: Embed; comfyUrl: string;
  currentGraph: Record<string, unknown>; history?: BuildTurn[]; proxy?: string; signal?: AbortSignal;
}) {
  return apiPost<BuildResult>("/ai/build/module", {
    base_url: args.chat.baseUrl, api_key: args.chat.apiKey, model: args.chat.modelName, proxy: args.proxy || "",
    ...sseEmbed(args.embed),
    need: args.need, comfy_url: args.comfyUrl, current_graph: args.currentGraph, history: args.history || [],
  }, WORKFLOW_BUILD_EXECUTE_TIMEOUT_MS, args.signal);
}

// 精简直连：信任强模型(Opus)一次到位，只调 1 次模型，不 audit 自修/不重写/不回喂重试。最快。
export function buildDirect(args: {
  need: string; chat: ChatCfg; embed: Embed; comfyUrl: string;
  currentGraph?: Record<string, unknown>; history?: BuildTurn[]; proxy?: string; signal?: AbortSignal;
}) {
  return apiPost<BuildResult>("/ai/build/direct", {
    base_url: args.chat.baseUrl, api_key: args.chat.apiKey, model: args.chat.modelName, proxy: args.proxy || "",
    ...sseEmbed(args.embed),
    need: args.need, comfy_url: args.comfyUrl, current_graph: args.currentGraph || {}, history: args.history || [],
  }, WORKFLOW_BUILD_EXECUTE_TIMEOUT_MS, args.signal);
}

// 顾问模式：只产出给人看的中文方案文本，不改画布。用户确认后再走 build/module 执行。
export function buildPlan(args: {
  need: string; chat: ChatCfg; embed: Embed; comfyUrl: string;
  currentGraph?: Record<string, unknown>; history?: BuildTurn[]; proxy?: string; signal?: AbortSignal;
}) {
  return apiPost<{ plan: string }>("/ai/build/plan", {
    base_url: args.chat.baseUrl, api_key: args.chat.apiKey, model: args.chat.modelName, proxy: args.proxy || "",
    ...sseEmbed(args.embed),
    need: args.need, comfy_url: args.comfyUrl, current_graph: args.currentGraph || {}, history: args.history || [],
  }, WORKFLOW_BUILD_PLAN_TIMEOUT_MS, args.signal);
}

// 把前端手改后的画布 graph 直接落盘（不经 AI）
export function saveWorkflow(args: {
  graph: Record<string, unknown>; embed: Embed; workflowDir: string; name?: string;
}) {
  return apiPost<{ ok: boolean; path: string }>("/ai/build/save", {
    ...sseEmbed(args.embed),
    graph: args.graph, workflow_dir: args.workflowDir, name: args.name || "",
  });
}

// —— 骨架底座：AI 搭工作流的正确起点 ——
export interface Skeleton {
  id: string; name: string; desc: string; kind: string;
  source: "builtin" | "file"; node_count: number; path: string;
}
// 列出骨架候选（内置 + 工作流文件夹里的 .json）
export function listSkeletons(workflowDir: string) {
  return apiPost<{ skeletons: Skeleton[] }>("/ai/skeletons", { workflow_dir: workflowDir });
}
// 取某骨架的 graph（load 进画布用；文件只读不改）
export function skeletonGraph(skeletonId: string, workflowDir: string) {
  return apiPost<{ graph: Record<string, unknown> }>("/ai/skeleton/graph", {
    skeleton_id: skeletonId, workflow_dir: workflowDir,
  });
}

// —— 搭建会话：进度保存 + 多开 ——
export interface BuildSessionMeta { id: string; name: string; updated_at: number; node_count: number; msg_count: number; }
export interface BuildSessionFull { id: string; name: string; msgs: unknown[]; graph: Record<string, unknown>; skeleton_id: string; updated_at: number; }

export function listBuildSessions() {
  return apiGet<{ sessions: BuildSessionMeta[] }>("/ai/build/sessions");
}
export function getBuildSession(id: string) {
  return apiGet<BuildSessionFull>(`/ai/build/session?id=${encodeURIComponent(id)}`);
}
export function saveBuildSession(args: {
  id?: string; name: string; msgs: unknown[]; graph: Record<string, unknown>; skeletonId?: string;
}) {
  return apiPost<{ id: string; name: string; updated_at: number }>("/ai/build/session/save", {
    id: args.id || "", name: args.name, msgs: args.msgs, graph: args.graph, skeleton_id: args.skeletonId || "",
  });
}
export function deleteBuildSession(id: string) {
  return apiPost<{ ok: boolean }>("/ai/build/session/delete", { id });
}

export type WorkflowBuildTaskStatus = "queued" | "running" | "done" | "error" | "cancelled";
export interface WorkflowBuildTask {
  id: string; session_id: string; mode: "direct" | "module" | "workflow" | "plan";
  need: string; status: WorkflowBuildTaskStatus; result?: BuildResult | { plan: string };
  error?: string; created_at: number; updated_at: number;
}
export function enqueueWorkflowBuildTask(payload: {
  sessionId: string; mode: WorkflowBuildTask["mode"]; need: string;
  chat: Chat; embed: Embed; comfyUrl: string; workflowDir: string;
  currentGraph: Record<string, unknown>; history: BuildTurn[];
}) {
  return apiPost<WorkflowBuildTask>("/ai/build/tasks", {
    session_id: payload.sessionId, mode: payload.mode, need: payload.need,
    ...chatBody(payload.chat), comfy_url: payload.comfyUrl, workflow_dir: payload.workflowDir,
    ...sseEmbed(payload.embed), current_graph: payload.currentGraph, history: payload.history,
  });
}
export function listWorkflowBuildTasks(sessionId = "") {
  return apiGet<{ tasks: WorkflowBuildTask[] }>(
    `/ai/build/tasks?session_id=${encodeURIComponent(sessionId)}`, POLL_TIMEOUT_MS,
  );
}
export function cancelWorkflowBuildTask(id: string) {
  return apiPost<WorkflowBuildTask>("/ai/build/task/cancel", { id });
}
