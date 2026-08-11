import type { ImageModel } from "../stores/settings";
import type {
  AiImageRegeneration, RegenerationSnapshot, TemplateRegeneration, WorkflowRegeneration,
} from "../types/chat";

export function workflowRegenerationSnapshot(
  graph: unknown,
  comfyuiUrl: string,
  outputNodeIds: string[],
): WorkflowRegeneration {
  return {
    kind: "workflow",
    graph: JSON.parse(JSON.stringify(graph)),
    comfyuiUrl,
    outputNodeIds: [...outputNodeIds],
    prompt: "",
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

export function resolveImageRegenerationModel(
  snapshot: AiImageRegeneration,
  models: readonly ImageModel[],
): ImageModel | undefined {
  return models.find((model) =>
    model.baseUrl === snapshot.model.baseUrl
    && model.modelName === snapshot.model.modelName,
  );
}
