import { apiGet, apiPost, apiPut } from "./client";
import type { EmbedModel } from "../stores/settings";

// 一条 LoRA 触发词记录（对应后端 lora_index._row_to_item）
export interface LoraTriggerItem {
  lora_name: string;      // 相对 loras 目录，与 ComfyUI 的 LoraLoader.lora_name 一致
  triggers: string[];
  note: string;
  suggested_weight: number;
  suggested_prompt: string;
  source: string;         // metadata=从文件头提取 / sidecar=从 .civitai.info / manual=手填
  missing: boolean;       // 磁盘上已找不到该文件（记录保留，不删）
  updated_at: number;
}

// 同步进度（对应后端 lora_index._PROGRESS）
export interface LoraSyncProgress {
  running: boolean;
  done: number;
  total: number;
  current: string;
  added: number;      // 新增
  updated: number;    // 自动重提
  kept: number;       // 手填条目，保留未动
  missing: number;    // 本轮标记为消失的
  error: string;
  finished: boolean;
}

// 嵌入配置的 wire 形状，对应后端 ai_common.EmbedModelReq。
// 触发词主存 SQLite，这里只为向量库镜像服务，故嵌入配置缺失也不影响主流程。
function embedBody(embed: EmbedModel) {
  return {
    embed_base_url: embed.baseUrl || "",
    embed_api_key: embed.apiKey || "",
    embed_model: embed.modelName || "embedding-3",
    embed_mode: embed.mode || "remote",
    embed_model_dir: embed.modelDir || "",
    reranker_model_dir: embed.rerankerDir || "",
  };
}

export function listLoras() {
  return apiGet<{ items: LoraTriggerItem[] }>("/loras/");
}

// 磁盘上实际存在的 LoRA 文件（供选择器下拉，不依赖触发词同步）
export interface AvailableLora {
  lora_name: string;      // 相对 loras 目录，与 ComfyUI 的 LoraLoader.lora_name 一致
  has_triggers: boolean;  // 触发词表里是否已录该文件（未录也能选，只是不自动注入触发词）
  trigger_status?: "configured" | "not_required" | "unconfirmed";
  suggested_weight: number;
}

export function availableLoras(modelsDir: string) {
  return apiGet<{ items: AvailableLora[] }>(
    `/loras/available?models_dir=${encodeURIComponent(modelsDir)}`,
  );
}

export function getSyncProgress() {
  return apiGet<LoraSyncProgress>("/loras/sync-progress");
}

// full=true 连手填条目一并重提（默认保护手填内容不被覆盖）
export function syncLoras(embed: EmbedModel, modelsDir: string, full = false) {
  return apiPost<{ total: number; already_running: boolean }>(
    "/loras/sync",
    { ...embedBody(embed), models_dir: modelsDir, full },
  );
}

// 保存即标记为手填，此后同步不再覆盖
export function saveLoraTriggers(
  embed: EmbedModel, loraName: string, triggers: string[], note = "", suggestedWeight = 0.8,
  suggestedPrompt = "",
) {
  return apiPut<LoraTriggerItem>(
    "/loras/item",
    {
      ...embedBody(embed), lora_name: loraName, triggers, note,
      suggested_weight: suggestedWeight, suggested_prompt: suggestedPrompt,
    },
  );
}

// 用 POST 而非 DELETE：嵌入配置要随 body 传，DELETE 带 body 在部分代理上会被剥掉
export function deleteLoraTriggers(embed: EmbedModel, loraName: string) {
  return apiPost<{ ok: boolean }>(
    "/loras/delete",
    { ...embedBody(embed), lora_name: loraName },
  );
}
