import client from "./client";

export interface ProviderOut {
  id: string;
  name: string;
  base_url: string;
  api_key_masked: string;
  default_model: string;
  max_image_px: number;
  max_tokens: number;
  is_remote: boolean;
  created_at: string;
}

export interface ProviderCreate {
  name: string;
  base_url: string;
  api_key?: string;
  default_model?: string;
  max_image_px?: number;
  max_tokens?: number;
}

export interface ProviderUpdate {
  name?: string;
  base_url?: string;
  api_key?: string;
  default_model?: string;
  max_image_px?: number;
  max_tokens?: number;
}

export const providersApi = {
  list: () => client.get<ProviderOut[]>("/providers/").then((r) => r.data),
  create: (body: ProviderCreate) =>
    client.post<ProviderOut>("/providers/", body).then((r) => r.data),
  update: (id: string, body: ProviderUpdate) =>
    client.patch<ProviderOut>(`/providers/${id}`, body).then((r) => r.data),
  delete: (id: string) => client.delete(`/providers/${id}`),
  fetchModels: (id: string) =>
    client.get<{ models: string[] }>(`/providers/${id}/models`).then((r) => r.data.models),
};
