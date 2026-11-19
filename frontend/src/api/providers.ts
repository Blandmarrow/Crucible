import client from "./client";

export interface ProviderOut {
  id: string;
  name: string;
  base_url: string;
  api_key_masked: string;
  default_model: string;
  max_image_px: number;
  max_tokens: number;
  timeout_s: number;
  /** Caption requests sent at once (1-16). */
  max_concurrent: number;
  is_remote: boolean;
  /** The explicit setting; null = follow the URL. */
  billed: boolean | null;
  /** The effective value: billed, else is_remote. */
  is_billed: boolean;
  /** What the analyst may send this provider: the stored override (null = follow
   *  `billed`), and the result laid over the defaults. */
  analyst_privacy: Partial<Record<string, boolean>> | null;
  analyst_privacy_effective: Record<string, boolean>;
  /** The tool-call check's verdict per model (docs/dev/analyst/tools.md); a model
   *  missing here was never checked, and the analyst offers it no tools. */
  tools_probe: Record<string, ToolProbe>;
  created_at: string;
}

export interface ToolProbe {
  ok: boolean;
  /** ISO time, UTC. */
  checked_at: string;
  detail: string;
}

export interface ProviderCreate {
  name: string;
  base_url: string;
  api_key?: string;
  default_model?: string;
  max_image_px?: number;
  max_tokens?: number;
  timeout_s?: number;
  max_concurrent?: number;
  billed?: boolean;
}

export interface ProviderUpdate {
  name?: string;
  base_url?: string;
  api_key?: string;
  default_model?: string;
  max_image_px?: number;
  max_tokens?: number;
  timeout_s?: number;
  max_concurrent?: number;
  billed?: boolean;
  analyst_privacy?: Partial<Record<string, boolean>>;
  /** Back to NULL: follow `billed` again. */
  analyst_privacy_reset?: boolean;
}

export const providersApi = {
  list: () => client.get<ProviderOut[]>("/providers/").then((r) => r.data),
  create: (body: ProviderCreate) =>
    client.post<ProviderOut>("/providers/", body).then((r) => r.data),
  update: (id: string, body: ProviderUpdate) =>
    client.patch<ProviderOut>(`/providers/${id}`, body).then((r) => r.data),
  delete: (id: string) => client.delete(`/providers/${id}`),
  /** Two real calls to the model, so it can take a while; a 502 is "no verdict". */
  probeTools: (id: string, model?: string) =>
    client.post<ToolProbe & { model: string }>(`/providers/${id}/probe-tools`, model ? { model } : {}, { timeout: 330_000 })
      .then((r) => r.data),
  fetchModels: (id: string) =>
    client.get<{ models: string[] }>(`/providers/${id}/models`).then((r) => r.data.models),
};
