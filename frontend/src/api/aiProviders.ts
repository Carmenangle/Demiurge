import { apiDelete, apiGet, apiPost, apiPut } from "./client";
import type { AIProvider, AIProviderPayload, DiscoverModelsResponse } from "../types";

export function listAIProviders() {
  return apiGet<AIProvider[]>("/ai/providers/");
}

export function createAIProvider(payload: AIProviderPayload) {
  return apiPost<AIProvider>("/ai/providers/", payload);
}

export function updateAIProvider(providerId: string, payload: AIProviderPayload) {
  return apiPut<AIProvider>(`/ai/providers/${providerId}`, payload);
}

export function deleteAIProvider(providerId: string) {
  return apiDelete<{ ok: boolean }>(`/ai/providers/${providerId}`);
}

export function addManualProviderModel(providerId: string, model_name: string) {
  return apiPost<{ ok: boolean }>(`/ai/providers/${providerId}/models`, { model_name });
}

export function discoverProviderModels(base_url: string, api_key: string) {
  return apiPost<DiscoverModelsResponse>("/ai/providers/discover-models", {
    base_url,
    api_key,
  });
}