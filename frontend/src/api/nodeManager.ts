import { apiGet, apiPost } from "./client";

// 节点包记录（对应后端 comfy_manager._norm_pack）
export interface NodePack {
  id: string;
  title: string;
  author: string;
  repository: string;
  description: string;
  install_type: string;
  state: string;          // enabled / disabled / not-installed
  updatable: boolean;
  version: string;
  stars: number;
  last_update: string;
  trust: boolean;
  // 本地 git 信息（listInstalled 传 path 时补，对齐图1启动器）
  commit?: string;      // 短哈希
  git_date?: string;    // 最后提交日期 YYYY-MM-DD
  is_git?: boolean | null; // true=git 仓库 false=非Git仓库 null=磁盘未找到
  dir?: string;         // 本地目录名，供自建检查更新按目录匹配 updatable
}

export interface QueueStatus {
  total_count: number;
  done_count: number;
  in_progress_count: number;
  is_processing: boolean;
}

const q = (url: string) => `?url=${encodeURIComponent(url)}`;

// —— 只读 ——
export function listInstalled(url: string, path = "") {
  const p = path ? `&path=${encodeURIComponent(path)}` : "";
  return apiGet<{ items: NodePack[] }>(`/node-manager/installed${q(url)}${p}`);
}

export function listMarket(url: string) {
  return apiGet<{ items: NodePack[] }>(`/node-manager/market${q(url)}`);
}

export function queueStatus(url: string) {
  return apiGet<QueueStatus>(`/node-manager/queue-status${q(url)}`);
}

// —— 写操作（改环境）——
export function installNode(url: string, pack: NodePack, selectedVersion = "") {
  return apiPost<Record<string, unknown>>("/node-manager/install", { url, pack, selected_version: selectedVersion });
}

export function updateNode(url: string, pack: NodePack) {
  return apiPost<Record<string, unknown>>("/node-manager/update", { url, pack });
}

// 直连 git 更新（nightly/git-HEAD 包）：git pull --ff-only，绕开 Manager 队列，立即生效
export interface GitUpdateResult { ok: boolean; dir: string; old: string; new: string; updated: boolean; }
export function gitUpdateNode(path: string, pack: NodePack) {
  return apiPost<GitUpdateResult>("/node-manager/git-update", { path, pack });
}

// 带进度与结果校验的 git 更新（见后端 services/node_update）。
// 相比 gitUpdateNode：后台跑、可轮询字节级进度、依赖要改共享库时会先停下等确认。
export interface UpdateDep {
  file: string;
  bytes: number;
  cached: boolean;      // true=命中 pip 缓存，未走网络
}

export interface UpdateProgress {
  running: boolean;
  finished: boolean;
  note: string;
  phase: "" | "download" | "resolve" | "preflight" | "deps" | "deps-install" | "needs-confirm" | "done";
  percent: number;
  received_bytes: number;
  speed_bps: number;
  objects_done: number;
  objects_total: number;
  deps: UpdateDep[];
  deps_total_bytes: number;
  old: string;
  new: string;
  changed: boolean;         // 关键：HEAD 真的变了才是 true
  error: string;
  message: string;
  pending_sensitive: string[];   // 非空=等用户确认是否改动这些共享依赖
}

export interface TrackedUpdateOptions {
  pythonExe?: string;
  proxy?: string;
  allowSensitive?: boolean;
  skipDeps?: boolean;
}

export function startTrackedUpdate(path: string, pack: NodePack, o: TrackedUpdateOptions = {}) {
  return apiPost<{ already_running: boolean }>("/node-manager/update-tracked", {
    path, pack,
    python_exe: o.pythonExe || "",
    proxy: o.proxy || "",
    allow_sensitive: !!o.allowSensitive,
    skip_deps: !!o.skipDeps,
  });
}

export function updateProgress() {
  return apiGet<UpdateProgress>("/node-manager/update-progress");
}

// 核对 ComfyUI 本体依赖（只预检不安装）。切版本只换代码不装依赖，
// 会留下「新代码 + 旧依赖」的半更新态，典型表现是量化模型/CLIP 加载失败。
export interface CoreRequirements {
  satisfied: boolean;
  missing: { name: string; version: string }[];
  sensitive: string[];
  note: string;
}

export function checkCoreRequirements(path: string, pythonExe = "", proxy = "") {
  return apiPost<CoreRequirements>("/node-manager/core-requirements", {
    path, python_exe: pythonExe, proxy,
  });
}

export function uninstallNode(url: string, pack: NodePack) {
  return apiPost<Record<string, unknown>>("/node-manager/uninstall", { url, pack });
}

export function disableNode(url: string, pack: NodePack) {
  return apiPost<Record<string, unknown>>("/node-manager/disable", { url, pack });
}

// 执行已入队的装/更新/卸载任务（入队后必调）
export function startQueue(url: string) {
  return apiPost<Record<string, unknown>>("/node-manager/start", { url });
}

// 自建检查更新：后端直接 git fetch（带代理）比对，绕开 Manager 超时。
// 返回 updatable 按目录名索引 + 检查数 + 失败目录。
export function checkUpdatesGit(path: string, proxyUrl: string) {
  return apiPost<{ updatable: Record<string, boolean>; checked: number; failed: string[] }>(
    "/node-manager/check-updates-git",
    { path, proxy_url: proxyUrl },
  );
}

export function updateComfyUI(url: string) {
  return apiPost<Record<string, unknown>>("/node-manager/update-comfyui", { url });
}

// 用 GitHub 链接安装插件（自动装 requirements.txt 依赖）
export function installGit(url: string, gitUrl: string) {
  return apiPost<Record<string, unknown>>("/node-manager/install-git", { url, git_url: gitUrl });
}

// ComfyUI 可切换版本列表（nightly=开发版，vX=稳定版）
export interface ComfyVersions {
  versions: string[];
  current: string;
}
export function comfyuiVersions(url: string) {
  return apiGet<ComfyVersions>(`/node-manager/comfyui-versions${q(url)}`);
}

// 全量版本（读 git tag，带发布日期）。path = ComfyUI 目录
export interface GitVersion { version: string; date: string; }
export interface GitVersions { versions: GitVersion[]; current: string; }
export function comfyuiGitVersions(path: string) {
  return apiGet<GitVersions>(`/node-manager/comfyui-git-versions?path=${encodeURIComponent(path)}`);
}
export function switchComfyui(url: string, ver: string) {
  return apiPost<Record<string, unknown>>("/node-manager/switch-comfyui", { url, ver });
}

// Manager 软重启（进程级重启用 api/comfyui.ts 的 restartComfy）
export function rebootComfy(url: string) {
  return apiPost<Record<string, unknown>>("/node-manager/reboot", { url });
}

// —— 工作流识别安装 ——
export interface AnalyzeResult {
  missing_packs: string[];
  packs: NodePack[];
  unresolved: string[];
}

export function analyzeWorkflow(url: string, workflow: Record<string, unknown>) {
  return apiPost<AnalyzeResult>("/node-manager/analyze-workflow", { url, workflow });
}
