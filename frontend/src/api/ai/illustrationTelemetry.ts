// 插画/音频提交链遥测（原 api/ai.ts 遥测段，2026-10-24 按资源域拆分）：
// 失败上报 / 提交认领 / 阶段标记 / 提交登记 / 音频槽位。全部 fire-and-forget 语义（遥测不得阻断出图）。
import { apiPost } from "../client";

export function reportIllustrationFailure(payload: {
  threadId: string; repoId: string; messageId: string; slotId: string;
  stage: string; error: string; promptId?: string; comfyuiUrl?: string;
}) {
  return apiPost<{ ok: boolean; removed: boolean; cancelled?: { deleted: boolean; interrupted: boolean } }>("/ai/image-agent/illustration-failure", {
    thread_id: payload.threadId,
    repo_id: payload.repoId,
    message_id: payload.messageId,
    slot_id: payload.slotId,
    stage: payload.stage,
    error: payload.error,
    prompt_id: payload.promptId || "",
    comfyui_url: payload.comfyuiUrl || "",
  });
}

export function claimIllustrationSubmission(payload: {
  threadId: string; messageId: string; slotId: string;
}) {
  return apiPost<{ ok: boolean; claimed: boolean }>("/ai/image-agent/illustration-claim", {
    thread_id: payload.threadId,
    message_id: payload.messageId,
    slot_id: payload.slotId,
  });
}

/**
 * 插画提交链的阶段标记（2026-10-19）。
 *
 * claim → failed 之间原本是观测黑洞：实锤一次卡 7 分 47 秒，后端 trace 零事件，
 * 只能靠时间线倒推「卡在编译还是提交」。这里在四个节点各报一条，纯观测。
 * ⚠ 必须 fire-and-forget（调用方 `.catch(() => undefined)`）：遥测不得阻断出图。
 */
export function reportIllustrationStage(payload: {
  threadId: string; repoId?: string; turnId?: string; messageId: string; slotId: string;
  stage: "claim" | "compile" | "upload" | "submit";
  status?: "start" | "ok" | "error";
  detail?: string; elapsedMs?: number;
}) {
  return apiPost<{ ok: boolean }>("/ai/image-agent/illustration-stage", {
    thread_id: payload.threadId,
    repo_id: payload.repoId || "",
    turn_id: payload.turnId || "",
    message_id: payload.messageId,
    slot_id: payload.slotId,
    stage: payload.stage,
    status: payload.status || "start",
    detail: payload.detail || "",
    elapsed_ms: payload.elapsedMs,
  }, 8_000);   // 遥测用短超时：网络差时别拖住主流程
}

export function reportIllustrationSubmission(payload: {
  threadId: string; repoId: string; turnId?: string; messageId: string; slotId: string;
  templateId: string; promptId: string; prompt: string; promptProfile: string;
  loraName?: string; loraWeight?: number; latentWidth: number; latentHeight: number;
  loraMode?: "none" | "single" | "multi"; loraNames?: string[];
  valueKeys: string[];
  source?: "automatic" | "manual";
}) {
  return apiPost<{ ok: boolean }>("/ai/image-agent/illustration-submission", {
    thread_id: payload.threadId,
    repo_id: payload.repoId,
    turn_id: payload.turnId || "",
    message_id: payload.messageId,
    slot_id: payload.slotId,
    template_id: payload.templateId,
    prompt_id: payload.promptId,
    prompt: payload.prompt,
    prompt_profile: payload.promptProfile,
    lora_name: payload.loraName || "",
    lora_weight: payload.loraWeight,
    lora_mode: payload.loraMode || "single",
    lora_names: payload.loraNames || [],
    latent_width: payload.latentWidth,
    latent_height: payload.latentHeight,
    value_keys: payload.valueKeys,
    source: payload.source || "automatic",
  });
}

export function reportAudioSubmission(payload: {
  threadId: string; repoId: string; turnId?: string; messageId: string; slotId: string;
  speaker: string; text: string; voiceRef: string; templateId: string; promptId: string;
  emotion?: Record<string, number>; valueKeys: string[];
  source?: "automatic" | "manual";
}) {
  return apiPost<{ ok: boolean }>("/ai/image-agent/audio-submission", {
    thread_id: payload.threadId,
    repo_id: payload.repoId,
    turn_id: payload.turnId || "",
    message_id: payload.messageId,
    slot_id: payload.slotId,
    speaker: payload.speaker,
    text: payload.text,
    voice_ref: payload.voiceRef,
    template_id: payload.templateId,
    prompt_id: payload.promptId,
    emotion: payload.emotion || {},
    value_keys: payload.valueKeys,
    source: payload.source || "automatic",
  });
}

export function ensureAudioSlot(payload: {
  threadId: string; messageId: string; slotId: string;
  speaker?: string; seq?: number; total?: number;
}) {
  return apiPost<{ ok: boolean }>("/ai/image-agent/ensure-audio-slot", {
    thread_id: payload.threadId,
    message_id: payload.messageId,
    slot_id: payload.slotId,
    speaker: payload.speaker || "",
    seq: payload.seq,
    total: payload.total,
  });
}
