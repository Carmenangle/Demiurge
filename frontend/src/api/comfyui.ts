import { apiGet, apiPost, apiUrl } from "./client";
import { comfyClientId } from "../lib/comfyProgress";
import type { RegenerationSnapshot } from "../types/chat";

export interface ComfyStatus {
  running: boolean;
  managed: boolean;
}

export function comfyStatus(url: string) {
  return apiGet<ComfyStatus>(`/comfyui/status?url=${encodeURIComponent(url)}`);
}

export function startComfy(path: string, url: string, pythonPath = "") {
  return apiPost<{ running: boolean; managed: boolean; message: string }>("/comfyui/start", {
    path,
    url,
    python_path: pythonPath,
  });
}

// 关闭 ComfyUI（装插件/依赖前需先关）
export function stopComfy(url: string, path = "") {
  return apiPost<{ stopped: boolean; message: string }>("/comfyui/stop", { url, path });
}

// 重启 ComfyUI（装完插件生效）：先关再起，需 path 重新拉起
export function restartComfy(path: string, url: string, pythonPath = "") {
  return apiPost<{ running: boolean; managed: boolean; message: string }>("/comfyui/restart", {
    path,
    url,
    python_path: pythonPath,
  });
}

// 把 ComfyUI 路径/地址落盘到后端，供 start-dev 脚本读取
export function saveComfyConfig(path: string, url: string, pythonPath = "") {
  return apiPost<{ path: string; url: string; python_path: string }>("/comfyui/config", {
    path, url, python_path: pythonPath,
  });
}

export interface SubmitResult {
  ok: boolean;
  prompt_id?: string;
  node_count?: number;
}

export function submitWorkflow(
  templateId: string,
  values: Record<string, unknown>,
  url: string,
  prompt = "",
) {
  return apiPost<SubmitResult>("/comfyui/submit", {
    template_id: templateId,
    values,
    prompt,
    url,
    client_id: comfyClientId(),
  });
}

export interface UploadResult {
  name: string;
  raw: Record<string, unknown>;
}

// 上传图片到 ComfyUI 的 input 目录，返回可供 LoadImage 引用的文件名
export async function uploadImage(file: File, url: string): Promise<UploadResult> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("url", url);
  const resp = await fetch(apiUrl("/comfyui/upload"), { method: "POST", body: fd });
  if (!resp.ok) throw new Error(`API request failed: ${resp.status}`);
  return resp.json();
}

export interface ResultImage {
  filename: string;
  subfolder: string;
  type: string;
}

export interface GenResult {
  status: "pending" | "running" | "completed" | "not_found";
  images: ResultImage[];
  videos: ResultImage[];
  texts: string[];
}

export function getResult(promptId: string, url: string, nodeIds: string[] = []) {
  const extra = nodeIds.length > 0 ? `&node_ids=${encodeURIComponent(nodeIds.join(","))}` : "";
  return apiGet<GenResult>(
    `/comfyui/result?prompt_id=${encodeURIComponent(promptId)}&url=${encodeURIComponent(url)}${extra}`,
  );
}

export interface FinalizedMessage {
  id: string;
  role: "assistant";
  text: string;
  image?: string;
  video?: string;
  regeneration?: RegenerationSnapshot;
}

export interface FinalizeGenerationResponse {
  prompt_id: string;
  durable: boolean;
  complete: boolean;
  messages: FinalizedMessage[];
  images: { message_id: string; display_url: string; persisted: boolean; indexed: boolean; snapshotted: boolean; errors: string[] }[];
}

export function finalizeGeneration(args: {
  threadId: string; repoId: string; promptId: string; prompt: string;
  images: ResultImage[]; videos?: ResultImage[]; outputDir: string; comfyuiUrl: string;
  embed: { baseUrl: string; apiKey: string; modelName: string };
  chat: { baseUrl: string; apiKey: string; modelName: string };
  regeneration?: RegenerationSnapshot;
}) {
  return apiPost<FinalizeGenerationResponse>("/comfyui/finalize-generation", {
    thread_id: args.threadId, repo_id: args.repoId, prompt_id: args.promptId,
    prompt: args.prompt, images: args.images, videos: args.videos || [], output_dir: args.outputDir,
    comfyui_url: args.comfyuiUrl, embed_base: args.embed.baseUrl,
    embed_key: args.embed.apiKey, embed_model: args.embed.modelName,
    chat_base: args.chat.baseUrl, chat_key: args.chat.apiKey, chat_model: args.chat.modelName,
    regeneration: args.regeneration,
  });
}

// 拼出经后端代理的取图地址
export function viewUrl(img: ResultImage, url: string): string {
  const qs = new URLSearchParams({
    filename: img.filename,
    type: img.type,
    subfolder: img.subfolder,
    url,
  });
  return apiUrl(`/comfyui/view?${qs.toString()}`);
}

// 把原图留存到设置的 outputDir，返回本地文件路径
export function saveLocal(args: {
  img: ResultImage; repoId: string; outputDir: string; url: string;
}) {
  return apiPost<{ ok: boolean; path: string }>("/comfyui/save-local", {
    filename: args.img.filename,
    subfolder: args.img.subfolder,
    type: args.img.type,
    repo_id: args.repoId,
    output_dir: args.outputDir,
    url: args.url,
  });
}

// 本地留存原图的访问地址
export function localViewUrl(path: string): string {
  return apiUrl(`/comfyui/local-view?path=${encodeURIComponent(path)}`);
}

// 通用模式留存：把任意图片地址（云端直链 / data URI）存到 outputDir。
// subdir 非空时落到 <repo>/<subdir>/ 子夹（用户上传的参考图 → reference）。
export function saveLocalSrc(args: { src: string; repoId: string; outputDir: string; subdir?: string }) {
  return apiPost<{ ok: boolean; path: string }>("/comfyui/save-local", {
    src: args.src,
    repo_id: args.repoId,
    output_dir: args.outputDir,
    subdir: args.subdir || "",
  });
}

// 从锁定画布回传的完整工作流直接提交生成
export function submitGraph(workflow: unknown, url: string) {
  return apiPost<SubmitResult>("/comfyui/submit_graph", { workflow, url, client_id: comfyClientId() });
}

// 强行停止 ComfyUI 生图（人工打断工作流）：删排队项 + 中断执行。prompt_id 可空。
export function interruptComfy(url: string, promptId = "") {
  return apiPost<{ ok: boolean; deleted: boolean; interrupted: boolean }>(
    "/comfyui/interrupt",
    { url, prompt_id: promptId },
  );
}
