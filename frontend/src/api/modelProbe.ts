import { apiPost } from "./client";

export type ModelProbeKind = "chat" | "image" | "video" | "embedding" | "embedding-local" | "reranker-local" | "vlm" | "vlm-local";

export interface ModelProbeResult {
  status: "success" | "warning" | "error";
  message: string;
  source?: string;
  billable: boolean;
}

export function probeModel(input: {
  kind: ModelProbeKind;
  baseUrl?: string;
  apiKey?: string;
  modelName?: string;
  modelDir?: string;
  ggufPath?: string;
  proxyUrl?: string;
}) {
  return apiPost<ModelProbeResult>("/ai/model-probe", {
    kind: input.kind,
    base_url: input.baseUrl || "",
    api_key: input.apiKey || "",
    model_name: input.modelName || "",
    model_dir: input.modelDir || "",
    gguf_path: input.ggufPath || "",
    proxy_url: input.proxyUrl || "",
  }, 180000);
}
