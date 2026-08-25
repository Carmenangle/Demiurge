import type { ImageModel } from "../stores/settings";
import type {
  AiImageRegeneration, RegenerationSnapshot, TemplateRegeneration, WorkflowRegeneration,
} from "../types/chat";

export function workflowRegenerationSnapshot(
  graph: unknown,
  comfyuiUrl: string,
  outputNodeIds: string[],
  prompt = "",
  templateName?: string,
  modelName?: string,
  loraNames?: string[],
): WorkflowRegeneration {
  return {
    kind: "workflow",
    graph: JSON.parse(JSON.stringify(graph)),
    comfyuiUrl,
    outputNodeIds: [...outputNodeIds],
    prompt,
    templateName,
    modelName,
    loraNames: loraNames ? [...loraNames] : undefined,
  };
}

export function templateRegenerationSnapshot(
  templateId: string,
  values: Record<string, unknown>,
  comfyuiUrl: string,
  outputNodeIds: string[],
  prompt: string,
  loras: { name: string; weight: number }[] = [],
  loraMode: "none" | "single" | "multi" = "single",
  characterLoraActor = "",
): TemplateRegeneration {
  return {
    kind: "template",
    templateId,
    values: JSON.parse(JSON.stringify(values)),
    comfyuiUrl,
    outputNodeIds: [...outputNodeIds],
    prompt,
    ...(loras.length ? { loras: JSON.parse(JSON.stringify(loras)) } : {}),
    ...(loraMode !== "single" ? { loraMode } : {}),
    ...(characterLoraActor ? { characterLoraActor } : {}),
  };
}

export function comfyRegenerationUrl(snapshot: RegenerationSnapshot | undefined): string {
  return snapshot?.kind === "workflow" || snapshot?.kind === "template"
    ? snapshot.comfyuiUrl
    : "";
}

export function regenerationPrompt(snapshot: RegenerationSnapshot | undefined): string {
  return snapshot?.kind === "workflow" || snapshot?.kind === "template"
    ? snapshot.prompt
    : "";
}

export function legacyGenerationPrompt(
  imageUrl: string,
  generations: readonly { image_url: string; prompt: string }[],
): string {
  return generations.find((item) => item.image_url === imageUrl)?.prompt.trim() || "";
}

// ===== 从 ComfyUI API 图提取「用什么模板/主模型/LoRA 生成」的元数据 =====
// capturedGraph 是 API 格式 {节点id: {class_type, inputs}}（graphToPrompt 产物）。
// 这些信息用于卡片展示「用哪个模板、哪个主模型、哪个 LoRA 生成的」，与提示词分离。

type ApiGraphNode = { class_type?: string; inputs?: Record<string, unknown> };
type ApiGraph = Record<string, ApiGraphNode>;

// UI 格式（ComfyUI 原始 workflow JSON：{nodes:[{type,title,widgets_values}]}）兜底提取。
// 画布工具卡 captured 可能因节点 prop 未回流而为旧值/空，此时退回 wfDraft 提取元数据。
type UiGraphNode = { type?: string; title?: string; widgets_values?: unknown[] };

function uiNodes(graphValue: unknown): UiGraphNode[] {
  if (!graphValue || typeof graphValue !== "object" || Array.isArray(graphValue)) return [];
  const nodes = (graphValue as { nodes?: unknown }).nodes;
  return Array.isArray(nodes) ? (nodes as UiGraphNode[]) : [];
}

function firstWidgetString(node: UiGraphNode): string {
  const w = node.widgets_values;
  if (Array.isArray(w) && typeof w[0] === "string" && w[0].trim()) return w[0].trim();
  return "";
}

function linkedNodeId(value: unknown): string | null {
  if (Array.isArray(value)) {
    const id = value[0];
    return typeof id === "string" || typeof id === "number" ? String(id) : null;
  }
  return null;
}

function firstTextValue(inputs: Record<string, unknown>): string {
  for (const key of ["text", "text_g", "text_l", "prompt", "positive"]) {
    const v = inputs[key];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return "";
}

/** 主模型：图上第一个 checkpoint/unet 加载器（CheckpointLoaderSimple 等）的模型名；API 格式取不到时回退 UI 格式 widgets_values */
export function workflowModelName(graphValue: unknown): string {
  if (!graphValue || typeof graphValue !== "object" || Array.isArray(graphValue)) return "";
  const graph = graphValue as ApiGraph;
  for (const node of Object.values(graph)) {
    const cls = node.class_type || "";
    if (!/checkpoint|unet/i.test(cls)) continue;
    const inputs = node.inputs || {};
    for (const key of ["ckpt_name", "unet_name"]) {
      const v = inputs[key];
      if (typeof v === "string" && v.trim()) return v.trim();
    }
  }
  for (const node of uiNodes(graphValue)) {
    if (!/checkpoint|unet/i.test(node.type || "")) continue;
    const name = firstWidgetString(node);
    if (name) return name;
  }
  return "";
}

/** LoRA 列表：图上所有 lora 加载器的 lora_name（去重保序）；API 格式取不到时回退 UI 格式 widgets_values */
export function workflowLoraNameList(graphValue: unknown): string[] {
  if (!graphValue || typeof graphValue !== "object" || Array.isArray(graphValue)) return [];
  const graph = graphValue as ApiGraph;
  const seen = new Set<string>();
  const out: string[] = [];
  for (const node of Object.values(graph)) {
    if (!/lora/i.test(node.class_type || "")) continue;
    const v = node.inputs?.lora_name;
    const name = typeof v === "string" ? v.trim() : "";
    if (!name || seen.has(name)) continue;
    seen.add(name);
    out.push(name);
  }
  if (out.length > 0) return out;
  for (const node of uiNodes(graphValue)) {
    if (!/lora/i.test(node.type || "")) continue;
    const name = firstWidgetString(node);
    if (!name || seen.has(name)) continue;
    seen.add(name);
    out.push(name);
  }
  return out;
}

/** 正向提示词：从采样器的 positive 上游链找 CLIPTextEncode 文本（找不到则取任意 CLIPTextEncode） */
export function workflowPositivePrompt(graphValue: unknown): string {
  if (!graphValue || typeof graphValue !== "object" || Array.isArray(graphValue)) return "";
  const graph = graphValue as ApiGraph;
  const samplerInputs = Object.values(graph).filter((n) => /sampler/i.test(n.class_type || ""));
  const roots = samplerInputs
    .map((n) => linkedNodeId(n.inputs?.positive))
    .filter((id): id is string => !!id);
  const visited = new Set<string>();
  const found: string[] = [];
  const visit = (id: string) => {
    if (visited.has(id)) return;
    visited.add(id);
    const node = graph[id];
    if (!node) return;
    if (/clip.*text.*encode|text.*encode/i.test(node.class_type || "")) {
      const text = firstTextValue(node.inputs || {});
      if (text) found.push(text);
      return; // 文本链在此终止
    }
    for (const value of Object.values(node.inputs || {})) {
      const upstream = linkedNodeId(value);
      if (upstream) visit(upstream);
    }
  };
  roots.forEach(visit);
  if (found.length > 0) return found[0];
  // 兜底：任意 CLIPTextEncode 的第一段文本
  for (const node of Object.values(graph)) {
    if (!/clip.*text.*encode|text.*encode/i.test(node.class_type || "")) continue;
    const text = firstTextValue(node.inputs || {});
    if (text) return text;
  }
  // 兜底 2：UI 格式（wfDraft）——优先非负面标题的 CLIPTextEncode
  const encoders = uiNodes(graphValue)
    .filter((n) => /clip.*text.*encode|text.*encode/i.test(n.type || ""));
  const positive = encoders.find((n) => !/负面|negative/i.test(n.title || ""));
  const picked = positive || encoders[0];
  if (picked) {
    const text = firstWidgetString(picked);
    if (text) return text;
  }
  return "";
}

export interface WorkflowGenMetadata {
  templateName: string;
  modelName: string;
  loraNames: string[];
  prompt: string;
}

/** 一键提取工作流卡的全部生成元数据（模板名 + 主模型 + LoRA + 真实提示词）。
 *  fallbackGraph：captured（API 格式）提取为空时回退（画布工具卡 captured 未回流时的 wfDraft UI 格式）。 */
export function workflowGenMetadata(
  templateName: string,
  graphValue: unknown,
  fallbackGraph?: unknown,
): WorkflowGenMetadata {
  const loras = workflowLoraNameList(graphValue);
  return {
    templateName: templateName || "",
    modelName: workflowModelName(graphValue)
      || (fallbackGraph ? workflowModelName(fallbackGraph) : ""),
    loraNames: loras.length > 0
      ? loras
      : (fallbackGraph ? workflowLoraNameList(fallbackGraph) : []),
    prompt: workflowPositivePrompt(graphValue)
      || (fallbackGraph ? workflowPositivePrompt(fallbackGraph) : ""),
  };
}

export function resolveImageRegenerationModel(
  snapshot: AiImageRegeneration,
  models: readonly ImageModel[],
): ImageModel | undefined {
  return models.find((model) =>
    model.baseUrl === snapshot.model.baseUrl
    && model.modelName === snapshot.model.modelName,
  );
}
