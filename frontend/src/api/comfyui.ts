import { apiDelete, apiGet, apiPost, apiUrl } from "./client";
import { comfyClientId } from "../lib/comfyProgress";
import type { RegenerationSnapshot } from "../types/chat";

export interface ComfyStatus {
  running: boolean;
  managed: boolean;
}

export function comfyStatus(url: string) {
  // 6s 超时：NodeCard 等组件用它做 5s 轮询探活。后端 is_up 最坏可达 ~10s（HTTP 5s + TCP 5s），
  // 且 8010 繁忙时响应更慢——若无超时，单次请求挂起会让调用方的轮询链永久中断（表现为
  // 「ComfyUI 未启动，等待中…」后不再加载）。
  // 30s：插画链每个槽提交前都会探一次状态，999 模式 13 个并发探测曾把 6s 超时挤爆
  // （2026-09-24 实锤：5/13 张 stage=submit「请求超时（超过 6 秒）」）。
  return apiGet<ComfyStatus>(`/comfyui/status?url=${encodeURIComponent(url)}`, 30_000);
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

// 把 ComfyUI 路径/地址落盘到后端，供 start-dev 脚本读取；后端顺带把该主机登记进白名单
export function saveComfyConfig(path: string, url: string, pythonPath = "") {
  return apiPost<{
    path: string;
    url: string;
    python_path: string;
    registered: string;       // "loopback" / 主机名 / "" = 未登记
    register_error: string;   // 未登记的原因（保存仍成功）
  }>("/comfyui/config", {
    path, url, python_path: pythonPath,
  });
}

// 地址簿「新增机台」：只放宽 SSRF 白名单，**不改默认地址**（与 /config 的分工）。
// 默认地址那条由 saveComfyConfig 顺带登记；这条让第二、第三台机不必顶掉当前默认机。
export function registerComfyHost(url: string) {
  return apiPost<{ registered: string }>("/comfyui/hosts", { url });
}

// 地址簿删条目：幂等（不在簿子里也 200）；环境变量声明的机台会 400，调用方静默忽略。
export function unregisterComfyHost(url: string) {
  return apiDelete<{ removed: boolean }>(`/comfyui/hosts?url=${encodeURIComponent(url)}`);
}

export interface SubmitResult {
  ok: boolean;
  prompt_id?: string;
  node_count?: number;
}

// 提交类请求的超时口径（2026-10-31 实锤改的 60→90）。
// 后端一次提交的真实耗时＝节点清单探测（冷首轮实测 22s、预算封顶 40s；120s 内二次提交≈0）+
// /prompt 30s + 接单救回窗口 25s。60s 那道墙比后端自己的救回窗口还短，等于「后端还在正常
// 干活、前端先判死」——图其实生成了，槽位却已删（两条孤儿产物就是这么来的）。
const SUBMIT_TIMEOUT_MS = 90_000;
// 文案必须点名「ComfyUI 提交」：默认文案「需求太复杂，建议把需求拆小」是 LLM 场景的，
// 对生图提交完全误导——用户在多元数据插入面板里既没有「生图模型」也没有引擎可换，
// 无处可拆（2026-10-19 与 2026-10-31 两次实锤）。
const SUBMIT_TIMEOUT_HINT =
  "ComfyUI 未在时限内接受本次任务：可能队列里还有别的任务在跑、正在加载模型，"
  + "或工作流节点过多。这不是需求太复杂，无需拆小重发——"
  + "请确认 ComfyUI 空闲后点「重新生成」；反复出现时看「后台活动」里的耗时。";

export function submitWorkflow(
  templateId: string,
  values: Record<string, unknown>,
  url: string,
  prompt = "",
  loras: { name: string; weight: number }[] = [],
  loraMode: "none" | "single" | "multi" = "single",
) {
  // 提交阶段必须有超时：后端挂起时前端要能失败并走自愈/失败槽（2026-08-29 实锤：
  // 无超时 ⇒ 槽位静默卡 pending，既不失败也不自愈，只能刷新页面靠孤儿清理）。
  return apiPost<SubmitResult>("/comfyui/submit", {
    template_id: templateId,
    values,
    prompt,
    url,
    client_id: comfyClientId(),
    loras,
    lora_mode: loraMode,
  }, SUBMIT_TIMEOUT_MS, undefined, SUBMIT_TIMEOUT_HINT);
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
  status: "pending" | "running" | "completed" | "failed" | "not_found";
  /** 第 59 刀（队列感知停顿）：status=pending 时附带 —— ComfyUI 有任务在执行 ⇒ 队列在推进 */
  queue_busy?: boolean;
  error?: string;
  images: ResultImage[];
  videos: ResultImage[];
  audios: ResultImage[];
  texts: string[];
}

export function getResult(promptId: string, url: string, nodeIds: string[] = []) {
  const extra = nodeIds.length > 0 ? `&node_ids=${encodeURIComponent(nodeIds.join(","))}` : "";
  return apiGet<GenResult>(
    `/comfyui/result?prompt_id=${encodeURIComponent(promptId)}&url=${encodeURIComponent(url)}${extra}`,
  );
}

// ---- 在途出图登记（2026-10-19）----
// localStorage 存根换浏览器/清缓存就丢，服务端这份让「后台活动」跨端可见。
// 真源分工：这里只回答「prompt_id 属于哪个 thread」，任务结果仍走 getResult/归档链。
export interface ComfyPendingItem {
  prompt_id: string;
  thread_id: string;
  created_at: number;   // epoch 秒
  updated_at?: number;
}

export function listComfyPending() {
  // 轮询类接口必须带超时：后端挂起时否则 await 永不返回，面板轮询链会静默断掉。
  return apiGet<{ items: ComfyPendingItem[] }>("/comfyui/pending", 8_000);
}

export function registerComfyPending(promptId: string, threadId: string, createdAt: number) {
  return apiPost<{ ok: boolean; item: ComfyPendingItem }>(
    "/comfyui/pending",
    { prompt_id: promptId, thread_id: threadId, created_at: createdAt / 1000 },
    8_000,
  );
}

export function clearComfyPending(promptId: string) {
  return apiPost<{ ok: boolean; removed: boolean }>(
    "/comfyui/pending/finish", { prompt_id: promptId }, 8_000,
  );
}

export interface FinalizedMessage {
  id: string;
  role: "assistant";
  text: string;
  image?: string;
  video?: string;
  audio?: string;
  regeneration?: RegenerationSnapshot;
}

export interface FinalizeGenerationResponse {
  prompt_id: string;
  durable: boolean;
  complete: boolean;
  messages: FinalizedMessage[];
  images: { message_id: string; display_url: string; persisted: boolean; indexed: boolean; snapshotted: boolean; errors: string[] }[];
  target?: { message_id: string; slot_id: string; media_type: "image" | "video" | "audio"; url: string } | null;
}

export function finalizeGeneration(args: {
  threadId: string; repoId: string; promptId: string; prompt: string;
  images: ResultImage[]; videos?: ResultImage[]; audios?: ResultImage[]; outputDir: string; comfyuiUrl: string;
  embed: { baseUrl: string; apiKey: string; modelName: string };
  chat: { baseUrl: string; apiKey: string; modelName: string };
  regeneration?: RegenerationSnapshot;
  templateName?: string; modelName?: string; loraNames?: string[];
  target?: { messageId: string; slotId: string };
  baseSlotRef?: { messageId: string; slotId: string };
}) {
  return apiPost<FinalizeGenerationResponse>("/comfyui/finalize-generation", {
    thread_id: args.threadId, repo_id: args.repoId, prompt_id: args.promptId,
    prompt: args.prompt, images: args.images, videos: args.videos || [], audios: args.audios || [], output_dir: args.outputDir,
    comfyui_url: args.comfyuiUrl, embed_base: args.embed.baseUrl,
    embed_key: args.embed.apiKey, embed_model: args.embed.modelName,
    chat_base: args.chat.baseUrl, chat_key: args.chat.apiKey, chat_model: args.chat.modelName,
    regeneration: args.regeneration,
    template_name: args.templateName || "",
    model_name: args.modelName || "",
    lora_names: args.loraNames ? args.loraNames.join(",") : "",
    target_message_id: args.target?.messageId || "",
    target_slot_id: args.target?.slotId || "",
    base_slot_ref: args.baseSlotRef || null,
  });
}

// 音频分条按顺序拼接成完整版（后端 ffmpeg concat + 落盘回写快照）
export function mergeAudio(args: { threadId: string; messageId: string; force?: boolean }) {
  return apiPost<{ ok: boolean; url: string }>("/comfyui/merge-audio", {
    thread_id: args.threadId,
    message_id: args.messageId,
    force: !!args.force,
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

// P5 首尾帧顺序链：把 ComfyUI 生成的产物图（output 目录）转成视频模板可引用的
// input 文件名（fetch 取回 → 上传回 input 目录）。视频模板的 first_frame_image/
// last_frame_image 期望 LoadImage 可引用的 input 文件名（B3 同套路）。
export async function moveComfyOutputToInput(img: ResultImage, url: string): Promise<string> {
  const blob = await (await fetch(viewUrl(img, url))).blob();
  const file = new File([blob], img.filename, { type: blob.type || "image/png" });
  return (await uploadImage(file, url)).name;
}

// W3 转场视频：把「上尾帧图」（任意可 fetch 的 URL，如 localViewUrl 包装后的本地留存路径）
// 取回并上传回 ComfyUI input 目录，供转场视频模板的 first_frame_image（图片1=起点）引用。
export async function uploadRemoteImageToInput(srcUrl: string, comfyuiUrl: string): Promise<string> {
  const resp = await fetch(srcUrl);
  if (!resp.ok) throw new Error(`获取转场参考图失败：HTTP ${resp.status}`);
  const blob = await resp.blob();
  const name = srcUrl.split(/[\\/]/).pop() || "ref.png";
  const file = new File([blob], name, { type: blob.type || "image/png" });
  return (await uploadImage(file, comfyuiUrl)).name;
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
  // 与 submitWorkflow 同一条超时口径与文案：提交阶段必须有超时，且不能拿 LLM 的
  // 「需求拆小」误导用户（2026-10-19 / 2026-10-31 两次实锤）。
  return apiPost<SubmitResult>(
    "/comfyui/submit_graph",
    { workflow, url, client_id: comfyClientId() },
    SUBMIT_TIMEOUT_MS,
    undefined,
    SUBMIT_TIMEOUT_HINT,
  );
}

// 强行停止 ComfyUI 生图（人工打断工作流）：删排队项 + 中断执行。prompt_id 可空。
export function interruptComfy(url: string, promptId = "") {
  return apiPost<{ ok: boolean; deleted: boolean; interrupted: boolean }>(
    "/comfyui/interrupt",
    { url, prompt_id: promptId },
  );
}
