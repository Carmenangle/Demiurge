export type DraftParams = {
  positive_prompt?: string;
  negative_prompt?: string;
  checkpoint?: string;
  width?: number;
  height?: number;
};

export type AIProvider = {
  id: string;
  name: string;
  base_url: string;
  provider_type: string;
  default_model: string;
  enabled: boolean;
  models: string[];
};

export type AIProviderPayload = {
  name: string;
  base_url: string;
  api_key: string;
  provider_type: string;
  default_model: string;
  enabled: boolean;
  models: string[];
};

export type DiscoverModelsResponse = {
  ok: boolean;
  models: string[];
  source: string;
  error: string;
};