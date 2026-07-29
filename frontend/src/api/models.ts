import { apiGet, apiPost } from "./client";

export interface DownloadStatus {
  status: "pending" | "downloading" | "done" | "error" | "unknown";
  downloaded?: number;
  total?: number;
  filename?: string;
  error?: string;
}

// 下载任务（跨 tab 下载面板用）：在 DownloadStatus 基础上带 id/展示名/类型/创建时间
export interface DownloadTask extends DownloadStatus {
  id: string;
  name?: string;
  model_type?: string;
  created?: number;
}

export type ModelType =
  | "checkpoint" | "lora" | "vae" | "controlnet" | "embedding" | "upscale" | "clip"
  | "clip_vision" | "text_encoder" | "diffusion_model" | "unet" | "style_model"
  | "hypernetwork" | "ipadapter" | "gligen" | "ultralytics" | "sam" | "vae_approx"
  | "photomaker" | "diffuser" | "audio_encoder" | "other";

// 启动模型下载（HF/Civitai → ComfyUI models 目录），返回 task_id
export function downloadModel(args: {
  url: string;
  modelType: ModelType;
  modelsDir: string;
  hfToken?: string;
  civitaiToken?: string;
  name?: string;
  proxy?: string;
}) {
  return apiPost<{ ok: boolean; task_id: string }>("/models/download", {
    url: args.url,
    model_type: args.modelType,
    models_dir: args.modelsDir,
    hf_token: args.hfToken || "",
    civitai_token: args.civitaiToken || "",
    name: args.name || "",
    proxy: args.proxy || "",
  });
}

// 下载工作流模板到默认工作流文件夹（.json 直落，.zip 抽 json）。进度走同一下载面板。
export function downloadWorkflowTemplate(args: {
  url: string; workflowDir: string; name?: string; civitaiToken?: string; proxy?: string;
}) {
  return apiPost<{ ok: boolean; task_id: string }>("/models/download/workflow", {
    url: args.url, workflow_dir: args.workflowDir,
    name: args.name || "", civitai_token: args.civitaiToken || "", proxy: args.proxy || "",
  });
}

// 查询下载进度
export function downloadStatus(taskId: string) {
  return apiGet<DownloadStatus>(`/models/download/status?task_id=${encodeURIComponent(taskId)}`);
}

// 全部下载任务（跨 tab 下载面板轮询）
export function listDownloads() {
  return apiGet<{ items: DownloadTask[] }>("/models/download/tasks");
}

export interface ModelInfo {
  name: string;
  description: string;
  images: string[];
  download_url: string;
}

// 拉取模型预览图+介绍（下载前预览）
export function fetchModelInfo(url: string, hfToken?: string, civitaiToken?: string, proxy?: string) {
  return apiPost<ModelInfo>("/models/info", {
    url, hf_token: hfToken || "", civitai_token: civitaiToken || "", proxy: proxy || "",
  });
}

// —— CivitAI 浏览 ——
export interface CivitaiCard {
  id: number;
  name: string;
  type: string;
  nsfw: boolean;
  creator: string;
  downloads: number;
  likes: number;
  cover: string;
  base_model: string;
  version_id: number | null;
  download_url: string;
  model_url: string;
}

export interface CivitaiBrowseArgs {
  proxy?: string;
  query?: string;
  types?: string;
  sort?: string;
  period?: string;
  baseModels?: string;
  nsfw?: boolean;
  cursor?: string;
  limit?: number;
  civitaiToken?: string;
}

export function browseCivitai(a: CivitaiBrowseArgs) {
  return apiPost<{ items: CivitaiCard[]; next_cursor: string }>("/models/browse/civitai", {
    proxy: a.proxy || "", query: a.query || "", types: a.types || "",
    sort: a.sort || "Highest Rated", period: a.period || "AllTime",
    base_models: a.baseModels || "", nsfw: a.nsfw || false,
    cursor: a.cursor || "", limit: a.limit || 24, civitai_token: a.civitaiToken || "",
  });
}

// —— CivArchive 浏览（跨平台归档，按 sha256 聚合多下载源）——
export interface CivArchiveCard {
  id: string;
  name: string;
  type: string;
  kind: string;        // version / file
  nsfw: boolean;
  downloads: number;
  cover: string;
  base_model: string;
  platform: string;
  sha256: string;
  direct_url: string;
  civarchive_url: string;
}
export interface CivArchiveSource {
  filename: string;
  url: string;
  source: string;      // civitai / huggingface / mirror
  is_gated: boolean;
  is_paid: boolean;
}

export function browseCivArchive(a: { proxy?: string; query?: string; type?: string; page?: number; nsfw?: boolean }) {
  return apiPost<{ items: CivArchiveCard[]; total: number }>("/models/browse/civarchive", {
    proxy: a.proxy || "", query: a.query || "", type: a.type || "", page: a.page || 1, nsfw: a.nsfw || false,
  });
}
export function civArchiveSources(proxy: string, sha256: string) {
  return apiPost<{ files: CivArchiveSource[]; model: Record<string, unknown> }>(
    "/models/browse/civarchive/sources", { proxy, sha256 });
}
