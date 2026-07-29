import type { ImageModel } from "../stores/settings";
import type { AiImageRegeneration, WorkflowRegeneration } from "../types/chat";

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

export function resolveImageRegenerationModel(
  snapshot: AiImageRegeneration,
  models: readonly ImageModel[],
): ImageModel | undefined {
  return models.find((model) =>
    model.baseUrl === snapshot.model.baseUrl
    && model.modelName === snapshot.model.modelName,
  );
}
