import type { LoraTriggerItem } from "../api/loras";
import type { PortOp } from "../api/ai";
import { qualityPromptTagsFromSuggestion, splitPromptTags } from "./chatGeneration";

type ApiNode = { class_type?: string; inputs?: Record<string, unknown> };
type ApiGraph = Record<string, ApiNode>;

const TEXT_FIELDS = new Set(["text", "text_g", "text_l", "prompt", "positive"]);
const WEIGHT_FIELDS = new Set([
  "strength_model", "strength_clip", "strength", "model_strength", "clip_strength",
  "lora_strength", "weight",
]);

function tagKey(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

function uniqueTags(values: readonly string[]): string[] {
  const seen = new Set<string>();
  return values.filter((value) => {
    const key = tagKey(value);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function distillLoraSuggestedPrompt(
  suggestion: string,
  triggers: readonly string[],
): string[] {
  const triggerKeys = new Set(triggers.map(tagKey));
  return qualityPromptTagsFromSuggestion(suggestion, triggers)
    .filter((tag) => !triggerKeys.has(tagKey(tag)));
}

export function mergeWorkflowLoraPrompt(
  prompt: string,
  triggers: readonly string[],
  qualityTags: readonly string[],
): string {
  const exactTriggers = uniqueTags(triggers);
  const triggerKeys = new Set(exactTriggers.map(tagKey));
  const exactQuality = uniqueTags(qualityTags).filter((tag) => !triggerKeys.has(tagKey(tag)));
  const injectedKeys = new Set([...exactTriggers, ...exactQuality].map(tagKey));
  const remaining = splitPromptTags(prompt).filter((tag) => !injectedKeys.has(tagKey(tag)));
  const contentLine = uniqueTags([...exactQuality, ...remaining]).join(", ");
  return exactTriggers.length > 0
    ? `${exactTriggers.join(", ")}\n${contentLine}`.trimEnd()
    : contentLine;
}

function linkedNodeId(value: unknown): string | null {
  if (!Array.isArray(value) || value.length < 2) return null;
  const id = value[0];
  return typeof id === "string" || typeof id === "number" ? String(id) : null;
}

function positiveTextNodeIds(graph: ApiGraph): string[] {
  const roots: string[] = [];
  for (const node of Object.values(graph)) {
    if (!/sampler/i.test(node.class_type || "")) continue;
    const id = linkedNodeId(node.inputs?.positive);
    if (id && !roots.includes(id)) roots.push(id);
  }
  const found: string[] = [];
  const visited = new Set<string>();
  const visit = (id: string) => {
    if (visited.has(id)) return;
    visited.add(id);
    const node = graph[id];
    if (!node) return;
    if (/clip.*text.*encode|text.*encode/i.test(node.class_type || "")) found.push(id);
    for (const value of Object.values(node.inputs || {})) {
      const upstream = linkedNodeId(value);
      if (upstream) visit(upstream);
    }
  };
  roots.forEach(visit);
  return found;
}

export interface WorkflowLoraProposal {
  loraNames: string[];
  triggers: string[];
  qualityTags: string[];
  weightChanges: number;
  promptChanges: number;
  ops: PortOp[];
}

export function workflowLoraNames(graphValue: unknown): string[] {
  if (!graphValue || typeof graphValue !== "object" || Array.isArray(graphValue)) return [];
  const graph = graphValue as ApiGraph;
  return uniqueTags(Object.values(graph).flatMap((node) => {
    if (!/lora/i.test(node.class_type || "")) return [];
    const name = typeof node.inputs?.lora_name === "string" ? node.inputs.lora_name.trim() : "";
    return name ? [name] : [];
  }));
}

export function buildWorkflowLoraProposal(
  graphValue: unknown,
  records: readonly LoraTriggerItem[],
): WorkflowLoraProposal | null {
  if (!graphValue || typeof graphValue !== "object" || Array.isArray(graphValue)) return null;
  const graph = graphValue as ApiGraph;
  const byName = new Map(records.filter((item) => !item.missing).map((item) => [item.lora_name, item]));
  const selected: Array<{ id: string; node: ApiNode; record: LoraTriggerItem }> = [];
  for (const [id, node] of Object.entries(graph)) {
    if (!/lora/i.test(node.class_type || "")) continue;
    const name = typeof node.inputs?.lora_name === "string" ? node.inputs.lora_name.trim() : "";
    const record = byName.get(name);
    if (record) selected.push({ id, node, record });
  }
  if (selected.length === 0) return null;

  const triggers = uniqueTags(selected.flatMap(({ record }) => record.triggers));
  const qualityTags = uniqueTags(selected.flatMap(({ record }) =>
    distillLoraSuggestedPrompt(record.suggested_prompt || "", record.triggers),
  )).filter((tag) => !new Set(triggers.map(tagKey)).has(tagKey(tag)));
  const ops: PortOp[] = [];
  let weightChanges = 0;
  for (const { id, node, record } of selected) {
    for (const [input, value] of Object.entries(node.inputs || {})) {
      if (!WEIGHT_FIELDS.has(input) || typeof value !== "number") continue;
      if (value === record.suggested_weight) continue;
      ops.push({ node_id: id, input, action: "set_widget", value: record.suggested_weight });
      weightChanges += 1;
    }
  }

  let promptChanges = 0;
  for (const id of positiveTextNodeIds(graph)) {
    const node = graph[id];
    for (const [input, value] of Object.entries(node.inputs || {})) {
      if (!TEXT_FIELDS.has(input) || typeof value !== "string") continue;
      const merged = mergeWorkflowLoraPrompt(value, triggers, qualityTags);
      if (merged === value) continue;
      ops.push({ node_id: id, input, action: "set_widget", value: merged });
      promptChanges += 1;
    }
  }

  return {
    loraNames: uniqueTags(selected.map(({ record }) => record.lora_name)),
    triggers,
    qualityTags,
    weightChanges,
    promptChanges,
    ops,
  };
}
